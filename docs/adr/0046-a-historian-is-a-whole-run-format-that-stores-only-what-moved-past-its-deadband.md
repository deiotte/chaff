# ADR-0046: A historian is a whole-run format that stores only what moved past its deadband

- Status: Proposed
- Owner: Karl (implemented by Claude Code)
- Date: 2026-10-21
- Builds on: ADR-0045 (plant feeds), ADR-0033 and ADR-0038 (an answer key is an evaluation
  artifact, kept apart from the feed), ADR-0004 (seeded determinism), ADR-0009 (stateful entities)
- Consumer: `sidechain_adapters` `decisions/0078` (the learner reads a historian's exceptions) and
  `decisions/0082` (a generated site)

## Context
The consuming repository's learner proposes a band for a setpoint from thirty days of what a
historian stored. A historian does not store samples, it stores **exceptions**: a value is kept
only when it moved by more than the tag's deadband from the last value kept. The learner has to
widen its band by that deadband, because the history cannot show where the true value sat between
exceptions, and it refuses a tag whose deadband was not recorded.

Its only test history is one hand-authored month, in Rust. To judge the learner on many sites,
with drift of a known size, chaff has to generate a historian's output. ADR-0045 said this is not
the same problem as the plant feed and left it: a feed line stands alone, but whether a value is an
exception depends on every value stored before it. A compressor holds state across records.

What the learner reads (`cps-history/1`, strict JSON lines) has three kinds:
- `sample`: equipment, tag, instant, and the value the historian stored (held from that instant).
- `context`: the mode in force on a scope over `[from_ms, to_ms)`.
- `flag`: a window the engine raised a finding for on a scope, with the flag's name. The learner cuts
  learning in a flagged window (a slow drift must not train the band).

## Decision
1. **A new whole-file encoder, `cps_history`.** Registered with `@encoder`, not `@record_encoder`.
   chaff already has whole-file formats (xlsx, parquet) that see every row and cannot stream, and an
   encoder is `(spec, rows) -> bytes`, pure. A compressor over the whole run fits that seam with no
   change to the engine (INV-4). Selecting it with a streaming sink fails at once and says why,
   as every whole-file format does.
2. **The compressor is the plain exception deadband.** Per equipment and tag, walking rows in tick
   order: the first value is stored; a later value is stored when `abs(value - last_stored)` is
   **strictly greater** than the deadband, so a move of exactly the deadband is not stored. Values
   are rounded to the column's declared precision before the comparison, so the rule is stated in
   the numbers the file carries. No swinging door and no max-time heartbeat by default: the
   learner's reading of a sample is "held from that instant", and a heartbeat that restates a
   value would add zero-step pairs it would then count. A `max_gap_seconds` option may be added
   later by an ADR that says what the learner does with it.
3. **The deadband is required, per tag.** `tags: {column: {"tag", "equipment_column"?, "deadband"}}`.
   A tag without a finite, non-negative `deadband` is refused. This repeats the learner's own rule
   (a tag listed without one does not parse) so a generated history can never be one the learner
   would refuse for a reason the generator hid.
4. **Contexts come from a column.** `context: {"column": ..., "values": {"0": "run", "1": "defrost"}}`
   (or the column's strings as they are). Consecutive ticks with one mode become one span
   `[from, to)` on the equipment's scope; the last span ends one interval after the last tick. Spans
   are contiguous and never overlap, which is what the learner requires of a scope.
5. **Flags come from columns the spec authors.** `flags: [{"column": ..., "name": ...}]`: every
   maximal run of ticks where the column is truthy is one `flag` window. A flag here is **the
   generator's knowledge of when a finding should stand**, not an engine's finding. It lets the
   learner's freeze be tested against a window of known size. That the real engine raises such a
   flag on such a drift is the consumer's shadow run, and this format does not claim it.
6. **A drift needs no new updater.** An attacker walking a setpoint is a `schedule` updater with
   many points on the setpoint column, and the flag column is a second `schedule`. The preset
   spells it out.
7. **Pure and deterministic.** No rng, no clock: time comes from the shared `_timing`, lines are
   written with sorted keys and no spaces, non-finite values are refused (INV-3). Same spec and
   seed give the same bytes.
8. **The preset.** `examples/cps_case_history.json`: one case, thirty days at a five-minute tick,
   the setpoint written twice a day, a defrost on a clock, a drift on one day with its flag
   window, a deadband of 0.5. The consumer commits its output and its learner runs over it.

## Alternatives rejected
- **A streaming record encoder that keeps per-tag state.** An encoder holds no state between
  calls (INV-2), a sink may restart it, and the last stored value cannot be flushed at the end.
- **Compress inside an updater, so rows are dropped.** The rows are the plant's state at each tick,
  and every other format of the same spec needs all of them. A compressed series is a view of the
  plant, not the plant.
- **Compress in the consumer** (a converter beside the learner, from `cps-feed/1` samples to
  `cps-history/1`). This is the closest alternative and it reuses the day ADR-0045 already
  generates. It is rejected because the learner would then be judged against a compressor its own
  repository wrote, so an agreement between the two proves less than two independent
  implementations do, and because modes and flags do not exist in `cps-feed/1` and would still need
  a generator. If the owner prefers it, items 3 to 5 carry over unchanged.

## Consequences
- The learner can be judged on many seeded months, each with a drift whose size the spec states.
- **Not here:** the learner's **answer key**, the band and the frozen windows it should arrive at.
  It follows ADR-0033 and ADR-0038: a separate evaluation artifact, never part of the history. The
  spec holds everything it needs (the values before compression, the flag windows). It is its own
  ADR once the format exists.
- **Not here:** a historian for process values the learner does not read. `sidechain_adapters`
  `decisions/0078` D6 leaves learned physics out.
- The consuming repository needs its own record for committing the output and a learner
  configuration naming the generated tag with its deadband. That is its ADR, not this one.
- Whole-file formats do not stream, so a history is generated to a file and not to a socket. That is
  right for a history.

## Tests the build owes
Seed determinism byte for byte; a move of exactly the deadband is not stored and one just over is;
the first value is stored; a missing or negative deadband is refused; a non-finite value is
refused; contexts are contiguous and cover the run; a flag column yields one window per run;
a streaming sink is refused with a clear message; and the preset's output is read back by the
consumer's reader in the consumer's repository.
