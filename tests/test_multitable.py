"""Multi-table + FK integrity tests (ADR-0008)."""

import pytest

from chaff.engine import generate_rows, generate_tables, run
from chaff.spec import load_spec


def retail(**over):
    d = {
        "name": "customers", "seed": 42, "rows": 8,
        "columns": [
            {"name": "customer_id", "generator": "pattern", "params": {"pattern": "CUST-#####"}},
            {"name": "name", "generator": "full_name"},
        ],
        "output": {"format": "csv"},
        "tables": [
            {"name": "orders", "rows": 25, "columns": [
                {"name": "order_id", "generator": "row_id"},
                {"name": "customer_id", "generator": "fk",
                 "params": {"table": "customers", "column": "customer_id"}},
            ]},
            {"name": "lines", "rows": 60, "columns": [
                {"name": "line_id", "generator": "row_id"},
                {"name": "order_id", "generator": "fk",
                 "params": {"table": "orders", "column": "order_id"}},
            ]},
        ],
    }
    d.update(over)
    return load_spec(d)


def test_tables_generated_in_dependency_order():
    tables = generate_tables(retail())
    assert list(tables) == ["customers", "orders", "lines"]  # parents first
    assert {k: len(v) for k, v in tables.items()} == {"customers": 8, "orders": 25, "lines": 60}


def test_fk_values_are_all_valid_parents():
    t = generate_tables(retail())
    cust_ids = {r["customer_id"] for r in t["customers"]}
    order_ids = {r["order_id"] for r in t["orders"]}
    assert all(o["customer_id"] in cust_ids for o in t["orders"])
    assert all(l["order_id"] in order_ids for l in t["lines"])


def test_multi_table_is_deterministic():
    assert generate_tables(retail()) == generate_tables(retail())
    assert generate_tables(retail(seed=1)) != generate_tables(retail())


def test_missing_fk_reference_fails_fast():
    spec = load_spec({
        "name": "a", "rows": 2, "output": {"format": "csv"},
        "columns": [{"name": "x", "generator": "fk", "params": {"table": "ghost", "column": "y"}}],
    })
    with pytest.raises(ValueError, match="unknown table 'ghost'"):
        generate_tables(spec)


def test_self_reference_rejected():
    spec = load_spec({
        "name": "a", "rows": 2, "output": {"format": "csv"},
        "columns": [{"name": "x", "generator": "fk", "params": {"table": "a", "column": "x"}}],
    })
    with pytest.raises(ValueError, match="fk to itself"):
        generate_tables(spec)


def test_cyclic_dependency_detected():
    spec = load_spec({
        "name": "a", "rows": 2, "output": {"format": "csv"},
        "columns": [{"name": "x", "generator": "fk", "params": {"table": "b", "column": "y"}}],
        "tables": [{"name": "b", "rows": 2, "columns": [
            {"name": "y", "generator": "fk", "params": {"table": "a", "column": "x"}}]}],
    })
    with pytest.raises(ValueError, match="cyclic fk dependency"):
        generate_tables(spec)


def test_duplicate_table_names_rejected():
    with pytest.raises(Exception, match="duplicate table names"):
        load_spec({
            "name": "a", "rows": 2, "columns": [{"name": "x", "generator": "uuid"}],
            "output": {"format": "csv"},
            "tables": [{"name": "a", "rows": 2, "columns": [{"name": "y", "generator": "uuid"}]}],
        })


def test_fk_without_parent_context_is_clear_error():
    # An fk column in a single-table spec has no parent tables to draw from.
    spec = load_spec({
        "name": "solo", "rows": 3, "output": {"format": "csv"},
        "columns": [{"name": "ref", "generator": "fk", "params": {"table": "nope", "column": "id"}}],
    })
    with pytest.raises(KeyError, match="unknown/ungenerated table"):
        generate_rows(spec)


def test_run_writes_one_file_per_table(tmp_path):
    spec = retail(sink={"sink": "file", "options": {"path": str(tmp_path / "x.csv")}})
    receipt = run(spec)
    for name in ("customers", "orders", "lines"):
        assert (tmp_path / f"{name}.csv").is_file()
        assert name in receipt


def test_single_table_spec_unaffected():
    # No `tables` => classic path; sanity that generate_rows still works.
    spec = load_spec({"name": "t", "seed": 5, "rows": 4,
                      "columns": [{"name": "id", "generator": "row_id"}],
                      "output": {"format": "csv"}})
    assert [r["id"] for r in generate_rows(spec)] == [1, 2, 3, 4]


def test_streaming_sink_rejected_for_multi_table():
    spec = retail(sink={"sink": "http", "options": {"url": "http://x"}})
    with pytest.raises(ValueError, match="don't support multi-table"):
        run(spec)


def test_api_rejects_multi_table():
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from api.main import app
    client = TestClient(app)
    body = retail().model_dump()
    assert client.post("/generate", json=body).status_code == 400
    assert client.post("/preview", json=body).status_code == 400
