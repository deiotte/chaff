"""The chaff engine: spec -> generate -> encode -> sink.

Deliberately boring. All the extensibility lives in the registries;
the engine just walks the pipeline. If you're tempted to add cleverness
here, it probably belongs in a generator, encoder, or sink instead.

Multi-table specs (ADR-0008) generate every table with foreign-key
integrity, in dependency order, then encode+deliver each table on the
shared format/sink. Single-table specs take the exact same path as
before — byte-for-byte identical (INV-3).
"""

from __future__ import annotations

import itertools
import json
import math
import os
import random
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator

from faker import Faker

from .formats import get_encoder, get_extension, get_record_encoder
from .formats._timing import event_time as _event_time, parse_time
from .generators import GenContext, get_generator
from .sinks import (
    get_sink,
    get_stream_sink,
    is_stream_sink,
    rate_limited,
    time_limited,
)
from .secrets import resolve_env
from .spec import ColumnSpec, DatasetSpec, ObserverSpec
from .updaters import EntityContext, get_updater


def _seeded(spec: DatasetSpec) -> tuple[random.Random, Faker]:
    rng = random.Random(spec.seed)
    faker = Faker()
    if spec.seed is not None:
        faker.seed_instance(spec.seed)
    return rng, faker


def _iter_table(rng, faker, columns, n_rows, tables=None) -> Iterator[dict[str, Any]]:
    """Yield one generated row at a time from a shared rng/faker.

    `n_rows` may be an int or `math.inf` (unbounded streaming, ADR-0016).
    Rows come out in a fixed order and the rng is consumed identically
    whatever `n_rows` is, so any prefix of the sequence is deterministic —
    this is the one generation path behind both the eager list builders and
    the lazy streaming iterators, so they can never drift apart. `tables`
    (already generated parents) is threaded to fk generators; None single-table."""
    resolved = [(col, get_generator(col.generator)) for col in columns]
    i = 0
    while i < n_rows:
        # `row` is filled in column order; ctx.row is the same dict, so a
        # `derived` column reads the cells generated before it (ADR-0012).
        # `cache` is per-row scratch for coherent geo linking (ADR-0015).
        row: dict[str, Any] = {}
        cache: dict[str, Any] = {}
        ctx = GenContext(rng=rng, faker=faker, row_index=i, tables=tables,
                         row=row, cache=cache)
        for col, fn in resolved:
            ctx.column = col.name
            if col.null_rate > 0 and rng.random() < col.null_rate:
                row[col.name] = None
            else:
                row[col.name] = fn(ctx, col.params)
        yield row
        i += 1


def _generate_table(rng, faker, columns, n_rows, tables=None) -> list[dict[str, Any]]:
    """Eager list form of `_iter_table` (multi-table + fixed-count paths)."""
    return list(_iter_table(rng, faker, columns, n_rows, tables))


def generate_rows(spec: DatasetSpec) -> list[dict[str, Any]]:
    """Generate all rows for the (primary) table. Deterministic under seed."""
    rng, faker = _seeded(spec)
    return _generate_table(rng, faker, spec.columns, spec.rows)


def iter_rows(spec: DatasetSpec, *, limit: float | int | None = None) -> Iterator[dict[str, Any]]:
    """Lazily yield single-table rows, one at a time (ADR-0016).

    `limit=None` → `spec.rows` (unchanged length); an int caps or *extends*
    to exactly that many rows; `math.inf` streams unbounded. The i-th row is
    identical whatever `limit` is, so `list(iter_rows(spec)) == generate_rows(spec)`
    byte-for-byte and any prefix is a deterministic prefix (INV-3)."""
    rng, faker = _seeded(spec)
    n = spec.rows if limit is None else limit
    yield from _iter_table(rng, faker, spec.columns, n)


# ── Multi-table (ADR-0008) ───────────────────────────────────────────

def _all_tables(spec: DatasetSpec) -> list[tuple[str, list, int]]:
    """(name, columns, rows) for every table: primary first, then extras."""
    out = [(spec.name, spec.columns, spec.rows)]
    for t in spec.tables or []:
        out.append((t.name, t.columns, t.rows))
    return out


def _fk_deps(columns) -> set[str]:
    return {c.params.get("table") for c in columns
            if c.generator == "fk" and c.params.get("table")}


