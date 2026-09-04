# ADR-0033: A scene, its observers, and the answer key

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-04

## Context

ADR-0032 made chaff's CoT output reach every field a real consumer maps. That
closed a fidelity gap and revealed a bigger one: **fidelity is a property of a
feed, and the interesting consumers reason over more than one.**

A cross-source correlator's whole job is deciding whether two reports, arriving
from different sensors under different identifiers, are the same real-world
object. Nothing chaff produced could ask it that question. One spec made one
feed, so a consumer with two inputs had one of them synthetic and the other
missing — and the code paths that exist only for the second input had never
run against generated data at all.

The generalisation is not "emit a second format". It is that chaff has been
conflating two different things:

- **the scene** — where things actually were, tick by tick; and
- **a feed** — one sensor's imperfect, self-interested account of it.

Entity specs (ADR-0009) modelled the scene and then emitted it *as if* it were
a feed, which works exactly as long as there is only one.

## Decision

### 1. `EntitySpec.observers` — several accounts of one scene

An observer renders every `(entity, tick)` snapshot into its own output file,
with its own identifiers, its own position error, and its own `reports` (what
the sensor claims about itself, such as its horizontal error). Format and sink
stay shared, so this is a property of the scene rather than a third axis
(INV-2), and no encoder or sink knows observers exist.

**Different ids are the point, not a detail.** Two observers naming one object
identically is the thing a correlating consumer is supposed to work out; hand
it matching ids and the question disappears.

### 2. Adding an observer never changes the scene

Each observer draws from a generator derived from `(seed, observer name)`, not
from the scene's own. Sharing it would make a thing's trajectory depend on how
many sensors happened to be watching it — wrong, and invisible until someone
diffed two runs. A test pins it.

### 3. Position error is bounded, not Gaussian

Uniform over a disc of `position_error_m`. A normal distribution is the more
faithful model and the wrong choice here: it has a tail, so a fixture built on
one asserts something merely *usually* true, and a consumer's gate radius would
be cleared on nine runs in ten. Bounded error makes the worst case arithmetic —
two observers disagree by at most the sum of their radii — so a downstream test
can state its expectation exactly rather than probabilistically.

### 4. The scene ships its own answer key

`scene_truth()` writes which observer-side ids are the same entity, beside the
feeds and never inside one.

This is the part that changes what a consumer can be held to. Without it you
can watch a correlator pair two tracks but not tell whether it paired the
*right* two — and **a false pairing looks exactly like a true one**. With it, a
gate can count pairings found, pairings missed, and pairings invented, which is
the failure an ambiguity floor exists to prevent and which nothing was
measuring.

**It is an evaluation artifact and never part of a feed.** No sensor emits it.
Anything reading it is a test harness; a consumer handed it has been given the
answer to the question it exists to answer.

### 5. A colliding id scheme is refused, not merged

`TRUTH-##` is a hundred names, and eight entities drawn from it collide about a
quarter of the time. The collision is silent everywhere that matters — the
feeds look fine — and surfaces only as an answer key that has quietly merged
two things into one. Scoring against that marks a correct refusal wrong and an
incorrect pairing right, so `scene_truth` raises instead. Found by a test that
expected four entities and got three.

## Consequences

chaff can now produce the input a multi-source consumer needs, and the input a
*scorer* of one needs. `examples/correlated_scene.json` is the worked case: two
feeds over eight moving things, with kinematics and error radii chosen against
a real consumer's declared gate radius and speed bound.

**Choosing those numbers is now part of authoring a scene.** A scene whose
things move faster than a consumer believes anything can move will have every
pairing contradicted as impossible travel — correct behaviour by the consumer,
and an hour of debugging the wrong component. The preset documents its own
arithmetic for that reason.

**Known gap, inherited rather than introduced.** An updater that writes a new
key into entity state (`movement` writes `heading`) creates a column the spec
never declared, and a column-oriented encoder refuses the row. `observers`
declares its own `reports` keys on the view for exactly this reason, but the
updater case predates this ADR and is unchanged by it: declare such a column in
the spec. Worth closing generally, and not here.

**What this does not model.** Uniform error inside a radius is not what a real
sensor does — no bias, no dropout, no error correlated between neighbouring
ticks, no missed detections. A consumer that scores perfectly against these
feeds has demonstrated its logic is right, not that it will perform at a site.
