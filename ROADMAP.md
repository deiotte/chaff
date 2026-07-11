# chaff Roadmap

Phased. Finish and verify a phase before starting the next (Build DNA §3).

## Phase 1 — Core engine + file outputs  ← COMPLETE
- [x] Spec contract (`DatasetSpec`, versioned)
- [x] Generator registry: people, location, ids, pattern, numeric
      distributions, weighted categoricals, dates, lorem (~24 generators)
- [x] Formats: csv, tsv, json, ndjson, sql (postgres/sqlite/tsql dialects)
- [x] File sink
- [x] CLI (generate / validate / registry)
- [x] Invariant test suite; `make check` loop
- [x] Preset library seed: 4 example specs
- [x] **CLAUDE CODE — Phase 1 completion:**
  - [x] Web UI: form-based spec builder against `/registry`, live preview
        via `/preview`, download via `/generate`. Static, build-free page
        in `api/static/`, served by the API process (ADR-0006).
  - [x] API: streaming download (`/generate` → file attachment) + request
        size limit (`CHAFF_API_MAX_ROWS`, default 100k → 413)
  - [x] Excel (.xlsx) format encoder (openpyxl, `formats-extra` extra;
        deterministic — zip + doc-props timestamps pinned, INV-3)
  - [x] GitHub Actions: `make check` + docker build on push

## Phase 2 — Heavy formats + streaming sinks  ← COMPLETE
- [x] Parquet (pyarrow), Avro (fastavro), XML encoders — deterministic
      (Avro sync marker + Parquet writer pinned; XML uses `name` attrs so
      any column name round-trips). pyarrow/fastavro under `formats-extra`.
- [x] Streaming sink signature: per-record iterator + rate control (rec/sec)
      — second `@stream_sink` signature negotiated by the engine (ADR-0007),
      per-record encoder registry in formats, engine-applied `rate_limited`.
- [x] Kafka sink (confluent-kafka; compose `streaming` profile is the fixture)
      — `@stream_sink`, per-record produce with backpressure + loud
      delivery-failure detection. Unit-tested via a fake producer; live
      broker round-trip runs against the compose fixture.
- [x] HTTP POST sink (single/batch, retry policy, auth header passthrough)
      — `httpx` under the `streaming` extra; 4xx fails loud, 5xx/transport
      retries with backoff, secrets never logged.
- [x] TCP/UDP raw sinks — stdlib-only `@stream_sink`s (no extra); TCP
      surfaces connection-refused loudly, UDP guards the datagram-size
      limit. Tested against real local listeners.

## Phase 3 — The fun stuff
- [ ] Saved/recalled schemas: spec library with named saves (backlog: Karl)
- [ ] Preset gallery in UI: pick a predefined schema and go (backlog: Karl)
- [x] Multi-table specs with FK integrity (customers -> orders -> lines)
      — `TableSpec` + `DatasetSpec.tables` (ADR-0008), `fk` generator,
      dependency-ordered generation (cycle/missing detection), one file per
      table. Deterministic; single-table path unchanged. CLI-only for now
      (API rejects multi-table). Example: `examples/retail_orders.json`.
- [x] Stateful entities over time: tracks that move, lifecycles that
      transition — `EntitySpec` on the `entity` seam (ADR-0009), `@updater`
      registry (movement/lifecycle/drift) + `EntityContext`, `count × ticks`
      time-ordered snapshots. Deterministic; composes with streaming sinks.
      Examples: `moving_tracks.json`, `order_lifecycle.json`.
- [ ] Cursor-on-Target (CoT) format encoder — XML events with lat/lon/time;
      pairs with TCP/UDP/streaming sinks to feed a TAK server live synthetic
      tracks. Depends on: stateful entities + streaming sinks.
- [ ] Natural-language spec building: describe the dataset in plain English,
      get a draft spec to review/edit (Anthropic API; the UI's NL input box)

## Non-goals (permanent)
- AI/ML training data production (INV-5)
- Data anonymization / production-data masking (that's Tonic's lane; chaff
  generates from nothing)
