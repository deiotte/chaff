# chaff

**Fake signals convincing enough to work with.**

chaff generates synthetic demo data and test feeds from a reusable JSON spec.
Use it to populate a development system, test a migration, or give an integration
something repeatable to consume without copying production records. The same
engine serves the browser UI, CLI, and Python library.

Its useful difference from a bag of random rows is **relationships and known
answers**: a hearing can belong to its case's judge, a damaged extract can ship
with a separate clean answer key, and two sensor feeds can describe the same
scene under different IDs.

> chaff produces demo/test data only. It is not a training-data pipeline
> and will not become one. It is a working local/trusted-operator tool;
> shared-service hardening and realistic sensor/plant modelling remain limited.

## A migration test with an answer key

Suppose you are replacing a case system. Clean random rows will not test whether
your importer handles duplicate case numbers, two-digit years, damaged names,
retired status codes, or docket entries pointing at nonexistent cases.

After the [source setup](#python-source), run:

```bash
chaff generate examples/legacy_migration.json --format csv
```

The supplied spec writes four files:

| File | Rows | Give it to |
|---|---:|---|
| `out/legacy_cases.csv` | 2,000 | Your importer: case records with seeded defects |
| `out/legacy_docket.csv` | 6,000 | Your importer: related docket records, including orphans |
| `out/answer_key_cases.csv` | 2,000 | Your evaluator: expected values, defect flags, and intended actions |
| `out/answer_key_docket.csv` | 6,000 | Your evaluator: expected dates/descriptions and orphan flags |

Answer-key rows align one-for-one with their source rows. Hidden columns hold
clean values during generation so later columns and answer-key tables can read
them; those helper cells do not appear in the extracts. Keep the answer keys
apart from what the importer receives.

Omit `--format csv` for fixed-width `.txt` files. Use
`--format fixed_width_layout` for corresponding `.cpy` layout descriptions.
The format is shared by all four tables; there is no mixed-format run bundle.
These are synthetic migration fixtures, not an actual court's export format or
an authoritative migration policy. Your importer and evaluator still own the
transformation, reconciliation, and acceptance decision.

## Try it

### Python source

Python 3.10+ is declared supported; CI tests Python 3.12. From Linux:

```bash
git clone https://github.com/deiotte/chaff.git
cd chaff
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[api,formats-extra,streaming]'

chaff validate examples/retail_orders.json
chaff generate examples/retail_orders.json
uvicorn api.main:app --host 127.0.0.1 --port 8000
```

The retail command writes `out/customers.csv` (50 rows), `out/orders.csv`
(200 rows), and `out/lines.csv` (600 rows), with foreign keys connecting them.
Open [localhost:8000](http://localhost:8000), choose a **Library** preset,
**Preview**, then **Download**. Related tables download together as a ZIP.
Use **+ Related table** or **+ Time series** to author those modes in the UI.

For the core CLI/library alone, `python -m pip install -e .` is enough.
The API/UI live in the checkout's `api/` directory; run the server from the
repository root. `chaff validate` checks the spec contract, not every generator
option or destination: generate a small fixture before relying on a new spec.

### Docker

With Git and Docker Compose available:

```bash
git clone https://github.com/deiotte/chaff.git
cd chaff
docker compose up --build
```

Open [localhost:8000](http://localhost:8000). Compose publishes the web port on
loopback by default; stop with `Ctrl+C`. Saved specs persist in `./spec-library`
and CLI file output can use `./out`; UI downloads go to your browser.

The default image includes API, streaming, and Anthropic/OpenAI dependencies.
For XLSX/Parquet/Avro, copy `.env.example` to `.env`, set the following, and rebuild:

```dotenv
CHAFF_EXTRAS=api,formats-extra,streaming,nl,nl-openai
```

### Windows and macOS

[Release downloads](https://github.com/deiotte/chaff/releases/latest) provide
`chaff.exe`, a per-user `chaff.msi` installer, and `chaff-macos.zip` containing
`chaff.app`. The macOS build targets the runner's native architecture, currently
Apple Silicon, rather than a universal Intel/ARM bundle. Each app opens the
browser UI; **Quit chaff** stops its local server.

**Release packages contain their tagged source, not today's `main`.** The last
published release verified on 2026-10-21 is
[v0.2.0](https://github.com/deiotte/chaff/releases/tag/v0.2.0); the migration,
plant-feed, and later sensor work described here requires newer source until
another tag is released. Desktop bundles include extra file formats and
Anthropic/OpenAI drafting, but omit Kafka/MQTT clients and Google drafting.
Unsigned packages can trigger OS warnings.

[How releases build and attach the desktop files](docs/RELEASING.md) explains
the `v*` tag trigger, manual rebuilds, signing, and current version-label limits.

## Choose the fixture your test needs

| Test | Start with | Implemented behaviour |
|---|---|---|
| Related business records | [retail_orders](examples/retail_orders.json) | Customers, orders, and lines generated in dependency order |
| Court case integration | [case_management](examples/case_management.json) | Eight linked tables; child dates bounded by the case; hearings use its judge; magistrates match the court |
| Legacy migration | [legacy_migration](examples/legacy_migration.json) | Dirty extracts, hidden clean helpers, separate answer keys, fixed-width output and copybook-style layouts |
| Stateful records | [order_lifecycle](examples/order_lifecycle.json) | Stable entities advancing through seeded lifecycle states |
| Derived values | [orders_with_totals](examples/orders_with_totals.json) | Totals from earlier cells using a bounded expression interpreter |
| Linked geography | [crm_contacts_geo](examples/crm_contacts_geo.json) | Country-anchored geography, currency, and timezone values |
| TAK map input | [cot_tracks](examples/cot_tracks.json) | CoT XML position events; see the [TAK runbook](docs/TAK.md) |
| Cross-sensor correlation | [correlated_multikind](examples/correlated_multikind.json) | One scene as CoT and VMTI, different local IDs, a separate truth file |
| Plausible but wrong sensor reports | [displaced_parent](examples/displaced_parent.json), [mislabelled_units](examples/mislabelled_units.json), [skewed_clock](examples/skewed_clock.json) | Deliberate frame-centre, numeric-scale, and clock faults that require comparison with truth |
| Plant sample/write feed | [cps_case_feed](examples/cps_case_feed.json) | Scheduled setpoints and defrost windows, a lagging temperature, and `cps-feed/1` JSON lines |

`chaff registry` lists the installed generators, formats, sinks, and updaters;
`chaff library` lists the presets and saved specs. Generators include names
(`first_name` can read an earlier sex/gender column), IDs, distributions, dates,
and telemetry-shaped values. Presets are editable starting points, not validated
models of the organisations or devices they resemble.

### Plant feeds: events, not controller protocol bytes

```bash
chaff generate examples/cps_case_feed.json
```

This preset writes `out/cps_case_feed.jsonl`: one refrigerated case over 288
five-minute snapshots, producing 288 temperature samples and two setpoint writes.
`schedule` applies UTC time-of-day rules; `follow` moves temperature toward its
target with optional noise and a lift during defrost. Keep each schedule's
`base_time` and `interval_seconds` aligned with the output options.

The format emits nested `cps-feed/1` events with equipment/tag identifiers and
metadata. A `protocol_ref` such as `bacnet:…` is a label; chaff does not implement
a BACnet/Modbus wire encoder, run a controller, detect attacks, or learn safe
operating bands. A consuming adapter owns protocol encoding/decoding and any
decision. See [ADR-0045](docs/adr/0045-plant-feeds-schedule-follow-and-cps-feed.md).

**Historian compression is proposed, not implemented.**
[ADR-0046](docs/adr/0046-a-historian-is-a-whole-run-format-that-stores-only-what-moved-past-its-deadband.md)
describes a future `cps_history` whole-run format with per-tag deadbands,
contexts, and flags. There is no registered encoder or history preset yet;
the learner's answer key is also future work.

## One spec, one engine

The path is `spec → generate → encode → sink`:

1. The UI, CLI, or Python caller supplies a `DatasetSpec`.
2. The engine generates flat rows, resolves tables in dependency order, or
   advances stable entities through ticks. Related-table and entity modes
   cannot be combined in one spec.
3. A pure encoder turns records into bytes; a sink writes or delivers them.
   The API can instead return a download or serve a WebSocket.

Columns declare a registered generator and its parameters. `derived` reads
earlier cells; `fk` selects a parent; `parent` copies from that selected row.
Foreign-key filters support equality (`where`/`match`), and dates can use
`after`/`before` references. A filter with no qualifying parent yields null.
`hidden: true` keeps a generated helper cell out of public output.

Change the seed to create another fixture, or override selected settings:

```bash
chaff generate examples/crm_contacts.json --seed 7 --rows 50 --out out/small.csv
chaff generate examples/legacy_migration.json --format fixed_width_layout
```

For exact replay, preserve the spec, explicit seed, chaff revision, and dependency
versions. Tests compare repeated seeded output byte-for-byte, including file
metadata; dependency or code upgrades can change those bytes. Time-limited live
runs also depend on runtime speed for their final record count.

### Formats and delivery

| Formats | Files/downloads | Push streams | Encoding dependencies |
|---|---|---|---|
| CSV, JSON, NDJSON, CoT, `cps_feed` | Yes | Yes | Standard library |
| VMTI KLV (`klv`, `klv0601`) | Yes | One target per packet | Standard library |
| TSV, SQL, XML, `fixed_width`, `fixed_width_layout` | Yes | Not implemented | Standard library |
| XLSX, Parquet, Avro | Yes | Not implemented | `formats-extra` |

Sinks are `file`, `http`, `kafka`, `mqtt`, `tcp`, and `udp`. HTTP/Kafka/MQTT
require `streaming`; TCP/UDP use raw sockets with no TLS or authentication.
Streaming JSON encodes individual records rather than a batch array.
Multi-table and multi-observer runs are file/batch-only. KLV `frame_column`
mode groups several targets per frame and refuses push streaming.

The UI's **Stream** tab can **View live** over `/stream` or **Push** via a
background `/stream/jobs` job. Jobs require record/time bounds; status counts
records handed to the sink, not downstream acceptance. With `cps_feed`, one
snapshot can encode several event lines, so a record cap is not an event-line
cap. Stop and duration limits cannot forcibly interrupt blocked sink I/O.
See [operating controls](docs/OPERATIONS.md#live-streams).

### Sensor formats and truth

Standalone `klv` emits MISB ST 0903.6 VMTI. `klv0601` embeds it as Item 74 in a
minimal ST 0601 parent; this is not a general UAS Datalink encoder.
[vmti_frames](examples/vmti_frames.json) exercises multiple targets per frame;
[vmti_embedded](examples/vmti_embedded.json) includes frames missing required
centre metadata.

Observer scenes write one feed per observer and a separate `-truth.json` with
identity mappings, positions, speed/course values, event times, declared clock
offsets, and error radii. Use that answer key in the evaluator, never as input
to the receiving system. Local tests pin framing, checksums, mappings, and
reference vectors. ADRs describe separate-consumer decode results; that consumer
and its interoperability gate are not part of `make check`.

## Trust and current limits

- **Access:** without `CHAFF_API_TOKEN`, functional routes serve loopback
  callers only. With a token, all callers must provide it. The bootstrap UI,
  favicon, and license routes stay public. Set a token behind a reverse proxy;
  use HTTPS remotely. Browser WebSocket tokens appear in query parameters.
- **Destinations and credentials:** push jobs check egress policy; the trusted
  CLI does not. Saved specs reject recognised literal sink secrets, but push
  jobs still lack equivalent rejection/redaction and environment-placeholder
  resolution. Credential-bearing URLs can leak through job status/errors.
- **Resource controls:** row caps, stream caps, connection admission, and draft
  budgets are specific protections, not service-wide isolation. Batch output
  materialises in memory. Jobs and rate state are per-process/in-memory, with
  no tenant accounts, per-job ownership, durable history, or multi-worker coordination.
- **Output:** CSV/TSV formula guards, XLSX text typing, and SQL quoting cover
  specific consumer hazards. Inspect generated SQL and import behaviour.
  Hidden columns are an output-shaping feature, not a secret-storage boundary.
- **Realism:** motion is degrees per tick; reported speed is separate from
  displacement. There are no scripted routes, visibility/outage/reacquisition
  models, or coordinated live multi-observer feeds. The plant's first-order
  lag is a fixture, not validated equipment physics. chaff does not anonymise
  production data or provide general malformed-input fuzzing.

[Operating chaff](docs/OPERATIONS.md) preserves the detailed access/egress rules,
default ceilings, broker fixture setup, output guards, troubleshooting, and
residual risks. [TAK integration](docs/TAK.md) covers live event time, pacing,
plaintext test inputs, TLS relays, and receiver-side verification.

Optional **Describe it in English** drafting uses Anthropic, OpenAI, or Google
to propose a spec for review; generation still runs in the deterministic engine.
It needs the provider's extra/key, while ordinary generation needs neither.
Pasted keys persist in browser `localStorage` and pass through the chaff server
to the provider. See [drafting setup and budgets](docs/OPERATIONS.md#optional-ai-assisted-spec-drafting).

## Engineering evidence

From the checkout with your virtual environment active:

```bash
python -m pip install -e '.[dev,dev-browser,api,formats-extra,streaming,nl,nl-openai]'
python -m playwright install chromium
CHAFF_REQUIRE_BROWSER_TESTS=1 make check
make examples
```

`make check` runs tests and validates the example specs. Tests also generate and
encode shipped presets, verify parent/child consistency and migration defect
flags, compare seeded bytes, exercise auth/egress/budgets, and drive the real UI
to check whole-spec preservation. Browser tests skip locally without a browser;
CI requires them and the network-dependent pinned-action runtime checks. CI
also builds Docker. Desktop workflows build on their target OS and smoke-test
serving `/registry` and quitting through `/shutdown`.

These are repository regression checks, not independent proof of realism,
throughput, operational interoperability, real-certificate signing, or every
feature inside a frozen app. Dependencies use version ranges without a lockfile
or SBOM; action/base-image pins do not make desktop builds reproducible.

## Contributor map and next work

| Path | Role |
|---|---|
| `src/chaff/spec.py`, `engine.py` | Shared contract and execution path |
| `src/chaff/generators/`, `formats/`, `sinks/`, `updaters/` | Registered generation, encoding, delivery, and state-update plugins |
| `api/` | FastAPI service, browser UI, job lifecycle and access/egress/drafting controls |
| `examples/`, `tests/` | Runnable fixtures and regression checks |
| `packaging/`, `.github/workflows/` | Desktop launcher, installers, CI and release builds |
| `docs/adr/` | Decisions, proposed work, and scoped claims |

Read [AGENTS.md](AGENTS.md) for invariants and [ROADMAP.md](ROADMAP.md) for
status. Highest-value gaps include safe stream-secret handling and cancellation,
release/version alignment, stronger build/signing governance, scripted sensor
visibility, and the remaining case-management fixtures. Historian compression
has a design proposal; gRPC/protobuf and production-data masking remain out of scope.

## License

chaff is [MIT licensed](LICENSE). Desktop packages include
[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt) and expose license routes.
Run `make notices` and `make license-check` in the packaging dependency
environment described in the [Makefile](Makefile) when updating bundled attribution.
