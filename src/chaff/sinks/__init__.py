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

import importlib.util
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ..spec import DatasetSpec

SinkFn = Callable[[DatasetSpec, bytes], str]
StreamSinkFn = Callable[[DatasetSpec, "Iterator[bytes]"], str]

_REGISTRY: dict[str, SinkFn] = {}
_STREAM_REGISTRY: dict[str, StreamSinkFn] = {}
# sink_id -> (modules it imports lazily, the pip extra that installs them).
# Declared at registration so "can this sink run here?" is answerable without
# importing a heavy client or attempting delivery (ADR-0047).
_REQUIREMENTS: dict[str, tuple[tuple[str, ...], str]] = {}


def sink(sink_id: str) -> Callable[[SinkFn], SinkFn]:
    def deco(fn: SinkFn) -> SinkFn:
        _guard(sink_id)
        _REGISTRY[sink_id] = fn
        return fn
    return deco


def stream_sink(
    sink_id: str,
    *,
    requires: tuple[str, ...] = (),
    extra: str | None = None,
) -> Callable[[StreamSinkFn], StreamSinkFn]:
    """Register a streaming sink.

    `requires` names the modules the sink imports lazily; `extra` is the
    `chaff[...]` extra that installs them. Both are optional — a stdlib-only
    sink (tcp/udp) declares neither.
    """
    if requires and not extra:
        raise ValueError(f"sink '{sink_id}' declares requires= without extra=")

    def deco(fn: StreamSinkFn) -> StreamSinkFn:
        _guard(sink_id)
        _STREAM_REGISTRY[sink_id] = fn
        if requires:
            _REQUIREMENTS[sink_id] = (tuple(requires), extra)  # type: ignore[assignment]
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


def _module_present(name: str) -> bool:
    """True if `name` is importable, without importing it (the client
    libraries are heavy; asking must stay cheap)."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # a missing parent package
        return False


def install_hint(sink_id: str) -> str | None:
    """The pip command that makes `sink_id` usable, or None if it needs nothing."""
    req = _REQUIREMENTS.get(sink_id)
    return f"pip install 'chaff[{req[1]}]'" if req else None


def sink_status(sink_id: str) -> dict[str, Any]:
    """Whether a registered sink can run in this install, and if not, how to fix it.

    `{"available": bool, "missing": [module, ...], "install": str | None}`.
    """
    if sink_id not in _REGISTRY and sink_id not in _STREAM_REGISTRY:
        raise KeyError(f"unknown sink '{sink_id}'. Registered: {list_sinks()}")
    modules, _extra = _REQUIREMENTS.get(sink_id, ((), None))
    missing = [m for m in modules if not _module_present(m)]
    return {
        "available": not missing,
        "missing": missing,
        "install": install_hint(sink_id) if missing else None,
    }


def list_sink_status() -> dict[str, dict[str, Any]]:
    """`sink_status` for every registered sink, keyed by id."""
    return {s: sink_status(s) for s in list_sinks()}


def missing_dependency(sink_id: str, package: str) -> RuntimeError:
    """The one error a sink raises when its client library isn't installed —
    so every sink names the same, correct install command."""
    return RuntimeError(
        f"{sink_id} sink needs {package}, which isn't installed. "
        f"Install it with: {install_hint(sink_id)}"
    )


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


def time_limited(
    items: Iterable[bytes],
    duration: float | None,
    *,
    now: Callable[[], float] = time.monotonic,
) -> Iterator[bytes]:
    """Yield from `items` until `duration` seconds elapse, then stop.

    A delivery-time bound for open-ended streams (unbounded generation /
    serve-forever): it decides *where to cut*, never *what the bytes are*, so
    INV-3 holds — whatever records come out are a deterministic prefix
    (ADR-0016). `duration` None/<=0 means no bound. `now` is injectable so
    tests bound against a fake clock. Note the emitted *count* depends on the
    wall-clock and machine speed, so it is deliberately not reproducible —
    only the content of each emitted record is."""
    if not duration or duration <= 0:
        yield from items
        return
    start = now()
    for item in items:
        if now() - start >= duration:
            return
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
# stdlib-only; `http`/`mqtt` use core dependencies; `kafka` needs the opt-in
# `kafka` extra (ADR-0047). All three keep their client import lazy, and
# declare it via `requires=` so `sink_status()` can say what's missing.
from . import http, kafka, mqtt, raw  # noqa: E402,F401
