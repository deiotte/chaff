# ADR-0020: The whole spec round-trips through every interface

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

Two spec features shipped in Phase 3 — stateful entities (ADR-0009) and
multi-table specs (ADR-0008) — were reachable only from the CLI. The web UI's
spec builder was written before either existed and reads only `spec.columns`.
Nothing was ever added to carry the rest.

The result was not a missing feature. It was **silent data loss**:

- `loadSpecIntoForm` read `spec.columns` and dropped `spec.entity` and
  `spec.tables` on the floor. `buildSpec` then rebuilt the spec from form
  fields alone, so the dropped keys never came back.
- Clicking `moving_tracks` in the gallery returned **HTTP 200** with rows
  carrying no `track_id` and no `t` — every "track" a single disconnected
  point. Clicking `order_lifecycle` returned 200 with every row still
  `status: placed`; the lifecycle never transitioned. No error, no warning.
- Clicking `retail_orders` at least failed loudly (400, "use the CLI"), but
  its gallery card advertised "50 rows · 5 cols" for a 3-table, 850-row spec.

Four of the twelve shipped presets were unusable from the UI, and three of
them failed by handing back confidently wrong data. Against the North Star —
office Joe builds a dataset and walks into a demo without asking an engineer —
this is the worst available outcome: Joe cannot tell he has the wrong data
until he is standing in front of the customer.

The API's blanket multi-table rejection was the load-bearing cause of the
fourth case. `_reject_multitable` guarded `/preview` and `/generate` on the
reasoning that one request yields one file. That reasoning was never
re-examined after ADR-0008 landed, and it made a whole spec feature
permanently invisible to every non-CLI interface — which is INV-1 backwards:
the spec is the product, and an interface that cannot carry the product is
the defect.

## Decision

**1. Interfaces preserve the whole spec, or fail loudly.** Silently dropping
a spec key is never acceptable. The UI now stashes `entity` and `tables` on
load and re-emits them verbatim on build. Where the form cannot yet *edit* a
part of the spec, it must still *carry* it — carrying is the invariant,
editing is a feature.

**2. What the UI cannot edit, it must still show.** An "Advanced" panel
renders whatever rode along — `10 entities × 30 ticks = 300 rows`, or the
related-table list — each with a Remove button. Read-only by design: a full
entity/table editor is a larger piece of work, but Joe must be able to see
why he is getting 300 rows from a spec whose `rows` box says 100, and be able
to get back to a flat table without reloading the page. The `rows` input is
disabled while an entity is attached, because `count × ticks` decides the
length and a live-looking input that does nothing is its own small lie.

**3. Multi-table is served over the API as a zip.** `_reject_multitable` is
gone. One request still yields one file — that file is a zip holding one
encoded file per table (`X-Chaff-Tables` names them in order). `/preview`
gains a `tables` map alongside the existing `rows`, so single-table callers
are untouched and the UI can show the foreign keys lining up before download.

**4. Gallery cards report the shape the spec actually has.** `rows` on a card
is now what the spec emits: `count × ticks` for an entity spec, the sum over
all tables for a multi-table spec. Cards carry `tables` and `entity` hints so
the meta line reads "850 rows · 3 tables · csv" instead of "50 rows · 5 cols".

## Consequences

- **INV-1 holds properly for the first time.** Every interface now produces a
  complete `DatasetSpec`. No generation logic moved out of `src/chaff/`: the
  API calls `encode_tables()` and zips the bytes it gets back.
- **INV-2 untouched.** `table_views()` hands every encoder an ordinary
  single-table `DatasetSpec`; no encoder knows multi-table exists, and the zip
  is packaging, not encoding.
- **INV-3 extends to the archive.** Entry timestamps are pinned to the zip
  epoch and entries are `ZIP_STORED` (deflate output varies across zlib
  builds), so the same spec + seed yields a byte-identical zip — not merely
  identical members. Verified against the CLI: zipped bytes equal
  written-file bytes, table for table.
- **Streaming still refuses multi-table**, in both the job runner and the
  `/stream` socket. That refusal is correct and stays: a per-record socket
  has no framing for several interleaved tables. It fails loudly, which is
  the whole point of this ADR.
- **`effective_row_count` now sums every table**, so the `CHAFF_API_MAX_ROWS`
  ceiling sees all 850 rows of `retail_orders` rather than the primary
  table's 50. A spec that previously slipped under the ceiling by hiding rows
  in child tables no longer does.
- **Regression cost.** The JS has no test runner in CI, so the round-trip
  contract is guarded by asserting on the UI source (`tests/test_spec_roundtrip.py`)
  — checking that `loadSpecIntoForm` reads both keys and `baseSpec` re-emits
  them. Coarse, and it will need updating if the UI is restructured, but it
  fails when the original bug is reintroduced (verified by mutation). A real
  browser-driven test is the better answer if the UI grows further.

## Two defects found while verifying this

Adversarial review of the new download path surfaced two pre-existing bugs
in the code it touches. Both are fixed here rather than left for later,
because the new multi-table path would otherwise inherit them.

- **A hostile `spec.name` broke the download.** `name` is free text and went
  straight into `Content-Disposition`. A CR/LF in it produced an invalid
  header that uvicorn rejects at the wire, so the request died on a dropped
  connection instead of returning a file. Not an exploitable header
  injection — the server refuses to send it — but a crash on user input.
  Filenames and the `X-Chaff-Tables` values are now stripped to
  `[A-Za-z0-9._-]`, with a `dataset` fallback.
- **`entity` + `tables` in one spec was accepted, then crashed.** `run()`
  takes the multi-table path first and never reads `entity`, so the entity
  config was silently lost; and since `rows` is legally omitted on an entity
  spec, the table path then had no row count and raised a `TypeError` deep in
  generation (a 500 over the API, a traceback from the CLI). The two are
  different generation modes, so `DatasetSpec` now rejects the combination at
  load time with one clear message — the same failure this ADR is about,
  caught at the contract instead of at runtime.

## Alternatives considered

- **Leave the API rejecting multi-table, teach the UI to hide those presets.**
  Cheaper, and wrong: it would make a shipped spec feature permanently
  CLI-only and quietly shrink the product to what the form happens to
  support.
- **Return a tar, or several responses.** Zip is what a non-technical user on
  Windows or macOS can open by double-clicking, which is the whole audience.
- **Build full entity/table editors now.** The right eventual answer, and far
  larger than the bug. Carrying the spec faithfully and showing what is
  attached fixes the data-loss defect today without half-building an editor.
