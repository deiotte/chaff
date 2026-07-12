# ADR-0015: Correlated (anchored) columns

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-12

## Context
Geo/finance generators drew independently, so a record could read "Russian
Federation / America/Bogota / EUR" — internally incoherent. Real demo data has
a source of truth (the country) that its neighbours agree with. This is the
geo analogue of derived columns (ADR-0012): a value that depends on another
cell in the same row.

## Decision
Add opt-in **anchor + dependent** correlation. A `country` column marked
`{"link": true}` resolves **one coherent location per row** and caches it;
dependent columns declare `{"from": "<country column>"}` and read a facet of
that same location, so they fan out consistently.

- **Coherent source:** faker's `local_latlng(country_code=cc)` /
  `location_on_land()` return a matching `(lat, lon, place, alpha2, IANA_tz)`
  tuple. `currency_code` fills faker's one gap (no country→currency) via a
  curated `alpha2 → {name, currency}` table.
- **The seam:** two new `GenContext` fields — `cache` (per-row scratch, keyed
  by the anchor's column name) and `column` (the column being generated, so
  the anchor knows its own cache key). The engine creates a fresh `cache = {}`
  per row (and per entity tick-0) and sets `ctx.column` before each cell.
  No engine switch statements — correlation lives entirely in the generators
  (INV-4).
- **Curated data as a `.py` module** (`src/chaff/data/`), not JSON: `pyproject`
  uses `packages.find` with no `package-data`, so a JSON file would be dropped
  from the wheel and the `.exe`.
- **Load-time validation** (`spec.py:_validate_geo_links`): a `from` must name a
  `country` column with `link` declared *before* it, and is only valid on
  `city/timezone/lat/lon/currency_code`. Structural mistakes fail at load with a
  precise message.
- **Linkable facets:** `timezone`, `city`, `lat`, `lon`, `currency_code`.
- **NL drafter** learns the pattern so "Describe it in English" emits it.
  Example preset: `examples/crm_contacts_geo.json`.

## Consequences
- **Determinism (INV-3) holds.** `local_latlng`/`location_on_land` draw from
  faker (seeded via `seed_instance`), and constrained picks use `ctx.rng`; both
  are the same seeded streams as every other generator. Dependents only *read*
  the cache — zero entropy — so they never shift the stream.
- **Fully backward-compatible.** Without `link`/`from`, every generator's
  default branch is the original expression, and the new `cache`/`column`
  assignments consume no entropy — so specs that don't use the feature produce
  byte-identical output to before. (A linked column, like *any* generator that
  draws, does advance the shared rng/faker stream for later rows — that's
  inherent, not a regression.)
- **Graceful degradation.** A nulled or unplaceable anchor caches nothing;
  dependents fall back to an independent draw. A country outside the currency
  table yields `None` (explicit unknown), never a random currency.

## Scoped out (documented, not bugs)
- Place names are **romanized** (faker gives "Solntsevo", not Cyrillic).
- **State/region** correlation: faker `state()` is US-centric and the coherent
  tuple carries no admin-1 region, so `state` stays independent and is not a
  `from`-linkable facet. A future richer dataset could add it.
- `values` must be alpha-2 codes faker can place (135 countries); others
  degrade to an independent fallback.
