"""The court case_management preset: the data must hold together the way
a court's records do, or a tester loading it into a case system chases
errors chaff invented. FK integrity alone isn't enough — a case numbered
`2-24-CV-…` filed in 2021, or a terminated date on an open case, would read
as a defect in the system under test."""

import json
import re
from pathlib import Path

import pytest

from chaff.engine import generate_tables
from chaff.spec import load_spec

PRESET = Path("examples/case_management.json")
CASE_NUMBER = re.compile(r"^[1-4]-\d{2}-(CV|CR|MC|MJ)-\d{5}$")
CUTOFF = "2026-09-30"


@pytest.fixture(scope="module")
def tables():
    return generate_tables(load_spec(json.loads(PRESET.read_text())))


def test_every_table_is_present(tables):
    assert set(tables) == {"cases", "judges", "attorneys", "parties", "representations",
                           "docket_entries", "hearings", "case_events"}


@pytest.mark.parametrize("child, column, parent, key", [
    ("cases", "assigned_judge_id", "judges", "judge_id"),
    ("parties", "case_id", "cases", "case_id"),
    ("representations", "party_id", "parties", "party_id"),
    ("representations", "attorney_id", "attorneys", "attorney_id"),
    ("docket_entries", "case_id", "cases", "case_id"),
    ("hearings", "case_id", "cases", "case_id"),
    ("hearings", "judge_id", "judges", "judge_id"),
    ("case_events", "case_id", "cases", "case_id"),
])
def test_foreign_keys_resolve(tables, child, column, parent, key):
    keys = {r[key] for r in tables[parent]}
    assert all(r[column] in keys for r in tables[child])


def test_case_numbers_are_well_formed_and_match_the_filing_year(tables):
    for c in tables["cases"]:
        assert CASE_NUMBER.match(c["case_number"]), c["case_number"]
        assert c["case_number"].split("-")[1] == c["date_filed"][2:4]


def test_only_closed_cases_are_terminated_and_never_before_filing(tables):
    for c in tables["cases"]:
        if c["status"] == "Closed":
            assert c["date_filed"] <= c["date_terminated"] <= CUTOFF
        else:
            assert c["date_terminated"] is None


def test_nature_of_suit_is_civil_only(tables):
    for c in tables["cases"]:
        assert (c["nature_of_suit"] is not None) == (c["case_type"] == "CV")


def test_only_individuals_appear_pro_se(tables):
    assert any(p["pro_se"] for p in tables["parties"])
    assert all(p["party_type"] == "Individual" for p in tables["parties"] if p["pro_se"])


# ── Children agree with their parents (ADR-0043, issue #77) ──────────

def _by(rows, key):
    return {r[key]: r for r in rows}


def test_case_court_is_the_assigned_judges_court(tables):
    judges = _by(tables["judges"], "judge_id")
    for c in tables["cases"]:
        judge = judges[c["assigned_judge_id"]]
        assert judge["bench"] == "District" and judge["court"] == c["court"]


def test_referred_magistrate_sits_in_the_same_court(tables):
    judges = _by(tables["judges"], "judge_id")
    referred = [c for c in tables["cases"] if c["referred_magistrate_id"] is not None]
    assert len(referred) > len(tables["cases"]) // 2
    for c in referred:
        m = judges[c["referred_magistrate_id"]]
        assert m["title"] == "Magistrate Judge" and m["court"] == c["court"]


@pytest.mark.parametrize("child, column", [
    ("parties", "date_added"), ("docket_entries", "filed_at"),
    ("hearings", "scheduled_at"), ("case_events", "occurred_at"),
])
def test_child_dates_fall_inside_their_case(tables, child, column):
    cases = _by(tables["cases"], "case_id")
    for r in tables[child]:
        c = cases[r["case_id"]]
        day = r[column][:10]
        assert c["date_filed"] <= day, (child, r)
        if c["date_terminated"]:
            assert day <= c["date_terminated"], (child, r)


def test_hearings_are_held_by_the_cases_judge(tables):
    cases = _by(tables["cases"], "case_id")
    for h in tables["hearings"]:
        assert h["judge_id"] == cases[h["case_id"]]["assigned_judge_id"]


def test_only_future_hearings_are_still_scheduled(tables):
    for h in tables["hearings"]:
        assert (h["outcome"] == "Scheduled") == (h["scheduled_at"] > CUTOFF + "T23:59:59")


def test_criminal_only_filings_stay_on_criminal_cases(tables):
    criminal_only = {"Indictment", "Plea Agreement", "Sentencing Memorandum"}
    for e in tables["docket_entries"]:
        if e["case_type"] != "CR":
            assert e["document_type"] not in criminal_only, e
            assert e["filed_by"] != "Government", e
