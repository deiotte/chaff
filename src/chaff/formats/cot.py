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

**Absence is emitted as absence** (ADR-0032). CoT's numeric fields have no
"missing" encoding, so the standard reserves the literal 9999999 to mean *I
do not know*. A consumer reads that as "no value"; it reads a 0.0 as a
measurement of zero. Every optional numeric here is therefore either the
row's real value or the sentinel — never a fabricated default.

options:
  position   lat_column ('lat'), lon_column ('lon'), hae_column ('hae'),
             ce_column ('ce'), le_column ('le')
  identity   uid_column (auto: track_id/entity_id/uid/id), callsign_column
             (auto: callsign, else the uid)
  kinematics speed_column (auto: speed), course_column (auto: course/heading)
  reporter   battery_column (auto: battery), geopointsrc, altsrc,
             takv_platform ('chaff' — see `_detail_element`), takv_version
  event      type ('a-f-G-U-C'), how ('m-g')
  time       time_column (optional ISO timestamp), tick_column ('tick'/'t'
             auto-detected), base_time ('2025-01-01T00:00:00Z'),
             interval_seconds (1.0), stale_seconds (300)
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any

from . import encoder, record_encoder
from ..spec import DatasetSpec

_UID_FALLBACKS = ("track_id", "entity_id", "uid", "id")
_TICK_FALLBACKS = ("tick", "t")
_SPEED_FALLBACKS = ("speed",)
_COURSE_FALLBACKS = ("course", "heading")
_BATTERY_FALLBACKS = ("battery",)

#: CoT's literal for "this value is not known", used in `hae`, `ce` and `le`.
#:
#: A real number in a field that otherwise holds real numbers. Emitting it is
#: how an absent column stays absent to a consumer: a receiver maps the
#: sentinel back to "no value", where a 0.0 would arrive as a measurement.
UNKNOWN_VALUE_SENTINEL = 9999999.0


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
    """A required numeric, falling back to `default`.

    Non-finite is treated as unparseable: a NaN formats as the literal `nan`,
    which is not a number any CoT consumer can read, and an infinity is not a
    position. Neither should reach the wire.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _optional_num(spec: DatasetSpec, row: dict, option: str,
                  fallbacks: tuple[str, ...]) -> float | None:
    """The row's value for an optional numeric column, or `None` when there
    isn't one.

    `None` means *the data does not carry this*, and callers turn that into
    an omitted attribute or the unknown sentinel — never into a zero. A value
    that is present but unusable (unparseable, NaN, infinite) is also `None`:
    we know we do not have it, which is exactly what absence means.
    """
    key = _first_key(row, _opt(spec, option, None), fallbacks)
    if key is None or row.get(key) is None:
        return None
    try:
        f = float(row[key])
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _sentinel(value: float | None, places: int) -> str:
    return f"{UNKNOWN_VALUE_SENTINEL:.1f}" if value is None else f"{value:.{places}f}"


def _detail_element(spec: DatasetSpec, row: dict, ev: ET.Element, callsign: str) -> None:
    """Build `<detail>`, in the child order TAK itself writes.

    Every child is conditional: a `<track>` with no speed and no course, or a
    `<status>` with no battery, would assert the reporter said something it
    did not. Only `<contact>` is unconditional, because the callsign is
    derived from the uid when no column supplies one.
    """
    opts = spec.output.options
    detail = ET.SubElement(ev, "detail")

    speed = _optional_num(spec, row, "speed_column", _SPEED_FALLBACKS)
    course = _optional_num(spec, row, "course_column", _COURSE_FALLBACKS)
    if speed is not None or course is not None:
        track: dict[str, str] = {}
        if speed is not None:
            track["speed"] = f"{speed:.2f}"
        if course is not None:
            # Degrees true, [0, 360). A generator asked for 0..360 inclusive
            # would otherwise emit an out-of-range 360.0 on its top bin.
            track["course"] = f"{course % 360.0:.1f}"
        ET.SubElement(detail, "track", track)

    geopointsrc, altsrc = opts.get("geopointsrc"), opts.get("altsrc")
    if geopointsrc or altsrc:
        precision: dict[str, str] = {}
        if geopointsrc:
            precision["geopointsrc"] = str(geopointsrc)
        if altsrc:
            precision["altsrc"] = str(altsrc)
        ET.SubElement(detail, "precisionlocation", precision)

    # Defaults to "chaff", so every event this encoder produces says in its own
    # provenance field that a synthetic generator made it. Opt-out (set it to
    # something else, or "" to drop the element) rather than opt-in: a consumer
    # that has to be told out-of-band which feed is synthetic will eventually
    # not be told. Deliberately NOT the running chaff version — that would make
    # the bytes vary by install and break INV-3 across releases.
    platform, version = opts.get("takv_platform", "chaff"), opts.get("takv_version")
    if platform or version:
        takv: dict[str, str] = {}
        if platform:
            takv["platform"] = str(platform)
        if version:
            takv["version"] = str(version)
        ET.SubElement(detail, "takv", takv)

    battery = _optional_num(spec, row, "battery_column", _BATTERY_FALLBACKS)
    if battery is not None:
        ET.SubElement(detail, "status", {"battery": f"{int(battery)}"})

    ET.SubElement(detail, "contact", {"callsign": callsign})


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
    ET.SubElement(ev, "point", {
        "lat": f"{_num(row.get(opts.get('lat_column', 'lat'))):.6f}",
        "lon": f"{_num(row.get(opts.get('lon_column', 'lon'))):.6f}",
        "hae": _sentinel(_optional_num(spec, row, "hae_column", ("hae",)), 1),
        "ce": _sentinel(_optional_num(spec, row, "ce_column", ("ce",)), 1),
        "le": _sentinel(_optional_num(spec, row, "le_column", ("le",)), 1),
    })
    _detail_element(spec, row, ev, callsign)
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
