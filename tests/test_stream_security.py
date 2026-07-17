"""Security hardening for the streaming surface (ADR-0018, follow-up ADR-0019).

Covers the four findings against PRs #27/#28:
  #1/#2  opt-in CHAFF_API_TOKEN gates /stream/jobs* and the /stream WS
  #2     SSRF egress policy: cloud-metadata/link-local always blocked, opt-in
         CHAFF_STREAM_ALLOWED_HOSTS allowlist
  #3     the /stream WS applies the server record/second ceilings
  #4     entity materialization is bounded by the cap (no 1M-entity freeze)
  LOW    invalid WS query params error instead of silently going unlimited

ADR-0019 closes the WS connection-exhaustion DoS left open by ADR-0018:
  C-1    the /stream WS bounds the wait for the opening spec frame, so an idle
         socket can't be held open forever (CHAFF_STREAM_HANDSHAKE_TIMEOUT)
  C-3    the /stream WS caps concurrent live sockets (CHAFF_STREAM_MAX_SESSIONS)
"""

import itertools
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from api import netpolicy  # noqa: E402
from api.main import app  # noqa: E402
from chaff.engine import generate_entity_rows, iter_records  # noqa: E402
from chaff.generators import _REGISTRY  # noqa: E402
from chaff.sinks import stream_sink  # noqa: E402
from chaff.spec import load_spec  # noqa: E402

client = TestClient(app)


# A broker-free streaming sink (no host → passes egress policy) so job-lifecycle
# tests never touch the network. Guarded against a double import registering it.
try:
    @stream_sink("mem_sec")
    def _mem_sink(spec, records):
        return f"collected {sum(1 for _ in records)} record(s)"
except ValueError:
    pass


def _install_probe(monkeypatch):
    """Register a call-counting generator for the duration of one test only.
    Installed via monkeypatch.setitem so it never lands in the real registry
    (the generator-grouping tripwire would flag an ungrouped generator)."""
    calls = {"n": 0}

    def probe(ctx, params):
        calls["n"] += 1
        return calls["n"]

    monkeypatch.setitem(_REGISTRY, "count_probe", probe)
    return calls


def _job_spec(sink="mem_sec", options=None, fmt="ndjson"):
    return {
        "name": "js", "seed": 1, "rows": 20,
        "columns": [{"name": "id", "generator": "row_id"}],
        "output": {"format": fmt},
        "sink": {"sink": sink, "options": options if options is not None else {"topic": "demo"}},
    }


def _stream_spec(rows=50):
    return {
        "name": "evts", "seed": 1, "rows": rows,
        "columns": [{"name": "id", "generator": "row_id"}],
        "output": {"format": "ndjson"},
    }


def _entity_spec(count, ticks=5):
    return load_spec({
        "name": "ents", "seed": 1,
        "columns": [{"name": "x", "generator": "count_probe"}],
        "output": {"format": "ndjson"},
        "entity": {"count": count, "ticks": ticks, "updates": []},
    })


# ── Finding #1/#2: auth token gates the streaming surface ────────────

def test_auth_off_by_default_lists_jobs():
    # No CHAFF_API_TOKEN -> the localhost demo is unchanged (open).
    assert client.get("/stream/jobs").status_code == 200


def test_auth_on_rejects_missing_token(monkeypatch):
    monkeypatch.setenv("CHAFF_API_TOKEN", "s3cret")
    assert client.get("/stream/jobs").status_code == 401
    assert client.post("/stream/jobs", json={
        "spec": _job_spec(), "max_records": 5, "max_seconds": 30}).status_code == 401
    assert client.delete("/stream/jobs/anything").status_code == 401