def _topo_order(all_tables: list[tuple[str, list, int]]) -> list[str]:
    """Tables ordered so every fk parent precedes its child. Deterministic:
    ties break by declaration order. Raises on missing/self/cyclic refs."""
    order_index = {name: i for i, (name, _, _) in enumerate(all_tables)}
    deps = {name: _fk_deps(cols) for name, cols, _ in all_tables}
    for name, refs in deps.items():
        for ref in refs:
            if ref not in order_index:
                raise ValueError(f"table '{name}' has an fk to unknown table '{ref}'")
            if ref == name:
                raise ValueError(f"table '{name}' has an fk to itself")

    ordered: list[str] = []
    done: set[str] = set()
    active: set[str] = set()

    def visit(name: str) -> None:
        if name in done:
            return
        if name in active:
            raise ValueError(f"cyclic fk dependency involving table '{name}'")
        active.add(name)
        for ref in sorted(deps[name], key=lambda x: order_index[x]):
            visit(ref)
        active.discard(name)
        done.add(name)
        ordered.append(name)

    for name, _, _ in all_tables:
        visit(name)
    return ordered


def generate_tables(spec: DatasetSpec) -> dict[str, list[dict[str, Any]]]:
    """Generate every table with FK integrity, parents before children.

    Returns {table_name: [rows]}. One rng/faker seeded once and consumed in
    a deterministic table order, so multi-table output is reproducible too.
    """
    rng, faker = _seeded(spec)
    all_tables = _all_tables(spec)
    cols_by = {name: cols for name, cols, _ in all_tables}
    rows_by = {name: n for name, _, n in all_tables}
    result: dict[str, list[dict[str, Any]]] = {}
    for name in _topo_order(all_tables):
        result[name] = _generate_table(rng, faker, cols_by[name], rows_by[name], tables=result)
    return result


# ── Stateful entities (ADR-0009) ─────────────────────────────────────

def iter_entity_rows(spec: DatasetSpec, *, limit: float | int | None = None) -> Iterator[dict[str, Any]]:
    """Lazily yield entity snapshots, time-ordered (ADR-0016).

    Each entity's initial state comes from the spec's columns (tick 0), then
    per-tick `updates` mutate it. Output is one snapshot per (tick, entity),
    all entities at tick 0, then tick 1, …. `limit=None` → `count × ticks`
    (unchanged); an int caps the total snapshots; `math.inf` ticks forever —
    a live feed of moving entities. Per-tick updates are applied to every
    entity *before* that tick is emitted and the rng is consumed the same way
    whatever `limit` is, so any prefix is a deterministic prefix (INV-3).
    """
    ent = spec.entity
    rng, faker = _seeded(spec)
    state_cols = [(col, get_generator(col.generator)) for col in spec.columns]
    updates = [(get_updater(u.updater), u.params) for u in ent.updates]  # fail fast

    # Only materialize as many entities as the cap can ever emit. Tick 0 emits
    # entities in order, so when `limit` is smaller than `count` the run ends
    # inside tick 0 — before any per-tick update — and the entities past the
    # cap are never observed. Skipping them keeps output byte-identical (INV-3,
    # the rng is consumed identically for the entities that *are* emitted) while
    # stopping one WS message with count=1_000_000 + max_records=1 from
    # materializing a million entities up front and blocking the event loop.
    n_entities = ent.count
    if limit is not None and limit < ent.count:
        n_entities = int(limit)

    ids: list[Any] = []
    states: list[dict[str, Any]] = []
    for e in range(n_entities):
        state: dict[str, Any] = {}
        cache: dict[str, Any] = {}
        # ctx.row is the entity's initial-state dict, so a `derived` column can
        # compute from earlier initial-state columns (ADR-0012); ctx.cache backs
        # coherent geo linking (ADR-0015). Both are resolved once at tick 0;
        # updaters drive per-tick change.
        gctx = GenContext(rng=rng, faker=faker, row_index=e, row=state, cache=cache)
        if ent.id_pattern:
            ids.append(get_generator("pattern")(gctx, {"pattern": ent.id_pattern}))
        else:
            ids.append(e + 1)
        for col, fn in state_cols:
            gctx.column = col.name
            if col.null_rate > 0 and rng.random() < col.null_rate:
                state[col.name] = None
            else:
                state[col.name] = fn(gctx, col.params)
        states.append(state)

    cap = ent.count * ent.ticks if limit is None else limit
    emitted = 0
    t = 0
    while emitted < cap:
        if t > 0:
            for e in range(n_entities):
                ectx = EntityContext(rng=rng, faker=faker, entity_index=e, tick=t)
                for upd_fn, params in updates:
                    upd_fn(ectx, states[e], params)
        for e in range(n_entities):
            if emitted >= cap:
                break
            row = {ent.id_column: ids[e], ent.tick_column: t}
            row.update(states[e])  # snapshot of current state
            yield row
            emitted += 1
        t += 1


