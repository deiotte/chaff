# ADR-0039: Kinematics in the answer key, and a unit fault to test them with

- **Status:** Accepted
- **Date:** 2026-09-04
- **Phase:** 9

## Context

`ADR-0038` gave the answer key geometry, and closed by naming what it still could not do:

> Only positions. A feed reporting every position perfectly and every velocity backwards is still
> invisible: nothing scores speed, course, or classification confidence against truth.

That gap is not a smaller version of the last one. A displaced parent breaks position *and*
pairing, so two gates could eventually see it. A value fault breaks **neither**. A sensor that
measures metres per second and writes knots under the same name moves no coordinate, breaks no
pairing, refuses no packet, and produces values that are finite, in range, and entirely plausible
— a vehicle at 4 m/s simply reads as one at 8.

## Decisions

### D1. The key carries kinematics beside positions

`kinematics` maps each entity to `[speed_m_s, course_deg]` per tick, read exactly as `positions`
is. The pairing matters: a feed can put a thing in precisely the right place and still say it is
travelling the wrong way at twice the speed, and nothing about the position says so.

**An absent measurement is `null`, never zero.** A scene that carries no speed column yields
`null` in that slot. Scoring a consumer against a fabricated zero would mark it wrong for being
right, and a zero speed is a real claim that some feed will legitimately make.

### D2. `ObserverSpec.misreports` scales a column, and is named as a fault

`{"speed": 1.9438}` is a sensor measuring metres per second and putting knots on the wire while
still calling them metres per second.

Named `misreports` rather than `scales` or `units` because that is what it is. It sits beside
`reports` — what the sensor says about itself — as its opposite: what the sensor says wrongly
about the scene.

It applies **last**, after `reports`, so it lands on whatever the row ended up carrying. A column
that holds no number is left alone rather than coerced: a scale factor is a claim about a
measurement, and a column with no measurement has nothing to be wrong about.

### D3. Position keeps its own channel

`misreports` does not touch position, and a test asserts it. `position_error_m` is the positional
fault channel, and a value fault that also moved things would be caught by `ADR-0038`'s gate —
proving nothing about attribute scoring. The fixture is only worth having while it is invisible to
every older gate.

### D4. The preset's fault is on one attribute of one feed

`examples/mislabelled_units.json` puts the knots fault on the **CoT** observer's speed and leaves
its course alone.

One attribute wrong and its neighbour right, in the same feed, in the same events, is what shows a
consumer scores per attribute rather than judging a feed as a whole. A fault that spoiled
everything a feed said would be indistinguishable from simply refusing the feed.

## Consequences

Measured through the consuming repository's scorer, on the shipped scenes:

| feed | attribute | worst difference |
|---|---|---|
| cot-01 (honest) | `entity.speed_m_s` | **0.000** m/s |
| cot-01 (honest) | `entity.course_deg` | 0.050° |
| vmti-01 (honest) | `entity.speed_m_s` | 0.073 m/s |
| **cot-01 (knots)** | **`entity.speed_m_s`** | **3.940 m/s** |
| cot-01 (knots) | `entity.course_deg` | 0.050° |

The honest numbers are pure encoding: CoT writes speed as a decimal string at two places, so it is
exact; it writes course at one place, so half a step is 0.05°; VMTI carries velocity as three
`IMAPB(-900, 900, 2)` components at 0.0625 m/s each, and two of them contribute to a ground speed.

**And the point of the whole round, in the same fixture:** pairing is 72 of 72 with none wrong,
both feeds' positions are inside their declared error, and there is not one decode refusal.
**Every gate that predates this ADR is green.** The consuming repository asserts that too, rather
than merely claiming it — a fixture that broke an older gate would prove nothing new and is
refused.

**What this does not cover.** Speed and course. A feed can still be wrong about classification
confidence, target priority, detection status, or any of the fifteen other attributes the VMTI
family carries, and nothing scores those. The key extends the same way it did here; each attribute
needs a truth column and a tolerance derived from its encoding.

**And a caveat this ADR adds rather than inherits.** `misreports` writes a deliberately wrong
measurement into otherwise-correct output, exactly as `frame_center_error_m` does. Anything
generated with it is a **test artifact for exercising a consumer's failure handling**, never a demo
dataset: a demo built on it shows plausible numbers that are quietly wrong, which is the precise
failure it exists to rehearse.