def test_auth_on_accepts_correct_token(monkeypatch):
    monkeypatch.setenv("CHAFF_API_TOKEN", "s3cret")
    hdr = {"X-Chaff-Token": "s3cret"}
    assert client.get("/stream/jobs", headers=hdr).status_code == 200
    # Bearer form is accepted too.
    assert client.get("/stream/jobs", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_auth_on_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("CHAFF_API_TOKEN", "s3cret")
    assert client.get("/stream/jobs", headers={"X-Chaff-Token": "nope"}).status_code == 401


def test_ws_auth_rejects_without_token(monkeypatch):
    monkeypatch.setenv("CHAFF_API_TOKEN", "s3cret")
    with client.websocket_connect("/stream") as ws:
        assert "error" in ws.receive_json()


def test_ws_auth_accepts_token_via_query(monkeypatch):
    monkeypatch.setenv("CHAFF_API_TOKEN", "s3cret")
    with client.websocket_connect("/stream?token=s3cret&max_records=2") as ws:
        ws.send_text(json.dumps(_stream_spec()))
        assert len(_drain_ws(ws)) == 2  # streamed, not rejected


# ── Finding #2: SSRF egress policy ───────────────────────────────────

def test_metadata_ip_is_blocked_at_job_start():
    r = client.post("/stream/jobs", json={
        "spec": _job_spec(sink="tcp", options={"host": "169.254.169.254", "port": 80}),
        "max_records": 5, "max_seconds": 30})
    assert r.status_code == 422 and "SSRF" in r.json()["detail"]


def test_check_destination_blocks_link_local_ipv4():
    spec = load_spec(_job_spec(sink="udp", options={"host": "169.254.169.254", "port": 9}))
    with pytest.raises(netpolicy.DestinationBlocked):
        netpolicy.check_destination(spec)


def test_check_destination_allows_loopback():
    # The localhost broker demo must keep working: loopback is never blocked.
    spec = load_spec(_job_spec(sink="tcp", options={"host": "127.0.0.1", "port": 8000}))
    netpolicy.check_destination(spec)  # no raise


def test_link_local_override_env_allows_metadata(monkeypatch):
    monkeypatch.setenv("CHAFF_STREAM_ALLOW_LINK_LOCAL", "1")
    spec = load_spec(_job_spec(sink="tcp", options={"host": "169.254.169.254", "port": 80}))
    netpolicy.check_destination(spec)  # no raise


def test_allowlist_blocks_unlisted_host(monkeypatch):
    monkeypatch.setenv("CHAFF_STREAM_ALLOWED_HOSTS", "broker.internal, kafka")
    spec = load_spec(_job_spec(sink="mqtt", options={"topic": "t", "host": "evil.example"}))
    with pytest.raises(netpolicy.DestinationBlocked):
        netpolicy.check_destination(spec)


def test_allowlist_permits_listed_host(monkeypatch):
    monkeypatch.setenv("CHAFF_STREAM_ALLOWED_HOSTS", "broker.internal, kafka")
    spec = load_spec(_job_spec(sink="mqtt", options={"topic": "t", "host": "kafka"}))
    netpolicy.check_destination(spec)  # no raise


def test_http_sink_url_host_is_vetted():
    spec = load_spec(_job_spec(sink="http", options={"url": "http://169.254.169.254/latest/meta-data"}))
    with pytest.raises(netpolicy.DestinationBlocked):
        netpolicy.check_destination(spec)


def test_hostless_sink_passes_untouched():
    # The in-memory test sink has no host — nothing to vet, never blocked.
    netpolicy.check_destination(load_spec(_job_spec()))


# ── Finding #3 + LOW: WS ceilings and strict params ──────────────────

def _drain_ws(ws) -> list[str]:
    """Collect record frames until the server closes the socket."""
    from starlette.websockets import WebSocketDisconnect
    frames = []
    try:
        while True:
            frames.append(ws.receive_text())
    except WebSocketDisconnect:
        pass
    return frames


def test_ws_clamps_max_records_to_ceiling(monkeypatch):
    monkeypatch.setenv("CHAFF_STREAM_MAX_RECORDS", "3")
    with client.websocket_connect("/stream?max_records=100") as ws:
        ws.send_text(json.dumps(_stream_spec(rows=50)))
        assert len(_drain_ws(ws)) == 3  # clamped to the ceiling, not 100


def test_ws_invalid_param_errors_not_unlimited():
    with client.websocket_connect("/stream?max_records=lots") as ws:
        ws.send_text(json.dumps(_stream_spec()))
        body = ws.receive_json()
    assert "error" in body and "max_records" in body["error"]


def test_ws_negative_param_is_rejected():
    with client.websocket_connect("/stream?duration=-5") as ws:
        ws.send_text(json.dumps(_stream_spec()))
        body = ws.receive_json()
    assert "error" in body and "positive" in body["error"]


# ── Finding #4: entity materialization bounded by the cap ────────────

def test_entity_materialization_bounded_by_small_limit(monkeypatch):
    calls = _install_probe(monkeypatch)
    spec = _entity_spec(count=1000, ticks=5)  # natural length 5000
    rows = list(iter_records(spec, limit=2))
    # Only the entities the cap can emit are materialized — 2, not 1000.
    assert calls["n"] == 2
    assert len(rows) == 2


def test_entity_small_limit_is_a_deterministic_prefix():
    # Bounding materialization must not change the bytes (INV-3): the capped
    # stream is exactly the head of the full eager output. Uses real
    # deterministic generators (the count_probe above is intentionally stateful
    # and only valid for counting calls).
    spec = load_spec({
        "name": "tracks", "seed": 7,
        "columns": [
            {"name": "lat", "generator": "float_normal", "params": {"mean": 40, "stddev": 1}},
            {"name": "lon", "generator": "float_normal", "params": {"mean": -70, "stddev": 1}},
        ],
        "output": {"format": "ndjson"},
        "entity": {"count": 50, "ticks": 4, "id_pattern": "TRK-####",
                   "updates": [{"updater": "movement", "params": {"speed": 0.01}}]},
    })
    capped = list(iter_records(spec, limit=3))
    assert capped == generate_entity_rows(spec)[:3]


def test_entity_limit_spanning_ticks_materializes_all_entities(monkeypatch):
    # When the cap reaches past tick 0, every entity is needed (they all move
    # each tick) — materialization is full, output unchanged.
    calls = _install_probe(monkeypatch)
    spec = _entity_spec(count=4, ticks=3)  # natural length 12
    rows = list(iter_records(spec, limit=10))
    assert calls["n"] == 4
    assert [r["tick"] for r in rows] == [0, 0, 0, 0, 1, 1, 1, 1, 2, 2]


# ── C-1: the /stream WS bounds the opening-frame wait (ADR-0019) ──────

def test_ws_handshake_timeout_drops_idle_socket(monkeypatch):
    # A client that connects and never sends a spec used to hold the socket
    # open forever (auth is off by default). Now it's dropped after the
    # handshake window with a readable error frame — no more slow-loris hold.
    monkeypatch.setenv("CHAFF_STREAM_HANDSHAKE_TIMEOUT", "0.2")
    with client.websocket_connect("/stream") as ws:
        body = ws.receive_json()  # server closes after the window; don't send
    assert "error" in body and "timed out" in body["error"]


def test_ws_prompt_client_is_not_dropped_by_handshake_timeout(monkeypatch):
    # A client that sends its spec promptly streams normally even under a tight
    # handshake window — the timeout only bites idle sockets, never real ones.
    monkeypatch.setenv("CHAFF_STREAM_HANDSHAKE_TIMEOUT", "0.2")
    with client.websocket_connect("/stream?max_records=2") as ws:
        ws.send_text(json.dumps(_stream_spec()))
        assert len(_drain_ws(ws)) == 2


# ── C-3: the /stream WS caps concurrent live sockets (ADR-0019) ──────

def test_ws_session_cap_rejects_when_full(monkeypatch):
    # Simulate the process already holding its only slot; the next connect is
    # turned away with a capacity error instead of piling on unbounded sockets.
    import api.main as main
    monkeypatch.setenv("CHAFF_STREAM_MAX_SESSIONS", "1")
    monkeypatch.setattr(main, "_active_ws_sessions", 1)
    with client.websocket_connect("/stream") as ws:
        body = ws.receive_json()
    assert "error" in body and "capacity" in body["error"]


def test_ws_admitted_session_is_released(monkeypatch):
    # An admitted session must free its slot on clean end-of-stream, or the cap
    # would leak toward zero available. Drive a bounded stream to completion and
    # confirm the live-session count is back to baseline.
    import api.main as main
    baseline = main._active_ws_sessions
    with client.websocket_connect("/stream?max_records=2") as ws:
        ws.send_text(json.dumps(_stream_spec()))
        _drain_ws(ws)
    assert main._active_ws_sessions == baseline
