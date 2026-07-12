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

from ._expr import FormulaError, referenced_names, safe_eval

GeneratorFn = Callable[["GenContext", dict[str, Any]], Any]

_REGISTRY: dict[str, GeneratorFn] = {}
# gen_id -> a concrete, copy-pasteable params example. Interfaces (the UI's
# params placeholder, docs) read this so office Joe sees the exact shape each
# generator expects instead of guessing. INV-4 still holds: this lives beside
# the registry, so a generator carries its own example — no UI hardcoding.
_EXAMPLES: dict[str, dict[str, Any]] = {}


def generator(
    gen_id: str, *, example: dict[str, Any] | None = None
) -> Callable[[GeneratorFn], GeneratorFn]:
    def deco(fn: GeneratorFn) -> GeneratorFn:
        if gen_id in _REGISTRY:
            raise ValueError(f"generator '{gen_id}' already registered")
        _REGISTRY[gen_id] = fn
        if example is not None:
            _EXAMPLES[gen_id] = example
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


def list_generator_examples() -> dict[str, dict[str, Any]]:
    """gen_id -> concrete params example, for every generator that registered
    one. Interfaces render these so a user sees the exact params shape (e.g.
    `choice` -> {"values": [...]}) instead of a one-size-fits-all guess."""
    return dict(_EXAMPLES)


# Display grouping for UIs. INV-4 still holds: the registry is the source of
# truth and the UI renders these <optgroup>s — it never hardcodes a list. The
# order here is the order shown. Every registered generator must land in
# exactly one group; test_generator_groups.py enforces it, so a new generator
# can't silently drop out of the dropdown (and a typo'd id here is caught too).
_GROUP_ORDER: list[tuple[str, list[str]]] = [
    ("People", ["full_name", "first_name", "last_name", "email", "phone",
                "username", "company", "job_title", "age", "gender"]),
    ("Location", ["street_address", "city", "state", "zip_code", "country",
                  "timezone", "lat", "lon"]),
    ("Identifiers", ["row_id", "uuid", "ulid", "pattern", "sha256", "api_key"]),
    ("Numbers & distributions", ["int_range", "float_uniform", "float_normal",
                                 "money", "lognormal", "exponential", "poisson",
                                 "power_law"]),
    ("Categories & dates", ["choice", "choice_weighted", "bool_rate",
                            "date_between", "timestamp_between"]),
    ("Text", ["lorem_sentence", "lorem_paragraph", "slug"]),
    ("Web & network", ["ipv4", "ipv6", "mac_address", "domain", "url",
                       "user_agent", "http_method", "http_status", "port"]),
    ("Finance", ["currency_code", "credit_card", "iban"]),
    ("Relationships", ["fk"]),
    ("Computed", ["derived"]),
]


def list_generator_groups() -> list[dict[str, Any]]:
    """Registered generators as ordered display groups: [{group, generators}].
    Anything registered but not placed above lands in a trailing 'Other' group,
    so nothing ever disappears from the UI."""
    placed: set[str] = set()
    groups: list[dict[str, Any]] = []
    for name, ids in _GROUP_ORDER:
        present = [g for g in ids if g in _REGISTRY]
        placed.update(present)
        if present:
            groups.append({"group": name, "generators": present})
    leftover = sorted(set(_REGISTRY) - placed)
    if leftover:
        groups.append({"group": "Other", "generators": leftover})
    return groups


@dataclass
class GenContext:
    """Per-run context handed to every generator call."""

    rng: Any          # random.Random seeded once per run
    faker: Faker      # Faker instance seeded from the same seed
    row_index: int    # 0-based row number (drives incrementing ids)
    tables: Any = None  # {table: [rows]} of already-generated parents (fk), else None
    row: Any = None   # the in-progress row dict (cells generated so far), for `derived`
    cache: Any = None   # per-row scratch shared across a row's generators (geo linking)
    column: Any = None  # name of the column currently being generated (cache key)


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

def _linked_tuple(ctx: GenContext, p: dict):
    """Coherent location tuple `(lat, lon, place, alpha2, tz)` cached by a
    linked `country` anchor (ADR-0015), or None to fall back to an independent
    draw. None when: no `from`; the anchor cell was nulled or couldn't be
    placed; or `from` names a column that wasn't a linked anchor. Reading the
    cache adds zero entropy (like `derived`), so it never perturbs determinism."""
    src = p.get("from")
    if not src or ctx.cache is None:
        return None
    return ctx.cache.get(src)


