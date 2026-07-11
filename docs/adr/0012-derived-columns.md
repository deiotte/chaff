# ADR-0012: Derived / computed columns

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-11

## Context
Every chaff column was independent — a column of prices and a column of
quantities never agreed with a "total". Real demo data has internal
correlation (`total = price * qty`, `full_name = first + last`,
`tier = 'wholesale' if net > 500 else 'retail'`). Without it, an analyst
or ML demo built on chaff data falls apart the moment someone checks that
the numbers add up. This is the highest-value "realism unlock" in Phase 4.

## Decision
Add a **`derived` generator** whose value is computed from **other cells in
the same row**, via a **formula string** where column names are the
variables:

```json
{"name": "total", "generator": "derived", "params": {"expr": "price * qty", "precision": 2}}
```

- **The engine exposes the in-progress row.** `GenContext` gains a `row`
  field — the same dict the per-column loop fills — so a `derived` column
  reads the cells generated before it. Columns generate in declaration
  order, so **a derived column must come after the columns it references**
  (validated at load time). Applies to single-table rows, each table in a
  multi-table spec, and an entity's tick-0 initial state.
- **No `eval`. Ever.** A spec can arrive over the API or from an LLM draft,
  so the formula is parsed to an AST and walked against a strict whitelist
  (`src/chaff/generators/_expr.py`): numeric/string/bool literals, column
  names, `+ - * / // % **`, comparisons, boolean ops, ternary, and a fixed
  set of functions (`round/min/max/abs/len/int/float/str/bool`). No
  attribute access, no subscripts, no calls to anything else — so the
  classic `().__class__.__bases__…` and `__import__` escapes have nothing
  to reach. A `**` exponent is capped to avoid pathological blowups.
- **Load-time validation.** `load_spec` parses each formula and checks its
  referenced names are declared earlier, raising a precise error (which
  column, what's wrong) before generation — the UI/CLI surfaces it inline
  (D4O: fail early, name the fix).
- **Null in, null out.** If any referenced cell is null (from `null_rate`),
  the derived value is null rather than raising.

## Consequences
- **INV-3 (determinism) is free.** A derived value is a pure function of
  already-generated cells — no rng draw, no clock — so it adds zero entropy
  and doesn't perturb the stream for later columns. Same spec + seed = same
  bytes, unchanged.
- **INV-4 holds.** It's one registered generator; the engine change is a
  single field on `GenContext`, not a switch on column type.
- **INV-1 holds.** Still just spec: the UI shows a plain "formula" field for
  derived columns (bare `price * qty`, wrapped to `{expr}` on build), and the
  NL drafter emits `derived` when the description implies a computed field.
  Example preset: `examples/orders_with_totals.json`.
- Scope: derived values compute once at generation (tick 0 for entities).
  Recomputing a derived column every entity tick is a natural follow-on and
  is intentionally out of scope here.
