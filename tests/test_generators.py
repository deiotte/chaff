"""Tests for the Tier-1 generators: statistical distributions + web/telemetry."""

import re
import statistics

from chaff.engine import generate_rows
from chaff.generators import list_generators
from chaff.spec import load_spec

NEW = [
    "lognormal", "exponential", "poisson", "power_law",
    "ipv4", "ipv6", "mac_address", "url", "domain", "username", "user_agent",
    "slug", "sha256", "http_method", "http_status", "port", "ulid", "api_key",
]


def col(gen, rows=2000, seed=42, **params):
    spec = load_spec({"name": "t", "seed": seed, "rows": rows,
                      "columns": [{"name": "v", "generator": gen, "params": params}],
                      "output": {"format": "csv"}})
    return [r["v"] for r in generate_rows(spec)]


def test_all_new_generators_registered():
    registered = set(list_generators())
    assert set(NEW) <= registered


# ── distributions ────────────────────────────────────────────────────

def test_lognormal_positive_and_skewed():
    vals = col("lognormal", mu=0, sigma=1)
    assert all(v > 0 for v in vals)
    assert statistics.mean(vals) > statistics.median(vals)  # right-skew


def test_exponential_matches_rate():
    vals = col("exponential", rate=2.0)  # mean = 1/rate = 0.5
    assert all(v >= 0 for v in vals)
    assert 0.4 < statistics.mean(vals) < 0.6


def test_poisson_is_nonneg_int_with_mean():
    vals = col("poisson", mean=3.0)
    assert all(isinstance(v, int) and v >= 0 for v in vals)
    assert 2.6 < statistics.mean(vals) < 3.4


def test_power_law_respects_scale():
    vals = col("power_law", alpha=2.0, scale=10)
    assert all(v >= 10 for v in vals)


def test_distribution_clamps():
    vals = col("lognormal", mu=0, sigma=2, min=0.5, max=5)
    assert all(0.5 <= v <= 5 for v in vals)


# ── web / telemetry ──────────────────────────────────────────────────

def test_ipv4_shape():
    assert re.match(r"^\d{1,3}(\.\d{1,3}){3}$", col("ipv4", rows=5)[0])


def test_mac_shape():
    assert re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", col("mac_address", rows=5)[0])


def test_http_status_weighted_to_200():
    vals = col("http_status")
    assert sum(1 for v in vals if v == 200) / len(vals) > 0.55
    assert all(isinstance(v, int) for v in vals)


def test_http_method_weighted_to_get():
    vals = col("http_method")
    assert sum(1 for v in vals if v == "GET") / len(vals) > 0.45


def test_port_in_range():
    assert all(1024 <= v <= 65535 for v in col("port"))


def test_ulid_is_26_crockford_chars_and_sorts_by_row():
    vals = col("ulid", rows=10)
    assert all(len(v) == 26 for v in vals)
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for v in vals for c in v)
    assert [v[:10] for v in vals] == sorted(v[:10] for v in vals)  # time prefix increases


def test_api_key_prefix_and_length():
    v = col("api_key", rows=5, prefix="key_", length=20)[0]
    assert v.startswith("key_") and len(v) == 24


def test_sha256_length_param():
    assert len(col("sha256", rows=5, length=12)[0]) == 12


def test_new_generators_are_deterministic():
    spec = {"name": "logs", "seed": 7, "rows": 50, "output": {"format": "ndjson"},
            "columns": [{"name": c, "generator": c} for c in
                        ("ipv4", "user_agent", "url", "ulid")]
            + [{"name": "lat_ms", "generator": "lognormal", "params": {"mu": 3, "sigma": 1}},
               {"name": "status", "generator": "http_status"}]}
    assert generate_rows(load_spec(spec)) == generate_rows(load_spec(spec))
