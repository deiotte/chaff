"""When a sensor record says it happened.

Shared by the sensor-format encoders, so `base_time`, `interval_seconds`,
`tick_column` and `time_column` mean one thing whichever format a spec picks.
Two definitions of "when" is the kind of divergence nobody notices until two
feeds of one scene disagree about the time — which is exactly the case the
observers (ADR-0033) exist to produce.

Determinism (INV-3) is the whole point: every instant here is derived from the
data or from a spec constant, never from the wall clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from ..spec import DatasetSpec

TICK_FALLBACKS = ("tick", "t")


def parse_time(value: str) -> datetime:
    s = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def first_key(row: dict, explicit: str | None, fallbacks: tuple[str, ...]) -> str | None:
    if explicit:
        return explicit
    return next((k for k in fallbacks if k in row), None)


def event_time(spec: DatasetSpec, row: dict) -> datetime:
    """A `time_column` if the row carries one, else `base_time + tick × interval`."""
    opts = spec.output.options
    time_col = opts.get("time_column")
    if time_col and row.get(time_col) is not None:
        return parse_time(row[time_col])
    base = parse_time(opts.get("base_time", "2025-01-01T00:00:00Z"))
    tick_col = first_key(row, opts.get("tick_column"), TICK_FALLBACKS)
    if tick_col is not None and isinstance(row.get(tick_col), (int, float)):
        return base + timedelta(seconds=float(opts.get("interval_seconds", 1.0)) * row[tick_col])
    return base


def epoch_micros(dt: datetime) -> int:
    """Microseconds since the 1970 epoch — the unit MISB timestamps use."""
    return int(dt.timestamp() * 1_000_000)


def optional_num(spec: DatasetSpec, row: dict, option: str,
                 fallbacks: tuple[str, ...]) -> float | None:
    """A row's value for an optional numeric column, or `None` when there isn't one.

    `None` means *the data does not carry this*, and a caller turns that into an
    omitted item — never into a zero. A value present but unusable (unparseable,
    NaN, infinite) is also `None`: knowing we do not have it is what absence
    means, and a non-finite number must never reach a wire format.
    """
    import math

    key = first_key(row, spec.output.options.get(option), fallbacks)
    if key is None or row.get(key) is None:
        return None
    try:
        f = float(row[key])
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None
