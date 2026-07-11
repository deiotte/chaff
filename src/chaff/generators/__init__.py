"""Semantic generators. See generators/AGENTS.md before adding one.

A generator is a callable: (ctx: GenContext, params: dict) -> value.
Register with @generator("id"). All randomness MUST come from ctx.rng
or ctx.faker (which is seeded from the same source) — never `random`
module globals, never time-derived entropy. That rule is what makes
seed determinism (ADR-0004) true.
"""

from __future__ import annotations

import math
import string
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable

from faker import Faker

GeneratorFn = Callable[["GenContext", dict[str, Any]], Any]

_REGISTRY: dict[str, GeneratorFn] = {}


def generator(gen_id: str) -> Callable[[GeneratorFn], GeneratorFn]:
    def deco(fn: GeneratorFn) -> GeneratorFn:
        if gen_id in _REGISTRY:
            raise ValueError(f"generator '{gen_id}' already registered")
        _REGISTRY[gen_id] = fn
        return fn
    return deco


def get_generator(gen_id: str) -> GeneratorFn:
    try:
        return _REGISTRY[gen_id]
    except KeyError:
        raise KeyError(
            f"unknown generator '{gen_id}'. Registered: {sorted(_REGISTRY)}"
        ) from None


def list_generators() -> list[str]:
    return sorted(_REGISTRY)


@dataclass
class GenContext:
    """Per-run context handed to every generator call."""

    rng: Any          # random.Random seeded once per run
    faker: Faker      # Faker instance seeded from the same seed
    row_index: int    # 0-based row number (drives incrementing ids)
    tables: Any = None  # {table: [rows]} of already-generated parents (fk), else None


# ── Identity / people ────────────────────────────────────────────────

@generator("full_name")
def full_name(ctx: GenContext, p: dict) -> str:
    return ctx.faker.name()


@generator("first_name")
def first_name(ctx: GenContext, p: dict) -> str:
    return ctx.faker.first_name()


@generator("last_name")
def last_name(ctx: GenContext, p: dict) -> str:
    return ctx.faker.last_name()


@generator("email")
def email(ctx: GenContext, p: dict) -> str:
    return ctx.faker.email()


@generator("phone")
def phone(ctx: GenContext, p: dict) -> str:
    return ctx.faker.phone_number()


@generator("company")
def company(ctx: GenContext, p: dict) -> str:
    return ctx.faker.company()


# ── Location ─────────────────────────────────────────────────────────

@generator("city")
def city(ctx: GenContext, p: dict) -> str:
    return ctx.faker.city()


@generator("state")
def state(ctx: GenContext, p: dict) -> str:
    return ctx.faker.state_abbr() if p.get("abbr", True) else ctx.faker.state()


@generator("street_address")
def street_address(ctx: GenContext, p: dict) -> str:
    return ctx.faker.street_address()


@generator("lat")
def lat(ctx: GenContext, p: dict) -> float:
    lo, hi = p.get("min", -90.0), p.get("max", 90.0)
    return round(ctx.rng.uniform(lo, hi), int(p.get("precision", 6)))


@generator("lon")
def lon(ctx: GenContext, p: dict) -> float:
    lo, hi = p.get("min", -180.0), p.get("max", 180.0)
    return round(ctx.rng.uniform(lo, hi), int(p.get("precision", 6)))


# ── Identifiers ──────────────────────────────────────────────────────

@generator("uuid")
def uuid_(ctx: GenContext, p: dict) -> str:
    return ctx.faker.uuid4()


@generator("row_id")
def row_id(ctx: GenContext, p: dict) -> int:
    """Incrementing integer id. params: start (default 1), step (default 1)."""
    return int(p.get("start", 1)) + ctx.row_index * int(p.get("step", 1))


@generator("pattern")
def pattern(ctx: GenContext, p: dict) -> str:
    """Pattern-based id. In `params.pattern`: '#'=digit, '?'=A-Z, else literal.

    Example: 'XX-####-?????' -> 'XX-4821-QNRVB'
    """
    pat = p.get("pattern", "####")
    out = []
    for ch in pat:
        if ch == "#":
            out.append(ctx.rng.choice(string.digits))
        elif ch == "?":
            out.append(ctx.rng.choice(string.ascii_uppercase))
        else:
            out.append(ch)
    return "".join(out)


# ── Relationships (multi-table FK, ADR-0008) ─────────────────────────

