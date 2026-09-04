# ADR-0040: When it happened, and the difference between wrong and out of order

- **Status:** Accepted
- **Date:** 2026-09-04
- **Phase:** 9

## Context

`ADR-0038` and `ADR-0039` gave the answer key *where* and *what*. Neither gave it **when**, and time
is the one dimension where two entirely different faults wear the same word.

- A clock **set wrong** shifts every instant by the same amount. The feed stays ordered, its
  intervals stay exact, and nothing about the sequence betrays it.
- A feed **out of order** carries instants that are each individually correct and arrive in the
  wrong sequence. Every value passes any value-wise check.

The first is invisible to an ordering check. The second is invisible to a value check. They need
different instruments, and this ADR is about supplying the truth both of them need.

## Decisions

### D1. The key records when each tick really happened

`event_times` is epoch milliseconds per tick, in tick order — **one list, not one per entity**,
because every entity in a tick shares that tick's instant. That is not an implementation detail: it
is exactly why a reordering *within* a tick is unobservable in the time sequence, and a consumer of
this key should know it.

### D2. A declared offset is scene design; the key records it

`observer_clock_offset_ms` is how far each observer's clock is **declared** to be from the scene's,
derived from its `base_time` override.

Two feeds of one scene with clocks a couple of seconds apart is the case observers exist to produce
— the consuming repository's own scene notes say so — so pretending the offset is zero would make
every legitimate scene look faulty. A consumer is held to the clock **its feed declares**, exactly
as `ADR-0038` holds it to the position error its feed declares.

### D3. `clock_error_s` is the undeclared remainder, and is recorded nowhere

The fault channel, beside `misreports` and `frame_center_error_m`, and named the same way: a clock
nobody knows is wrong.

It is folded into the observer's effective `base_time` on the way to the encoder and never reaches
the key. A test asserts precisely that: a declared offset appears in the key, an undeclared error
does not. Recording it would hand a consumer the answer to the question the fixture asks.

### D4. The preset's error is one a person would actually make

`examples/skewed_clock.json` puts −25200 seconds on the CoT observer. The scene sits at 34.07 N,
−118.26 E — Los Angeles — and seven hours is PDT under a UTC label.

Not an arbitrary number. The point of a fault fixture is to rehearse a mistake somebody will make,
and putting local time on the wire is near the top of that list.

## Consequences

Measured through the consuming repository's scorer:

| scene | feed | worst error | out-of-order steps |
|---|---|---|---|
| correlated_multikind | cot-01 | **0 ms** | 0 |
| correlated_multikind | vmti-01 | **0 ms** | 0 |
| skewed_clock | vmti-01 | 0 ms | 0 |
| **skewed_clock** | **cot-01** | **25,200,000 ms** | **0** |

Honest feeds are exact to the millisecond. The skewed one is out by seven hours **and perfectly
ordered** — which is the finding, not a detail. A gate that asked only whether a feed's instants
marched forward would report it healthy, and the consuming repository asserts that the skewed feed
stays ordered precisely so the fixture keeps demonstrating it.

**And every older gate is green on it**: 72 of 72 pairs, positions inside their declared error,
speed and course inside tolerance, zero decode refusals.

**What this does not do.** It does not ship a fixture that is out of order. Every entity in a tick
shares that tick's instant, so a within-tick reordering is invisible in the time sequence, and an
across-tick one changes which entities a presentation cycle contains — which breaks pairing and
would make the fixture prove something other than what it claims. The consuming repository's
ordering check is mutation-verified instead, by reversing a feed, so it is a check known to work
rather than one that has merely never fired. A fixture that reorders *within* a tick would need the
emitter to stagger instants inside a frame, which is a real thing a sensor does and is not this
round.

**And the warning these fault channels now share.** `clock_error_s`, `misreports` and
`frame_center_error_m` all write a deliberately wrong measurement into otherwise-correct output.
Anything generated with any of them is a **test artifact for exercising a consumer's failure
handling**, never a demo dataset.
