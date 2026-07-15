# ADR-0016: Lazy generation + determinism-as-prefix

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-15

## Context
Every generation path materializes a finite `list` up front: `generate_rows`
returns `spec.rows` rows, `generate_entity_rows` returns `count × ticks`
snapshots. That's right for a file download, but it's the wrong shape for
*serving* a stream. Two problems:

1. **Memory.** A long feed has to fit in RAM before the first byte ships.
2. **Open-endedness.** "Serve up a data stream" (Phase 6) wants a feed that
   runs for a duration, or until a consumer disconnects — not a fixed count
   decided before the first record.

ADR-0007 foresaw this: *"the record-iterator seam makes lazy generation a
later drop-in (yield rows instead of returning a list) with no signature
change."* This ADR walks through that seam. It is the keystone the Phase 6
serve work (WebSocket, MQTT) sits on top of.

The wrinkle is INV-3. "Same spec + same seed = identical output, forever"
was easy when the row count was fixed. An unbounded — or duration-bounded —
stream has no fixed count, and a duration bound depends on wall-clock and
machine speed. Does streaming break the invariant?

## Decision
**Generation becomes lazy, and INV-3 is restated as determinism-*per-record*
(a deterministic prefix), not determinism-of-a-fixed-count.**

- One generation primitive, `_iter_table`, *yields* rows and accepts an
  `n_rows` that may be an int or `math.inf`. Both the eager list builders
  (`generate_rows`, `generate_tables`) and the new lazy iterators
  (`iter_rows`, `iter_entity_rows`, `iter_records`) run through it, so there
  is exactly one generation path and no chance of drift. `generate_rows(spec)`
  is now `list(iter_rows(spec))` in spirit and byte-for-byte identical.
- `iter_records(spec, limit=...)` is the lazy entry point. `limit=None` is
  the spec's natural length (unchanged); an int caps *or extends* to exactly
  that many records; `math.inf` streams unbounded.
- **The contract:** the i-th record depends only on the spec, its seed, and
  i — never on `limit`, `duration`, or the wall-clock. So any stream, of any
  length, is a deterministic *prefix* of the one infinite deterministic
  sequence that spec+seed defines. `list(iter_records(spec, limit=k))` equals
  the first `k` records of `iter_records(spec, limit=inf)`, always.

**Run-mode controls** live on `spec.sink.options`, alongside `rate`:
- `max_records` — cap (or extend) the total record count.
- `duration` — stop after N wall-clock seconds. Implemented as `time_limited`,
  a delivery-time wrapper next to `rate_limited`. A `duration` with no
  `max_records` generates unbounded (`limit=inf`) and lets the clock cut it.

## Consequences
- INV-3 holds, precisely stated: **record content is reproducible; record
  *count* under a `duration` bound is not** (it depends on machine speed).
  `test_seed_determinism_is_byte_for_byte` is untouched and still green;
  new tests assert prefix-equality across `limit` values.
- The eager API/CLI download paths are byte-for-byte unchanged — they still
  call `generate_records`/`generate_rows`, which still return the same lists.
- An entity spec with `limit=inf` is a genuine live feed: entities keep
  ticking (moving, transitioning) forever. This is the natural fixture for
  the Phase 6 WebSocket serve endpoint.
- `time_limited`/`max_records` are pure delivery-time bounds — they touch
  neither generation, the rng, nor payload bytes, so format ⟂ sink (INV-2)
  and seed determinism (INV-3) are both preserved.
- Multi-table specs still don't stream (whole-file per table); the engine
  routes them to `_run_multi` before the lazy path, unchanged.
