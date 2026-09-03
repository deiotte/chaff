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
      table. Deterministic; single-table path unchanged. Available from the
      CLI, the API and the UI (ADR-0020). Example: `examples/retail_orders.json`.
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
- [x] macOS `.app` bundle + Windows MSI installer with a Start-menu shortcut
      (ADR-0023). MSI wraps the same `chaff.exe` (packaging, not a second
      build) and adds a Start-menu entry, Add/Remove Programs, per-user
      install and `MajorUpgrade`. macOS ships a onedir `.app`, zipped with
      `ditto` to keep the bundle intact. Both specs now share
      `packaging/bundle_config.py`, so a hidden import can't go missing on one
      platform. CI smoke-tests the real artifact on both — starts it, asserts
      `/registry` answers with desktop mode armed, stops it via `/shutdown` —
      because "the file exists" never caught a missing hidden import.
- [x] **Desktop Quit path (ADR-0023).** A macOS `.app` runs windowless, so
      "close the console window" stopped being a quit story. Desktop builds
      set `CHAFF_DESKTOP=1` and get `POST /shutdown` plus a Quit button;
      off by default and loopback-only, so the Docker/web deployment never
      exposes what would be a one-click DoS.
- [ ] **Code-signing — blocked on certificates, pipeline ready.** Both
      workflows sign when credentials exist and emit a loud `::warning` when
      they don't, so an unsigned build never looks signed. What's needed:
      Windows OV/EV certificate (~$200–700/yr; since 2023 the key must be on
      hardware or a cloud HSM, so Azure Trusted Signing is the CI-friendly
      route) and an Apple Developer account ($99/yr) for a Developer ID cert
      plus notarization. Secret names and the exact steps are in ADR-0023.
      **Untested** — nobody has run these paths with a real certificate.

### Known gaps
- macOS builds are single-architecture (whatever `macos-latest` is — arm64
  today). Intel Macs uncovered; universal2 needs universal wheels that pyarrow
  doesn't reliably ship.
- Unsigned macOS is a worse first run than Windows: Gatekeeper refuses a
  double-click outright and doesn't say why. Right-click → Open, once.
  Documented in the README; the strongest practical case for the Apple
  membership.
- No `.dmg` (drag-to-Applications) and no `.pkg` — the latter needs a second
  Apple certificate before it beats the zip.

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
- [x] **MQTT publish sink:** `@stream_sink("mqtt")` (paho-mqtt under the
      `streaming` extra), same shape as kafka — publish per record to a topic,
      qos 0/1/2, secrets from options/env (never logged), fail loud on
      broker-unreachable. Fake-client unit tests + a Mosquitto broker in the
      compose `streaming` profile for live round-trips.
- [x] **Streaming lifecycle + Stream tab (ADR-0017):** a Batch/Stream tab
      split in the UI over one shared spec. The Stream tab surfaces both serve
      models to Office Joe: **live view** (WebSocket, records into the page) and
      **push to a broker/endpoint** (Kafka/MQTT/HTTP/TCP/UDP). Push runs as a
      bounded server-side job — `POST/GET/DELETE /stream/jobs` (Start/Status/
      Stop) — since the browser can't hold a broker connection. **Guardrail:**
      every run declares a mandatory record + time cap, each clamped to a hard
      server ceiling (`CHAFF_STREAM_MAX_RECORDS`/`_SECONDS`); a run that hits a
      cap re-confirms ("run again?") so nothing floods a pipeline unattended.
      `streaming` extra baked into the default image. Verified in a real browser
      (both models, TCP round-trip). Closes the job-queue TODO from ADR-0007.
- [x] **Harden the network-facing streaming surface (ADR-0018):** opt-in
      `CHAFF_API_TOKEN` gates `/stream/jobs*` + the `/stream` WS; an egress
      policy blocks cloud-metadata/link-local always and honors an opt-in
      `CHAFF_STREAM_ALLOWED_HOSTS` allowlist (SSRF); the WS now applies the same
      record/second ceilings as the job runner (and errors on bad params instead
      of streaming unbounded); entity materialization is bounded by the cap so a
      tiny `max_records` over a huge `entity.count` can't freeze the loop.
      Defaults keep the localhost demo zero-config.
