# ADR-0008: Multi-table specs with foreign-key integrity

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-11

## Context
Realistic demo data is relational: customers have orders, orders have
lines. Phase 3 calls for multi-table specs where child rows reference real
parent keys. This is the first change to the spec contract and the engine
since Phase 1, so it must not disturb single-table generation (INV-3) and
must keep format ⟂ sink intact (INV-2).

## Decision
- **Spec shape.** A `TableSpec` (name, rows, columns) describes an
  additional table; `DatasetSpec.tables: Optional[list[TableSpec]]` holds
  them. The top-level name/columns/rows remain the **primary** table.
  Absent `tables` => a single-table spec, unchanged. Fully backward
  compatible; existing presets and the `test_seed_determinism` tripwire are
  untouched.
- **FK generator.** `fk` with params `{table, column}` draws a value from a
  parent table's column via `ctx.rng` (deterministic). `GenContext` gains an
  optional `tables` map of already-generated parents; it defaults to `None`,
  so single-table generation is byte-for-byte identical.
- **Ordering.** `generate_tables()` computes a deterministic topological
  order from FK references (ties break by declaration order) and generates
  parents before children, threading results into each child's
  `GenContext.tables`. Missing refs, self-refs, and cycles fail fast with
  clear errors.
- **Output.** Each table is encoded and delivered as an ordinary
  single-table view on the **shared** format/sink — so encoders and sinks
  need no changes (INV-2). The file sink writes one file per table
  (`<table><ext>`) in the configured path's directory.

## Consequences
- One rng/faker seeded once and consumed in a fixed table order → multi-table
  output is reproducible too (INV-3 extends cleanly).
- Whole-file, per-table delivery means **streaming sinks aren't supported
  for multi-table yet** (what would "one Kafka stream of three tables" mean?);
  `run()` raises a clear error. A future chunk can define per-table streams.
- The **API** (`/preview`, `/generate`) rejects multi-table specs (one HTTP
  request = one file); the CLI (`chaff generate`) is the multi-table path.
  A UI multi-table builder and a zip-download endpoint are follow-ups.
- The reserved `entity` seam (stateful entities, 3C) is unaffected and still
  open.
