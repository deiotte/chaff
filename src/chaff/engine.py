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

import math
import os
import random
from pathlib import Path
from typing import Any, Iterator

from faker import Faker

from .formats import get_encoder, get_extension, get_record_encoder
from .generators import GenContext, get_generator
from .sinks import (
    get_sink,
    get_stream_sink,
    is_stream_sink,
    rate_limited,
    time_limited,
)
from .secrets import resolve_env
from .spec import DatasetSpec
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
        return spec.entity.count * spec.entity.ticks
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
    records = (rec_enc(spec, r) for r in iter_records(spec, limit=limit))
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
        payload = get_encoder(spec.output.format)(spec, rows)
        receipt = get_sink(sink_id)(spec, payload)

    return _egg(spec, receipt)


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


def _egg(spec: DatasetSpec, receipt: str) -> str:
    if spec.seed == _JENNY:
        receipt += "\n☎  867-5309 — thanks for the seed, Jenny."
    return receipt