@generator("city")
def city(ctx: GenContext, p: dict) -> str:
    """A city. With {"from": "<linked country column>"} returns a city inside
    that country (ADR-0015); otherwise an independent faker draw."""
    tup = _linked_tuple(ctx, p)
    return tup[2] if tup is not None else ctx.faker.city()


@generator("state")
def state(ctx: GenContext, p: dict) -> str:
    return ctx.faker.state_abbr() if p.get("abbr", True) else ctx.faker.state()


@generator("street_address")
def street_address(ctx: GenContext, p: dict) -> str:
    return ctx.faker.street_address()


@generator("lat", example={"min": -90, "max": 90, "precision": 6})
def lat(ctx: GenContext, p: dict) -> float:
    """Latitude. With {"from": "<linked country column>"} the coordinate falls
    inside that country and matches the linked city (ADR-0015)."""
    tup = _linked_tuple(ctx, p)
    if tup is not None:
        return round(float(tup[0]), int(p.get("precision", 6)))
    lo, hi = p.get("min", -90.0), p.get("max", 90.0)
    return round(ctx.rng.uniform(lo, hi), int(p.get("precision", 6)))


@generator("lon", example={"min": -180, "max": 180, "precision": 6})
def lon(ctx: GenContext, p: dict) -> float:
    """Longitude. With {"from": "<linked country column>"} the coordinate falls
    inside that country and matches the linked city (ADR-0015)."""
    tup = _linked_tuple(ctx, p)
    if tup is not None:
        return round(float(tup[1]), int(p.get("precision", 6)))
    lo, hi = p.get("min", -180.0), p.get("max", 180.0)
    return round(ctx.rng.uniform(lo, hi), int(p.get("precision", 6)))


# ── Identifiers ──────────────────────────────────────────────────────

@generator("uuid")
def uuid_(ctx: GenContext, p: dict) -> str:
    return ctx.faker.uuid4()


@generator("row_id", example={"start": 1, "step": 1})
def row_id(ctx: GenContext, p: dict) -> int:
    """Incrementing integer id. params: start (default 1), step (default 1)."""
    return int(p.get("start", 1)) + ctx.row_index * int(p.get("step", 1))


@generator("pattern", example={"pattern": "XX-####-?????"})
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

@generator("fk", example={"table": "users", "column": "id"})
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

@generator("int_range", example={"min": 0, "max": 100})
def int_range(ctx: GenContext, p: dict) -> int:
    return ctx.rng.randint(int(p.get("min", 0)), int(p.get("max", 100)))


@generator("float_uniform", example={"min": 0, "max": 1, "precision": 2})
def float_uniform(ctx: GenContext, p: dict) -> float:
    return round(ctx.rng.uniform(float(p.get("min", 0.0)), float(p.get("max", 1.0))),
                 int(p.get("precision", 2)))


@generator("float_normal", example={"mean": 0, "stddev": 1})
def float_normal(ctx: GenContext, p: dict) -> float:
    """Gaussian — this is what makes demo charts look like real data."""
    v = ctx.rng.gauss(float(p.get("mean", 0.0)), float(p.get("stddev", 1.0)))
    if "min" in p:
        v = max(v, float(p["min"]))
    if "max" in p:
        v = min(v, float(p["max"]))
    return round(v, int(p.get("precision", 2)))


@generator("money", example={"min": 1, "max": 1000})
def money(ctx: GenContext, p: dict) -> float:
    return round(ctx.rng.uniform(float(p.get("min", 1.0)), float(p.get("max", 1000.0))), 2)


# ── Categorical ──────────────────────────────────────────────────────

@generator("choice", example={"values": ["Open", "Pending", "Closed"]})
def choice(ctx: GenContext, p: dict) -> Any:
    """Uniform pick from `values` (required, non-empty). params example:
    {"values": ["Open", "Pending", "Closed"]}."""
    return ctx.rng.choice(p["values"])


@generator(
    "choice_weighted",
    example={"values": ["Open", "Pending", "Closed"], "weights": [0.7, 0.2, 0.1]},
)
def choice_weighted(ctx: GenContext, p: dict) -> Any:
    """params: values ['Open','Pending','Closed'], weights [0.7,0.2,0.1]."""
    return ctx.rng.choices(p["values"], weights=p["weights"], k=1)[0]


@generator("bool_rate", example={"true_rate": 0.5})
def bool_rate(ctx: GenContext, p: dict) -> bool:
    return ctx.rng.random() < float(p.get("true_rate", 0.5))