@generator("fk")
def fk(ctx: GenContext, p: dict) -> Any:
    """Foreign key: a value drawn from a parent table's column.

    params: table (parent table name), column (its key column). The parent
    must be generated before this table — the engine orders generation by
    FK dependency, so a valid reference always exists. Value is picked with
    ctx.rng, so it stays deterministic under a fixed seed.

    Example: {"table": "customers", "column": "customer_id"}
    """
    table, column = p["table"], p["column"]
    if ctx.tables is None or table not in ctx.tables:
        raise KeyError(f"fk references unknown/ungenerated table '{table}'")
    parent = ctx.tables[table]
    if not parent:
        raise ValueError(f"fk parent table '{table}' produced no rows")
    return ctx.rng.choice(parent)[column]


# ── Numbers ──────────────────────────────────────────────────────────

@generator("int_range")
def int_range(ctx: GenContext, p: dict) -> int:
    return ctx.rng.randint(int(p.get("min", 0)), int(p.get("max", 100)))


@generator("float_uniform")
def float_uniform(ctx: GenContext, p: dict) -> float:
    return round(ctx.rng.uniform(float(p.get("min", 0.0)), float(p.get("max", 1.0))),
                 int(p.get("precision", 2)))


@generator("float_normal")
def float_normal(ctx: GenContext, p: dict) -> float:
    """Gaussian — this is what makes demo charts look like real data."""
    v = ctx.rng.gauss(float(p.get("mean", 0.0)), float(p.get("stddev", 1.0)))
    if "min" in p:
        v = max(v, float(p["min"]))
    if "max" in p:
        v = min(v, float(p["max"]))
    return round(v, int(p.get("precision", 2)))


@generator("money")
def money(ctx: GenContext, p: dict) -> float:
    return round(ctx.rng.uniform(float(p.get("min", 1.0)), float(p.get("max", 1000.0))), 2)


# ── Categorical ──────────────────────────────────────────────────────

@generator("choice")
def choice(ctx: GenContext, p: dict) -> Any:
    return ctx.rng.choice(p["values"])


@generator("choice_weighted")
def choice_weighted(ctx: GenContext, p: dict) -> Any:
    """params: values ['Open','Pending','Closed'], weights [0.7,0.2,0.1]."""
    return ctx.rng.choices(p["values"], weights=p["weights"], k=1)[0]


@generator("bool_rate")
def bool_rate(ctx: GenContext, p: dict) -> bool:
    return ctx.rng.random() < float(p.get("true_rate", 0.5))


# ── Time ─────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@generator("date_between")
def date_between(ctx: GenContext, p: dict) -> str:
    start = _parse_dt(p.get("start", "2024-01-01"))
    end = _parse_dt(p.get("end", "2026-01-01"))
    delta = (end - start).days
    return (start + timedelta(days=ctx.rng.randint(0, max(delta, 0)))).date().isoformat()


@generator("timestamp_between")
def timestamp_between(ctx: GenContext, p: dict) -> str:
    start = _parse_dt(p.get("start", "2024-01-01T00:00:00"))
    end = _parse_dt(p.get("end", "2026-01-01T00:00:00"))
    secs = int((end - start).total_seconds())
    return (start + timedelta(seconds=ctx.rng.randint(0, max(secs, 0)))).isoformat()


# ── Text ─────────────────────────────────────────────────────────────

@generator("lorem_sentence")
def lorem_sentence(ctx: GenContext, p: dict) -> str:
    return ctx.faker.sentence(nb_words=int(p.get("words", 8)))


@generator("lorem_paragraph")
def lorem_paragraph(ctx: GenContext, p: dict) -> str:
    return ctx.faker.paragraph(nb_sentences=int(p.get("sentences", 3)))


# ── Distributions ────────────────────────────────────────────────────
# Real-world data is usually skewed, not Gaussian. These make demo charts,
# dashboards, and ML/anomaly scenarios behave like the real thing. All
# entropy flows through ctx.rng, so they stay seed-deterministic (INV-3).

@generator("lognormal")
def lognormal(ctx: GenContext, p: dict) -> float:
    """Log-normal (skewed positive): incomes, latencies, file sizes.

    params: mu (underlying-normal mean, default 0), sigma (default 1),
            precision (2), optional min/max clamps.
    """
    v = ctx.rng.lognormvariate(float(p.get("mu", 0.0)), float(p.get("sigma", 1.0)))
    return _clamp_round(v, p)


@generator("exponential")
def exponential(ctx: GenContext, p: dict) -> float:
    """Exponential: inter-arrival / wait times. params: rate (lambda,
    default 1.0), precision (4), optional min/max."""
    v = ctx.rng.expovariate(float(p.get("rate", 1.0)))
    return _clamp_round(v, p, default_precision=4)


