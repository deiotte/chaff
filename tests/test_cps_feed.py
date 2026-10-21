"""cps_feed encoder and the schedule/follow updaters (ADR-0045)."""

import json

import pytest

from chaff.engine import generate_entity_rows
from chaff.formats import get_encoder, get_record_encoder, list_record_formats
from chaff.spec import load_spec

BASE = "2026-10-14T00:00:00Z"


def _spec(seed=5, ticks=288, **out):
    return load_spec({
        "name": "case", "seed": seed,
        "columns": [
            {"name": "setpoint", "generator": "float_uniform", "params": {"min": -18, "max": -18, "precision": 1}},
            {"name": "case_temp", "generator": "float_uniform", "params": {"min": -18, "max": -18, "precision": 1}},
            {"name": "defrost", "generator": "int_range", "params": {"min": 0, "max": 0}},
        ],
        "entity": {"count": 1, "ticks": ticks, "id_column": "equipment", "id_pattern": "site/case-07",
                   "tick_column": "tick", "updates": [
            {"updater": "schedule", "params": {
                "column": "defrost", "base_time": BASE, "interval_seconds": 300,
                "entries": [{"at": "02:00", "for_minutes": 30, "value": 1}]}},
            {"updater": "schedule", "params": {
                "column": "setpoint", "default": -21, "fired_column": "fired",
                "base_time": BASE, "interval_seconds": 300,
                "entries": [{"at": "05:00", "value": -19}, {"at": "21:00", "value": -21}]}},
            {"updater": "follow", "params": {
                "column": "case_temp", "target": "setpoint", "rate": 0.3, "stddev": 0.0,
                "lift_column": "defrost", "lift": 12}},
        ]},
        "output": {"format": "cps_feed", "options": {
            "base_time": BASE, "interval_seconds": 300,
            "samples": {"case_temp": {"tag": "C7.TEMP", "unit": "degC"}, "defrost": {"tag": "C7.DEFROST"}},
            "writes": {"setpoint": {"tag": "C7.SP", "fire_column": "fired", "channel": "supervisor",
                                    "address": "10.0.7.12"}},
            **out}},
    })


def _lines(spec):
    return [json.loads(x) for x in get_encoder("cps_feed")(spec, list(generate_entity_rows(spec))).decode().splitlines()]


def test_cps_feed_is_record_capable():
    assert "cps_feed" in list_record_formats()


def test_record_and_file_framing_agree():
    spec = _spec(ticks=20)
    rows = list(generate_entity_rows(spec))
    rec = get_record_encoder("cps_feed")
    assert b"".join(rec(spec, r) for r in rows) == get_encoder("cps_feed")(spec, rows)


def test_same_seed_same_bytes():
    a = get_encoder("cps_feed")(_spec(), list(generate_entity_rows(_spec())))
    b = get_encoder("cps_feed")(_spec(), list(generate_entity_rows(_spec())))
    assert a == b


def test_lines_have_the_feed_shape():
    lines = _lines(_spec(ticks=3))
    assert all(x["schema"] == "cps-feed/1" for x in lines)
    s = lines[0]
    assert s["event"] == "sample" and s["target"]["equipment"] == "site/case-07"
    assert s["at_ms"] == 1791936000000 and isinstance(s["value"], float)


def test_the_setpoint_is_written_when_the_schedule_fires_and_only_then():
    writes = [x for x in _lines(_spec()) if x["event"] == "write"]
    assert [(w["at_ms"], w["value"]) for w in writes] == [
        (1791936000000 + 5 * 3600 * 1000, -19.0), (1791936000000 + 21 * 3600 * 1000, -21.0)]
    assert writes[0]["origin"] == {"channel": "supervisor", "address": "10.0.7.12"}


def test_a_window_opens_holds_and_closes():
    d = {x["at_ms"]: x["value"] for x in _lines(_spec()) if x["target"]["tag"] == "C7.DEFROST"}
    at = lambda h, m: d[1791936000000 + (h * 60 + m) * 60000]
    assert at(1, 55) == 0 and at(2, 0) == 1 and at(2, 25) == 1 and at(2, 30) == 0


def test_the_case_warms_during_the_window_and_recovers():
    t = {x["at_ms"]: x["value"] for x in _lines(_spec()) if x["target"]["tag"] == "C7.TEMP"}
    h = lambda hh, mm: t[1791936000000 + (hh * 60 + mm) * 60000]
    assert h(2, 25) > h(1, 55) + 5
    assert h(4, 30) < h(2, 25)


@pytest.mark.parametrize("bad", [{"samples": {"case_temp": {"tag": "has space"}}},
                                 {"equipment_column": "no_such_column"}])
def test_names_the_feed_cannot_carry_are_refused_loudly(bad):
    with pytest.raises(ValueError):
        _lines(_spec(**bad))
