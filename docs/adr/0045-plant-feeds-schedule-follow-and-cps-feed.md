# ADR-0045: Plant feeds — a schedule, a first-order lag, and the `cps_feed` lines

- Status: Proposed
- Owner: Karl (implemented by Claude Code)
- Date: 2026-10-21
- Builds on: ADR-0009 (stateful entities), ADR-0007 (streaming sinks), ADR-0004 (seeded determinism)

## Context
The consuming repository (`sidechain_adapters`, ADR-0082 there) proves its protocol tap against
one authored night. It needs more sites, each reproducible and with a known answer, and a site
is a day of a plant's behaviour: a defrost that runs on a clock, a setpoint a supervisor rewrites
twice a day, a case temperature that lags both. Chaff's updaters were a random walk (`drift`), a
random-weighted state machine (`lifecycle`) and a heading walk (`movement`): none follows a clock,
and a temperature does not random-walk. Its formats were flat, and the feed the consumer reads is
nested JSON (`cps-feed/1`).

## Decision
1. **`schedule` updater.** Sets a column from the time of day, UTC: windows (`for_minutes`) that
   return to a default, and points that hold until the next. Optionally flags the tick an entry
   starts (`fired_column`). Pure over the tick, `base_time` and `interval_seconds`; no rng.
   Those two params repeat the output options of the same names, because an updater cannot see
   the output; a spec states them once per updater.
2. **`follow` updater.** A first-order lag toward a target (a number or another column), with an
   optional lift while a column is truthy and optional noise from `ctx.rng`.
3. **`cps_feed` format.** One line per mapped column per snapshot (a `sample`), plus a `write`
   line when a mapping's `fire_column` is truthy. Per-record, so it streams over tcp/udp.
   Time comes from the shared `_timing`. It carries no protocol bytes: the consumer encodes
   those with the encoder its decoders are pinned to, so there is one BACnet encoder, not two.
4. **A write line carries no `unit`.** The wire does not say it and the consumer's decoder adds
   the one its site table knows, so a generated write that named one would not round-trip.

## Consequences
- `examples/cps_case_feed.json` is the first plant preset. The consumer commits its output as
  `corpus-cps/chaff-case-07` and tests that it survives its encoder and decoder.
- A different site is a different spec and seed. A fleet is `entity.count` (the consumer's site
  table must declare each equipment).
- **Not here:** a historian's deadband (a stateful compressor does not fit a pure per-record
  encoder; it needs its own ADR, likely as a whole-run post-pass format), faults on the generated
  feed (drops, bursts and reordering are the consumer's player, by plan), and an answer key for
  the learner.