@generator("poisson")
def poisson(ctx: GenContext, p: dict) -> int:
    """Poisson counts per interval: orders/hour, errors/day.

    params: mean (lambda, default 1.0). Returns a non-negative integer.
    Knuth's algorithm, driven entirely by ctx.rng (deterministic).
    """
    lam = float(p.get("mean", p.get("lam", 1.0)))
    target = math.exp(-lam)
    k, product = 0, 1.0
    while True:
        k += 1
        product *= ctx.rng.random()
        if product <= target:
            return k - 1


@generator("power_law")
def power_law(ctx: GenContext, p: dict) -> float:
    """Power-law / Pareto tail: popularity, "80/20", city sizes.

    params: alpha (shape, default 2.0; smaller = heavier tail),
            scale (multiplier, default 1.0), precision (2), optional min/max.
    Values are >= scale.
    """
    v = ctx.rng.paretovariate(float(p.get("alpha", 2.0))) * float(p.get("scale", 1.0))
    return _clamp_round(v, p)


def _clamp_round(v: float, p: dict, default_precision: int = 2) -> float:
    if "min" in p:
        v = max(v, float(p["min"]))
    if "max" in p:
        v = min(v, float(p["max"]))
    return round(v, int(p.get("precision", default_precision)))


# ── Web / network / telemetry ────────────────────────────────────────
# The primitives that make log, event, and API-trace demos look real.
# Faker-backed where possible (seeded from the same source, INV-3).

@generator("ipv4")
def ipv4(ctx: GenContext, p: dict) -> str:
    return ctx.faker.ipv4()


@generator("ipv6")
def ipv6(ctx: GenContext, p: dict) -> str:
    return ctx.faker.ipv6()


@generator("mac_address")
def mac_address(ctx: GenContext, p: dict) -> str:
    return ctx.faker.mac_address()


@generator("url")
def url(ctx: GenContext, p: dict) -> str:
    return ctx.faker.url()


@generator("domain")
def domain(ctx: GenContext, p: dict) -> str:
    return ctx.faker.domain_name()


@generator("username")
def username(ctx: GenContext, p: dict) -> str:
    return ctx.faker.user_name()


@generator("user_agent")
def user_agent(ctx: GenContext, p: dict) -> str:
    return ctx.faker.user_agent()


@generator("slug")
def slug(ctx: GenContext, p: dict) -> str:
    return ctx.faker.slug()


@generator("sha256")
def sha256(ctx: GenContext, p: dict) -> str:
    """A hex digest, for commit/content hashes. params: length (default 64)."""
    return ctx.faker.sha256()[: int(p.get("length", 64))]


@generator("http_method")
def http_method(ctx: GenContext, p: dict) -> str:
    """params: values/weights override the defaults (GET-heavy)."""
    values = p.get("values", ["GET", "POST", "PUT", "PATCH", "DELETE"])
    weights = p.get("weights", [0.6, 0.2, 0.08, 0.05, 0.07])
    return ctx.rng.choices(values, weights=weights, k=1)[0]


@generator("http_status")
def http_status(ctx: GenContext, p: dict) -> int:
    """Weighted toward 2xx by default. params: values/weights override."""
    values = p.get("values", [200, 201, 204, 301, 400, 401, 403, 404, 500, 503])
    weights = p.get("weights", [0.7, 0.06, 0.04, 0.03, 0.05, 0.03, 0.02, 0.04, 0.02, 0.01])
    return int(ctx.rng.choices(values, weights=weights, k=1)[0])


@generator("port")
def port(ctx: GenContext, p: dict) -> int:
    return ctx.rng.randint(int(p.get("min", 1024)), int(p.get("max", 65535)))


@generator("ulid")
def ulid(ctx: GenContext, p: dict) -> str:
    """ULID-shaped 26-char Crockford base32 id (lexicographically sortable
    by the time prefix, which is derived deterministically from row_index)."""
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    time_part = ctx.row_index  # deterministic, monotonically increasing
    ts = "".join(alphabet[(time_part >> (5 * i)) & 31] for i in range(9, -1, -1))
    rand = "".join(ctx.rng.choice(alphabet) for _ in range(16))
    return ts + rand


@generator("api_key")
def api_key(ctx: GenContext, p: dict) -> str:
    """Fake API-key-shaped token. params: prefix (default 'sk-'),
    length (random part, default 32)."""
    prefix = str(p.get("prefix", "sk-"))
    chars = string.ascii_letters + string.digits
    body = "".join(ctx.rng.choice(chars) for _ in range(int(p.get("length", 32))))
    return prefix + body
