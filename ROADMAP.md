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
- [x] **F-02 Auth covered streaming only (ADR-0025).** With `CHAFF_API_TOKEN`
      set, `/registry`, `/library`, `/preview`, `/generate` and `/draft` still
      answered unauthenticated — 15 routes, 4 protected. Now: a token, when
      set, is required on every route from every client including loopback (a
      reverse proxy makes every request look local, so a loopback exemption
      would disable auth for exactly that deployment); with no token, loopback
      is served and remote callers get a 401 that says what to do. The
      zero-config localhost demo is unchanged. Enforced by **middleware, not a
      per-route dependency** — the finding exists because routes were added and
      the dependency wasn't, so a new route is now protected until someone
      deliberately exempts it, and a test fails on any route not accounted for.
      The UI wraps `fetch` for the same reason, moved its token box to the
      header, and explains a refusal instead of dying on "Loading…".
- [x] **F-03/F-04 Egress policy checked the wrong thing (ADR-0026).** Three
      confirmed bypasses: only the first `bootstrap.servers` entry was vetted;
      bracketed IPv6 (`[fe80::1]`) didn't parse so "resolves to nothing" read
      as "not blocked"; and the nested `options.config` — a passthrough to
      confluent-kafka — could replace `bootstrap.servers` *after* the check,
      so the policy approved a safe broker while the producer was built with
      the metadata address. Now `kafka.effective_config()` computes the exact
      producer dict and both the sink and the policy call it, so they cannot
      disagree; `sink_hosts()` returns every endpoint, canonicalised in one
      parser. Strict egress (`CHAFF_STREAM_EGRESS`) allows only
      globally-routable unicast — written as an allow-rule because a deny-list
      misses CGNAT (not `is_private` in the stdlib) and multicast (reports
      `is_global`) — and defaults to on when a token is configured, i.e. when
      chaff is a network service rather than a local tool. **Deliberately not
      blanket default-deny:** pushing demo data to your own `localhost:9092` or
      `kafka:9092` is the feature, and tests assert the local demo still works.
- [x] **F-05 Saved specs stored sink credentials in clear JSON (ADR-0027).**
      A bearer token round-tripped through `/library` verbatim. Saving a spec
      with a literal secret is now refused before any bytes hit disk, with an
      error naming the field and the replacement; `"${MY_SINK_TOKEN}"` is
      resolved from the environment at run time so an authenticated sink stays
      expressible (refusing without an alternative would have removed a
      feature). Reads strip secrets from files written before the rule and
      report the paths so the UI can tell the user to rotate. Curated field
      list, not a heuristic — Kafka's `options.key` is a *message* key and a
      substring match would have flagged it.
      **Residual, not fixable by reading:** a credential already saved is
      still in the file, in backups and in git history. Rotate it.
- [x] **F-09 No cost budget or active-job cap (ADR-0029).** Two shapes of one
      thing: a cheap input buying expensive work. A derived formula built
      100 MB from one cell in 441 ms — and it does that *per row*, so the
      100,000-row API cap was no help (a 100-row preview would be 10 GB). The
      multiplier could come from a column, so the spec carried no suspicious
      literal, and the exponent guard was per-node, so `(10 ** 1000) ** 1000`
      walked through it. Now every amplifying operator is judged from its
      operands *before* it runs — refusals cost ~0 ms and allocate nothing —
      and size counts nesting, since `[[x] * 1000] * 1000` has a length of
      1000 and a million elements. Separately, `start_job` never counted
      running jobs (the existing ceilings bound each job's *length*, not how
      many start): 70 launched, 70 threads, no refusal. Now capped at 8
      (`CHAFF_STREAM_MAX_ACTIVE_JOBS`) with the count and insert under one
      lock hold, so 70 simultaneous starts admit exactly 8, answering **429**
      — the spec is fine, the server is busy.
      **Worth recording:** the backstop found the `%`-on-text amplifier
      (`"%1000000d" % 5` is a megabyte), not the design. The enumeration of
      amplifying operators was wrong, which is the argument for a check that
      doesn't depend on the enumeration being right.
      **Residual:** a sink hung on a socket still holds its slot (`max_seconds`
      bounds the generator, not the sink); the budget is per value, not a
      whole-run byte accounting; nothing here rate-limits requests.
