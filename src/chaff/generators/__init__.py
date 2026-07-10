"""Semantic generators. See generators/AGENTS.md before adding one.

A generator is a callable: (ctx: GenContext, params: dict) -> value.
Register with @generator("id"). All randomness MUST come from ctx.rng
or ctx.faker (which is seeded from the same source) — never `random`
module globals, never time-derived entropy. That rule is what makes
seed determinism (ADR-0004) true.
"""

from __future__ import annotations

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
