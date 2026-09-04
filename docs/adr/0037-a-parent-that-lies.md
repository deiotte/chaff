# ADR-0037: A parent that lies about where it is looking

- **Status:** Accepted
- **Date:** 2026-09-04
- **Phase:** 9

## Context

`ADR-0036` gave chaff an ST 0601 parent frame and a way to **withhold** its frame centre. That
models a parent which is not conforming — ST 0903.6 Table 6 makes the centre Required — and a
consumer catches it easily, because the targets have nowhere to be and it can say so.

The harder fault is the one next to it: a parent that supplies a frame centre which is simply
**wrong**.

Measured before building anything, against the consuming decoder, at three magnitudes of error:

| declared error | packets | observations | refused | unresolved | attributes |
|---|---|---|---|---|---|
| 0 m | 40 | 240 | 0 | 0 | 18/21 |
| 250 m | 40 | 240 | 0 | 0 | 18/21 |
| 5000 m | 40 | 240 | 0 | 0 | 18/21 |

**Every number is identical.** A five-kilometre error produces a census indistinguishable from a
correct one, down to the confidence set. Nothing is malformed, every offset resolves, every
position is finite and in range, and all of them are five kilometres from where the targets are.

That is not a gap in a consumer. It is a property of the format: **a frame centre is a claim the
child has no way to check.** No adapter can detect it, because the information required to detect
it is not in the packets.

## Decisions

### D1. `frame_center_error_m` displaces the declared centre, and only the declared centre

The option states an error in metres, on a bearing given by
`frame_center_error_bearing_deg` (clockwise from north, default due east). The parent's items 23
and 24 carry the displaced centre; **the child's offsets are still computed against the true one.**

Same discipline `withhold_frame_center_column` follows, and for the same reason. Computing the
offsets from the declared centre would make the packet internally consistent and the error would
vanish — there would be nothing to detect, because the sensor would simply be reporting a scene
that had moved. The whole of the error must be the parent's, and it is only provably so if the
child bytes are identical either way. A test asserts exactly that.

### D2. Metres, not degrees, and the constant is shared with the engine

An error stated in degrees means different distances at different latitudes, and a scene author
comparing it against an observer's `position_error_m` would be comparing two things in different
units.

`_M_PER_DEG` is duplicated from `engine._M_PER_DEG` rather than imported, because `engine` imports
this package and the cycle would not resolve. A test asserts the two agree: if they drifted, a
scene stating both a parent error and an observer error in metres would mean two different
distances, and the one number a reader compares them by would be wrong.

### D3. The preset is a *scene*, because one feed cannot show anything

`examples/displaced_parent.json` is a correlated scene: six entities, one CoT observer, and one
embedded-VMTI observer whose parent is displaced 250 m.

A single-feed preset would have been pointless. There is nothing to see in one feed — that is the
finding, not a limitation of the fixture. The error is visible only from **outside**: against a
second sensor watching the same things, or against ground truth. 250 m is chosen against the
scene's own association gate of 25 m: ten times over, unambiguous, and not near enough to the
boundary that the result depends on the noise draw.

## Consequences

**Measured through the consuming repository's correlator**, the displaced scene produces:

| | honest parent | displaced 250 m |
|---|---|---|
| observations | 144 | 144 |
| adapt refusals | 0 | 0 |
| truth pairs | 72 | 72 |
| **pairs found** | **72** | **0** |
| pairs wrongly bound | 0 | 0 |

The feed is immaculate and the correlation is total loss. And the consumer **declines rather than
errs** — zero pairs bound wrongly, not one. It has no way to know the origin is wrong, and binding
across a 250 m gap would be far worse than binding nothing.

**What this generates that nothing else could.** Every previous preset produced a feed whose
faults were visible in the feed. This one produces a feed with no detectable fault at all, and puts
the fault in the *relationship* between two feeds — where the only instrument that can see it is a
consumer that fuses them.

**A warning that belongs with the option.** This writes a deliberately wrong measurement into
otherwise-correct output. Anything generated with `frame_center_error_m` set is a **test artifact
for exercising a consumer's failure handling**, and is not a demo dataset: a demo built on it would
show a map with everything quietly in the wrong place, which is the exact failure the option exists
to rehearse. The preset says so in its own description.

**What this does not do.** It does not give chaff a position answer key. The scene's `truth.json`
names which observer-side ids are the same entity, not where those entities were, so a consumer is
scored here on whether it *pairs* correctly rather than on how far its positions are from the
truth. Scoring absolute positional accuracy would need the answer key to carry geometry, and that
is a larger change than this one.
