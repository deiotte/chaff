"""The legacy_migration preset (#86): every defect it claims is really in the
extract, and the answer key says exactly where. A migration team scores its
ETL against these keys, so a key that is wrong about one row is worse than no
key at all — each flag is checked against the dirty data itself, never
against the hidden value that produced it."""

import collections
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chaff.cli import app
from chaff.engine import encode_tables, generate_tables
from chaff.spec import load_spec

PRESET = Path("examples/legacy_migration.json")


@pytest.fixture(scope="module")
def spec():
    return load_spec(json.loads(PRESET.read_text()))


@pytest.fixture(scope="module")
def t(spec):
    return generate_tables(spec)


def test_answer_keys_line_up_one_to_one(t):
    assert len(t["answer_key_cases"]) == len(t["legacy_cases"])
    assert [k["legacy_case_no"] for k in t["answer_key_cases"]] == [c["CASE_NO"] for c in t["legacy_cases"]]
    assert [k["legacy_entry_no"] for k in t["answer_key_docket"]] == [d["ENTRY_NO"] for d in t["legacy_docket"]]


def test_no_clean_value_leaks_into_the_extract(t):
    leaked = {"filed_clean", "status", "clean_name", "nos_code", "expected_case_number"}
    assert not leaked & set(t["legacy_cases"][0])
    assert not {"entry_clean", "doc_type", "orphan_roll"} & set(t["legacy_docket"][0])


def test_duplicate_flags_match_the_keys_that_repeat(t):
    counts = collections.Counter(c["CASE_NO"] for c in t["legacy_cases"])
    for c, k in zip(t["legacy_cases"], t["answer_key_cases"]):
        assert k["is_duplicate_key"] == (counts[c["CASE_NO"]] > 1), c["CASE_NO"]
    assert sum(k["is_duplicate_key"] for k in t["answer_key_cases"]) > 10


def test_null_dates_and_mixed_formats(t):
    dates = [c["FILED_DT"] for c in t["legacy_cases"]]
    for c, k in zip(t["legacy_cases"], t["answer_key_cases"]):
        assert k["has_null_date"] == (c["FILED_DT"] == "00000000")
        assert (k["expected_filed_date"] is None) == k["has_null_date"]
    assert any(d.count("/") == 2 and d.endswith("/99") for d in dates)   # 19xx, two-digit
    assert any(d.count("/") == 2 and d.endswith("/03") for d in dates)   # 20xx, two-digit
    assert any(len(d) == 8 and d.isdigit() and d != "00000000" for d in dates)
    assert any("-" in d and d[3:6].isalpha() for d in dates)


def test_status_codes_and_free_text(t):
    legal = {"O", "C", "P", "D"}
    for c, k in zip(t["legacy_cases"], t["answer_key_cases"]):
        assert k["has_retired_status"] == (c["STAT_CD"] not in legal), c
        assert k["has_free_text_nos"] == (c["NOS"] not in {"", "190", "360", "440", "442", "530", "550"})


def test_name_damage_is_flagged_exactly(t):
    damaged = 0
    for c, k in zip(t["legacy_cases"], t["answer_key_cases"]):
        assert k["name_damaged"] == (c["PARTY_NM"] != k["expected_party_name"])
        damaged += k["name_damaged"]
    names = [c["PARTY_NM"] for c in t["legacy_cases"]]
    assert damaged > 100 and any("Ã" in n for n in names) and any(n != n.rstrip() for n in names)


def test_orphans_point_at_cases_that_do_not_exist(t):
    real = {c["CASE_NO"] for c in t["legacy_cases"]}
    for d, k in zip(t["legacy_docket"], t["answer_key_docket"]):
        assert k["is_orphan"] == (d["CASE_NO"] not in real), d
        assert k["expected_action"] == ("reject" if k["is_orphan"] else "load")
    assert sum(k["is_orphan"] for k in t["answer_key_docket"]) > 50


def test_rekeyed_numbers_keep_the_year_and_sequence(t):
    for k in t["answer_key_cases"]:
        if k["is_duplicate_key"]:
            continue
        old_type, old_year, old_seq = k["legacy_case_no"].split("-")
        office, yy, new_type, seq = k["expected_case_number"].split("-")
        assert (new_type, yy, seq) == (old_type, old_year[2:], old_seq) and office in "1234"


def test_docket_entries_never_predate_their_case(t):
    filed = {c["CASE_NO"]: k["expected_filed_date"]
             for c, k in zip(t["legacy_cases"], t["answer_key_cases"]) if not k["is_duplicate_key"]}
    for k in t["answer_key_docket"]:
        f = filed.get(k["legacy_case_no"])
        if f and not k["is_orphan"]:
            assert k["expected_entry_date"] >= f


def test_reconciliation_counts_come_straight_from_the_key(t):
    actions = collections.Counter(k["expected_action"] for k in t["answer_key_cases"])
    assert set(actions) == {"load", "clean_text", "map_code", "quarantine"}
    assert sum(actions.values()) == len(t["legacy_cases"])


def test_fixed_width_files_are_rectangular(spec):
    for name, _, payload in encode_tables(spec):
        lengths = {len(line) for line in payload.decode().splitlines()}
        assert len(lengths) == 1, name


def test_cli_format_override_writes_csv(tmp_path):
    out = tmp_path / "x.csv"
    r = CliRunner().invoke(app, ["generate", str(PRESET), "--format", "csv", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert (tmp_path / "answer_key_cases.csv").read_text().startswith("legacy_case_no,")