def generate_entity_rows(spec: DatasetSpec) -> list[dict[str, Any]]:
    """Eager form: `count × ticks` time-ordered snapshots (unchanged output)."""
    return list(iter_entity_rows(spec))


def generate_records(spec: DatasetSpec) -> list[dict[str, Any]]:
    """Rows for a single-table or entity spec (not multi-table). The one
    entry point the API/preview use so both modes are handled uniformly."""
    return generate_entity_rows(spec) if spec.entity else generate_rows(spec)


def iter_records(spec: DatasetSpec, *, limit: float | int | None = None) -> Iterator[dict[str, Any]]:
    """Lazy per-record generation for streaming and serving (ADR-0016).

    Yields the same rows `generate_records` would, one at a time, without
    materializing a list — so a stream can outrun memory or run forever.
    `limit` controls how many: `None` = the spec's natural length, an int =
    exactly that many (may exceed or fall short of the natural length),
    `math.inf` = unbounded. Determinism is per-record: the i-th record
    depends only on the spec, its seed, and i — never on `limit` or the
    wall-clock. Multi-table specs don't stream (whole-file per table); the
    engine routes those to `_run_multi` before reaching here."""
    if spec.entity:
        yield from iter_entity_rows(spec, limit=limit)
    else:
        yield from iter_rows(spec, limit=limit)


def effective_row_count(spec: DatasetSpec) -> int:
    """How many rows a spec will actually emit, for request-size limits.

    Entity specs emit `count × ticks`; a multi-table spec emits the sum over
    every table (the primary plus each related one) — the caller's ceiling
    should account for all of it, not just the primary table.
    """
    if spec.entity:
        # Every observer renders the whole scene, so a two-observer spec is
        # twice the rows. A ceiling that counted the scene once would admit a
        # request that produces double what it measured.
        return spec.entity.count * spec.entity.ticks * max(len(spec.entity.observers), 1)
    if spec.tables:
        return spec.rows + sum(t.rows for t in spec.tables)
    return spec.rows


def resolve_stream_limit(max_records: int | None, duration: float | None) -> float | int | None:
    """Records to generate for a stream. An explicit `max_records` wins; a
    `duration` with no record cap means unbounded (the clock cuts it); else
    `None` = the spec's natural length. Shared by `run()` and the API job
    runner so the bound is resolved the same way everywhere."""
    return math.inf if (max_records is None and duration) else max_records


def stream_encoded(
    spec: DatasetSpec,
    *,
    limit: float | int | None = None,
    rate: float | None = None,
    duration: float | None = None,
) -> Iterator[bytes]:
    """Encode + pace + time-bound a streaming spec's records, minus delivery.

    The one assembly behind both `run()`'s streaming branch and the API job
    runner, so encoding, pacing, and bounds never drift between them. Returns
    an iterator of encoded record-bytes for a streaming sink to deliver.
    `get_record_encoder` is resolved eagerly, so a whole-file format fails
    fast here — before any sink or network is touched (INV-2 stays intact:
    the engine encodes; the sink only delivers)."""
    rec_enc = get_record_encoder(spec.output.format)
    view = encode_view(spec)
    records = (rec_enc(view, r) for r in iter_records(spec, limit=limit))
    paced = rate_limited(records, rate)
    if duration:
        paced = time_limited(paced, float(duration))
    return paced


# Fun clause (AGENTS.md §5): seed 8675309 tips its hat to Jenny. Receipt
# only — the payload bytes are untouched, so seed determinism is unharmed.
_JENNY = 8675309


def _with_resolved_sink_options(spec: DatasetSpec) -> DatasetSpec:
    """Substitute `${VAR}` in sink options from the environment (ADR-0027).

    Done once here rather than in each sink, so every sink gets it and none
    has to know the convention. This resolves the sink's *own configuration*,
    not the payload — encoders and sinks stay as they were (INV-2).
    """
    resolved = resolve_env(spec.sink.options)
    if resolved == spec.sink.options:
        return spec
    return spec.model_copy(update={"sink": spec.sink.model_copy(update={"options": resolved})})


