# ADR-0004: Seeded determinism, byte-for-byte

- Status: Accepted
- Owner: Karl
- Date: 2026-07-10

## Context
When a demo works, you need the exact same data on the demo box next week.

## Decision
One optional `seed` on the spec. All entropy flows from a single
`random.Random(seed)` and a Faker instance seeded from the same value,
threaded through `GenContext`. Same spec + same seed = identical bytes.

## Consequences
- No `random` module globals, no time-derived entropy, anywhere (INV-3).
- `test_seed_determinism_is_byte_for_byte` is the permanent tripwire.
- Parallel generation (future perf work) must partition the RNG
  deterministically (e.g. per-row child seeds), not share one stream.
