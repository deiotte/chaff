# ADR-0032: The CoT encoder emits absence as absence

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

The CoT encoder (ADR-0009, Phase 3D) was written to prove the format worked:
uid, type, timestamps, a lat/lon point, a callsign. It did that. What nobody
had checked was whether the events it produces are any *use* to a consumer
that actually reads CoT.

They were measured against one — a strict CoT 2.0 reader with a documented
field-by-field mapping. All 480 events from `examples/cot_tracks.json`
decoded with no refusals and no anomalies, which is the good news. The
reader reached **6 of the 12** fields its mapping defines, and one of those
six was wrong:

- `<track speed course>`, `<status battery>` and `<precisionlocation>` were
  never emitted at all. The preset generated a `heading` column and the
  encoder dropped it on the floor.
- `ce` and `le` were hardcoded to the literal `9999999.0`, so every event
  claimed its positional error was unknown.
- `hae` was hardcoded to `0.0` whenever the spec declared no height column.

That last one is the defect, and it is not "a missing field". `9999999` is
what CoT reserves to mean *I do not know*; a consumer maps it back to no
value. `0.0` is a number, and a consumer reads it as **a measurement of zero
height** — a fabricated observation, indistinguishable from a real one, in
data whose entire purpose is to look real. The encoder was already doing the
right thing for `ce` and `le` and the wrong thing for `hae`, one field over.

## Decision

### 1. Every optional numeric is the row's value or the sentinel, never a default

`hae`, `ce` and `le` come from columns (`hae_column`, `ce_column`,
`le_column`, auto-detected as `hae`/`ce`/`le`) and fall back to
`UNKNOWN_VALUE_SENTINEL` — never to zero. A value that is present but
unusable (unparseable, NaN, infinite) also becomes the sentinel: knowing we
do not have it is exactly what absence means, and a NaN would otherwise
format as the literal `nan`, which is not a number any CoT reader accepts.

Elements work the same way by being conditional. A `<track>` with neither
speed nor course, or a `<status>` with no battery, would assert the reporter
said something about itself when it said nothing, so neither is emitted.

### 2. `<takv platform>` defaults to `chaff`

Every event this encoder produces says, in its own provenance field, that a
synthetic generator made it. Opt-out (set another platform, or `""` to drop
the element) rather than opt-in.

This is the cheap half of a real problem. chaff's whole job is output
convincing enough to work with, and CoT is a format that flows into systems
that make decisions. A consumer told out-of-band which feed is synthetic
will eventually not be told — the note is lost, the demo box gets
re-purposed, the capture gets replayed a year later by someone who wasn't
there. A marker inside the bytes travels with the bytes.

Deliberately **not** the running chaff version, which would make output vary
by install and break INV-3 across releases. The version is available as
`takv_version` for anyone who wants it in a fixed spec.

### 3. chaff does not emit deliberately malformed CoT

Considered and rejected. Exercising a consumer's *refusal* paths — truncated
documents, missing uids, unparsable timestamps — is worth doing, and it is
not chaff's job: that is a fuzzer or a hand-authored fixture, and INV-5 says
chaff generates demo data. An encoder with a "now emit something invalid"
switch is one wrong option away from a spec that silently produces garbage,
and the guarantee people rely on here is that chaff's output is *well-formed
by construction*.

## Consequences

All 12 mapped fields are now reachable, and the preset exercises every one.

**Existing specs change bytes.** Any spec without a `hae` column now emits
`hae="9999999.0"` where it used to emit `hae="0.0"`. That is the fix, not a
regression — but it is a visible diff for anyone with a stored golden, and
`examples/cot_tracks.json` is one of them. INV-3 is unaffected: same spec and
seed still give the same bytes, on the new behaviour.

The encoder stays a pure `(spec, rows) -> bytes` function with no I/O and no
wall-clock read, so INV-2 and INV-3 hold, and it remains stdlib-only.
