"""Invariant tests. These encode the promises in the ADRs — if one of
these fails, the contract broke, not the test."""

import json

from chaff.engine import generate_rows, run
from chaff.formats import get_encoder
from chaff.spec import load_spec


def base_spec(**over):
    d = {
        "name": "demo_cases",
        "seed": 1337,
        "rows": 200,
        "columns": [
            {"name": "case_id", "generator": "pattern", "params": {"pattern": "DEA-####-?????"}},
            {"name": "agent", "generator": "full_name"},
            {"name": "status", "generator": "choice_weighted",
             "params": {"values": ["Open", "Pending", "Closed"], "weights": [0.7, 0.2, 0.1]}},
            {"name": "opened", "generator": "date_between",
             "params": {"start": "2024-01-01", "end": "2025-12-31"}},
            {"name": "amount", "generator": "money", "params": {"min": 100, "max": 50000},
             "null_rate": 0.1},
        ],
        "output": {"format": "csv"},
    }
    d.update(over)
    return load_spec(d)


def test_seed_determinism_is_byte_for_byte():
    """ADR-0004: same spec + same seed = identical output."""
    a = generate_rows(base_spec())
    b = generate_rows(base_spec())
    assert a == b
    enc = get_encoder("csv")
    assert enc(base_spec(), a) == enc(base_spec(), b)


def test_different_seed_different_data():
    assert generate_rows(base_spec()) != generate_rows(base_spec(seed=42))


def test_weighted_choice_roughly_honors_weights():
    rows = generate_rows(base_spec(rows=2000))
    open_rate = sum(1 for r in rows if r["status"] == "Open") / len(rows)
    assert 0.60 < open_rate < 0.80  # 0.7 +/- sampling noise


def test_null_rate_applied():
    rows = generate_rows(base_spec(rows=2000))
    null_rate = sum(1 for r in rows if r["amount"] is None) / len(rows)
    assert 0.05 < null_rate < 0.15


def test_pattern_generator_shape():
    rows = generate_rows(base_spec(rows=5))
    for r in rows:
        cid = r["case_id"]
        assert cid.startswith("DEA-")
        assert cid[4:8].isdigit()
        assert cid[9:].isalpha() and cid[9:].isupper() and len(cid[9:]) == 5


def test_unknown_generator_fails_fast():
    import pytest
    with pytest.raises(KeyError, match="unknown generator"):
        generate_rows(base_spec(columns=[{"name": "x", "generator": "nope"}]))


def test_sql_encoder_produces_create_and_inserts():
    spec = base_spec(output={"format": "sql", "options": {"dialect": "postgres"}})
    payload = get_encoder("sql")(spec, generate_rows(spec)).decode()
    assert 'CREATE TABLE "demo_cases"' in payload
    assert "INSERT INTO" in payload
    assert "''" not in payload.split("CREATE")[0]  # header comment clean


def test_ndjson_one_object_per_line():
    spec = base_spec(rows=10, output={"format": "ndjson"})
    payload = get_encoder("ndjson")(spec, generate_rows(spec)).decode()
    lines = [ln for ln in payload.splitlines() if ln]
    assert len(lines) == 10
    for ln in lines:
        json.loads(ln)


def test_file_sink_end_to_end(tmp_path):
    spec = base_spec(rows=10, sink={"sink": "file", "options": {"path": str(tmp_path / "o.csv")}})
    receipt = run(spec)
    assert "wrote" in receipt
    assert (tmp_path / "o.csv").read_text().startswith("case_id,agent,status")


def test_xlsx_is_byte_for_byte_deterministic():
    """INV-3 extends to xlsx: a zip's timestamps must not leak wall-clock
    entropy. Same spec + same seed = identical bytes, twice."""
    spec = base_spec(rows=50, output={"format": "xlsx"})
    a = get_encoder("xlsx")(spec, generate_rows(spec))
    b = get_encoder("xlsx")(spec, generate_rows(spec))
    assert a == b
    assert a[:2] == b"PK"  # it's a real zip container


def test_xlsx_round_trips_header_and_rows():
    import io

    import pytest
    openpyxl = pytest.importorskip("openpyxl")

    spec = base_spec(rows=10, output={"format": "xlsx"})
    payload = get_encoder("xlsx")(spec, generate_rows(spec))
    wb = openpyxl.load_workbook(io.BytesIO(payload))
    ws = wb.active
    assert [c.value for c in ws[1]] == ["case_id", "agent", "status", "opened", "amount"]
    assert ws.max_row == 11  # header + 10 data rows


def test_jenny_egg_rides_the_receipt_only(tmp_path):
    """Fun clause: seed 8675309 nods to Jenny in the receipt, and the
    payload bytes are identical to any other seed's — determinism intact."""
    def spec(seed):
        return base_spec(rows=5, seed=seed,
                         sink={"sink": "file", "options": {"path": str(tmp_path / f"{seed}.csv")}})
    jenny_receipt = run(spec(8675309))
    assert "Jenny" in jenny_receipt
    assert "Jenny" not in run(spec(8675308))
    # The egg rides the receipt only — it never reaches the file payload.
    assert "Jenny" not in (tmp_path / "8675309.csv").read_text()


def test_duplicate_column_names_rejected():
    import pytest
    with pytest.raises(Exception, match="duplicate"):
        base_spec(columns=[
            {"name": "a", "generator": "uuid"},
            {"name": "a", "generator": "uuid"},
        ])
