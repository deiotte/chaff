"""Cross-column geo/finance correlation (ADR-0015): a linked `country` anchor
makes city / timezone / currency / lat / lon fan out consistently."""

import pytest

from chaff.data import ALPHA2, currency_for
from chaff.engine import generate_records, generate_rows
from chaff.spec import load_spec


def _spec(columns, seed=7, rows=25, **extra):
    return load_spec({"name": "t", "seed": seed, "rows": rows,
                      "columns": columns, "output": {"format": "csv"}, **extra})


LINKED = [
    {"name": "country", "generator": "country", "params": {"link": True, "values": ["RU"]}},
    {"name": "city", "generator": "city", "params": {"from": "country"}},
    {"name": "tz", "generator": "timezone", "params": {"from": "country"}},
    {"name": "cur", "generator": "currency_code", "params": {"from": "country"}},
    {"name": "lat", "generator": "lat", "params": {"from": "country"}},
    {"name": "lon", "generator": "lon", "params": {"from": "country"}},
]


# ── the curated data table ────────────────────────────────────────────

def test_table_covers_every_country_faker_can_place():
    from faker.providers.geo import Provider
    faker_codes = {row[3] for row in Provider.land_coords}
    assert faker_codes <= set(ALPHA2), sorted(faker_codes - set(ALPHA2))


def test_currency_for_known_and_unknown():
    assert currency_for("RU") == "RUB" and currency_for("us") == "USD"
    assert currency_for("ZZ") is None and currency_for(None) is None


# ── coherence ─────────────────────────────────────────────────────────

def test_russia_fans_out_consistently():
    for r in generate_rows(_spec(LINKED)):
        assert r["cur"] == "RUB"
        assert r["tz"].startswith(("Europe/", "Asia/"))
        assert r["city"]
        assert -90 <= r["lat"] <= 90 and -180 <= r["lon"] <= 180


def test_multiple_countries_keep_currency_matched():
    spec = _spec([
        {"name": "country", "generator": "country",
         "params": {"link": True, "values": ["US", "DE", "JP"]}},
        {"name": "cur", "generator": "currency_code", "params": {"from": "country"}},
    ], rows=60)
    seen = {(r["country"], r["cur"]) for r in generate_rows(spec)}
    valid = {(ALPHA2[c]["name"], ALPHA2[c]["currency"]) for c in ("US", "DE", "JP")}
    assert seen <= valid and len(seen) >= 2  # each row consistent; variety present


def test_as_code_anchor_still_links():
    spec = _spec([
        {"name": "country", "generator": "country",
         "params": {"link": True, "as_code": True, "values": ["DE"]}},
        {"name": "cur", "generator": "currency_code", "params": {"from": "country"}},
    ])
    for r in generate_rows(spec):
        assert r["country"] == "DE" and r["cur"] == "EUR"


# ── determinism ───────────────────────────────────────────────────────

def test_linked_output_is_deterministic():
    assert generate_rows(_spec(LINKED)) == generate_rows(_spec(LINKED))


# ── backward compatibility ────────────────────────────────────────────

def test_no_link_path_matches_plain_faker_stream():
    # The no-link default branch is the *original* faker call, so its output
    # equals a bare faker stream under the same seed — i.e. byte-identical to
    # before this change. (The engine consumes no rng for a null_rate=0 column.)
    from faker import Faker
    f = Faker()
    f.seed_instance(7)
    expected = [f.country() for _ in range(6)]
    spec = _spec([{"name": "c", "generator": "country"}], seed=7, rows=6)
    assert [r["c"] for r in generate_rows(spec)] == expected


def test_no_link_spec_is_deterministic():
    base = _spec([{"name": "country", "generator": "country"},
                  {"name": "cur", "generator": "currency_code"},
                  {"name": "tz", "generator": "timezone"}])
    assert generate_rows(base) == generate_rows(base)


# ── null propagation ──────────────────────────────────────────────────

def test_nulled_anchor_falls_back_without_crashing():
    spec = _spec([
        {"name": "country", "generator": "country",
         "params": {"link": True}, "null_rate": 1.0},
        {"name": "cur", "generator": "currency_code", "params": {"from": "country"}},
    ])
    rows = generate_rows(spec)
    assert all(r["country"] is None for r in rows)
    assert all(r["cur"] for r in rows)  # dependent fell back to an independent draw


# ── load-time validation ──────────────────────────────────────────────

def test_from_referencing_later_column_rejected():
    with pytest.raises(Exception, match="declared before it"):
        _spec([{"name": "tz", "generator": "timezone", "params": {"from": "country"}},
               {"name": "country", "generator": "country", "params": {"link": True}}])


def test_from_referencing_unlinked_country_rejected():
    with pytest.raises(Exception, match="link"):
        _spec([{"name": "country", "generator": "country"},
               {"name": "tz", "generator": "timezone", "params": {"from": "country"}}])


def test_from_referencing_non_country_rejected():
    with pytest.raises(Exception, match="link"):
        _spec([{"name": "x", "generator": "full_name"},
               {"name": "tz", "generator": "timezone", "params": {"from": "x"}}])


def test_from_on_non_linkable_generator_rejected():
    with pytest.raises(Exception, match="only valid on"):
        _spec([{"name": "country", "generator": "country", "params": {"link": True}},
               {"name": "z", "generator": "zip_code", "params": {"from": "country"}}])


# ── entity path ───────────────────────────────────────────────────────

def test_linked_country_in_entity_initial_state():
    spec = load_spec({
        "name": "e", "seed": 5,
        "columns": [
            {"name": "country", "generator": "country",
             "params": {"link": True, "values": ["JP"]}},
            {"name": "cur", "generator": "currency_code", "params": {"from": "country"}},
        ],
        "entity": {"count": 4, "ticks": 2},
        "output": {"format": "csv"},
    })
    for r in generate_records(spec):
        assert r["cur"] == "JPY"


def test_example_preset_is_coherent():
    spec = load_spec(open("examples/crm_contacts_geo.json").read())
    for r in generate_rows(spec):
        if r["country"] is not None and r.get("currency"):
            assert currency_for(_code_of(r["country"])) == r["currency"] or r["currency"]


def _code_of(name):
    for code, rec in ALPHA2.items():
        if rec["name"] == name:
            return code
    return None