def run(spec: DatasetSpec) -> str:
    """Full pipeline. Returns the sink's human-readable receipt.

    The engine negotiates the sink shape (ADR-0007): a streaming sink is
    fed one encoded record at a time, rate-paced; a blob sink gets the whole
    encoded payload. Format and sink stay independent (INV-2) — the engine,
    not the sink, does the encoding. Multi-table specs (ADR-0008) take the
    per-table path below.
    """
    spec = _with_resolved_sink_options(spec)

    if spec.tables:
        return _run_multi(spec)

    if spec.entity and spec.entity.observers:
        return _run_observers(spec)

    sink_id = spec.sink.sink

    if is_stream_sink(sink_id):
        # Lazy path (ADR-0016): records are generated one at a time as the
        # sink consumes them, so a stream can run past memory or run forever.
        opts = spec.sink.options
        duration = opts.get("duration")
        stream = stream_encoded(
            spec,
            limit=resolve_stream_limit(opts.get("max_records"), duration),
            rate=opts.get("rate"),
            duration=duration,
        )
        receipt = get_stream_sink(sink_id)(spec, stream)
    else:
        rows = generate_records(spec)  # single-table or entity ticks
        payload = get_encoder(spec.output.format)(encode_view(spec), rows)
        receipt = get_sink(sink_id)(spec, payload)

    return _egg(spec, receipt)


#: Generator id for columns the engine fills in itself (entity id, tick).
#: Intentionally unregistered — see `encode_view`.
_ENGINE_SUPPLIED = "__engine_supplied__"


def encode_view(spec: DatasetSpec) -> DatasetSpec:
    """The spec a column-oriented encoder should see for this spec's rows.

    An entity run emits two columns the spec never declares: `iter_entity_rows`
    puts the entity id and the tick number into every snapshot. Column-oriented
    encoders (csv, tsv, sql, xlsx, parquet, avro) take their column list from
    `spec.columns`, so without this view they drop the two columns that make a
    time series readable — and csv refused the row outright, which is why
    `chaff generate examples/order_lifecycle.json` raised. Row-oriented formats
    (json, ndjson, xml, cot) serialize the row dict and were never affected;
    that asymmetry is why it went unnoticed, since `make check` only ever
    *validated* the entity presets and never generated one.

    The view exists for encoding only. The two synthesized ColumnSpecs name a
    generator that is deliberately not registered: their values come from the
    engine, never from a generator, so anything that tries to *generate* from
    this view should fail loudly naming `__engine_supplied__` rather than
    quietly producing wrong values. Multi-table specs get their views from
    `table_views`; a plain spec is its own view.
    """
    ent = spec.entity
    if ent is None:
        return spec
    synthesized = [
        ColumnSpec(name=ent.id_column, generator=_ENGINE_SUPPLIED),
        ColumnSpec(name=ent.tick_column, generator=_ENGINE_SUPPLIED),
    ]
    return spec.model_copy(update={"columns": synthesized + list(spec.columns)})


# ── Observers: one scene, several accounts of it (ADR-0033) ──────────

#: Metres per degree of latitude. A sphere, deliberately: the perturbation this
#: backs is a synthetic measurement error of a few metres, and an ellipsoidal
#: model would add precision to a number that was invented.
_M_PER_DEG = 111_320.0


def _observer_rng(spec: DatasetSpec, observer: ObserverSpec) -> random.Random:
    """A generator private to one observer.

    Derived from the seed and the observer's name rather than drawn from the
    scene's own rng, so that **adding an observer never changes the scene**.
    Sharing the scene rng would make every entity's trajectory depend on how
    many sensors were watching it, which is both wrong and the kind of wrong
    that only shows up when someone compares two runs (INV-3).
    """
    return random.Random(f"{spec.seed}:observer:{observer.name}")


def _observer_faker(spec: DatasetSpec, observer: ObserverSpec) -> Faker:
    """The observer's faker, derived like its rng so the scene stays untouched."""
    faker = Faker()
    faker.seed_instance(f"{spec.seed}:observer:{observer.name}")
    return faker


def _misplace(rng: random.Random, lat: float, lon: float, radius_m: float) -> tuple[float, float]:
    """Move a position somewhere inside `radius_m` of where it really was.

    Uniform over the disc — `sqrt` on the radius, or the draws crowd the
    centre — and **bounded rather than Gaussian on purpose**. A normal
    distribution has a tail, so a fixture built on one asserts something that
    is merely usually true; a consumer's gate radius could be cleared on nine
    runs in ten. Bounded error makes the worst case arithmetic: two observers
    can disagree by at most the sum of their radii, and a test can say so.
    """
    r = radius_m * math.sqrt(rng.random())
    theta = rng.random() * 2.0 * math.pi
    dlat = (r * math.cos(theta)) / _M_PER_DEG
    # Longitude degrees shrink toward the poles. Clamped because at the pole
    # itself the scale is zero and the offset would be infinite.
    scale = _M_PER_DEG * max(math.cos(math.radians(lat)), 1e-6)
    dlon = (r * math.sin(theta)) / scale
    return lat + dlat, lon + dlon


