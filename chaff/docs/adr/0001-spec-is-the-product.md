# ADR-0001: The spec is the product

- Status: Accepted
- Owner: Karl
- Date: 2026-07-10

## Context
Demo data tooling usually dies as one-off scripts. The recurring need is the
*description* of a dataset, not any particular script that produces it.

## Decision
A declarative `DatasetSpec` (Pydantic, JSON-serializable, versioned via
`spec_version`) is the single contract. UI, CLI, and API are spec producers;
only the engine consumes specs. Saved schemas, presets, sharing, and headless
mode all fall out of this for free.

## Consequences
- Generation logic outside `src/chaff/` is a defect (INV-1).
- Spec changes are breaking changes; bump `spec_version` and write migration
  notes.
- The UI is intentionally "just a form" that emits a spec.
