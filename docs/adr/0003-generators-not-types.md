# ADR-0003: Semantic generators, not physical types

- Status: Accepted
- Owner: Karl
- Date: 2026-07-10

## Context
The target user thinks "this column is a person's name," not "VARCHAR(50)."
Physical types differ per output format anyway.

## Decision
Columns declare a semantic generator id + params (weighted categoricals,
distributions, pattern ids, date ranges). Encoders map values to physical
types per format (e.g. SQL type inference from sampled values).

## Consequences
- Demo data looks organic (distributions, weights) instead of uniform noise.
- Generator registry is the main extension surface; adding one never touches
  the engine (INV-4).
- Type fidelity edge cases (e.g. DECIMAL precision in SQL) are encoder
  options, not generator concerns.