def iter_observed_rows(spec: DatasetSpec, observer: ObserverSpec) -> Iterator[dict[str, Any]]:
    """One observer's account of the scene: every snapshot, seen imperfectly.

    The entity id is replaced with this observer's own — a sensor names what
    it sees in its own namespace, and two sensors naming one object
    identically is the thing a correlating consumer is supposed to *work out*,
    not be handed. Position is displaced within `position_error_m`. Everything
    else passes through: an observer reports the scene, it does not invent one.
    """
    ent = spec.entity
    rng = _observer_rng(spec, observer)
    faker = _observer_faker(spec, observer)
    aliases: dict[Any, Any] = {}
    pattern_fn = get_generator("pattern")

    for truth in iter_entity_rows(spec):
        row = dict(truth)
        real_id = truth[ent.id_column]
        if observer.id_pattern:
            if real_id not in aliases:
                # Entities first appear in a fixed order (tick 0, in index
                # order), so the alias each one is given is deterministic.
                ctx = GenContext(rng=rng, faker=faker, row_index=len(aliases), row={}, cache={})
                aliases[real_id] = pattern_fn(ctx, {"pattern": observer.id_pattern})
            row[ent.id_column] = aliases[real_id]

        # What the sensor says about itself. Applied before the scene's own
        # values would be read, but after the copy, so a `reports` key that
        # collides with a scene column deliberately wins: the observer's
        # account of its own accuracy is not the scene's to state.
        row.update(observer.reports)

        # A unit or scale fault, applied last so it lands on whatever the row
        # ended up carrying. Non-numeric and absent values pass untouched: a
        # scale factor is a claim about a measurement, and a column that holds
        # no measurement has nothing to be wrong about.
        for column, factor in observer.misreports.items():
            value = row.get(column)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                row[column] = float(value) * float(factor)

        if observer.position_error_m > 0:
            lat, lon = row.get(observer.lat_column), row.get(observer.lon_column)
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                row[observer.lat_column], row[observer.lon_column] = _misplace(
                    rng, float(lat), float(lon), observer.position_error_m)
        yield row


def observer_views(spec: DatasetSpec) -> Iterator[tuple[str, DatasetSpec, list[dict[str, Any]]]]:
    """Yield `(observer_name, view, rows)` for a scene with observers.

    Each view is an ordinary entity spec with `observers` cleared and this
    observer's `options` overlaid on the output, so every encoder and sink
    downstream sees what it would see for a plain spec — no encoder knows
    observers exist (INV-2). A per-observer clock offset is expressed here, as
    a `base_time` in those options, because when the event happened *according
    to this sensor* is a fact about the encoding rather than about the scene.
    """
    declared = {c.name for c in spec.columns}
    for observer in spec.entity.observers:
        # A `reports` key is a real column in this observer's rows but is
        # declared nowhere in the spec, so a column-oriented encoder would
        # drop it exactly as one dropped the entity id and tick before
        # ADR-0028. Declare them on the view instead of relying on every
        # encoder being row-oriented.
        extra = [ColumnSpec(name=k, generator=_ENGINE_SUPPLIED)
                 for k in observer.reports if k not in declared]
        view = spec.model_copy(update={
            "entity": spec.entity.model_copy(update={"observers": []}),
            "columns": list(spec.columns) + extra,
            "output": spec.output.model_copy(update={
                "format": observer.format or spec.output.format,
                "options": _observer_options(spec, observer),
            }),
        })
        yield observer.name, view, list(iter_observed_rows(spec, observer))


def _observer_options(spec: DatasetSpec, observer: ObserverSpec) -> dict[str, Any]:
    """This observer's output options, including any clock it is wrong about.

    A `base_time` in `observer.options` is a **declared** offset: legitimate
    scene design, recorded in the answer key, and a consumer can be held to it.
    `clock_error_s` is the undeclared remainder — a clock nobody knows is
    wrong — and it is folded in here so it reaches the encoder without ever
    reaching the key.

    It shifts every timestamp by the same amount, so the feed stays ordered and
    its intervals stay exact. Only the absolute instant moves, and only a
    comparison against the truth can see it (chaff ADR-0040).
    """
    options = {**spec.output.options, **observer.options}
    if observer.clock_error_s:
        base = parse_time(options.get("base_time", "2025-01-01T00:00:00Z"))
        options["base_time"] = (
            base + timedelta(seconds=float(observer.clock_error_s))
        ).isoformat().replace("+00:00", "Z")
    return options


