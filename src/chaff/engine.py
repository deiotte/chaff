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

import random
from pathlib import Path
from typing import Any

from faker import Faker

from .formats import get_encoder, get_extension, get_record_encoder
from .generators import GenContext, get_generator
from .sinks import get_sink, get_stream_sink, is_stream_sink, rate_limited
from .spec import DatasetSpec
from .updaters import EntityContext, get_updater


def _seeded(spec: DatasetSpec) -> tuple[random.Random, Faker]:
    rng = random.Random(spec.seed)
    faker = Faker()
    if spec.seed is not None:
        faker.seed_instance(spec.seed)
    return rng, faker


def _generate_table(rng, faker, columns, n_rows, tables=None) -> list[dict[str, Any]]:
    """Generate one table's rows from a shared rng/faker. `tables` (already
    generated parents) is threaded to fk generators; None for single-table."""
    resolved = [(col, get_generator(col.generator)) for col in columns]
    rows: list[dict[str, Any]] = []
    for i in range(n_rows):
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
        rows.append(row)
    return rows


def generate_rows(spec: DatasetSpec) -> list[dict[str, Any]]:
    """Generate all rows for the (primary) table. Deterministic under seed."""
    rng, faker = _seeded(spec)
    return _generate_table(rng, faker, spec.columns, spec.rows)


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

def generate_entity_rows(spec: DatasetSpec) -> list[dict[str, Any]]:
    """Generate `count` entities over `ticks` time steps.

    Each entity's initial state comes from the spec's columns (tick 0), then
    per-tick `updates` mutate it. Output is one snapshot per (tick, entity),
    time-ordered (all entities at tick 0, then tick 1, …). Deterministic: one
    rng/faker seeded once and consumed in a fixed order.
    """
    ent = spec.entity
    rng, faker = _seeded(spec)
    state_cols = [(col, get_generator(col.generator)) for col in spec.columns]
    updates = [(get_updater(u.updater), u.params) for u in ent.updates]  # fail fast

    ids: list[Any] = []
    states: list[dict[str, Any]] = []
    for e in range(ent.count):
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

    rows: list[dict[str, Any]] = []
    for t in range(ent.ticks):
        if t > 0:
            for e in range(ent.count):
                ectx = EntityContext(rng=rng, faker=faker, entity_index=e, tick=t)
                for upd_fn, params in updates:
                    upd_fn(ectx, states[e], params)
        for e in range(ent.count):
            row = {ent.id_column: ids[e], ent.tick_column: t}
            row.update(states[e])  # snapshot of current state
            rows.append(row)
    return rows


def generate_records(spec: DatasetSpec) -> list[dict[str, Any]]:
    """Rows for a single-table or entity spec (not multi-table). The one
    entry point the API/preview use so both modes are handled uniformly."""
    return generate_entity_rows(spec) if spec.entity else generate_rows(spec)


def effective_row_count(spec: DatasetSpec) -> int:
    """How many rows a spec will actually emit (entities × ticks for an
    entity spec), for request-size limits."""
    if spec.entity:
        return spec.entity.count * spec.entity.ticks
    return spec.rows


# Fun clause (AGENTS.md §5): seed 8675309 tips its hat to Jenny. Receipt
# only — the payload bytes are untouched, so seed determinism is unharmed.
_JENNY = 8675309


def run(spec: DatasetSpec) -> str:
    """Full pipeline. Returns the sink's human-readable receipt.

    The engine negotiates the sink shape (ADR-0007): a streaming sink is
    fed one encoded record at a time, rate-paced; a blob sink gets the whole
    encoded payload. Format and sink stay independent (INV-2) — the engine,
    not the sink, does the encoding. Multi-table specs (ADR-0008) take the
    per-table path below.
    """
    if spec.tables:
        return _run_multi(spec)

    rows = generate_records(spec)  # single-table or entity ticks
    sink_id = spec.sink.sink

    if is_stream_sink(sink_id):
        rec_enc = get_record_encoder(spec.output.format)
        records = (rec_enc(spec, r) for r in rows)
        paced = rate_limited(records, spec.sink.options.get("rate"))
        receipt = get_stream_sink(sink_id)(spec, paced)
    else:
        payload = get_encoder(spec.output.format)(spec, rows)
        receipt = get_sink(sink_id)(spec, payload)

    return _egg(spec, receipt)


def _run_multi(spec: DatasetSpec) -> str:
    """Generate related tables (FK integrity) and deliver one per table on
    the shared format/sink. Each table encodes as an ordinary single-table
    view, so encoders stay unchanged (INV-2)."""
    if is_stream_sink(spec.sink.sink):
        raise ValueError(
            "multi-table specs produce whole-file output per table; streaming "
            "sinks (http/kafka/tcp/udp) don't support multi-table yet"
        )

    tables = generate_tables(spec)
    cols_by = {spec.name: spec.columns, **{t.name: t.columns for t in spec.tables}}
    ext = get_extension(spec.output.format)
    encoder = get_encoder(spec.output.format)
    sink = get_sink(spec.sink.sink)

    base = spec.sink.options.get("path")
    base_dir = Path(base).parent if base else Path(".")

    receipts = []
    for tname, rows in tables.items():
        view = spec.model_copy(update={
            "name": tname,
            "columns": cols_by[tname],
            "tables": None,
            "sink": spec.sink.model_copy(update={
                "options": {**spec.sink.options, "path": str(base_dir / f"{tname}{ext}")}
            }),
        })
        payload = encoder(view, rows)
        receipts.append(sink(view, payload))

    return _egg(spec, "\n".join(receipts))


def _egg(spec: DatasetSpec, receipt: str) -> str:
    if spec.seed == _JENNY:
        receipt += "\n☎  867-5309 — thanks for the seed, Jenny."
    return receipt