- [x] **F-10 `/draft` had no cost budget (ADR-0030).** The report's headline —
      unauthenticated — was already closed by ADR-0025; the *cost* half was
      not. Re-measured as a local operator: a 5,000,000-character description
      reached the provider verbatim, and 40 rapid requests made 40 provider
      calls (each of which can be two, since an invalid draft is retried).
      A token says who may spend; nothing said how much. Now a 4,000-character
      prompt ceiling (413), 10 requests per minute per client address (429),
      and a 60s timeout on every provider call, all before anything reaches a
      provider. Rate 0 turns drafting off. The limits apply to a pasted key
      too — bring-your-own-key spends someone else's quota, but chaff is still
      the proxy.
      **Residual:** this rate-limits one route, not the API; per address is
      not per person; the retry still makes one request cost two calls.
- [x] **F-11 PR-triggered release workflows and mutable build inputs
      (ADR-0031).** Signing gated on whether the secret existed, not on which
      event was running — so a PR would decrypt the certificate into the
      runner, and it was interpolated into the script body rather than passed
      through `env`. Watched firing on four consecutive PRs in this series.
      PR builds stay (they caught three WiX errors during ADR-0023) and now
      never sign: the event check lives in the one step every signing step
      gates on, and the secret expression is empty on a PR so the certificate
      never reaches the runner. Also: all five actions pinned to commit SHAs,
      base image pinned by digest, least-privilege `permissions` on every
      workflow, and Dependabot — because a pin nothing bumps freezes an
      unpatched base, which is worse than the mutable tag it replaced.
      **Residual, and it matters:** a contributor who can edit the workflow can
      delete the gate. Only repository settings stop that — a protected
      Environment holding the signing secrets with required reviewers, and
      branch protection on `.github/workflows/**`. Neither is a code change.
      Pinning also does not *verify*: `pip install -e .` still resolves ranges
      at build time, with no lockfile or SBOM.
- [ ] **Bump the Node 20 actions.** `actions/checkout@v4` and
      `actions/setup-python@v5` are pinned at the versions in use and emit a
      deprecation warning. Bumping majors is a deliberate change with breaking
      potential, not a rider on a security fix — do it on its own.
- [x] **F-07/F-08 Output injection into spreadsheets and SQL (ADR-0028).**
      `=HYPERLINK(...)` reached CSV as a live formula and .xlsx as a real
      formula cell; a dataset named `x]; DROP TABLE audit;--` closed its own
      T-SQL bracket. Neither can hurt chaff — the impact lands on whoever
      *opens* the file — and both are reachable because a spec is shareable.
      The blanket OWASP fix was rejected on measurement, not taste: 131 of
      52,658 generated strings start with a formula lead and every one is a
      phone number, so prefixing them all writes `'+1-289-253-5482x18761`
      into the CRM preset. The guard instead asks whether a value can reach
      *outside its own cell* (function call, DDE pipe, sheet reference, UNC
      path); `formula_guard: strict|off` covers the other two intents and an
      unknown mode raises rather than silently defaulting. .xlsx is fixed
      losslessly by typing the cell as a string, so there the guard is
      invisible. SQL identifiers now escape their own delimiter (`]]`, `""`)
      rather than restricting names, since columns are permissive by design.
      **Residual:** under `smart`, `+1+1` still evaluates as inert arithmetic;
      `strict` removes even that.
- [x] **Entity specs lost their id and tick columns** (found while fixing
      F-07, ADR-0028 appendix). `chaff generate examples/order_lifecycle.json`
      crashed on `main`, and the same spec in SQL *silently* emitted
      `status, amount` only — 1,200 snapshots with no entity and no time.
      Column-oriented encoders read `spec.columns`, which never declares what
      the engine adds; row-oriented ones dump the row dict and were fine.
      `make check` validates the presets but never generates one, so it read
      green throughout. Fixed with `engine.encode_view()`, and a test now
      generates and encodes every example spec in its declared format.