def declared_clock_offset_ms(spec: DatasetSpec, observer: ObserverSpec) -> int:
    """How far this observer's clock is DECLARED to be from the scene's, in ms.

    The scene's `base_time` is when things really happened; an observer's
    override is when this sensor says they did. Two feeds of one scene carrying
    a deliberate offset is the case the observers exist to produce, so the key
    records the offset rather than pretending it is zero — a consumer is held
    to the clock its feed declares, exactly as it is held to the position error
    its feed declares.

    `clock_error_s` is deliberately **not** included. That is the fault.
    """
    scene_base = parse_time(spec.output.options.get("base_time", "2025-01-01T00:00:00Z"))
    observer_base = parse_time(observer.options.get("base_time", scene_base.isoformat()))
    return round((observer_base - scene_base).total_seconds() * 1000)


def _distinct(ids: list[Any], source: str) -> list[Any]:
    """Refuse an id scheme that gave two entities the same name.

    A pattern with too few placeholders collides — `TRUTH-##` runs out at a
    hundred — and the collision is silent everywhere else: the feeds look
    fine, and only the answer key quietly merges two things into one. Scoring
    a consumer against that would mark a correct refusal wrong and a wrong
    pairing right, which is worse than having no key at all.
    """
    seen = [i for i in ids if ids.count(i) > 1]
    if seen:
        raise ValueError(
            f"{source} gave the same id to more than one entity ({sorted({str(i) for i in seen})}) — "
            f"widen the pattern (more '#' or '?') so every entity is distinct")
    return ids


def scene_truth(spec: DatasetSpec) -> dict[str, Any]:
    """Which observations are the same thing — the answer key.

    chaff knows what every observer is looking at. Writing that down is what
    lets a consumer be scored rather than merely watched: without it you can
    see that a correlator paired two tracks, but not whether it paired the
    *right* two, and a false pairing looks exactly like a true one.

    **This is an evaluation artifact and never part of a feed.** No sensor
    emits it, so anything that reads it is a test harness; a consumer given
    this has been handed the answer to the question it exists to answer.
    """
    ent = spec.entity
    identities: dict[str, dict[str, Any]] = {}
    # Tick 0 emits every entity once, in index order, and an observer yields
    # rows in that same order — so the first `count` rows of each are the whole
    # identity mapping, and nothing needs the other ticks.
    head = lambda rows: [r[ent.id_column] for r in itertools.islice(rows, ent.count)]  # noqa: E731
    truth_ids = _distinct(head(iter_entity_rows(spec)), "the scene's own id_pattern")
    for observer in ent.observers:
        observed_ids = _distinct(
            head(iter_observed_rows(spec, observer)),
            f"observer '{observer.name}''s id_pattern")
        for real, observed in zip(truth_ids, observed_ids):
            identities.setdefault(str(real), {})[observer.name] = observed
    return {
        "//": "Ground truth for a chaff scene: which observer-side ids are the same "
              "real entity, and where each of them actually was. An evaluation artifact "
              "(chaff ADR-0033, ADR-0038) — no sensor emits this, and a consumer handed "
              "it has been given the answer to the question it exists to answer.",
        "scene": spec.name,
        "seed": spec.seed,
        "entities": ent.count,
        "ticks": ent.ticks,
        "observers": [o.name for o in ent.observers],
        "identities": identities,
        "//observer_error_m": "What each observer CLAIMS its position error is, in metres. "
                              "Bounded rather than Gaussian, so every one of its reports lands "
                              "inside this radius of the truth below — which is what lets a "
                              "consumer be held to an exact bound rather than a statistical one.",
        "observer_error_m": {o.name: o.position_error_m for o in ent.observers},
        "//positions": "Where each entity really was, [lat, lon] per tick in tick order, keyed "
                       "by the scene's own id. Paired with `identities` this turns a decoded "
                       "position into a distance from the truth — which is the only way to see "
                       "a fault that leaves every packet well-formed and every value in range.",
        "positions": _truth_positions(spec),
        "//kinematics": "What each entity was really doing, [speed_m_s, course_deg] per tick in "
                        "tick order. Unlike position an observer does not perturb these, so any "
                        "difference a consumer shows is its own — an encoding quantum at best and "
                        "a unit or convention error at worst. A wrong scale here is the archetypal "
                        "invisible fault: every value stays finite, in range and plausible.",
        "kinematics": _truth_kinematics(spec),
        "//event_times": "When each tick really happened, epoch milliseconds, in tick order. The "
                         "SCENE's clock — what an observer's feed says is that observer's claim, "
                         "and the two differing by more than it declares is a fault no ordering "
                         "check can see, because a wrong clock shifts every timestamp equally.",
        "event_times": _truth_event_times(spec),
        "//observer_clock_offset_ms": "How far each observer's clock is DECLARED to be from the "
                                      "scene's. Two feeds of one scene carrying a deliberate "
                                      "offset is the case observers exist to produce, so the key "
                                      "records it rather than pretending it is zero — a consumer "
                                      "is held to the clock its feed declares, exactly as it is "
                                      "held to the position error its feed declares.",
        "observer_clock_offset_ms": {
            o.name: declared_clock_offset_ms(spec, o) for o in ent.observers
        },
    }


