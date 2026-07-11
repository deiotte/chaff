"""Cursor-on-Target (CoT) encoder. See formats/AGENTS.md.

CoT is the XML event format TAK servers speak: one `<event>` per position
report, carrying a uid, a type, timestamps, and a lat/lon `<point>`. This
module registers both a whole-file encoder and a **per-record** encoder, so
CoT streams to a TAK server one event at a time over the tcp/udp sinks —
which, paired with the moving-track entities (ADR-0009), is a live
synthetic feed. Pure functions, stdlib only (INV-2), no heavy dep.

Determinism (INV-3) is the trap: CoT events have time/start/stale stamps.
They are derived from the **data**, never the wall clock — from a
`time_column` if present, else `base_time + tick * interval_seconds`, else a
fixed `base_time`. Same spec + seed = same bytes.

options:
  lat_column ('lat'), lon_column ('lon'), hae_column (optional height),
  uid_column (auto: track_id/entity_id/uid/id), callsign_column (auto:
  callsign, else the uid), tick_column ('tick'/'t' auto-detected),
  type ('a-f-G-U-C'), how ('m-g'),
  time_column (optional ISO timestamp), base_time ('2025-01-01T00:00:00Z'),
  interval_seconds (1.0), stale_seconds (300).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

from . import encoder, record_encoder
from ..spec import DatasetSpec

_UID_FALLBACKS = ("track_id", "entity_id", "uid", "id")
_TICK_FALLBACKS = ("tick", "t")


def _opt(spec: DatasetSpec, key: str, default: Any) -> Any:
    return spec.output.options.get(key, default)


def _parse_time(value: str) -> datetime:
    s = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _cot_time(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _first_key(row: dict, explicit: str | None, fallbacks: tuple[str, ...]) -> str | None:
    if explicit:
        return explicit
    return next((k for k in fallbacks if k in row), None)


def _event_time(spec: DatasetSpec, row: dict) -> datetime:
    time_col = _opt(spec, "time_column", None)
    if time_col and row.get(time_col) is not None:
        return _parse_time(row[time_col])
    base = _parse_time(_opt(spec, "base_time", "2025-01-01T00:00:00Z"))
    tick_col = _first_key(row, _opt(spec, "tick_column", None), _TICK_FALLBACKS)
    if tick_col is not None and isinstance(row.get(tick_col), (int, float)):
        return base + timedelta(seconds=float(_opt(spec, "interval_seconds", 1.0)) * row[tick_col])
    return base


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _event_element(spec: DatasetSpec, row: dict) -> ET.Element:
    opts = spec.output.options
    uid_key = _first_key(row, opts.get("uid_column"), _UID_FALLBACKS)
    uid = str(row[uid_key]) if uid_key else "chaff"
    cs_key = _first_key(row, opts.get("callsign_column"), ("callsign",))
    callsign = str(row[cs_key]) if cs_key and row.get(cs_key) is not None else uid

    t = _event_time(spec, row)
    stale = t + timedelta(seconds=float(opts.get("stale_seconds", 300)))

    ev = ET.Element("event", {
        "version": "2.0",
        "uid": uid,
        "type": str(opts.get("type", "a-f-G-U-C")),
        "how": str(opts.get("how", "m-g")),
        "time": _cot_time(t),
        "start": _cot_time(t),
        "stale": _cot_time(stale),
    })
    hae = _num(row.get(opts.get("hae_column", "hae")), 0.0)
    ET.SubElement(ev, "point", {
        "lat": f"{_num(row.get(opts.get('lat_column', 'lat'))):.6f}",
        "lon": f"{_num(row.get(opts.get('lon_column', 'lon'))):.6f}",
        "hae": f"{hae:.1f}",
        "ce": "9999999.0",
        "le": "9999999.0",
    })
    detail = ET.SubElement(ev, "detail")
    ET.SubElement(detail, "contact", {"callsign": callsign})
    return ev


@record_encoder("cot")
def _cot_record(spec: DatasetSpec, record: dict) -> bytes:
    """One CoT <event> — the unit a tcp/udp/http sink frames to a TAK server."""
    return ET.tostring(_event_element(spec, record), encoding="utf-8", xml_declaration=False) + b"\n"


@encoder("cot", ".cot")
def to_cot(spec: DatasetSpec, rows: list[dict]) -> bytes:
    """A CoT stream: one <event> per row, newline-delimited (what a TAK feed
    is). Identical framing to the per-record encoder so file and stream match."""
    return b"".join(_cot_record(spec, r) for r in rows)
