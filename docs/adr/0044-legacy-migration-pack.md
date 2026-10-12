# ADR-0044: Legacy migration pack — hidden columns, 1:1 answer keys, fixed width

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-10-12
- Issue: #86 (builds on ADR-0043)

## Context
Replacing a case system almost always means migrating off an older one, and an
ETL needs test data shaped like that older system's output: fixed-width
extracts, two-digit years, retired codes, damaged names, orphaned rows. It also
needs an **answer key** saying what each dirty row should become, kept apart
from the file the ETL reads.

Making dirt means generating the clean value and then damaging it. Before
this ADR the clean value had to be an output column, so it would have shipped
inside the dirty file — the answer printed next to the question. An answer key
also has to line up 1:1 with the rows it scores, and `fk` only picked parents
at random.

## Decision
1. **Hidden columns.** `ColumnSpec.hidden: true` generates a column so later
   columns, `derived` formulas and child tables' `parent` columns can read it,
   but nothing the engine hands out carries it. Rows are stripped in the
   public generation functions (`generate_rows`, `iter_rows`,
   `generate_tables` after every table is built, `iter_entity_rows`), and
   column lists in `encode_view` / `table_views`, so every exit is covered:
   files, downloads, zips, preview, push streams and the `/stream` socket.
   That socket used the raw spec instead of `encode_view`; fixing it also
   fixes entity specs there, whose id/tick columns were missing from CSV
   records. The UI gets a **Hidden** checkbox per column (round-tripped,
   covered by the browser suite). A spec with no hidden columns takes the old
   code path unchanged.
2. **`fk` `each: true`.** Row *i* points at parent row *i*, no rng draw, so an
   answer-key table aligns 1:1 with what it scores. Rows past the parent's
   count get `None`. It can't be combined with `where`/`match` (rejected at load).
3. **`dirty` generator.** Copies an earlier column and, at `rate`, applies one
   of: `upper`, `trailing_space`, `leading_space`, `typo` (adjacent swap),
   `truncate`, `mojibake` (UTF-8 read as cp1252: José → JosÃ©), `blank`.
4. **`legacy_date` generator.** Re-renders an ISO date in per-row legacy
   formats (strftime list with weights) and a `null_token` for nulls
   (`00000000`). `%b` is spelled out from a fixed table, never the locale,
   so output can't vary by machine (INV-3).
5. **`fixed_width` format** (stdlib, core) with widths, alignment, pad
   characters, optional header, `overflow: error|truncate`, `lf|crlf`, and a
   legacy code page (`encoding`, `errors`). Values pass the ADR-0028 formula
   guard, since a .txt is often opened in Excel's import wizard. Auto widths
   are a pure function of the rows.
6. **`fixed_width_layout` format** renders the same layout as a COBOL-style
   copybook (`PIC X(n)` / `PIC 9(n)` with 1-based positions). It is a second
   encoder rather than a side file because an encoder returns one payload
   (INV-2); run the spec once per format with the same seed and they agree.
7. **CLI `--format`/`-f`** overrides the output format, keeping the other
   options, so one spec gives fixed-width extracts or CSV answer keys.
8. **`legacy_migration` preset**: a 2,000-case extract, a 6,000-entry docket,
   and two answer keys whose every flag is tested against the dirty data
   itself, not against the hidden value that made it.

## Consequences
- INV-1/2/4 hold: the new pieces are registered plugins. The engine change is
  stripping hidden cells at its exits, which is the engine's job (it decides
  what a row is).
- INV-3 holds: hidden columns, `each` and the copybook draw nothing new;
  `dirty`/`legacy_date` draw from `ctx.rng` only. Specs without the new
  fields are byte-identical (the ADR-0043 digest test still passes).
- Reconciliation counts are one GROUP BY over an answer key's flag columns.
  chaff doesn't emit a separate summary file: it would be an aggregate of
  another table, which the table model doesn't express.

## Scoped out
- Mixing encodings inside one file (a few cp1252 rows in a UTF-8 file). The
  `mojibake` kind gives the visible symptom; true mixed bytes would need a
  per-row encoding option on the encoder.
- EBCDIC and packed decimal (COMP-3). Possible later as `fixed_width`
  options; nobody has asked yet.
