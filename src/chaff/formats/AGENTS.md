# Path rules: formats

An encoder is a PURE function `(spec, rows) -> bytes` (INV-2). No file
writes, no network, no environment reads. If you need to know where the
data is going, you're writing a sink.

- Register with `@encoder("id", ".ext")`.
- Options come from `spec.output.options` only.
- **A column-oriented encoder takes its column list from `spec.columns` and
  must be given an encode view, not the raw spec.** The engine adds columns a
  spec never declares (an entity's id and tick), so `engine.encode_view()`
  supplies them — see ADR-0028 for the bug that came of skipping it. Callers
  do this; encoders just trust `spec.columns`.
- **Anything a spreadsheet might open is guarded** (ADR-0028). Run values
  *and* header names through `_formula.neutralize(value, mode)` with
  `mode = _formula.guard_mode(spec.output.options)`. A spec is shareable, so
  a constant or a column name is attacker-reachable. Escape by quoting the
  delimiter, never by restricting what a name may contain — names are
  permissive on purpose.
- Dialect/variant differences (SQL quoting, XML element naming) are
  options on one encoder, not new encoders — until they stop sharing
  structure, then split with an ADR.
- Phase 2 additions (parquet via pyarrow, avro via fastavro, xlsx via
  openpyxl) go in separate modules imported by `__init__.py`, with their
  deps under the `formats-extra` extra in pyproject — the core must keep
  importing with zero heavy deps.
