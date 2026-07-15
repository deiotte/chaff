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

## Phase 3 — The fun stuff  ← COMPLETE
- [x] Saved/recalled schemas: spec library with named saves — `chaff/library.py`
      (presets + writable saves), `/library` API, `chaff library` CLI.
- [x] Preset gallery in UI: pick a predefined schema and go — gallery cards
      from `/library`, click loads the spec into the builder, save-to-library.
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
- [x] Cursor-on-Target (CoT) format encoder — XML events with lat/lon/time,
      blob + per-record so it streams to a TAK server over TCP/UDP. Event
      time from the data (base_time + tick), never wall-clock (INV-3).
      Example: `examples/cot_tracks.json` (moving entities → CoT feed).
- [x] Natural-language spec building: describe the dataset in plain English,
      get a draft spec to review/edit — `api/nl.py` + `/draft` (ADR-0010),
      Anthropic API with a server-side key, JSON validated via `load_spec`
      with one retry. Interface layer (INV-1); engine stays deterministic
      (INV-5). UI "Describe it" box.

## Phase 4 — Richer data sources  ← COMPLETE
- [x] Statistical distributions: `lognormal`, `exponential`, `poisson`,
      `power_law` — skewed/real-world shapes so demo data behaves like the
      real thing (adds to `float_normal`). Deterministic via `ctx.rng`.
- [x] Web/telemetry generators: `ipv4`, `ipv6`, `mac_address`, `url`,
      `domain`, `username`, `user_agent`, `slug`, `sha256`, `http_method`,
      `http_status`, `port`, `ulid`, `api_key`. Example:
      `examples/web_access_logs.json`.
- [x] Derived/computed columns: a column computed from other columns in the
      same row (`total = price * qty`) — `GenContext.row` + a safe (no-eval)
      formula evaluator, validated at load time (ADR-0012). The
      internal-correlation "realism unlock" for demos.
- [x] Multi-provider NL drafting: OpenAI + Google alongside Anthropic,
      auto-detected by which API key env var is set.
- [x] Tier 2 generators: geo (`country`, `zip_code`, `timezone`), finance
      (`currency_code`, test `credit_card`, `iban`), people (`job_title`,
      `age`, `gender`). Deterministic via `ctx.faker`/`ctx.rng`. Example:
      `examples/employees.json`.
- [x] Correlated columns: a linked `country` anchor drives matching
      `city`/`timezone`/`currency_code`/`lat`/`lon` (`{"link": true}` +
      `{"from": "country"}`) — ADR-0015. Opt-in, deterministic, curated
      country→currency table. Example: `examples/crm_contacts_geo.json`.

## Phase 5 — Distribution
- [x] Windows one-click `.exe` (PyInstaller onefile, built in CI on
      windows-latest, attached to releases) — ADR-0014. Bundles every format
      + Anthropic/OpenAI drafting; Docker stays the primary cross-platform path.
- [ ] Code-signing (Authenticode) to drop the SmartScreen "Run anyway" prompt.
- [ ] macOS `.app` bundle / Windows MSI installer with a Start-menu shortcut.

## Phase 6 — Serve a live stream (not just build one to download)
Turn chaff from "generate a file and download it" into something that can
*serve* a data stream. Sequenced: the lazy-generation keystone unblocks the
rest. Push sinks (kafka/http/tcp/udp) already ship from Phase 2 — this phase
adds continuous/serve semantics and new transports.
- [x] **Keystone — lazy generation + determinism-as-prefix (ADR-0016):**
      `iter_records(spec, limit=…)` yields rows one at a time (`None` = natural
      length, int = cap/extend, `math.inf` = unbounded). One generation path
      behind eager + lazy, so no drift. Streaming run-mode on `sink.options`:
      `max_records` and `duration` (`time_limited`, a wall-clock cut next to
      `rate_limited`). INV-3 restated: record *content* is reproducible; record
      *count* under a duration bound is not. Eager download paths byte-identical.
- [x] **WebSocket serve endpoint:** `/stream` on the FastAPI app — client
      connects, sends a spec, chaff streams paced encoded records over the
      socket until the client disconnects (or duration/max_records); closing
      the socket marks end-of-stream. chaff *is* the server; no external
      broker. Async pacing (never blocks the loop), run-mode via query params
      (`rate`/`duration`/`max_records`) mirroring the streaming sink options.
      Whole-file formats + multi-table specs are refused up front.
- [ ] UI live-feed view: a "watch it stream" panel that opens the socket and
      shows records arriving (the D4O payoff — office Joe sees it move).
- [x] **MQTT publish sink:** `@stream_sink("mqtt")` (paho-mqtt under the
      `streaming` extra), same shape as kafka — publish per record to a topic,
      qos 0/1/2, secrets from options/env (never logged), fail loud on
      broker-unreachable. Fake-client unit tests + a Mosquitto broker in the
      compose `streaming` profile for live round-trips.
- [ ] Streaming lifecycle for operability (D4O): start/stop/status for a
      long-running stream, launchable from the UI/API — closes the job-queue
      TODO parked in ADR-0007.

### Deferred (weird outliers — seams noted, not built)
- gRPC server-streaming RPC: the enterprise-grade serve option (backpressure,
  bidirectional). New interface (INV-1 holds: still a DatasetSpec) + `.proto` +
  `grpcio`. Revisit if a consumer actually needs it.
- protobuf record encoder: a new format on the encoder axis (like avro), with
  a schema/descriptor generated from the spec's columns. Pairs with any
  streaming transport. Revisit alongside gRPC.
- tokio: N/A — a Rust async runtime; chaff is single-language Python. The
  concurrency need it implies is served by asyncio (FastAPI) on the serve paths.

## Non-goals (permanent)
- AI/ML training data production (INV-5)
- Data anonymization / production-data masking (that's Tonic's lane; chaff
  generates from nothing)
