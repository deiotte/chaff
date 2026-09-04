# ADR-0036: Embedded VMTI is a second format, not an option on the first

- **Status:** Accepted
- **Date:** 2026-09-04
- **Phase:** 9

## Context

`ADR-0034` gave chaff a MISB ST 0903.6 encoder and `ADR-0035` made its packets
carry several targets each. Both write the **standalone** framing: the VMTI
Local Set with its own SMPTE 336 Universal Label, targets at absolute
coordinates.

That is not how the standard is usually deployed. A VMTI set normally travels
*inside* a MISB ST 0601 UAS Datalink frame, as **Item 74**, and its targets are
written as **offsets** from the frame centre the parent declares — ST 0903.6
§10.2.2.11 says `targetLocationOffsetLat` *"adds to"* Frame Center Latitude
(ST 0601 Item 23) to give the target's latitude.

The consequence is the interesting part. **An embedded child is only half a
position.** The other half lives in a different Local Set, and a consumer that
cannot pair them has targets it can describe in every respect except where they
are. Nothing about either set is malformed when that happens; both parse
cleanly. The fault lives in the relationship between them.

chaff could not write a parent frame at all, so nothing it generated exercised
any of this.

## Decisions

### D1. A separate registered format, `klv0601`

`formats/AGENTS.md` says variants are options on one encoder *"until they stop
sharing structure, then split with an ADR"*. This is that ADR, and the test it
fails is specific: **the Universal Label differs**, so a decoder built for one
refuses the other outright. That is not a dialect. A `.klv` file whose format
nobody can name without reading its first sixteen bytes is worse than two
format ids.

Code sharing is achieved by sharing *functions*, not by sharing a registration.
`klv.local_set()` builds the item body and `klv.standalone()` wraps it, so the
standalone and embedded framings cannot drift apart: there is one encoder for
the child and two ways of framing it.

### D2. Offsets replace the absolute location; never both

A target written for an embedded parent carries items 10 and 11 (and item 12,
height, which is absolute and needs no parent). It does **not** also carry item
17.

Emitting both would be legal and useless. A consumer that sees item 17 prefers
it — resolving an offset stacks the parent's frame-centre error on top of the
child's — so the offsets would be dead weight and the embedded path would go
untested by the very packets written to exercise it.

### D3. A frame centre is required, and refused loudly when absent

Defaulting to `(0, 0)` would place an entire scene in the Gulf of Guinea, and
it would look exactly like data. A `klv0601` spec without `frame_center_lat`
and `frame_center_lon` raises.

### D4. `withhold_frame_center_column` writes a deliberately non-conforming parent

ST 0903.6 Table 6 makes Frame Center Latitude and Longitude **Required** parent
metadata. This option writes frames that omit them anyway.

That is the point. A consumer's handling of a non-conforming parent cannot be
exercised with conforming input, and "the parent stopped supplying an origin"
is one of exactly two faults that leave a well-formed child with nowhere to be.
Withholding changes the **parent only** — the offsets are still computed
against the true frame centre — so the child bytes of a resolvable frame and an
unresolvable one are byte-identical and the difference is provably the parent's.

### D5. Halves round away from zero

Python's built-in `round` is banker's rounding: `round(0.5)` is `0` and
`round(2.5)` is `2`. Every published ST 0601 mapping, and the decoder these
bytes are checked against, rounds halves *away* from zero.

Using the built-in would agree on every value except the exact halves — rare
enough to survive every test written by hand, and silent when it happens.
`_round_half_away_from_zero` exists for that one case and is tested against it.

## Consequences

**A new preset, `vmti_embedded`.** The same scene as `vmti_frames` — 6 targets
over 40 frames — differing in framing alone. A `lifecycle` updater flips the
parent between declaring its frame centre and withholding it.

**Verified against the consuming implementation, not against a reading of it.**
The angle mappings, the elevation mapping and a whole packet including its
checksum are pinned byte-for-byte to output printed by the shipped
`st0601::build::UasBuilder`. Decoded end to end, the preset produces 240
observations from 40 parent frames with **zero refusals**, and its resolved
positions land within one quantum of the coordinates that generated them.

**A property worth knowing: embedded positions are coarser.** Offsets use
`IMAPB(-19.2, 19.2, 3)`, a quantum of ~7.6e-6° or about 0.85 m. Absolute item 17
uses `IMAPB(-90, 90, 4)`, about 6 mm. Embedded framing therefore costs roughly
140× in positional resolution — irrelevant beside a typical association gate,
and worth stating rather than discovering.

**A trap avoided, and it nearly landed.** `lifecycle` is the natural way to vary
the withholding per frame, and it holds state *names*. Every non-empty string is
truthy, so a plain `bool()` would have read the `declared` state as withheld and
produced a file with no frame centre anywhere — decoding perfectly, every target
unresolved, and no error to notice. The false values are named explicitly.

**What this does not do.** chaff writes six ST 0601 items: the three ST 0903.6
Table 6 requires, plus elevation, version, and the child. ST 0601 has well over
a hundred. This is not a UAS Datalink encoder and should not grow into one —
what it emits is the parent metadata an embedded VMTI child needs, and the
scope is Table 6 rather than ST 0601 itself.