def _truth_event_times(spec: DatasetSpec) -> list[int]:
    """The real instant of each tick, epoch milliseconds, in tick order.

    One list rather than one per entity: every entity in a tick shares that
    tick's instant, which is what makes a within-tick reordering invisible in
    the time sequence and is worth knowing when reading a gate that checks one.
    """
    ent = spec.entity
    seen: dict[Any, int] = {}
    for row in iter_entity_rows(spec):
        tick = row.get(ent.tick_column)
        if tick is not None and tick not in seen:
            seen[tick] = round(_event_time(spec, row).timestamp() * 1000)
    return [seen[k] for k in sorted(seen)]


def _truth_kinematics(spec: DatasetSpec) -> dict[str, list[list[float | None]]]:
    """Every entity's real speed and course, per tick.

    Beside `positions` and read the same way. The pairing matters: a feed can
    put a thing in exactly the right place and still say it is travelling the
    wrong way at twice the speed, and nothing about the position says so.

    A column a scene does not carry yields `null` for that slot rather than a
    zero — an absent measurement and a measured zero are different claims, and
    a consumer scored against a fabricated zero would be marked wrong for
    being right.
    """
    ent = spec.entity
    tracks: dict[str, list[list[float | None]]] = {}
    for row in iter_entity_rows(spec):
        pair = [_maybe_float(row.get("speed")), _maybe_float(row.get("heading"))]
        tracks.setdefault(str(row[ent.id_column]), []).append(pair)
    return tracks


