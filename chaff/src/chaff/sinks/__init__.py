"""Sinks (Axis 2: where bytes go). See sinks/AGENTS.md.

A sink is a callable: (spec: DatasetSpec, payload: bytes) -> str (a
human-readable receipt: path written, records posted, etc.).

Phase 1 ships `file`. `kafka` and `http` are registered as explicit
stubs so the CLI can list them and fail with a roadmap pointer instead
of a mystery KeyError. Streaming sinks will additionally accept a
per-record iterator + rate control (records/sec) rather than one blob —
that interface lands with Phase 2 (see ROADMAP.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..spec import DatasetSpec

SinkFn = Callable[[DatasetSpec, bytes], str]

_REGISTRY: dict[str, SinkFn] = {}


def sink(sink_id: str) -> Callable[[SinkFn], SinkFn]:
    def deco(fn: SinkFn) -> SinkFn:
        if sink_id in _REGISTRY:
            raise ValueError(f"sink '{sink_id}' already registered")
        _REGISTRY[sink_id] = fn
        return fn
    return deco


def get_sink(sink_id: str) -> SinkFn:
    try:
        return _REGISTRY[sink_id]
    except KeyError:
        raise KeyError(f"unknown sink '{sink_id}'. Registered: {sorted(_REGISTRY)}") from None


def list_sinks() -> list[str]:
    return sorted(_REGISTRY)


@sink("file")
def file_sink(spec: DatasetSpec, payload: bytes) -> str:
    from ..formats import get_extension  # local import avoids cycle

    opts = spec.sink.options
    path = Path(opts.get("path") or f"{spec.name}{get_extension(spec.output.format)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return f"wrote {len(payload)} bytes -> {path}"


@sink("http")
def http_sink(spec: DatasetSpec, payload: bytes) -> str:
    raise NotImplementedError(
        "http sink is Phase 2 (POST per record or batch to a developer's "
        "endpoint, with records/sec rate control). See ROADMAP.md."
    )


@sink("kafka")
def kafka_sink(spec: DatasetSpec, payload: bytes) -> str:
    raise NotImplementedError(
        "kafka sink is Phase 2 (confluent-kafka producer, per-record "
        "messages from ndjson/avro, rate control). docker-compose.yml "
        "already carries a broker under the 'streaming' profile. See ROADMAP.md."
    )
