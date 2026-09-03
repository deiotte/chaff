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


# ── Absence stays absence (ADR-0032) ─────────────────────────────────
# CoT has no "missing" encoding for its numeric fields, so the standard
# reserves the literal 9999999. The bug these guard against is the quiet
# one: emitting 0.0 for a column the spec never declared, which arrives at
# a consumer as a *measurement of zero* rather than as no measurement.

def detail_of(spec, rows=None):
    """The first event's <detail>, as an Element."""
    rows = generate_rows(spec) if rows is None else rows
    return ET.fromstring(
        get_encoder("cot")(spec, rows).decode().splitlines()[0])


def test_cot_absent_optional_numerics_use_the_unknown_sentinel():
    ev = detail_of(point_spec())  # no hae, ce or le column in point_spec
    pt = ev.find("point")
    assert pt.get("hae") == "9999999.0"
    assert pt.get("ce") == "9999999.0"
    assert pt.get("le") == "9999999.0"


def test_cot_present_optional_numerics_are_emitted_verbatim():
    spec = point_spec()
    row = {"track_id": "U-1", "lat": 34.0, "lon": -118.0,
           "callsign": "CS-1", "hae": 194.9, "ce": 37.4, "le": 42.2}
    pt = detail_of(spec, [row]).find("point")
    assert (pt.get("hae"), pt.get("ce"), pt.get("le")) == ("194.9", "37.4", "42.2")


def test_cot_non_finite_numbers_never_reach_the_wire():
    """A NaN formats as the literal `nan`, which is not a CoT number.

    An optional field degrades to the sentinel (we know we don't have it);
    a required one degrades to 0.0, which is what `_num` has always done.
    """
    spec = point_spec()
    row = {"track_id": "U-1", "lat": float("nan"), "lon": float("inf"),
           "callsign": "CS-1", "hae": float("nan"), "ce": float("-inf")}
    pt = detail_of(spec, [row]).find("point")
    assert pt.get("lat") == "0.000000" and pt.get("lon") == "0.000000"
    assert pt.get("hae") == "9999999.0" and pt.get("ce") == "9999999.0"


# ── <track>, <status>, <precisionlocation>, <takv> ───────────────────

def test_cot_track_is_omitted_when_the_row_has_no_kinematics():
    """A <track> with neither attribute would assert the reporter said
    something about its motion when it said nothing."""
    assert detail_of(point_spec()).find("detail/track") is None


def test_cot_track_carries_speed_and_course():
    spec = point_spec()
    row = {"track_id": "U-1", "lat": 34.0, "lon": -118.0, "callsign": "CS-1",
           "speed": 8.987, "course": 328.0}
    track = detail_of(spec, [row]).find("detail/track")
    assert track.get("speed") == "8.99" and track.get("course") == "328.0"


def test_cot_course_falls_back_to_a_heading_column():
    spec = point_spec()
    row = {"track_id": "U-1", "lat": 34.0, "lon": -118.0, "callsign": "CS-1",
           "heading": 90}
    track = detail_of(spec, [row]).find("detail/track")
    assert track.get("course") == "90.0" and track.get("speed") is None


def test_cot_course_stays_below_360():
    """Degrees true is [0, 360). An int_range(0, 360) tops out at exactly
    360, which a strict reader rejects as out of range."""
    spec = point_spec()
    row = {"track_id": "U-1", "lat": 34.0, "lon": -118.0, "callsign": "CS-1",
           "course": 360}
    assert detail_of(spec, [row]).find("detail/track").get("course") == "0.0"


def test_cot_status_battery_tracks_the_column():
    spec = point_spec()
    base = {"track_id": "U-1", "lat": 34.0, "lon": -118.0, "callsign": "CS-1"}
    assert detail_of(spec, [base]).find("detail/status") is None
    assert detail_of(spec, [{**base, "battery": 94}]).find(
        "detail/status").get("battery") == "94"


def test_cot_precisionlocation_is_opt_in():
    assert detail_of(point_spec()).find("detail/precisionlocation") is None
    pl = detail_of(point_spec(geopointsrc="GPS", altsrc="DTED0")).find(
        "detail/precisionlocation")
    assert pl.get("geopointsrc") == "GPS" and pl.get("altsrc") == "DTED0"


def test_cot_takv_marks_the_feed_as_synthetic_by_default():
    """chaff says so in its own provenance field, so a consumer never has to
    be told out-of-band which feed is synthetic."""
    assert detail_of(point_spec()).find("detail/takv").get("platform") == "chaff"


def test_cot_takv_platform_can_be_overridden_or_dropped():
    assert detail_of(point_spec(takv_platform="ATAK-CIV")).find(
        "detail/takv").get("platform") == "ATAK-CIV"
    assert detail_of(point_spec(takv_platform="")).find("detail/takv") is None


def test_cot_detail_child_order_is_stable():
    """Determinism (INV-3) covers element order, not just values."""
    spec = point_spec(geopointsrc="GPS")
    row = {"track_id": "U-1", "lat": 34.0, "lon": -118.0, "callsign": "CS-1",
           "speed": 4.0, "battery": 50}
    children = [el.tag for el in detail_of(spec, [row]).find("detail")]
    assert children == ["track", "precisionlocation", "takv", "status", "contact"]