- [x] **Close the `/stream` connection-exhaustion DoS (ADR-0019):** the
      ADR-0018 record/second ceilings only bound a socket *after* a spec
      arrives. Re-verification found two holes on the same class: the opening
      `receive_text()` had no timeout (an idle unauthenticated socket held open
      forever — the default config binds `0.0.0.0` with auth off), and nothing
      capped concurrent live sockets. Now the handshake wait is bounded
      (`CHAFF_STREAM_HANDSHAKE_TIMEOUT`, default 10s) and concurrent `/stream`
      sockets are ceilinged (`CHAFF_STREAM_MAX_SESSIONS`, default 64), both
      env-tunable, defaults leaving the localhost demo unchanged. Residuals
      (WS-token-in-query, DNS-rebinding) re-verified and documented in the ADR.

### Deferred (weird outliers — seams noted, not built)
- gRPC server-streaming RPC: the enterprise-grade serve option (backpressure,
  bidirectional). New interface (INV-1 holds: still a DatasetSpec) + `.proto` +
  `grpcio`. Revisit if a consumer actually needs it.
- protobuf record encoder: a new format on the encoder axis (like avro), with
  a schema/descriptor generated from the spec's columns. Pairs with any
  streaming transport. Revisit alongside gRPC.
- tokio: N/A — a Rust async runtime; chaff is single-language Python. The
  concurrency need it implies is served by asyncio (FastAPI) on the serve paths.