# ── Time ─────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@generator("date_between", example={"start": "2024-01-01", "end": "2026-01-01"})
def date_between(ctx: GenContext, p: dict) -> str:
    start = _parse_dt(p.get("start", "2024-01-01"))
    end = _parse_dt(p.get("end", "2026-01-01"))
    delta = (end - start).days
    return (start + timedelta(days=ctx.rng.randint(0, max(delta, 0)))).date().isoformat()


@generator(
    "timestamp_between",
    example={"start": "2024-01-01T00:00:00", "end": "2026-01-01T00:00:00"},
)
def timestamp_between(ctx: GenContext, p: dict) -> str:
    start = _parse_dt(p.get("start", "2024-01-01T00:00:00"))
    end = _parse_dt(p.get("end", "2026-01-01T00:00:00"))
    secs = int((end - start).total_seconds())
    return (start + timedelta(seconds=ctx.rng.randint(0, max(secs, 0)))).isoformat()


# ── Text ─────────────────────────────────────────────────────────────

@generator("lorem_sentence", example={"words": 8})
def lorem_sentence(ctx: GenContext, p: dict) -> str:
    return ctx.faker.sentence(nb_words=int(p.get("words", 8)))


@generator("lorem_paragraph", example={"sentences": 3})
def lorem_paragraph(ctx: GenContext, p: dict) -> str:
    return ctx.faker.paragraph(nb_sentences=int(p.get("sentences", 3)))


# ── Distributions ────────────────────────────────────────────────────
# Real-world data is usually skewed, not Gaussian. These make demo charts,
# dashboards, and ML/anomaly scenarios behave like the real thing. All
# entropy flows through ctx.rng, so they stay seed-deterministic (INV-3).

@generator("lognormal", example={"mu": 0, "sigma": 1})
def lognormal(ctx: GenContext, p: dict) -> float:
    """Log-normal (skewed positive): incomes, latencies, file sizes.

    params: mu (underlying-normal mean, default 0), sigma (default 1),
            precision (2), optional min/max clamps.
    """
    v = ctx.rng.lognormvariate(float(p.get("mu", 0.0)), float(p.get("sigma", 1.0)))
    return _clamp_round(v, p)


@generator("exponential", example={"rate": 1.0})
def exponential(ctx: GenContext, p: dict) -> float:
    """Exponential: inter-arrival / wait times. params: rate (lambda,
    default 1.0), precision (4), optional min/max."""
    v = ctx.rng.expovariate(float(p.get("rate", 1.0)))
    return _clamp_round(v, p, default_precision=4)


@generator("poisson", example={"mean": 1.0})
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


@generator("power_law", example={"alpha": 2.0, "scale": 1.0})
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


@generator("sha256", example={"length": 64})
def sha256(ctx: GenContext, p: dict) -> str:
    """A hex digest, for commit/content hashes. params: length (default 64)."""
    return ctx.faker.sha256()[: int(p.get("length", 64))]


@generator("http_method", example={"values": ["GET", "POST"], "weights": [0.8, 0.2]})
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


@generator("port", example={"min": 1024, "max": 65535})
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


@generator("api_key", example={"prefix": "sk-", "length": 32})
def api_key(ctx: GenContext, p: dict) -> str:
    """Fake API-key-shaped token. params: prefix (default 'sk-'),
    length (random part, default 32)."""
    prefix = str(p.get("prefix", "sk-"))
    chars = string.ascii_letters + string.digits
    body = "".join(ctx.rng.choice(chars) for _ in range(int(p.get("length", 32))))
    return prefix + body


# ── Derived / computed columns (ADR-0012) ────────────────────────────

@generator("derived")
def derived(ctx: GenContext, p: dict) -> Any:
    """Compute a value from other cells in the same row via a safe formula.
    Column names are the variables; the formula never runs as code (see
    _expr). params: expr (required, e.g. 'price * qty'), precision (optional,
    round a float result). Add zero entropy, so determinism is untouched.

    Example: {"generator": "derived", "params": {"expr": "price * qty", "precision": 2}}
    Put a derived column *after* the columns it references.
    """
    expr = p.get("expr") or p.get("formula")
    if not expr:
        raise FormulaError("derived column needs an 'expr', e.g. {\"expr\": \"price * qty\"}")
    row = ctx.row or {}
    # Null in, null out: if any referenced cell is null (null_rate), so is this.
    if any(row.get(name) is None for name in referenced_names(expr) if name in row):
        return None
    value = safe_eval(expr, row)
    precision = p.get("precision")
    if precision is not None and isinstance(value, float):
        value = round(value, int(precision))
    return value


