# ADR-0035: A frame is several targets, and the gap between two counts is the point

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-04

## Context

ADR-0034 §4 emitted one VMTI packet per row — one target per frame — and said
so plainly: batching a tick's targets into one frame is a real thing a sensor
does, is not expressible on the per-record streaming seam, and was deferred
rather than half-built.

Deferring it left a specific hole, and it is not "packets are bigger than they
should be". ST 0903.6 carries two counts per frame: `totalTargetsDetected`
(item 5) and `numTargetsReported` (item 6). The **gap between them** is what
the sensor chose not to tell you, and a consumer reads that ratio as feed
health — a source quietly reporting less of what it sees looks exactly like a
scene getting calmer, which `FAILURE-MODES` §1 calls out as the dangerous
direction.

With one target per packet those two counts are 1 and 1 forever. The ratio
cannot move, so a consumer's culling assessment could never be exercised by
generated data — not partly, not weakly, but *at all*.

## Decision

### 1. `frame_column` groups consecutive rows into one packet

Rows sharing a value in that column, **consecutively**, become one frame. A
scene of six things over forty ticks is forty packets of six rather than 240
of one.

Grouping is by consecutive run and never by sorting. Rows arrive in the order
the engine generated them — time-ordered for an entity spec — and reordering
here would put the encoder in the business of deciding what happened when. A
column that alternates simply produces more frames, which is the honest
reading of it and is what a test pins.

Absent the option, every row is its own frame: the previous behaviour, byte
for byte.

### 2. `total_detected_column` lets a frame say what it did not report

Read from the frame's first row, since chaff has no frame-level data model and
every value lives on a row. Absent it, detected equals reported — this encoder
culls nothing, and claiming otherwise would fake the very number the gap
exists to carry.

**Never below the count actually carried.** A sensor cannot truthfully report
more targets than it detected, and emitting that would be an impossible packet
rather than a useful one — the same line ADR-0032 §3 draws about malformed
output.

### 3. A framed spec refuses to stream, loudly

A frame is several records; the per-record seam delivers one, with no way to
know whether the next belongs beside it. The per-record encoder raises with a
message naming the option and both ways out.

The alternative — silently emitting one-target packets on the streaming path —
is worse than a refusal in a way worth spelling out: it contradicts the spec's
own framing, and it does so by quietly restoring the exact 1:1 ratio the spec
was written to avoid. The failure would look like working software.

## Consequences

`examples/vmti_frames.json` is the worked case: 40 frames of 6, with a
`report_ratio` that moves between **0.188 and 1.000** across the run, and a
consumer raising its culling flag on **38 of 40** frames — both states present,
so the flag is demonstrably data-driven rather than constant.

Verified end to end against a real consumer: 40 packets, 0 refusals, 240
observations, 6 distinct entity refs, 21 of 21 declared attributes. The framed
file is also a third the size of the unframed one, because the frame-level
items are stated once instead of six times — incidental, but it is what a real
feed's bandwidth looks like.

**A trap worth recording, because it produced a plausible wrong answer.** The
first version of the preset generated `total_detected` as an ordinary column
and every frame reported the same value, pinning the ratio at a constant
0.429. Nothing was broken: an entity spec generates non-updated columns once,
at tick 0, so the column was frozen per entity and the frame's first row was
always the same entity. A frame-level quantity that must vary over time needs
an updater, and the preset uses `drift`. The number looked fine; it was simply
never going to move.

**Still not covered.** Embedded VMTI — an ST 0601 parent frame carrying a VMTI
set as Item 74 — remains unbuilt, and is the last shape of this format chaff
cannot produce.