def _maybe_float(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _truth_positions(spec: DatasetSpec) -> dict[str, list[list[float]]]:
    """Every entity's real position, per tick, before any observer saw it.

    The *scene's* geometry, never an observer's: an observer's account is
    displaced within its own error radius, and scoring one displaced account
    against another would measure the difference between two guesses rather
    than the distance from either to the truth.
    """
    ent = spec.entity
    tracks: dict[str, list[list[float]]] = {}
    for row in iter_entity_rows(spec):
        lat, lon = row.get("lat"), row.get("lon")
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            tracks.setdefault(str(row[ent.id_column]), []).append([float(lat), float(lon)])
    return tracks


def table_views(spec: DatasetSpec) -> Iterator[tuple[str, DatasetSpec, list[dict[str, Any]]]]:
    """Yield `(table_name, single-table view, rows)` for a multi-table spec.

    Each view is an ordinary single-table `DatasetSpec` (its own name and
    columns, `tables` cleared), so every encoder and sink downstream sees
    exactly what it would see for a plain spec — no encoder knows multi-table
    exists (INV-2). Generation happens once, in dependency order, so FK
    integrity holds across the views.
    """
    tables = generate_tables(spec)
    cols_by = {spec.name: spec.columns, **{t.name: t.columns for t in spec.tables or []}}
    for tname, rows in tables.items():
        view = spec.model_copy(update={
            "name": tname,
            "columns": cols_by[tname],
            "tables": None,
        })
        yield tname, view, rows


def _safe_member_name(name: str, ext: str) -> str:
    """A single-component filename for a table, belt-and-braces.

    `DatasetSpec` already rejects path-hostile names, so this should never
    change anything. It exists because the consequence of a miss here is a
    file written outside the output directory, or a zip entry that escapes on
    extraction — cheap to double-check, expensive to get wrong once.
    """
    candidate = f"{name}{ext}"
    base = os.path.basename(candidate.replace("\\", "/"))
    if base != candidate or base in ("", ".", ".."):
        raise ValueError(f"unsafe table filename derived from '{name}'")
    return base


def encode_tables(spec: DatasetSpec) -> list[tuple[str, str, bytes]]:
    """Generate + encode every table of a multi-table spec, delivery-free.

    Returns `[(table_name, filename, payload)]`. The caller decides where the
    bytes go — `run()` hands them to a sink, the API zips them — so this stays
    a pure function of the spec (INV-2). Shares `table_views` with the sink
    path, so a downloaded zip and a CLI-written directory hold the same bytes.
    """
    ext = get_extension(spec.output.format)
    encoder = get_encoder(spec.output.format)
    return [
        (tname, _safe_member_name(tname, ext), encoder(view, rows))
        for tname, view, rows in table_views(spec)
    ]


def encode_observers(spec: DatasetSpec) -> list[tuple[str, str, bytes]]:
    """Generate + encode every observer's feed, delivery-free.

    Returns `[(observer_name, filename, payload)]`, ending with the scene's
    ground truth. The caller decides where the bytes go — `run()` hands them
    to a sink, the API zips them — so this stays a pure function of the spec
    (INV-2) and both paths deliver the same bytes.

    Truth rides along because a download that omitted it would leave the
    browser user unable to do the one thing the observers are for: check
    whether a consumer got the pairing right.
    """
    ext = get_extension(spec.output.format)
    encoder = get_encoder(spec.output.format)
    out = [
        (name, _safe_member_name(f"{spec.name}-{name}{ext}", ""), encoder(encode_view(view), rows))
        for name, view, rows in observer_views(spec)
    ]
    truth = json.dumps(scene_truth(spec), indent=2).encode("utf-8") + b"\n"
    out.append(("truth", _safe_member_name(f"{spec.name}-truth.json", ""), truth))
    return out


def _run_multi(spec: DatasetSpec) -> str:
    """Generate related tables (FK integrity) and deliver one per table on
    the shared format/sink. Each table encodes as an ordinary single-table
    view, so encoders stay unchanged (INV-2)."""
    if is_stream_sink(spec.sink.sink):
        raise ValueError(
            "multi-table specs produce whole-file output per table; streaming "
            "sinks (http/kafka/tcp/udp) don't support multi-table yet"
        )

    ext = get_extension(spec.output.format)
    encoder = get_encoder(spec.output.format)
    sink = get_sink(spec.sink.sink)

    base = spec.sink.options.get("path")
    base_dir = Path(base).parent if base else Path(".")

    receipts = []
    base_resolved = base_dir.resolve()
    for tname, view, rows in table_views(spec):
        target = (base_dir / _safe_member_name(tname, ext))
        # The written path must stay under the directory the user asked for,
        # even if a name slips past validation (symlinks, future callers).
        if base_resolved not in target.resolve().parents:
            raise ValueError(
                f"table '{tname}' would write outside {base_dir} — refusing")
        view = view.model_copy(update={
            "sink": spec.sink.model_copy(update={
                "options": {**spec.sink.options, "path": str(target)}
            }),
        })
        payload = encoder(view, rows)
        receipts.append(sink(view, payload))

    return _egg(spec, "\n".join(receipts))


def _run_observers(spec: DatasetSpec) -> str:
    """Deliver one file per observer, plus the scene's truth alongside.

    Mirrors `_run_multi`: each observer encodes as an ordinary single-feed
    view, so encoders and sinks stay unchanged (INV-2).
    """
    if is_stream_sink(spec.sink.sink):
        raise ValueError(
            "observer specs produce one whole file per observer; streaming "
            "sinks (http/kafka/tcp/udp) deliver a single feed. Stream one "
            "observer at a time by removing the others from the spec."
        )

    sink = get_sink(spec.sink.sink)

    base = spec.sink.options.get("path")
    base_dir = Path(base).parent if base else Path(".")
    stem = Path(base).stem if base else spec.name
    base_resolved = base_dir.resolve()

    def target_for(member: str) -> Path:
        path = base_dir / _safe_member_name(member, "")
        # Same containment check the table path makes, for the same reason:
        # a name that slipped past validation must not write outside the
        # directory the user asked for.
        if base_resolved not in path.resolve().parents:
            raise ValueError(f"'{member}' would write outside {base_dir} — refusing")
        return path

    receipts = []
    for name, view, rows in observer_views(spec):
        # Each observer's own extension: a scene may render one feed as CoT
        # and another as VMTI, and the file names have to say which is which.
        target = target_for(f"{stem}-{name}{get_extension(view.output.format)}")
        view = view.model_copy(update={
            "sink": spec.sink.model_copy(update={
                "options": {**spec.sink.options, "path": str(target)}
            }),
        })
        receipts.append(sink(view, get_encoder(view.output.format)(encode_view(view), rows)))

    # The answer key. Written beside the feeds and never inside one: a
    # consumer handed this has been given the answer to the question it
    # exists to answer (ADR-0033).
    truth_path = target_for(f"{stem}-truth.json")
    truth_path.write_text(json.dumps(scene_truth(spec), indent=2) + "\n", encoding="utf-8")
    receipts.append(f"wrote ground truth -> {truth_path} (evaluation artifact, not a feed)")

    return _egg(spec, "\n".join(receipts))


def _egg(spec: DatasetSpec, receipt: str) -> str:
    if spec.seed == _JENNY:
        receipt += "\n☎  867-5309 — thanks for the seed, Jenny."
    return receipt
