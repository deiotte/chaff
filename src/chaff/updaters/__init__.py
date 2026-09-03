"""Per-tick entity updaters (Phase 3, ADR-0009). See updaters/AGENTS.md.

An updater is a callable `(ctx: EntityContext, state: dict, params: dict)`
that mutates one entity's `state` in place for the current tick. Register
with `@updater("id")` — adding one never edits the engine (INV-4).

All randomness MUST come from `ctx.rng` / `ctx.faker` (INV-3): same spec +
same seed = same evolution, forever. Never `random` globals, never
wall-clock time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable

from faker import Faker

UpdaterFn = Callable[["EntityContext", dict, dict], None]

_REGISTRY: dict[str, UpdaterFn] = {}

# Concrete params example per updater, so a UI can show the shape an updater
# expects instead of guessing. Mirrors the generator registry's `example=`
# (INV-4: the updater carries its own example — no UI hardcoding).
_EXAMPLES: dict[str, dict[str, Any]] = {}


def updater(
    updater_id: str, *, example: dict[str, Any] | None = None
) -> Callable[[UpdaterFn], UpdaterFn]:
    def deco(fn: UpdaterFn) -> UpdaterFn:
        if updater_id in _REGISTRY:
            raise ValueError(f"updater '{updater_id}' already registered")
        _REGISTRY[updater_id] = fn
        if example is not None:
            _EXAMPLES[updater_id] = example
        return fn
    return deco


def get_updater(updater_id: str) -> UpdaterFn:
    try:
        return _REGISTRY[updater_id]
    except KeyError:
        raise KeyError(
            f"unknown updater '{updater_id}'. Registered: {sorted(_REGISTRY)}"
        ) from None


def list_updaters() -> list[str]:
    return sorted(_REGISTRY)


def list_updater_examples() -> dict[str, dict[str, Any]]:
    """updater_id -> concrete params example, for every updater that
    registered one. The UI renders these when you pick an updater, so the
    required params (`lifecycle` needs `column` + `transitions`) are visible
    instead of guessed at."""
    return dict(_EXAMPLES)


@dataclass
class EntityContext:
    """Per-tick context handed to every updater call. Distinct from
    GenContext (which builds independent rows) so per-entity state never
    leaks into stateless generation."""

    rng: Any            # the run's shared random.Random
    faker: Faker        # the run's shared Faker
    entity_index: int   # 0-based entity number
    tick: int           # current tick (>= 1 when updaters run)


# ── Movement (tracks that move) ──────────────────────────────────────

@updater("movement", example={"lat_column": "lat", "lon_column": "lon",
                              "speed": 0.01, "turn_stddev": 15})
def movement(ctx: EntityContext, state: dict, p: dict) -> None:
    """Advance a lat/lon position by one step along a drifting heading.

    params: lat_column ('lat'), lon_column ('lon'), speed (deg/tick, 0.01),
            heading_column ('heading'), turn_stddev (deg, 15).
    Heading is seeded from state if present, else random; it random-walks
    each tick and is written back so the track curves realistically.
    """
    lat_c = p.get("lat_column", "lat")
    lon_c = p.get("lon_column", "lon")
    head_c = p.get("heading_column", "heading")
    speed = float(p.get("speed", 0.01))

    heading = state.get(head_c)
    if heading is None:
        heading = ctx.rng.uniform(0, 360)
    heading = (float(heading) + ctx.rng.gauss(0, float(p.get("turn_stddev", 15)))) % 360

    state[lat_c] = round(float(state.get(lat_c, 0.0)) + speed * math.cos(math.radians(heading)), 6)
    state[lon_c] = round(float(state.get(lon_c, 0.0)) + speed * math.sin(math.radians(heading)), 6)
    state[head_c] = round(heading, 2)


# ── Lifecycle (states that transition) ───────────────────────────────

@updater("lifecycle", example={"column": "status",
                               "transitions": {"placed": {"shipped": 0.9, "placed": 0.1},
                                               "shipped": {"delivered": 0.85, "returned": 0.15}}})
def lifecycle(ctx: EntityContext, state: dict, p: dict) -> None:
    """Advance a state column via weighted transitions.

    params: column (required), transitions (required) as
            {from_state: {to_state: weight, ...}, ...}.
    A state with no outgoing transitions is terminal (unchanged).

    Example: {"column": "status",
              "transitions": {"placed": {"shipped": 1.0},
                              "shipped": {"delivered": 0.9, "returned": 0.1}}}
    """
    col = p["column"]
    transitions = p["transitions"]
    options = transitions.get(state.get(col))
    if not options:
        return  # terminal state
    targets = list(options.keys())
    weights = [float(w) for w in options.values()]
    state[col] = ctx.rng.choices(targets, weights=weights, k=1)[0]


# ── Drift (sensor random walk) ───────────────────────────────────────

@updater("drift", example={"column": "reading", "stddev": 1.0, "precision": 2})
def drift(ctx: EntityContext, state: dict, p: dict) -> None:
    """Random-walk a numeric column (sensor drift).

    params: column (required), stddev (1.0), min, max (optional clamps),
            precision (2).
    """
    col = p["column"]
    value = float(state.get(col, 0.0)) + ctx.rng.gauss(0, float(p.get("stddev", 1.0)))
    if "min" in p:
        value = max(value, float(p["min"]))
    if "max" in p:
        value = min(value, float(p["max"]))
    state[col] = round(value, int(p.get("precision", 2)))
