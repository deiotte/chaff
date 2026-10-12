"""`dirty` and `legacy_date` (ADR-0044): legacy damage, seeded."""

import pytest

from chaff.engine import generate_rows, generate_tables
from chaff.spec import load_spec


def rows(col, n=200, source=("clean", "choice", {"values": ["José Álvarez"]})):
    name, gen, params = source
    return generate_rows(load_spec({
        "name": "t", "seed": 9, "rows": n,
        "columns": [{"name": name, "generator": gen, "params": params}, col],
        "output": {"format": "csv"},
    }))


@pytest.mark.parametrize("kind, check", [
    ("upper", lambda s: s == "JOSÉ ÁLVAREZ"),
    ("trailing_space", lambda s: s.rstrip() == "José Álvarez" and s != s.rstrip()),
    ("leading_space", lambda s: s.lstrip() == "José Álvarez" and s != s.lstrip()),
    ("typo", lambda s: sorted(s) == sorted("José Álvarez") and s != "José Álvarez"),
    ("truncate", lambda s: s == "José Álvar"),
    ("mojibake", lambda s: s == "JosÃ© Ã\x81lvarez" or s.startswith("JosÃ©")),
    ("blank", lambda s: s == ""),
])
def test_each_kind_damages_the_way_it_says(kind, check):
    out = rows({"name": "d", "generator": "dirty",
                "params": {"source": "clean", "rate": 1, "kinds": [kind]}})
    assert all(check(r["d"]) for r in out), out[0]


def test_rate_zero_is_a_faithful_copy():
    out = rows({"name": "d", "generator": "dirty", "params": {"source": "clean", "rate": 0}})
    assert all(r["d"] == r["clean"] for r in out)


def test_rate_is_roughly_honoured():
    out = rows({"name": "d", "generator": "dirty",
                "params": {"source": "clean", "rate": 0.3, "kinds": ["blank"]}}, n=2000)
    assert 0.25 < sum(r["d"] == "" for r in out) / 2000 < 0.35


def test_legacy_date_formats_and_null_token():
    out = rows({"name": "d", "generator": "legacy_date",
                "params": {"source": "clean", "formats": ["%m/%d/%y", "%Y%m%d", "%d-%b-%Y"],
                           "null_token": "00000000"}},
               source=("clean", "choice", {"values": ["1999-12-31", "2000-01-02", None]}))
    seen = {r["d"] for r in out}
    assert {"12/31/99", "01/02/00", "19991231", "20000102", "31-DEC-1999", "02-JAN-2000",
            "00000000"} <= seen


def test_month_names_do_not_depend_on_the_locale():
    """strftime's %b follows the machine locale; chaff spells it out (INV-3)."""
    out = rows({"name": "d", "generator": "legacy_date",
                "params": {"source": "clean", "formats": ["%d-%b-%Y"]}},
               source=("clean", "choice", {"values": ["2003-05-07"]}))
    assert {r["d"] for r in out} == {"07-MAY-2003"}


def test_fk_each_walks_parents_in_order():
    t = generate_tables(load_spec({
        "name": "p", "seed": 1, "rows": 4,
        "columns": [{"name": "id", "generator": "row_id", "params": {"start": 10}}],
        "output": {"format": "csv"},
        "tables": [{"name": "k", "rows": 6, "columns": [
            {"name": "p_id", "generator": "fk", "params": {"table": "p", "column": "id", "each": True}}]}],
    }))
    assert [r["p_id"] for r in t["k"]] == [10, 11, 12, 13, None, None]


@pytest.mark.parametrize("col, msg", [
    ({"name": "d", "generator": "dirty", "params": {"source": "later"}}, "needs 'source'"),
    ({"name": "d", "generator": "legacy_date", "params": {}}, "needs 'source'"),
])
def test_sources_must_come_first(col, msg):
    with pytest.raises(ValueError, match=msg):
        load_spec({"name": "t", "rows": 1, "columns": [col], "output": {"format": "csv"}})


def test_each_cannot_also_filter():
    with pytest.raises(ValueError, match="can't also filter"):
        load_spec({
            "name": "p", "rows": 1, "columns": [{"name": "id", "generator": "row_id"}],
            "output": {"format": "csv"},
            "tables": [{"name": "k", "rows": 1, "columns": [
                {"name": "p_id", "generator": "fk",
                 "params": {"table": "p", "column": "id", "each": True, "where": {"id": 1}}}]}],
        })