## Phase 7 — Close the interface gap  ← COMPLETE
The engine outgrew the UI. Phase 3 shipped stateful entities and multi-table
specs; the spec builder was written before either and only ever knew about
`columns`. Everything here is about interfaces carrying the whole spec, not
about new generation features.
- [x] **Whole-spec round-trip (ADR-0020).** The UI silently dropped `entity`
      and `tables` on load, so 4 of 12 shipped presets were broken from the
      gallery — and 3 of them failed *silently*, returning HTTP 200 with
      confidently wrong data (`moving_tracks` with no track id or tick;
      `order_lifecycle` with every row stuck at `placed`). The UI now carries
      both keys verbatim and shows what's attached in an Advanced panel
      (with Remove); `/preview` returns a per-table sample map and
      `/generate` returns a deterministic zip (`ZIP_STORED`, pinned entry
      timestamps — INV-3 covers the archive, not just its members), so
      multi-table is no longer CLI-only; `effective_row_count` sums every
      table so the API ceiling stops undercounting; gallery cards report the
      shape the spec actually has ("850 rows · 3 tables", "300 rows · 10×30
      time series"). Verified in a real browser. Fixed two pre-existing
      defects found while verifying: a CR/LF in `spec.name` broke the download
      header (uvicorn refused it — a dropped connection, not an injection),
      and a spec setting both `entity` and `tables` was accepted then crashed
      in generation; it's now rejected at load.
- [x] **Entity + related-table editors in the UI (ADR-0021).** ADR-0020's
      panel was read-only, so authoring either mode still meant hand-written
      JSON and the CLI. Both are now editable in the page: the entity editor
      covers count/ticks/id/tick columns plus per-tick update rules (with a
      live "N rows = entities × ticks" note), and each related table gets a
      name, a row count and its own full column editor — the same widget the
      primary table uses, so generators, params examples and the derived
      formula builder all work inside a related table. Update rules and their
      params now come from the registry (`@updater(example=...)`,
      `list_updater_examples()`, served via `/registry`) — INV-4, no updater
      id hardcoded in the UI. The entity/tables exclusivity from ADR-0020 is
      enforced by disabling the other button rather than 422-ing after the
      work is done. Column helpers (`addColumn`, `buildColumns`,
      `columnsBefore`) are now per-table; `columnsBefore` previously scanned
      the whole page, which would have offered one table's columns to
      another's derived column. Verified in a real browser: both modes built
      from scratch and edited from presets, FK integrity intact.
- [x] **A JS test runner (ADR-0022).** `tests/test_ui_browser.py` drives the
      real page with Playwright + Chromium against the real app on a real
      port, and asserts on **the spec the page puts on the wire** — that's the
      product (INV-1), and asserting on rendered DOM alone would have missed
      the ADR-0020 bug the same way the source guards did. A JS console error
      fails the test, since a silent exception is how that bug hid. Skips
      cleanly with no browser (`make check` stays runnable for everyone);
      CI installs Chromium and sets `CHAFF_REQUIRE_BROWSER_TESTS=1` so a
      broken install fails loudly instead of skipping into a green build.
      The superseded source guards are retired; the two a browser can't
      observe stay (INV-4 no-hardcoded-updaters, and `node --check`, which
      still runs when the browser suite skips). Mutation-verified: dropping
      the entity/tables emit fails 5 tests, dropping the editor read-back
      fails 4, unscoping `columnsBefore` fails 1.

### Known gaps
- No visual-regression coverage: the browser suite asserts behaviour and
  spec output, not layout or styling. Reviewed by eye for now.
- A contributor without a browser gets the Python tiers only (ADR-0022
  accepts this; CI is the backstop).

## Phase 8 — Security hardening (external red-team, 2026-09-03)  ← IN PROGRESS
An external assessment of `e7bfc04` reported 11 findings. Every one checked so
far reproduced exactly as described, so the report is treated as accurate
unless a specific claim fails to reproduce.
- [x] **F-01 Compose silently dropped its own hardening.** `docker-compose.yml`
      published `0.0.0.0:8000` and forwarded only the three AI provider keys —
      `CHAFF_API_TOKEN`, `CHAFF_STREAM_ALLOWED_HOSTS` and the ceilings reached
      Compose from the host `.env` but never the container process. Following
      the documented hardening got you none of it, with no error. Now every
      setting the code reads is forwarded (a test enumerates them and fails on
      a new one), and the port binds to `127.0.0.1` by default —
      `localhost:8000` is unchanged, `CHAFF_BIND=0.0.0.0` exposes it
      deliberately.
- [x] **F-06 Table names escaped the output directory.** Names became
      filenames unvalidated: `../escaped` wrote *outside* the requested
      directory via the CLI and produced a `../../outside/pwn.csv` member in
      the downloaded zip. Fixed at the contract (`DatasetSpec`/`TableSpec`
      reject path separators, traversal, control characters, drive letters and
      Windows device names) so every interface inherits it, plus an
      independent containment check at both write points.
- [ ] **F-02 Auth covers streaming only.** With `CHAFF_API_TOKEN` set,
      `/registry`, `/library`, `/preview`, `/generate` and `/draft` still
      answer unauthenticated. Agreed direction: require auth on every route
      when bound to a non-loopback interface, keep localhost open so the
      Docker quick start is unaffected. The loopback default above is the
      stopgap until this lands.
- [ ] **F-03/F-04 Egress is not default-deny**, and the Kafka policy check can
      be bypassed via nested `config.bootstrap.servers`, a safe first broker,
      or bracketed IPv6. Needs the policy applied to the *effective* producer
      config, not the pre-merge options.
- [ ] **F-05 Saved specs store sink credentials in clear JSON** — bearer
      tokens round-trip through `/library` verbatim.
- [ ] **F-09 No cost budget or active-job cap**: `expr: a * 1000000` yields a
      1 MB field from one row; 70 concurrent stream jobs started locally.
- [ ] **F-10 `/draft` is unauthenticated**, so provider quota is spendable by
      any caller.
- [ ] **F-11 Release workflows trigger on `pull_request` and consume signing
      secrets when present**; actions use floating tags and the base image is
      unpinned.
- [ ] **F-07 CSV/XLSX formula injection** and **F-08 T-SQL `]` not escaped** —
      both confirmed; output-injection, impact lands on the consumer.

## Non-goals (permanent)
- AI/ML training data production (INV-5)
- Data anonymization / production-data masking (that's Tonic's lane; chaff
  generates from nothing)
