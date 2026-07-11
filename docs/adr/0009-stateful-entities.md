# ADR-0009: Stateful entities over time

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-11

## Context
The reserved `entity` seam (Phase 3) is for data that *evolves*: tracks
that move, orders that transition placed→shipped→delivered, sensors that
drift. Independent-row generation can't express state carried across time.
This is also the foundation the Cursor-on-Target encoder (3D) sits on — a
TAK feed is a stream of moving tracks.

## Decision
A general **entity-tick** model, opt-in via `DatasetSpec.entity`:

- `EntitySpec`: `count` entities, `ticks` time steps, `id_column` /
  `tick_column` naming, optional `id_pattern`, and a list of `updates`.
- Initial state comes from the spec's existing `columns` (generated once
  per entity at tick 0). No new "initial state" concept — reuse.
- **Updaters** are a new registry (INV-4): `@updater("id")` functions
  `(EntityContext, state, params) -> None` that mutate one entity's state
  in place. Built-ins: `movement` (lat/lon along a drifting heading),
  `lifecycle` (weighted state transitions), `drift` (sensor random walk).
  Adding an updater never edits the engine.
- **`EntityContext`** is distinct from `GenContext` (as
  `generators/AGENTS.md` reserved) so per-entity, per-tick state never
  leaks into stateless row generation.
- Output is one snapshot per `(tick, entity)` — `count × ticks` rows, **time
  ordered** (all entities at tick 0, then tick 1, …). `rows` is unused in
  entity mode and becomes optional.

## Consequences
- **Determinism (INV-3)**: one rng/faker seeded once, consumed in a fixed
  (entity, then tick) order; all updater randomness flows through
  `ctx.rng`. Same spec + seed = same evolution.
- **Composes with everything downstream**: entity rows are ordinary rows, so
  they encode and deliver through the existing pipeline — including
  **streaming sinks**, which makes a live tick-by-tick feed (HTTP/Kafka/TCP/
  UDP) work today. This is exactly the seam 3D (CoT → TAK) needs.
- **API**: `/preview` and `/generate` handle entity specs via a single
  `generate_records` dispatcher; request limits apply to `count × ticks`.
- Single-table and multi-table paths are untouched; the determinism
  tripwire stays green. Multi-table + entity together is out of scope (a
  spec is one or the other for now).
