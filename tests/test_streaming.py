"""Streaming sink tests (ADR-0007): the second sink signature, rate
control, and the HTTP sink. Server-backed tests importorskip httpx so a
core-only checkout stays green; CI installs the streaming extra."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chaff.engine import run
from chaff.sinks import is_stream_sink, rate_limited
from chaff.spec import load_spec


def base_stream_spec(**sink_options):
    return load_spec({
        "name": "events",
        "seed": 1,
        "rows": 5,
        "columns": [
            {"name": "id", "generator": "row_id"},
            {"name": "who", "generator": "full_name"},
        ],
        "output": {"format": "ndjson"},
        "sink": {"sink": "http", "options": sink_options},
    })


# ── Rate control (no network) ────────────────────────────────────────

def test_rate_limited_paces_between_items():
    calls = []
    out = list(rate_limited([b"a", b"b", b"c"], rate=10, sleep=calls.append))
    assert out == [b"a", b"b", b"c"]
    # N items -> N-1 gaps, each 1/rate seconds.
    assert calls == [pytest.approx(0.1), pytest.approx(0.1)]


def test_rate_limited_unthrottled_never_sleeps():
    calls = []
    out = list(rate_limited([b"a", b"b"], rate=None, sleep=calls.append))
    assert out == [b"a", b"b"]
    assert calls == []


# ── Engine negotiation ───────────────────────────────────────────────

def test_http_is_registered_as_streaming():
    assert is_stream_sink("http")


def test_streaming_with_a_whole_file_format_fails_fast():
    """A format with no per-record encoder (sql) can't stream — the engine
    should refuse clearly before touching the network."""
    spec = load_spec({
        "name": "events", "seed": 1, "rows": 5,
        "columns": [{"name": "id", "generator": "row_id"}],
        "output": {"format": "sql"},
        "sink": {"sink": "http", "options": {"url": "http://127.0.0.1:1/none"}},
    })
    with pytest.raises(KeyError, match="no per-record encoder"):
        run(spec)


# ── HTTP sink (server-backed) ────────────────────────────────────────

@contextmanager
def capture_server(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()


def _recording_handler(store, status=200):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            store.append({"auth": self.headers.get("Authorization"),
                          "body": self.rfile.read(n)})
            self.send_response(status)
            self.end_headers()

        def log_message(self, *a):
            pass
    return H


def test_http_sink_delivers_batches_with_auth_and_no_secret_leak():
    pytest.importorskip("httpx")
    store = []
    with capture_server(_recording_handler(store)) as url:
        spec = base_stream_spec(url=f"{url}/ingest", batch_size=2,
                                headers={"Authorization": "Bearer SECRET"}, rate=0)
        receipt = run(spec)
    # 5 records, batch of 2 -> 3 requests.
    assert len(store) == 3
    assert store[0]["auth"] == "Bearer SECRET"
    assert "SECRET" not in receipt  # secrets never echoed into the receipt
    records = [json.loads(line) for s in store
               for line in s["body"].decode().splitlines() if line]
    assert len(records) == 5
    assert records[0]["id"] == 1


def test_http_sink_raises_loud_on_4xx():
    pytest.importorskip("httpx")
    store = []
    with capture_server(_recording_handler(store, status=400)) as url:
        spec = base_stream_spec(url=f"{url}/bad", max_retries=1)
        with pytest.raises(RuntimeError, match="returned 400"):
            run(spec)
    assert len(store) == 1  # 4xx is not retried


def _delivered_records(store):
    return [json.loads(line) for s in store
            for line in s["body"].decode().splitlines() if line]


def test_streaming_max_records_caps_delivery():
    """max_records below the spec's row count cuts the stream short (ADR-0016),
    end to end through run() and a real sink."""
    pytest.importorskip("httpx")
    store = []
    with capture_server(_recording_handler(store)) as url:
        spec = base_stream_spec(url=f"{url}/ingest", batch_size=100, rate=0, max_records=3)
        receipt = run(spec)  # spec has rows=5
    assert len(_delivered_records(store)) == 3
    assert "delivered 3 record(s)" in receipt


def test_streaming_max_records_extends_past_rows():
    """max_records above the row count keeps generating deterministically past
    the natural length — continuous streaming from a fixed spec."""
    pytest.importorskip("httpx")
    store = []
    with capture_server(_recording_handler(store)) as url:
        spec = base_stream_spec(url=f"{url}/ingest", batch_size=100, rate=0, max_records=8)
        run(spec)  # spec has rows=5
    records = _delivered_records(store)
    assert len(records) == 8
    assert [r["id"] for r in records] == list(range(1, 9))  # row_id keeps counting


def test_http_sink_retries_5xx_then_succeeds():
    pytest.importorskip("httpx")
    attempts = {"n": 0}

    class Flaky(BaseHTTPRequestHandler):
        def do_POST(self):
            attempts["n"] += 1
            int(self.headers.get("Content-Length", 0)) and self.rfile.read(
                int(self.headers.get("Content-Length", 0)))
            self.send_response(503 if attempts["n"] < 3 else 200)
            self.end_headers()

        def log_message(self, *a):
            pass

    with capture_server(Flaky) as url:
        spec = base_stream_spec(url=f"{url}/flaky", batch_size=5,
                                max_retries=3, backoff=0.01)
        receipt = run(spec)
    assert attempts["n"] == 3  # 503, 503, then 200
    assert "delivered 5 record(s)" in receipt