# ── Tier 2: geo / finance / people ───────────────────────────────────
# All entropy via ctx.faker (seeded from the run seed) or ctx.rng, so these
# stay byte-for-byte deterministic (INV-3).

@generator("country")
def country(ctx: GenContext, p: dict) -> str:
    """Country name, or ISO alpha-2 code with {"as_code": true}.

    Correlation (opt-in, ADR-0015): {"link": true} resolves ONE coherent
    location for the row — a real city, its IANA timezone, matching lat/lon,
    and (via the curated table) its currency — and caches it so dependent
    columns using {"from": "<this column>"} fan out consistently. Constrain
    the countries with {"values": ["US","RU","DE"]} (+ optional "weights");
    those must be alpha-2 codes faker can place (else an independent fallback).
    Without "link", behaviour is unchanged (an independent faker draw)."""
    if not p.get("link"):
        return ctx.faker.country_code() if p.get("as_code") else ctx.faker.country()

    from ..data import name_for
    values = p.get("values")
    if values:
        weights = p.get("weights")
        cc = (ctx.rng.choices(values, weights=weights, k=1)[0]
              if weights else ctx.rng.choice(values))
        tup = ctx.faker.local_latlng(country_code=str(cc).upper())  # coherent within cc
    else:
        cc = None
        tup = ctx.faker.location_on_land()                          # coherent, global
    # tup = (lat, lon, place, alpha2, IANA_tz), or None for an unplaceable code.

    if tup is None:
        # Country faker can't place -> degrade to a plain draw, cache nothing,
        # so dependents fall back independently.
        return str(cc).upper() if (cc and p.get("as_code")) else ctx.faker.country()

    if ctx.cache is not None and ctx.column is not None:
        ctx.cache[ctx.column] = tup
    alpha2 = tup[3]
    if p.get("as_code"):
        return alpha2
    return name_for(alpha2) or ctx.faker.country()


@generator("zip_code")
def zip_code(ctx: GenContext, p: dict) -> str:
    """Postal/ZIP code (locale-dependent shape)."""
    return ctx.faker.postcode()


@generator("timezone")
def timezone(ctx: GenContext, p: dict) -> str:
    """IANA timezone name, e.g. 'America/New_York'. With
    {"from": "<linked country column>"} returns that country's timezone (ADR-0015)."""
    tup = _linked_tuple(ctx, p)
    return tup[4] if tup is not None else ctx.faker.timezone()


@generator("currency_code")
def currency_code(ctx: GenContext, p: dict) -> str | None:
    """ISO 4217 currency code, e.g. 'USD'. With {"from": "<linked country
    column>"} returns that country's currency via the curated table (ADR-0015);
    None if the country isn't in the table."""
    tup = _linked_tuple(ctx, p)
    if tup is not None:
        from ..data import currency_for
        return currency_for(tup[3])
    return ctx.faker.currency_code()


@generator("credit_card")
def credit_card(ctx: GenContext, p: dict) -> str:
    """Test credit-card number (Luhn-valid fakes, safe for demos — never real).
    params: card_type (e.g. 'visa', 'mastercard', 'amex'); default any."""
    card_type = p.get("card_type")
    return ctx.faker.credit_card_number(card_type=card_type) if card_type \
        else ctx.faker.credit_card_number()


@generator("iban")
def iban(ctx: GenContext, p: dict) -> str:
    """Test IBAN (structurally valid fake)."""
    return ctx.faker.iban()


@generator("job_title")
def job_title(ctx: GenContext, p: dict) -> str:
    return ctx.faker.job()


@generator("age", example={"min": 18, "max": 90})
def age(ctx: GenContext, p: dict) -> int:
    """Integer age. params: min (default 18), max (default 90)."""
    return ctx.rng.randint(int(p.get("min", 18)), int(p.get("max", 90)))


@generator(
    "gender",
    example={"values": ["Female", "Male", "Non-binary"], "weights": [0.49, 0.49, 0.02]},
)
def gender(ctx: GenContext, p: dict) -> str:
    """Weighted gender label. params: values/weights override the defaults
    (Female/Male/Non-binary)."""
    values = p.get("values", ["Female", "Male", "Non-binary"])
    weights = p.get("weights", [0.49, 0.49, 0.02])
    return ctx.rng.choices(values, weights=weights, k=1)[0]
