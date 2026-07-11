"""Raw socket sinks: tcp, udp. See sinks/AGENTS.md.

Both are streaming sinks (ADR-0007) that send one encoded record at a
time over a raw socket. They use only the stdlib `socket` module — no
heavy dep, no extra — so they import unconditionally with core `chaff`.

INV-2: they write record-bytes straight to the wire; they neither parse
nor reframe them (the format axis already framed each record — pair with
ndjson so each record is a self-delimited line).

Failures are loud (sinks/AGENTS.md): TCP surfaces connection refused /
unreachable as a RuntimeError. UDP is connectionless, so a dead port
cannot be detected synchronously — the one guardable error, a datagram
larger than the wire allows, is raised rather than silently truncated.

options (both): host (required), port (required).
options (tcp): timeout (connect, s, default 10).
"""

from __future__ import annotations

import socket
from typing import Iterator

from . import stream_sink
from ..spec import DatasetSpec

# Max UDP payload over IPv4: 65535 - 8 (UDP header) - 20 (IP header).
_MAX_DATAGRAM = 65507


def _host_port(spec: DatasetSpec) -> tuple[str, int]:
    opts = spec.sink.options
    host, port = opts.get("host"), opts.get("port")
    if not host or port is None:
        raise ValueError(f"{spec.sink.sink} sink requires options.host and options.port")
    return str(host), int(port)


@stream_sink("tcp")
def tcp_sink(spec: DatasetSpec, records: Iterator[bytes]) -> str:
    host, port = _host_port(spec)
    timeout = float(spec.sink.options.get("timeout", 10))
    n = nbytes = 0
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            for rec in records:
                sock.sendall(rec)
                n += 1
                nbytes += len(rec)
    except OSError as e:
        raise RuntimeError(f"tcp sink: {host}:{port} — {e}") from e
    return f"sent {n} record(s), {nbytes} bytes -> tcp://{host}:{port}"


@stream_sink("udp")
def udp_sink(spec: DatasetSpec, records: Iterator[bytes]) -> str:
    host, port = _host_port(spec)
    addr = (host, port)
    n = nbytes = 0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for rec in records:
            if len(rec) > _MAX_DATAGRAM:
                raise ValueError(
                    f"udp sink: record of {len(rec)} bytes exceeds the "
                    f"{_MAX_DATAGRAM}-byte datagram limit; use a smaller record format"
                )
            sock.sendto(rec, addr)
            n += 1
            nbytes += len(rec)
    finally:
        sock.close()
    return f"sent {n} datagram(s), {nbytes} bytes -> udp://{host}:{port}"
