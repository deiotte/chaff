# ADR-0021: Entity and related-table editors in the UI

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

[ADR-0020](0020-whole-spec-round-trip.md) stopped the UI from silently
dropping `entity` (ADR-0009) and `tables` (ADR-0008), and gave each a
read-only panel showing what was attached with a Remove button. That fixed
the data-loss defect, and it deliberately stopped short of editing:

> Read-only by design: a full entity/table editor is a larger piece of work,
> but Joe must be able to see why he is getting 300 rows […] Carrying is the
> invariant, editing is a feature.

The feature half was still missing. A user could load `moving_tracks`, see
"10 entities × 30 ticks", and remove it — but could not change it to 5 × 60,
add a `drift` rule, or build a time series from nothing. Same for related
tables. Authoring either mode still meant hand-writing JSON and using the
CLI, which is exactly the "ask an engineer" the North Star rules out. Two of
chaff's strongest demo features were readable but not writable.

## Decision

**1. Both modes get a real editor, in the same page, in the same style.**
No modal, no separate screen: an "Add to this dataset" bar under the primary
table's Columns, and the editors render inline beneath it. Placement is
deliberate — the primary table's own columns come first, then what's related
to it, so the page reads in the order the data is shaped.

- **Entity editor:** count, ticks, id column, id pattern, tick column, plus a
  list of per-tick update rules. A live "N rows (entities × ticks)" note sits
  under the numbers, because the relationship between the inputs and the
  output size is the thing people get wrong.
- **Related-table editor:** one card per table with a name, a row count, and
  its own full column editor — the same `addColumn` widget the primary table
  uses, so every generator, the params examples and the derived-formula
  builder all work inside a related table with no second implementation.

**2. Update rules come from the registry, params included.** `list_updaters()`
gave ids only, so a UI could offer `lifecycle` but not say it needs `column`
and `transitions`. The updater registry now takes an `example=` exactly as
the generator registry does, `list_updater_examples()` exposes them, and
`/registry` serves them. Picking a rule fills its params box with that
example. INV-4 holds: no updater id or param shape is written in the UI.

**3. The two modes are mutually exclusive in the UI, not just the contract.**
ADR-0020 made `DatasetSpec` reject a spec carrying both. The UI now disables
the other button and says why, so the invalid spec cannot be built. A
disabled button beats a 422 after the user has done the work.

**4. The editors are authoritative; `baseSpec` reads them back.** Every
build/preview/save calls `syncAdvancedFromEditors()` first. Without it the
spec would carry the values the editor was *opened* with and silently discard
every edit — the same class of defect ADR-0020 was written about, one level
down.

## Consequences

- **INV-1 holds.** The UI still only builds a `DatasetSpec`; no engine
  behaviour changed. The only non-UI change is the updater registry gaining
  examples, which is registry metadata, not generation logic.
- **INV-4 holds and got stronger.** Updaters now carry their own example the
  way generators do, and a test asserts every registered updater has one.
- **Column helpers are now per-table.** `addColumn`, `buildColumns` and
  `columnsBefore` took an implicit "there is exactly one column list on the
  page" assumption. `columnsBefore` in particular scanned every `.col` in the
  document; left alone it would have offered table B's columns to a derived
  column in table A, which the engine then rejects at load (`spec.py`
  validates derived references per-table). All three now take a container.
- **The read-only panel is gone**, replaced by the editors. Remove buttons
  survive on both.
- **Guard coverage is still source-level.** The editor contract is asserted by
  parsing `index.html` (function names, the `syncAdvancedFromEditors()` call
  in `baseSpec`, the scoping of `columnsBefore`, no hardcoded updater ids).
  Every assertion was mutation-tested. A `node --check` test now parses the
  page's script for real when a JS engine is present — a syntax error would
  otherwise blank the page while every regex test still passed. A
  browser-driven test remains the better answer and stays on the roadmap.

## What is still not editable

`output.options` (e.g. the SQL dialect) and `sink.options` on the Batch tab
are unchanged by this ADR. Nothing regressed; they were never editable there
and no preset depends on it. Worth a look only if a preset starts needing it.

## Alternatives considered

- **A JSON textarea for the whole spec.** Cheapest by far, and it would have
  served an engineer fine. It fails the actual user: the person who needs the
  editor is the person who should never see the JSON.
- **A separate "advanced" page or modal.** Rejected — it hides the fact that
  these are part of one spec, and the multi-table case genuinely needs to be
  read next to the primary table's columns to make sense.
- **Reimplementing a slimmer column editor for related tables.** Would have
  avoided the container refactor, at the cost of two column editors drifting
  apart — related tables would quietly lose the derived-formula builder and
  the params examples. The refactor was three functions and one real bug
  (`columnsBefore`); worth it.
