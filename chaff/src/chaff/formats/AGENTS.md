# Path rules: formats

An encoder is a PURE function `(spec, rows) -> bytes` (INV-2). No file
writes, no network, no environment reads. If you need to know where the
data is going, you're writing a sink.

- Register with `@encoder("id", ".ext")`.
- Options come from `spec.output.options` only.
- Dialect/variant differences (SQL quoting, XML element naming) are
  options on one encoder, not new encoders — until they stop sharing
  structure, then split with an ADR.
- Phase 2 additions (parquet via pyarrow, avro via fastavro, xlsx via
  openpyxl) go in separate modules imported by `__init__.py`, with their
  deps under the `formats-extra` extra in pyproject — the core must keep
  importing with zero heavy deps.
