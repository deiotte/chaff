"""Correlated foreign keys (ADR-0043): a child row agrees with its parent.

`fk` used to copy only the key, so a hearing could be held by a judge who
wasn't on the case and a docket entry could predate the case it belongs to.
These tests pin the three seams that close that: `parent` copies a parent's
cell, `where`/`match` restrict which parents qualify, and `after`/`before`
bound a date by earlier cells. Each is opt-in and draws no extra entropy, so
existing specs stay byte-identical (INV-3)."""

import hashlib
import json
from pathlib import Path

import pytest

from chaff.engine import generate_tables
from chaff.spec import load_spec


def spec(**over):
    d = {
        "name": "cases", "seed": 3, "rows": 30,
        "columns": [
            {"name": "case_id", "generator": "row_id", "params": {"start": 1}},
            {"name": "court", "generator": "choice", "params": {"values": ["N", "S", "E"]}},
            {"name": "opened", "generator": "date_between",
             "params": {"start": "2020-01-01", "end": "2024-12-31"}},
            {"name": "closed", "generator": "date_between",
             "params": {"start": "2020-01-01", "end": "2025-12-31", "after": "opened"}},
        ],
        "output": {"format": "csv"},
        "tables": [
            {"name": "judges", "rows": 12, "columns": [
                {"name": "judge_id", "generator": "row_id", "params": {"start": 100}},
                {"name": "court", "generator": "choice", "params": {"values": ["N", "S", "E"]}},
                {"name": "title", "generator": "choice", "params": {"values": ["Judge", "Magistrate"]}},
            ]},
            {"name": "entries", "rows": 200, "columns": [
                {"name": "case_id", "generator": "fk", "params": {"table": "cases", "column": "case_id"}},
                {"name": "case_court", "generator": "parent", "params": {"via": "case_id", "column": "court"}},
                {"name": "case_opened", "generator": "parent", "params": {"via": "case_id", "column": "opened"}},
                {"name": "case_closed", "generator": "parent", "params": {"via": "case_id", "column": "closed"}},
                {"name": "filed_at", "generator": "timestamp_between",
                 "params": {"start": "2019-01-01T00:00:00", "end": "2026-01-01T00:00:00",
                            "after": "case_opened", "before": "case_closed"}},
                {"name": "magistrate", "generator": "fk",
                 "params": {"table": "judges", "column": "judge_id",
                            "where": {"title": "Magistrate"}, "match": {"court": "case_court"}}},
            ]},
        ],
    }
    d.update(over)
    return load_spec(d)


@pytest.fixture(scope="module")
def tables():
    return generate_tables(spec())


def test_parent_copies_the_chosen_parents_cells(tables):
    cases = {c["case_id"]: c for c in tables["cases"]}
    for e in tables["entries"]:
        case = cases[e["case_id"]]
        assert (e["case_court"], e["case_opened"], e["case_closed"]) == \
               (case["court"], case["opened"], case["closed"])


def test_dates_are_bounded_by_the_row(tables):
    for c in tables["cases"]:
        assert c["opened"] <= c["closed"]
    for e in tables["entries"]:
        assert e["case_opened"] <= e["filed_at"][:10] <= e["case_closed"]


def test_a_date_can_be_bounded_through_an_fk():
    s = json.loads(spec().model_dump_json())
    filed = s["tables"][1]["columns"][4]["params"]
    filed.update(after="case_id.opened", before="case_id.closed")
    t = generate_tables(load_spec(s))
    cases = {c["case_id"]: c for c in t["cases"]}
    for e in t["entries"]:
        c = cases[e["case_id"]]
        assert c["opened"] <= e["filed_at"][:10] <= c["closed"]


def test_where_and_match_restrict_the_parent(tables):
    judges = {j["judge_id"]: j for j in tables["judges"]}
    picked = [e for e in tables["entries"] if e["magistrate"] is not None]
    assert picked, "no entry found a magistrate; the filter test proves nothing"
    for e in picked:
        j = judges[e["magistrate"]]
        assert j["title"] == "Magistrate" and j["court"] == e["case_court"]


def test_no_qualifying_parent_gives_none():
    s = spec()
    s.tables[1].columns[-1].params["where"] = {"title": "Nobody"}
    assert all(e["magistrate"] is None for e in generate_tables(s)["entries"])


def test_correlated_tables_are_deterministic():
    assert generate_tables(spec()) == generate_tables(spec())


def test_plain_specs_are_byte_identical_to_before():
    """No new params → the same rng draws as before ADR-0043. This digest of
    the retail preset was taken on the commit before the change; if it moves,
    an fk/date edit started consuming entropy for specs that don't opt in."""
    data = json.loads(Path("examples/retail_orders.json").read_text())
    blob = json.dumps(generate_tables(load_spec(data)), sort_keys=True, default=str)
    assert hashlib.sha256(blob.encode()).hexdigest()[:16] == "eb75ee2ee792a8a7"


@pytest.mark.parametrize("patch, msg", [
    (lambda d: d["tables"][1]["columns"][1]["params"].update(via="nope"), "parent 'via' must name an fk"),
    (lambda d: d["tables"][1]["columns"][1]["params"].update(column="nope"), "has no column 'nope'"),
    (lambda d: d["tables"][1]["columns"][5]["params"].update(where={"nope": 1}), "fk filter names 'nope'"),
    (lambda d: d["tables"][1]["columns"][5]["params"].update(match={"court": "later"}), "reads 'later'"),
    (lambda d: d["tables"][1]["columns"][5]["params"].update(where={"title": ["a", "b"]}), "must be a single value"),
    (lambda d: d["columns"][3]["params"].update(after="later"), "'after' references 'later'"),
    (lambda d: d["tables"][1]["columns"][4]["params"].update(after="case_id.nope"), "references 'case_id.nope'"),
])
def test_broken_references_fail_at_load(patch, msg):
    d = json.loads(spec().model_dump_json())
    patch(d)
    with pytest.raises(ValueError, match=msg):
        load_spec(d)
