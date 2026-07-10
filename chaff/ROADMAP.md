# chaff Roadmap

Phased. Finish and verify a phase before starting the next (Build DNA §3).

## Phase 1 — Core engine + file outputs  ← YOU ARE HERE (scaffold complete)
- [x] Spec contract (`DatasetSpec`, versioned)
- [x] Generator registry: people, location, ids, pattern, numeric
      distributions, weighted categoricals, dates, lorem (~24 generators)
- [x] Formats: csv, tsv, json, ndjson, sql (postgres/sqlite/tsql dialects)
- [x] File sink
- [x] CLI (generate / validate / registry)
- [x] Invariant test suite; `make check` loop
- [x] Preset library seed: 4 example specs
- [ ] **CLAUDE CODE — Phase 1 completion:**
  - [ ] Web UI: form-based spec builder against `/registry`, live preview
        via `/preview`, download via `/generate`. Simple > clever; office
        Joe is the user. (frontend lives in `api/static` or a `ui/` dir —
        your call, ADR it)
  - [ ] API: streaming download for large datasets; request size limits
  - [ ] Excel (.xlsx) format encoder (openpyxl, `formats-extra` extra)
  - [ ] GitHub Actions: `make check` + docker build on push

## Phase 2 — Heavy formats + streaming sinks
- [ ] Parquet (pyarrow), Avro (fastavro), XML encoders
- [ ] Streaming sink signature: per-record iterator + rate control (rec/sec)
- [ ] Kafka sink (confluent-kafka; compose `streaming` profile is the fixture)
- [ ] HTTP POST sink (single/batch, retry policy, auth header passthrough)
- [ ] TCP/UDP raw sinks

## Phase 3 — The fun stuff
- [ ] Saved/recalled schemas: spec library with named saves (backlog: Karl)
- [ ] Preset gallery in UI: pick a predefined schema and go (backlog: Karl)
- [ ] Multi-table specs with FK integrity (customers -> orders -> lines)
- [ ] Stateful entities over time: tracks that move, lifecycles that
      transition (`DatasetSpec.entity` seam is reserved for this)
- [ ] Cursor-on-Target (CoT) format encoder — XML events with lat/lon/time;
      pairs with TCP/UDP/streaming sinks to feed a TAK server live synthetic
      tracks. Depends on: stateful entities + streaming sinks.
- [ ] Natural-language spec building: describe the dataset in plain English,
      get a draft spec to review/edit (Anthropic API; the UI's NL input box)

## Non-goals (permanent)
- AI/ML training data production (INV-5)
- Data anonymization / production-data masking (that's Tonic's lane; chaff
  generates from nothing)
