"""Sinks (Axis 2: where bytes go). See sinks/AGENTS.md.

Two sink shapes, two registries, negotiated by the engine (ADR-0007):

- **Blob sink** `(spec, payload: bytes) -> str` — the whole encoded
  payload in one shot. `file` is the canonical one. Register with `@sink`.
- **Streaming sink** `(spec, records: Iterator[bytes]) -> str` — one
  encoded record at a time, already rate-paced by the engine. `http`
  (and, in later chunks, kafka/tcp/udp) are these. Register with
  `@stream_sink`.

A sink never inspects or transforms payload content (INV-2): a streaming
sink may *group* record-bytes (batching) and deliver them, but it does
not parse or re-encode them — the format axis already produced the bytes.
Rate control is applied once, by the engine, to the iterator it hands the
sink, so every streaming sink is paced uniformly and none reimplement it.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ..spec import DatasetSpec

SinkFn = Callable[[DatasetSpec, bytes], str]
StreamSinkFn = Callable[[DatasetSpec, "Iterator[bytes]"], str]

_REGISTRY: dict[str, SinkFn] = {}
_STREAM_REGISTRY: dict[str, StreamSinkFn] = {}


def sink(sink_id: str) -> Callable[[SinkFn], SinkFn]:
    def deco(fn: SinkFn) -> SinkFn:
        _guard(sink_id)
        _REGISTRY[sink_id] = fn
        return fn
    return deco


def stream_sink(sink_id: str) -> Callable[[StreamSinkFn], StreamSinkFn]:
    def deco(fn: StreamSinkFn) -> StreamSinkFn:
        _guard(sink_id)
        _STREAM_REGISTRY[sink_id] = fn
        return fn
    return deco


def _guard(sink_id: str) -> None:
    if sink_id in _REGISTRY or sink_id in _STREAM_REGISTRY:
        raise ValueError(f"sink '{sink_id}' already registered")


def get_sink(sink_id: str) -> SinkFn:
    try:
        return _REGISTRY[sink_id]
    except KeyError:
        raise KeyError(f"unknown sink '{sink_id}'. Registered: {list_sinks()}") from None


def get_stream_sink(sink_id: str) -> StreamSinkFn:
    try:
        return _STREAM_REGISTRY[sink_id]
    except KeyError:
        raise KeyError(f"unknown streaming sink '{sink_id}'. Registered: {list_sinks()}") from None


def is_stream_sink(sink_id: str) -> bool:
    return sink_id in _STREAM_REGISTRY


def list_sinks() -> list[str]:
    return sorted(set(_REGISTRY) | set(_STREAM_REGISTRY))


def rate_limited(
    items: Iterable[bytes],
    rate: float | None,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[bytes]:
    """Yield `items` no faster than `rate` per second (records/sec).

    `rate` None/<=0 means unthrottled. Pacing is delivery timing only — it
    never touches generation, the RNG, or payload bytes, so INV-3 is
    unaffected. `sleep` is injectable so tests pace against a fake clock.
    """
    interval = 1.0 / float(rate) if rate and rate > 0 else 0.0
    first = True
    for item in items:
        if not first and interval:
            sleep(interval)
        first = False
        yield item


@sink("file")
def file_sink(spec: DatasetSpec, payload: bytes) -> str:
    from ..formats import get_extension  # local import avoids cycle

    opts = spec.sink.options
    path = Path(opts.get("path") or f"{spec.name}{get_extension(spec.output.format)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return f"wrote {len(payload)} bytes -> {path}"


# Streaming sinks registered for their side-effect. `raw` (tcp/udp) is
# stdlib-only and always available; `http`/`kafka` keep their heavy import
# (httpx, confluent-kafka) lazy so core `import chaff.sinks` stays dep-free.
from . import http, kafka, raw  # noqa: E402,F401