## Phase 9 — Emit like a real sensor  ← IN PROGRESS
chaff's sensor-format encoders were written to prove the format worked, not to
exercise a consumer. Measuring the CoT encoder against a strict CoT 2.0 reader
found it reached 6 of the 12 fields that reader maps, and fabricated one.
- [x] **CoT emitter fidelity (ADR-0032).** `hae` no longer defaults to `0.0` —
      a consumer reads that as a measured zero height, not as absence, and CoT
      reserves `9999999` for "unknown". Same rule now covers `ce`/`le`, which
      come from columns instead of being pinned to the sentinel. `<track speed
      course>`, `<status battery>` and `<precisionlocation>` are emitted when
      the data carries them (the preset generated `heading` and the encoder
      dropped it). `<takv platform>` defaults to `chaff` so every event says in
      its own provenance field that a generator made it. Non-finite values can
      no longer reach the wire. All 12 mapped fields now reachable; verified by
      decoding the preset's 480 events with a real reader — 0 refusals, 0
      anomalies.
- [x] **Embedded VMTI: the other half of the position (ADR-0036).** A VMTI set
      normally travels inside an ST 0601 UAS Datalink frame as Item 74, with
      targets written as *offsets* from the frame centre the parent declares —
      so an embedded child is only half a position and the other half lives in
      a different Local Set. New `klv0601` format (a separate one: the
      Universal Label differs, so a decoder for one refuses the other), sharing
      the child encoder with `klv` so the two framings cannot drift.
      `withhold_frame_center_column` writes a deliberately non-conforming
      parent, changing the parent alone so the child bytes stay identical and
      the difference is provably the parent's. Angle and elevation mappings
      pinned byte-for-byte to the consuming repo's own builder; 240
      observations from 40 frames, 0 refusals, positions within one quantum.
- [x] **Observers: one scene, several accounts of it (ADR-0033).** An entity
      spec modelled a scene and then emitted it *as if* it were a feed, which
      works right up until a consumer has two inputs. `EntitySpec.observers`
      renders the same scene once per sensor — own ids, own bounded position
      error, own self-reported accuracy, own format-option overlay — one file
      each, and a `-truth.json` answer key naming which ids are the same
      entity. Different ids are the point: that two tracks are one thing is
      what a correlating consumer is supposed to work out. Adding an observer
      never perturbs the scene behind it (derived rng, pinned by test), and a
      colliding id pattern is refused rather than silently merging two
      entities into one. Example: `examples/correlated_scene.json`.
- [x] **MISB ST 0903.6 (VMTI) encoder (ADR-0034).** Binary KLV: BER-TLV
      framing, the universal label, the ST 0903.6-119 checksum, VTarget packs
      with nested VTracker and VObject sets, an Ontology series, and ST 1201
      IMAPB numeric packing. Stdlib only. The trap noted here before starting
      is the whole story: a mis-scaled IMAPB value is a well-formed number of
      the right width in the right place meaning something else, so the port is
      pinned locally against reference vectors from the other implementation
      and remotely by a conformance gate. All 21 attributes a real consumer
      declares are reachable — verified by decoding the preset's 240 packets
      through it: 0 refusals, 0 anomalies, 21/21 attributes, 11 confidence
      entries. Example: `examples/vmti_targets.json`.
- [x] **An observer may choose its own format (ADR-0034 §5).** Two sensors
      watching one scene need not speak the same language, and a consumer that
      fuses across a *format* boundary is the one worth rehearsing against.
      `examples/correlated_multikind.json` renders one scene as CoT XML and as
      VMTI KLV at once, with its own truth file.

- [x] **Multi-target frames and the culling ratio (ADR-0035).** ST 0903.6
      carries two counts per frame — targets detected and targets reported —
      and the gap between them is what the sensor chose not to say. With one
      target per packet that ratio is 1:1 forever, so a consumer's culling
      assessment could never be exercised by generated data at all.
      `frame_column` groups consecutive rows into one packet;
      `total_detected_column` lets a frame declare what it withheld. A framed
      spec refuses to stream rather than silently emitting one-target packets
      that would restore the very ratio it was written to avoid. Example:
      `examples/vmti_frames.json` — 40 frames of 6, report ratio moving
      0.188..1.000, a consumer's culling flag raised on 38 of 40.

### Deferred
- Embedded VMTI: an ST 0601 parent frame carrying a VMTI set as Item 74. The
  last shape of this format chaff cannot produce.

## Non-goals (permanent)
- AI/ML training data production (INV-5)
- Data anonymization / production-data masking (that's Tonic's lane; chaff
  generates from nothing)
