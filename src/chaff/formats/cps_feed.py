"""`cps_feed`: a plant's samples and writes as `cps-feed/1` lines. See formats/AGENTS.md.

The feed is the line a controller-protocol decoder yields and the wire a tap
hears: one JSON object per event, a sample (a process value) or a write (an
actor changed a commandable property). The consuming repository turns these
lines into protocol bytes itself, so protocol knowledge stays there; this
encoder only says what happened, when, to which tag.

Streams per record (ADR-0045): one entity snapshot yields a sample line per
mapped column, plus a write line for each write mapping whose `fire_column`
is truthy on that snapshot. Pure and stdlib-only (INV-2); time comes from the
data (`_timing`), never the clock (INV-3).

options:
  equipment_column   the column naming the equipment (auto: equipment/track_id/entity_id/id)
  samples            {column: {"tag": "...", "protocol_ref": "...", "unit": "..."}}
  writes             {column: {"tag", "protocol_ref", "unit",
                               "fire_column": "col that is truthy when the write happens",
                               "kind": "present_value" | "priority_array",  "priority": 1-16,
                               "channel": "supervisor", "address": "10.0.0.1",
                               "session": {"kind": "external", "account":
                                           {"kind": "service_account", "name": "...", "attribution": 0.9}}}}
  time               time_column / tick_column / base_time / interval_seconds (as every sensor format)
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from . import encoder, record_encoder
from ._timing import event_time
from ..spec import DatasetSpec

SCHEMA = "cps-feed/1"
_EQUIPMENT_FALLBACKS = ("equipment", "track_id", "entity_id", "id")
_TAG = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_EQUIPMENT = re.compile(r"^[A-Za-z0-9._/-]{1,96}$")


def _finite(v: Any, what: str) -> float:
    f = float(v)
    if not math.isfinite(f):
        raise ValueError(f"cps_feed: {what} is not finite")
    return f


def _target(spec_map: dict, equipment: str) -> dict:
    tag = str(spec_map["tag"])
    if not _TAG.match(tag):
        raise ValueError(f"cps_feed: tag {tag!r} is not [A-Za-z0-9._-]{{1,64}}")
    t = {"equipment": equipment, "tag": tag}
    for k in ("protocol_ref", "unit"):
        if spec_map.get(k):
            t[k] = str(spec_map[k])
    return t


def _line(obj: dict) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


@record_encoder("cps_feed")
def _cps_feed_record(spec: DatasetSpec, record: dict) -> bytes:
    opts = spec.output.options
    key = opts.get("equipment_column") or next(
        (k for k in _EQUIPMENT_FALLBACKS if k in record), None)
    equipment = str(record[key]) if key and record.get(key) is not None else ""
    if not _EQUIPMENT.match(equipment):
        raise ValueError(f"cps_feed: equipment {equipment!r} is not [A-Za-z0-9._/-]{{1,96}}")
    at_ms = int(round(event_time(spec, record).timestamp() * 1000))
    out: list[bytes] = []
    for col, m in (opts.get("samples") or {}).items():
        if record.get(col) is None:
            continue
        out.append(_line({"schema": SCHEMA, "event": "sample", "at_ms": at_ms,
                          "target": _target(m, equipment),
                          "value": _finite(record[col], col)}))
    for col, m in (opts.get("writes") or {}).items():
        if not record.get(m.get("fire_column", "")):
            continue
        kind = m.get("kind", "present_value")
        origin: dict[str, Any] = {"channel": m.get("channel", "unknown")}
        if m.get("address"):
            origin["address"] = str(m["address"])
        if m.get("session"):
            origin["session"] = m["session"]
        line: dict[str, Any] = {"schema": SCHEMA, "event": "write", "at_ms": at_ms,
                                "kind": kind, "origin": origin,
                                "target": _target(m, equipment),
                                "value": _finite(record[col], col)}
        if kind == "priority_array":
            line["priority"] = int(m["priority"])
        out.append(_line(line))
    return b"".join(out)


@encoder("cps_feed", ".jsonl")
def to_cps_feed(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """Every row's lines in order — identical framing to the streaming path."""
    return b"".join(_cps_feed_record(spec, r) for r in rows)
