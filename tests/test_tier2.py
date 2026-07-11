"""Tier-2 generators: geo (country/zip_code/timezone), finance
(currency_code/credit_card/iban), people (job_title/age/gender)."""

import re

from chaff.engine import generate_rows
from chaff.generators import list_generators
from chaff.spec import load_spec

NEW = ["country", "zip_code", "timezone", "currency_code", "credit_card",
       "iban", "job_title", "age", "gender"]


def col(gen, rows=300, seed=42, **params):
    spec = load_spec({"name": "t", "seed": seed, "rows": rows,
                      "columns": [{"name": "v", "generator": gen, "params": params}],
                      "output": {"format": "csv"}})
    return [r["v"] for r in generate_rows(spec)]


def _luhn_ok(number: str) -> bool:
    digits = [int(d) for d in number]
    odd = digits[-1::-2]
    even = digits[-2::-2]
    return (sum(odd) + sum(sum(divmod(d * 2, 10)) for d in even)) % 10 == 0


def test_all_tier2_registered():
    assert set(NEW) <= set(list_generators())


# ── geo ──────────────────────────────────────────────────────────────

def test_country_name_and_code():
    assert all(isinstance(v, str) and v for v in col("country"))
    codes = col("country", as_code=True)
    assert all(re.fullmatch(r"[A-Z]{2}", c) for c in codes)


def test_zip_code_nonempty():
    assert all(isinstance(v, str) and v for v in col("zip_code"))


def test_timezone_is_iana_like():
    vals = col("timezone")
    assert all(isinstance(v, str) and v for v in vals)
    assert sum("/" in v for v in vals) / len(vals) > 0.9  # Region/City


# ── finance ──────────────────────────────────────────────────────────

def test_currency_code_shape():
    assert all(re.fullmatch(r"[A-Z]{3}", c) for c in col("currency_code"))


def test_credit_card_is_luhn_valid_test_number():
    nums = col("credit_card")
    assert all(n.isdigit() for n in nums)
    assert all(_luhn_ok(n) for n in nums)


def test_credit_card_type_filter():
    assert all(n.startswith("4") for n in col("credit_card", card_type="visa"))


def test_iban_shape():
    assert all(re.match(r"^[A-Z]{2}\d{2}", v) for v in col("iban"))


# ── people ───────────────────────────────────────────────────────────

def test_job_title_nonempty():
    assert all(isinstance(v, str) and v for v in col("job_title"))


def test_age_defaults_and_bounds():
    assert all(18 <= a <= 90 for a in col("age"))
    assert all(21 <= a <= 65 for a in col("age", min=21, max=65))


def test_gender_within_values_and_weighted():
    vals = col("gender", rows=1000)
    assert set(vals) <= {"Female", "Male", "Non-binary"}
    assert vals.count("Non-binary") < vals.count("Female")  # rare by default
    custom = col("gender", values=["F", "M"], weights=[0.5, 0.5])
    assert set(custom) <= {"F", "M"}


# ── determinism ──────────────────────────────────────────────────────

def test_tier2_deterministic_under_seed():
    for gen in NEW:
        assert col(gen, seed=7) == col(gen, seed=7)
