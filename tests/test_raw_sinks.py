"""TCP/UDP raw sink tests, exercised against real local listeners
(stdlib-only, no extra needed so these always run)."""

import socket
import socketserver
import threading

import pytest

from chaff.engine import run
from chaff.sinks import is_stream_sink
from chaff.spec import load_spec


def stream_spec(sink_id, **options):
    return load_spec({
        "name": "events", "seed": 1, "rows": 5,
        "columns": [{"name": "id", "generator": "row_id"},
                    {"name": "who", "generator": "full_name"}],
        "output": {"format": "ndjson"},
        "sink": {"sink": sink_id, "options": options},
    })


def test_tcp_and_udp_are_streaming_sinks():
    assert is_stream_sink("tcp")
    assert is_stream_sink("udp")


def test_tcp_sink_delivers_all_records():
    received = []

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            buf = b""
            while True:
                chunk = self.request.recv(4096)
                if not chunk:
                    break
                buf += chunk
            received.append(buf)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    host, port = srv.server_address
    try:
        receipt = run(stream_spec("tcp", host=host, port=port))
    finally:
        srv.shutdown()

    # Wait briefly for the handler thread to flush the closed connection.
    for _ in range(50):
        if received:
            break
        threading.Event().wait(0.02)
    lines = [ln for ln in b"".join(received).decode().splitlines() if ln]
    assert len(lines) == 5
    assert "sent 5 record(s)" in receipt


def test_tcp_sink_connection_refused_is_loud():
    # Bind then close a socket to obtain a definitely-closed port.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(RuntimeError, match="tcp sink"):
        run(stream_spec("tcp", host="127.0.0.1", port=port))


def test_udp_sink_delivers_datagrams():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    host, port = rx.getsockname()
    try:
        receipt = run(stream_spec("udp", host=host, port=port))
        rx.settimeout(0.5)
        datagrams = []
        try:
            while True:
                datagrams.append(rx.recvfrom(65535)[0])
        except socket.timeout:
            pass
    finally:
        rx.close()
    assert len(datagrams) == 5              # one datagram per record
    assert "sent 5 datagram(s)" in receipt


def test_udp_sink_rejects_oversized_datagram():
    # A single row whose value blows past the datagram limit.
    spec = load_spec({
        "name": "big", "seed": 1, "rows": 1,
        "columns": [{"name": "blob", "generator": "pattern",
                     "params": {"pattern": "#" * 70000}}],
        "output": {"format": "ndjson"},
        "sink": {"sink": "udp", "options": {"host": "127.0.0.1", "port": 9}},
    })
    with pytest.raises(ValueError, match="datagram limit"):
        run(spec)
