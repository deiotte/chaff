# ADR-0038: An answer key with geometry

- **Status:** Accepted
- **Date:** 2026-09-04
- **Phase:** 9

## Context

`ADR-0033` gave a scene an answer key naming **which observer-side ids are the same entity**, and
`ADR-0037` closed by saying what that key could not do:

> It does not give chaff a position answer key. [...] a consumer is scored here on whether it
> *pairs* correctly rather than on how far its positions are from the truth.

That gap has a shape, and `ADR-0037` measured it. A parent frame declaring the wrong frame centre
displaces every target in a feed by the same vector. Relative geometry survives untouched, so
**every pairing survives too** — the consuming repository found all 72 pairs once its association
gate was widened past the displacement, with none bound wrongly. An identity-only key cannot see
that, because nothing about identity is wrong.

The information needed is not in the feed and not in the pairing. It is the distance between what
the consumer reported and where the thing actually was, and only the emitter knows the second half.

## Decisions

### D1. The key carries the scene's geometry, per entity per tick

`positions` maps each entity's own id to `[lat, lon]` per tick, in tick order. Paired with
`identities`, that turns any decoded position into a distance from the truth.

It is the **scene's** geometry and never an observer's. An observer's account is displaced within
its own error radius, and scoring one displaced account against another would measure the
difference between two guesses rather than the distance from either to the truth. A test asserts
that a zero-error observer matches the key exactly and a noisy one does not.

### D2. The key also carries what each observer *claims*

`observer_error_m` states each observer's `position_error_m`.

This is what makes the bound the emitter's own claim rather than a number invented by whoever reads
the file. A consumer is not held to an arbitrary threshold; it is held to **the accuracy the feed
asserted about itself**, which is the only bound that is fair and the only one that stays correct
when a scene's observers change.

### D3. The bound is exact, because the displacement is bounded

`ADR-0033` chose uniform-in-a-disc displacement over Gaussian precisely so that *"a fixture's
expectations are exact rather than probabilistic"*. That choice pays off here: **every** report
lands inside the radius, so a gate can assert a maximum rather than a percentile. A Gaussian tail
would have made this a statistical test that passes on most runs.

## Consequences

Measured through the consuming repository's scorer, across every scene it holds:

| scene | feed | declares | worst report |
|---|---|---|---|
| correlated_scene | cot-01 | 6 m | 5.95 m |
| correlated_scene | cot-02 | 12 m | 11.95 m |
| correlated_multikind | cot-01 | 6 m | 5.95 m |
| correlated_multikind | vmti-01 | 12 m | 11.93 m |
| displaced_parent | cot-01 | 6 m | 5.95 m |
| **displaced_parent** | **vmti-01** | **12 m** | **260.53 m** |

Every honest feed lands just *under* its declared radius, which is what uniform-in-a-disc predicts
and is the sign the bound is tight rather than padded.

**The displaced parent is now caught from a single feed**, twenty times over its own claim, with no
second sensor and no correlator involved. That is the gap `ADR-0037` named, closed.

**What the key still is not.** It is an evaluation artifact and reaches nothing but a harness. The
`ADR-0033` rule is unchanged and now matters more: a consumer handed this file has been given the
answer to the question it exists to answer, and one handed the geometry could resolve every
position perfectly while decoding nothing.

**What this does not cover.** Only positions. A scene's key says nothing about the *other*
attributes a feed carries — speed, course, classification confidence — so a feed reporting every
position perfectly and every velocity backwards is still invisible here. Extending the key to those
is the same idea again and is not this ADR.
