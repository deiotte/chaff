# ADR-0043: Correlated foreign keys

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-10-12
- Issue: #77

## Context
`fk` (ADR-0008) copied only the parent's key, so a child row couldn't see
anything else about the parent it pointed at. In `case_management.json` that
produced records no real case system holds: docket entries dated before their
case was filed, hearings held by a judge who wasn't on the case, a magistrate
from another court. A tester loading that data logs those as defects in the
system under test, but chaff invented them. This is the multi-table analogue
of derived columns (ADR-0012) and geo anchors (ADR-0015): a cell that depends
on another cell, here one in the parent row.

## Decision
Three opt-in seams. None consumes entropy, so a spec that doesn't use them is
byte-identical to before (pinned by `test_plain_specs_are_byte_identical_to_before`).

1. **`fk` remembers its parent.** The chosen parent row is stored in
   `ctx.cache[<fk column>]`, the same per-row scratch ADR-0015 added.
2. **`parent` generator** copies a cell from that row:
   `{"via": "case_id", "column": "date_filed"}`. A null fk gives `None`.
3. **`fk` filters**: `where` (parent cell equals a literal) and `match`
   (parent cell equals a cell already generated in this row). Example: a
   referred magistrate is `{"where": {"bench": "Magistrate"}, "match":
   {"court": "court"}}`. Candidates keep table order, so `rng.choice` stays
   deterministic. No qualifying parent → `None`, not an error: a court with
   no magistrate is a real situation and seeded data will produce it.
4. **Row-bounded dates**: `date_between` / `timestamp_between` take
   `{"after": ref}` and `{"before": ref}`, where `ref` is an earlier column or
   `"<fk column>.<parent column>"` (read through the fk without a helper
   column). Null bounds leave the literal bound alone; a floor above the
   ceiling collapses to the floor.

**Engine change**, the only one: `_iter_table` creates one `memo` dict per
table and passes it as `GenContext.memo`. Filtered fks bucket their parent
once per `(table, filter)` there instead of rescanning per row (O(n+m), not
O(n·m), which matters for the load presets in #90). No generator id appears
in the engine (INV-4).

**Load-time validation** (`spec.py:_validate_parent_links`): `via` must be an
fk declared earlier; the copied column, `where`/`match` keys and dotted refs
must exist on the parent table; `match` and `after`/`before` must read cells
declared earlier. Mistakes fail at load with the column named.

## Consequences
- INV-1/INV-2 untouched; INV-3 holds (reads only, plus the same single
  `rng.choice` the fk always made); INV-4 holds (two generators changed,
  one added, the engine gains a scratch dict).
- Helper columns are still visible when a value has to exist in the row to be
  compared (e.g. `case_type` on docket entries, for the criminal-only
  filings rule). chaff has no hidden columns; that stays a known gap.
- `where` is equality only. Ranges and "not equal" go through a `derived`
  column on the parent (`bench` on judges) and an equality filter on it.

## Scoped out
- **Children-per-parent distributions** (docket entries per case following a
  power law). `fk` still picks parents uniformly. Worth a follow-up issue if
  a test needs realistic case "heaviness".
- Filters across more than one hop (a grandparent's cell) — chain `parent`
  columns instead.
