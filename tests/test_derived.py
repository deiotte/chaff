"""Derived/computed columns (ADR-0012): the safe formula evaluator, the
`derived` generator end-to-end, load-time validation, and the security
boundary (no arbitrary code execution)."""

import pytest

from chaff.engine import generate_records, generate_rows, run
from chaff.generators import list_generators
from chaff.generators._expr import FormulaError, referenced_names, safe_eval, validate_expr
from chaff.spec import load_spec


# ── the evaluator ────────────────────────────────────────────────────

def test_arithmetic_and_precedence():
    assert safe_eval("price * qty", {"price": 10, "qty": 3}) == 30
    assert safe_eval("a + b * c", {"a": 1, "b": 2, "c": 3}) == 7
    assert safe_eval("(a + b) * c", {"a": 1, "b": 2, "c": 3}) == 9
    assert safe_eval("total * (1 - discount)", {"total": 100, "discount": 0.2}) == 80.0


def test_comparisons_and_booleans_and_ternary():
    assert safe_eval("qty >= 3", {"qty": 5}) is True
    assert safe_eval("a > 0 and b > 0", {"a": 1, "b": -1}) is False
    assert safe_eval("'BULK' if qty >= 3 else 'single'", {"qty": 4}) == "BULK"
    assert safe_eval("1 < x < 10", {"x": 5}) is True


def test_string_concat_and_functions():
    assert safe_eval("first + ' ' + last", {"first": "Ada", "last": "L"}) == "Ada L"
    assert safe_eval("round(x, 2)", {"x": 3.14159}) == 3.14
    assert safe_eval("max(a, b)", {"a": 1, "b": 9}) == 9
    assert safe_eval("abs(x)", {"x": -4}) == 4


def test_evaluator_is_pure_deterministic():
    names = {"a": 2, "b": 7}
    assert safe_eval("a * b + 1", names) == safe_eval("a * b + 1", names) == 15


def test_referenced_names_excludes_functions():
    assert referenced_names("round(price * qty, 2)") == {"price", "qty"}


def test_unknown_column_raises():
    with pytest.raises(FormulaError, match="unknown column 'nope'"):
        safe_eval("nope + 1", {"a": 1})


# ── security: the whitelist must reject everything dangerous ──────────

@pytest.mark.parametrize("bad", [
    '__import__("os").system("x")',
    '().__class__.__bases__',
    'price.__class__',
    'open("/etc/passwd")',
    '[x for x in range(9)]',
    'lambda: 1',
    'x := 5',
    'a[0]',
])
def test_dangerous_expressions_rejected(bad):
    with pytest.raises(FormulaError):
        validate_expr(bad)


def test_pow_blowup_is_guarded():
    with pytest.raises(FormulaError, match="exponent too large"):
        safe_eval("9 ** 999999", {})


def test_empty_formula_rejected():
    with pytest.raises(FormulaError, match="empty"):
        validate_expr("   ")


# ── the `derived` generator end-to-end ───────────────────────────────

def _orders(seed=7, rows=200):
    return load_spec({
        "name": "orders", "seed": seed, "rows": rows,
        "columns": [
            {"name": "price", "generator": "money", "params": {"min": 5, "max": 50}},
            {"name": "qty", "generator": "int_range", "params": {"min": 1, "max": 6}},
            {"name": "total", "generator": "derived",
             "params": {"expr": "price * qty", "precision": 2}},
            {"name": "is_bulk", "generator": "derived", "params": {"expr": "qty >= 4"}},
        ],
        "output": {"format": "csv"},
    })


def test_derived_registered():
    assert "derived" in list_generators()


def test_derived_computes_from_row():
    rows = generate_rows(_orders())
    for r in rows:
        assert r["total"] == round(r["price"] * r["qty"], 2)
        assert r["is_bulk"] == (r["qty"] >= 4)


def test_derived_can_reference_an_earlier_derived():
    spec = load_spec({
        "name": "t", "seed": 1, "rows": 50,
        "columns": [
            {"name": "price", "generator": "money", "params": {"min": 10, "max": 20}},
            {"name": "qty", "generator": "int_range", "params": {"min": 1, "max": 3}},
            {"name": "total", "generator": "derived", "params": {"expr": "price * qty"}},
            {"name": "net", "generator": "derived",
             "params": {"expr": "round(total * 0.9, 2)"}},
        ],
        "output": {"format": "csv"},
    })
    for r in generate_rows(spec):
        assert r["net"] == round(r["price"] * r["qty"] * 0.9, 2)


def test_derived_propagates_null():
    spec = load_spec({
        "name": "t", "seed": 3, "rows": 40,
        "columns": [
            {"name": "a", "generator": "int_range", "params": {"min": 1, "max": 9},
             "null_rate": 0.5},
            {"name": "b", "generator": "derived", "params": {"expr": "a + 1"}},
        ],
        "output": {"format": "csv"},
    })
    for r in generate_rows(spec):
        assert (r["b"] is None) == (r["a"] is None)


def test_derived_adds_no_entropy_determinism_holds():
    # Same spec + seed => identical rows, and the derived column doesn't
    # perturb the rng stream for the columns after it.
    assert generate_records(_orders()) == generate_records(_orders())


def test_derived_in_entity_initial_state():
    spec = load_spec({
        "name": "e", "seed": 2,
        "columns": [
            {"name": "base", "generator": "int_range", "params": {"min": 10, "max": 20}},
            {"name": "doubled", "generator": "derived", "params": {"expr": "base * 2"}},
        ],
        "entity": {"count": 3, "ticks": 2},
        "output": {"format": "csv"},
    })
    for r in generate_records(spec):
        assert r["doubled"] == r["base"] * 2


# ── load-time validation (precise errors before generation) ──────────

def test_reference_to_later_column_is_rejected():
    with pytest.raises(Exception, match="declared before it"):
        load_spec({
            "name": "t", "seed": 1, "rows": 2,
            "columns": [
                {"name": "total", "generator": "derived", "params": {"expr": "price * qty"}},
                {"name": "price", "generator": "money"},
                {"name": "qty", "generator": "int_range"},
            ],
            "output": {"format": "csv"},
        })


def test_malformed_formula_rejected_at_load():
    with pytest.raises(Exception, match="not valid"):
        load_spec({
            "name": "t", "seed": 1, "rows": 2,
            "columns": [
                {"name": "a", "generator": "int_range"},
                {"name": "b", "generator": "derived", "params": {"expr": "a +"}},
            ],
            "output": {"format": "csv"},
        })


def test_missing_expr_rejected_at_load():
    with pytest.raises(Exception, match="needs an 'expr'"):
        load_spec({
            "name": "t", "seed": 1, "rows": 2,
            "columns": [{"name": "b", "generator": "derived", "params": {}}],
            "output": {"format": "csv"},
        })


def test_example_preset_generates():
    spec = load_spec(open("examples/orders_with_totals.json").read())
    rows = generate_rows(spec)
    assert rows and all(r["net_total"] == round(r["subtotal"] * (1 - r["discount_rate"]), 2)
                        for r in rows)
