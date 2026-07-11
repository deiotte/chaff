"""Cursor-on-Target encoder tests (Phase 3D)."""

import time
import xml.etree.ElementTree as ET

from chaff.engine import generate_entity_rows, generate_rows
from chaff.formats import get_encoder, get_record_encoder, list_record_formats
from chaff.spec import load_spec


def point_spec(**opts):
    return load_spec({
        "name": "pts", "seed": 3, "rows": 5,
        "columns": [
            {"name": "track_id", "generator": "pattern", "params": {"pattern": "U-###"}},
            {"name": "lat", "generator": "lat", "params": {"min": 34.0, "max": 34.1}},
            {"name": "lon", "generator": "lon", "params": {"min": -118.5, "max": -118.4}},
            {"name": "callsign", "generator": "pattern", "params": {"pattern": "CS-##"}},
        ],
        "output": {"format": "cot", "options": opts},
    })


def test_cot_is_record_capable():
    assert "cot" in list_record_formats()


def test_cot_emits_valid_events():
    spec = point_spec()
    payload = get_encoder("cot")(spec, generate_rows(spec)).decode()
    events = [ln for ln in payload.splitlines() if ln]
    assert len(events) == 5
    ev = ET.fromstring(events[0])
    assert ev.tag == "event" and ev.get("version") == "2.0"
    assert ev.get("uid").startswith("U-")          # from track_id
    pt = ev.find("point")
    assert 34.0 <= float(pt.get("lat")) <= 34.1
    assert ev.find("detail/contact").get("callsign").startswith("CS-")


def test_cot_time_is_deterministic_not_wallclock():
    spec = point_spec(base_time="2025-06-01T00:00:00Z")
    a = get_encoder("cot")(spec, generate_rows(spec))
    time.sleep(1.1)
    b = get_encoder("cot")(spec, generate_rows(spec))
    assert a == b  # INV-3: no datetime.now() leaking in
    assert b"2025-06-01T00:00:00.000Z" in a  # time came from base_time


def test_cot_time_advances_with_tick():
    spec = load_spec({
        "name": "trk", "seed": 1,
        "columns": [{"name": "lat", "generator": "lat"}, {"name": "lon", "generator": "lon"}],
        "output": {"format": "cot", "options": {
            "tick_column": "t", "base_time": "2025-01-01T00:00:00Z", "interval_seconds": 10}},
        "entity": {"count": 1, "ticks": 3, "tick_column": "t",
                   "updates": [{"updater": "movement", "params": {"speed": 0.01}}]},
    })
    events = [ET.fromstring(e) for e in
              get_encoder("cot")(spec, generate_entity_rows(spec)).decode().splitlines() if e]
    times = [e.get("time") for e in events]
    assert times == ["2025-01-01T00:00:00.000Z",
                     "2025-01-01T00:00:10.000Z",
                     "2025-01-01T00:00:20.000Z"]


def test_cot_stale_after_time():
    spec = point_spec(base_time="2025-01-01T00:00:00Z", stale_seconds=60)
    ev = ET.fromstring(get_encoder("cot")(spec, generate_rows(spec)).decode().splitlines()[0])
    assert ev.get("time") == "2025-01-01T00:00:00.000Z"
    assert ev.get("stale") == "2025-01-01T00:01:00.000Z"


def test_cot_record_and_blob_agree():
    spec = point_spec()
    rows = generate_rows(spec)
    blob = get_encoder("cot")(spec, rows)
    rec = get_record_encoder("cot")
    assert blob == b"".join(rec(spec, r) for r in rows)


def test_cot_type_override():
    spec = point_spec(type="a-h-A-M-F")  # hostile air
    ev = ET.fromstring(get_encoder("cot")(spec, generate_rows(spec)).decode().splitlines()[0])
    assert ev.get("type") == "a-h-A-M-F"
