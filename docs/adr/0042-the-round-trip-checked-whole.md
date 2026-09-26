# ADR-0042: The round-trip, checked whole

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-26

## Context

ADR-0020 made it a rule: an interface preserves the whole spec or fails
loudly — "carrying is the invariant, editing is a feature." Its browser test
checked that `entity` *arrived*, not that it arrived *intact*. An external
review (main at `cfe4373`) found two places where the rule had quietly lapsed
since observers (ADR-0033) and the sensor formats (Phase 9) landed:

- **The page dropped scenario settings.** The ADR-0021 entity editor rebuilt
  `entity` from its own inputs on every build, so `observers` — which it does
  not show — vanished. The spec skeleton wrote `output` as `{format}`, so every
  format option vanished with it: CoT timing, VMTI frame geometry, the SQL
  dialect. Loading `skewed_clock` and pressing Download produced one
  ungrounded feed with default timing: HTTP 200, no fault, no answer key, and
  none of the scenario the preset exists to demonstrate. Every one of the 20
  presets also lost its `description` on Save.
- **Push jobs ignored observers.** The CLI and the `/stream` WebSocket refuse
  an observer spec (one socket, several feeds). `POST /stream/jobs` did not,
  and streamed the scene's truth rows — the one thing no observer emits — as
  if they were a sensor feed.

Both are ADR-0020's failure again: confidently wrong data, discovered in front
of the customer.

## Decision

### 1. Carry what no editor shows

The entity read-back starts from the loaded entity and overwrites only the
fields the editor owns, so unseen fields (today `observers`) survive. A field
the editor owns and the user empties (`id_pattern`) is removed, not kept at
its loaded value. `description` is carried as loaded.

### 2. Format options belong to their format

`output.options` is re-emitted only while the chosen format matches the one
the options were loaded with. Switch a CoT preset to CSV and the options are
dropped: CoT timing on a CSV spec is a spec that says one thing and does
another.

### 3. The sink is not carried

A preset's `sink` is a CLI file path (`out/cot_tracks.cot`). The batch tab
delivers by download and the Stream tab builds its own sink from its own
fields, so carrying it would only put a stale path into saved specs. This is
the one top-level key the page deliberately does not round-trip.

### 4. Every streaming path refuses what it cannot carry

`start_job` rejects an observer spec with a 422 before a job or socket exists,
matching the CLI and the WebSocket. Delivering several feeds from one run is
real work (synchronized per-observer destinations) and is on the roadmap; a
push job that silently sends something else is not a stand-in for it.

### 5. Test the whole spec, for every preset

`test_every_preset_round_trips_through_the_form` loads each file in
`examples/` through the gallery's own loader, previews it, and compares the
sent spec to the preset after normalizing both through `DatasetSpec` (only
`sink` and, for entity specs, `rows` excluded). A new preset is covered with
no test edit, and a new spec field the page forgets fails on the first preset
that uses it. Download and Save are pinned separately because they are the
buttons that leave a lasting file.

## Consequences

- Against the old page, all 20 preset cases fail; with this change they pass.
- Adding a field to `EntitySpec` or `OutputSpec` no longer needs anyone to
  remember the UI — the parametrized test is the reminder.
- The UI still cannot *edit* observers or format options. It carries and
  sends them intact; editing them is a separate feature.
