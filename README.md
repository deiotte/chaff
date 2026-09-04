# chaff

**Fake signals convincing enough to work with.**

chaff turns a reusable JSON spec into synthetic datasets and live test feeds.
It is for people building demos, testing integrations, or filling a development
system without copying production records. Define the shape once, preview it
in a browser or run it from the CLI, then deliver files or encoded records to
your own consumer.

The useful difference from unrelated random rows is consistency: orders can
reference generated customers, totals can calculate from prices and quantities,
and the same entities can move or change state over successive ticks.

> chaff produces demo/test data only. It is not a training-data pipeline
> and will not become one. It is a working local/trusted-operator tool, not
> a multi-tenant production service or a production-data masking system.

## The 30-second example

A retail demo needs customers, orders, and line items whose keys actually line
up. After the [source setup](#python-source-cli-library-and-ui), run:

```bash
chaff generate examples/retail_orders.json
```

The supplied spec produces:

| File | Rows | Relationship |
|---|---:|---|
| `out/customers.csv` | 50 | Parent customer records |
| `out/orders.csv` | 200 | `customer_id` references a generated customer |
| `out/lines.csv` | 600 | `order_id` references a generated order |

Load the same `retail_orders` preset in the UI and **Preview** shows all three
tables; **Download** returns a ZIP containing them. Save the spec and seed with
the demo so you can regenerate its data in the same software environment.

chaff supplies the fixture. Your database, dashboard, broker, or receiving
application still owns ingestion, validation, permissions, and any downstream
actions. Send feeds only to systems you control and have configured for tests.

## Try the browser UI

### Docker

With Git and Docker Compose available (Docker Engine with the Compose plugin,
or Docker Desktop):

```bash
git clone https://github.com/deiotte/chaff.git
cd chaff
docker compose up --build
```

Open [localhost:8000](http://localhost:8000), choose a **Library** preset,
click **Preview**, then **Download**. Use **+ Related table** or **+ Time
series** to author those modes in the page. Stop the server with `Ctrl+C`.

Compose publishes the web port on `127.0.0.1` by default. Saved specs persist
in `./spec-library`; the file-output directory is mounted at `./out`. Batch
UI downloads go to your browser, not to the spec's file-sink path.

The default image includes the API, streaming clients, and Anthropic/OpenAI
SDKs. **XLSX, Parquet, and Avro require the `formats-extra` extra**, which is
not in that default. To include them, copy `.env.example` to `.env`, set the
following, and rebuild:

```dotenv
CHAFF_EXTRAS=api,formats-extra,streaming,nl,nl-openai
```

### Python source: CLI, library, and UI

Python 3.10+ is declared supported; CI uses Python 3.12. From a cloned checkout
on Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[api,formats-extra,streaming]'

chaff registry
chaff validate examples/retail_orders.json
chaff generate examples/retail_orders.json
chaff generate examples/crm_contacts.json --seed 7 --rows 50 --out out/small.csv

uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open [localhost:8000](http://localhost:8000). For just the core library and
CLI, `python -m pip install -e .` is enough. The API/UI live in the checkout's
`api/` directory; these instructions do not assume a self-contained published
web-app package.

`chaff validate` checks the spec contract, not every generator option or a
remote destination. Generate a small fixture as well before relying on a new
spec in a demo.

### Desktop downloads

The current [v0.2.0 release](https://github.com/deiotte/chaff/releases/tag/v0.2.0)
publishes all three packages below. Release binaries reflect their tag;
`main` can contain newer formats and presets, so use the source path when you
need work merged after that release.

| Asset | How to use it |
|---|---|
| `chaff.exe` | Run the standalone Windows executable; it opens the browser UI. |
| `chaff.msi` | Per-user Windows installer with a Start-menu shortcut and uninstall entry. |
| `chaff-macos.zip` | Unzip `chaff.app` and move it to Applications; Apple Silicon is the current target. |

If an installer or app archive is absent, use the Docker/source path. Desktop
bundles include the extra file formats and Anthropic/OpenAI drafting, but not
every optional streaming/provider dependency; use Docker/source for the full
streaming setup. **Quit chaff** stops a desktop instance. Unsigned builds can
trigger OS security warnings; an asset filename does not establish that it
was signed or notarized. See [ADR-0023](docs/adr/0023-installers-and-signing.md).

## One spec, one engine

The flow is `spec → generate → encode → sink`:

1. The UI, CLI, or Python caller supplies a `DatasetSpec`.
2. The engine generates rows, resolves related tables, or advances entities.
3. A pure encoder turns records into bytes.
4. A sink writes a file or delivers encoded records. The API can instead
   return a download or serve records over a WebSocket.

Generators, encoders, sinks, and entity updaters register independently.
Changing the delivery transport does not require moving generation logic into
the UI or rewriting an encoder.

For a small flat dataset, save this as `contacts.json`:

```json
{
  "name": "contacts",
  "seed": 7,
  "rows": 5,
  "columns": [
    {"name": "id", "generator": "row_id"},
    {"name": "name", "generator": "full_name"},
    {"name": "email", "generator": "email"},
    {"name": "status", "generator": "choice_weighted", "params": {
      "values": ["open", "pending", "closed"],
      "weights": [0.7, 0.2, 0.1]
    }}
  ],
  "output": {"format": "csv"},
  "sink": {"sink": "file", "options": {"path": "out/contacts.csv"}}
}
```

Run `chaff generate contacts.json`. Change `seed` for a different fixture;
change `output.format` for a different encoding.

## What is implemented

- **54 semantic generators** for people, geography, identifiers, dates,
  telemetry, finance-shaped test values, text, and distributions. Registry
  examples supply parameter shapes to the UI; `chaff registry` lists the IDs.
- **Related tables:** `tables` plus `fk` columns generate in dependency order.
  See [retail_orders](examples/retail_orders.json).
- **Stateful entities:** `entity.count × entity.ticks` snapshots, including
  engine-supplied ID and tick columns. `movement`, `lifecycle`, and `drift`
  update state. See [moving_tracks](examples/moving_tracks.json) and
  [order_lifecycle](examples/order_lifecycle.json). Entity and related-table
  modes are mutually exclusive.
- **Derived columns:** expressions such as `unit_price * qty` use a bounded
  expression interpreter, not Python `eval`. See
  [orders_with_totals](examples/orders_with_totals.json).
- **Linked geography:** an opt-in country anchor supplies consistent city,
  timezone, currency, and position fields. See
  [crm_contacts_geo](examples/crm_contacts_geo.json).
- **Correlated scenes:** one moving scene can be rendered through several
  observers, each with its own identifiers, bounded position error, and output
  format. A separate truth file records identity and position for scoring. See
  [correlated_scene](examples/correlated_scene.json) and
  [correlated_multikind](examples/correlated_multikind.json).
- **Reusable specs:** 18 shipped presets, saved schemas, previews, downloads,
  and whole-spec round trips through the browser editors.

### File formats and streaming compatibility

Format and destination are separate, but not every format can be encoded one
record at a time:

| Formats | File/batch output | Push-stream output | Extra dependencies |
|---|---|---|---|
| CSV, JSON, NDJSON, CoT | Yes | Yes | None for encoding |
| VMTI KLV (`klv`, `klv0601`) | Yes | Yes, one target per packet | None for encoding |
| TSV, SQL, XML | Yes | Not implemented | None |
| XLSX, Parquet, Avro | Yes | Not implemented | `formats-extra` |

The six sinks are `file`, `http`, `kafka`, `mqtt`, `tcp`, and `udp`. HTTP,
Kafka, and MQTT need the `streaming` extra; raw TCP/UDP use the standard
library. Streamed JSON is a sequence of individually encoded records, not one
batch JSON array. Multi-table and multi-observer output are file/batch-only.
KLV specs using `frame_column` deliberately refuse push streaming because a
multi-target frame spans several generated records; dropping the option emits
one target per packet instead of silently changing the requested framing.

### CoT position feeds

[cot_tracks](examples/cot_tracks.json) generates **8 entities × 60 ticks =
480 newline-delimited Cursor-on-Target XML events**:

```bash
chaff generate examples/cot_tracks.json
```

The preset separates device identity (`ANDROID-…` UIDs) from operator
callsigns (`RAVEN-…`). The encoder supports position/accuracy fields, speed
and course, battery status, and position-source metadata. Missing or unusable
optional `hae`, `ce`, and `le` values use the unknown-value sentinel
`9999999.0`; absent optional detail attributes are omitted. A default
`<takv platform="chaff">` identifies the generator inside the event.

[ADR-0032](docs/adr/0032-cot-emitter-fidelity.md) records the fidelity change
and its output compatibility impact. It does not make chaff a physical sensor
simulator: movement is a degrees-per-tick model, and the preset's reported
speed is not derived from its positional displacement.

Times come from the spec/data, not the wall clock. The preset starts at
`2026-01-01T00:00:00Z`; a live receiver may consider it stale. Set an explicit
`output.options.base_time` appropriate to your test before sending it. The
synthetic marker is configurable, not an enforced isolation mechanism. Keep
fixtures separate from operational feeds and validate your receiving system.

#### Point it at a TAK Server

First identify the exact input that TAK exposes. chaff's `tcp` and `udp` sinks
are raw sockets: they add no TLS, client certificate, authentication message,
or TAK protocol negotiation. TCP opens one connection and writes complete CoT
events head-to-tail; UDP sends one event per datagram. Every event ends with a
newline, but TAK's streaming CoT parser finds message boundaries at
`</event>`, so it does not depend on that newline.

| TAK input | Point chaff at | Operational meaning |
|---|---|---|
| Plain `protocol="tcp"` | `sink: "tcp"`, using that input's host and port | Recommended direct path for an isolated lab input. TCP connection failures are reported. |
| Plain `protocol="udp"` | `sink: "udp"`, using that input's host and port | One CoT event per datagram. UDP cannot confirm that the server or application received it. |
| `protocol="tls"` with X.509 client auth | A loopback TLS relay, then point chaff TCP at the relay | chaff cannot connect directly because its raw TCP sink has no TLS or certificate options. |
| CoT-over-protobuf, gRPC, QUIC, or another negotiated input | A compatible adapter | chaff emits XML CoT; it does not negotiate these protocols. |

The upstream TAK Server example enables TLS on 8089 and leaves plaintext TCP
and UDP on 8087 commented out. Those are examples, not a promise about your
server: confirm the live input in **Configuration → Input Definitions** or
with its administrator. See the official
[TAK Server input example](https://github.com/TAK-Product-Center/Server/blob/main/src/takserver-core/example/CoreConfig.example.docker.xml)
and its
[streaming CoT parser](https://github.com/TAK-Product-Center/Server/blob/main/src/takserver-core/src/main/java/com/bbn/marti/nio/protocol/connections/StreamingCotProtocol.java).

If you administer an isolated TAK test environment, a dedicated plaintext
input can look like this in `CoreConfig.xml` (or be created in the input UI):

```xml
<input _name="chaff-lab-tcp" protocol="tcp" port="18087" auth="anonymous"/>
```

Use a unique port, restrict it at the host/network boundary to the chaff
sender, and apply the TAK group/filter rules your test clients require. Do not
expose an anonymous plaintext input to an untrusted network. Input changes and
restart requirements depend on how your TAK Server is managed.

For a normal mutual-TLS input, put TLS outside chaff. For example, a local
[`stunnel` client](https://www.stunnel.org/howto.html) can own the TAK
certificate while chaff sends plain CoT only to loopback:

```ini
; chaff-tak.conf -- stunnel 5 client mode
foreground = yes
client = yes

[chaff-to-tak]
accept = 127.0.0.1:19089
connect = tak.example.test:8089
cert = /secure/path/chaff-client-cert.pem
key = /secure/path/chaff-client-key.pem
CAfile = /secure/path/tak-ca.pem
verifyChain = yes
checkHost = tak.example.test
```

Start the relay with `stunnel ./chaff-tak.conf`, then use
`127.0.0.1:19089` as chaff's TCP destination. Obtain a test client identity,
private key, and CA chain from the TAK administrator; the hostname in
`checkHost` must match the server certificate. If the credentials were issued
as PKCS#12, use the administrator's approved PEM-export procedure. Keep keys,
passwords, and relay configuration out of the repository and saved specs.

Now create a run-specific spec. Run this immediately before each test so its
event times are current. The four AOI variables are optional; without them the
tracks appear in the preset's Los Angeles rectangle.

```bash
# Direct lab TCP example. For the TLS relay, use 127.0.0.1 and 19089.
export CHAFF_TAK_HOST=tak-lab.example.test
export CHAFF_TAK_PORT=18087
export CHAFF_TAK_SINK=tcp       # tcp or udp

# Optional test area of interest:
# export CHAFF_TAK_MIN_LAT=34.00 CHAFF_TAK_MAX_LAT=34.05
# export CHAFF_TAK_MIN_LON=-118.30 CHAFF_TAK_MAX_LON=-118.20
# export CHAFF_TAK_STEP_DEG=0.0002

python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

spec = json.loads(Path("examples/cot_tracks.json").read_text())
spec["name"] = "cot-to-tak"

columns = {column["name"]: column for column in spec["columns"]}
overrides = {
    "CHAFF_TAK_MIN_LAT": ("lat", "min"),
    "CHAFF_TAK_MAX_LAT": ("lat", "max"),
    "CHAFF_TAK_MIN_LON": ("lon", "min"),
    "CHAFF_TAK_MAX_LON": ("lon", "max"),
}
for variable, (column, bound) in overrides.items():
    if variable in os.environ:
        columns[column]["params"][bound] = float(os.environ[variable])

# Make the test identities obvious and keep event time aligned with delivery.
columns["callsign"]["params"]["pattern"] = "CHAFF-RAVEN-##"
spec["entity"]["id_pattern"] = "CHAFF-####"
movement = next(update for update in spec["entity"]["updates"]
                if update["updater"] == "movement")
movement["params"]["speed"] = float(
    os.environ.get("CHAFF_TAK_STEP_DEG", "0.0002"))
options = spec["output"]["options"]
options["base_time"] = datetime.now(timezone.utc).replace(
    microsecond=0).isoformat().replace("+00:00", "Z")

entity_count = int(spec["entity"]["count"])
ticks = int(spec["entity"]["ticks"])
interval = float(options["interval_seconds"])
sink = os.environ.get("CHAFF_TAK_SINK", "tcp").lower()
if sink not in {"tcp", "udp"}:
    raise SystemExit("CHAFF_TAK_SINK must be tcp or udp")

sink_options = {
    "host": os.environ["CHAFF_TAK_HOST"],
    "port": int(os.environ["CHAFF_TAK_PORT"]),
    "rate": entity_count / interval,
    "max_records": entity_count * ticks,
    "duration": ticks * interval + float(options["stale_seconds"]),
}
if sink == "tcp":
    sink_options["timeout"] = 10
spec["sink"] = {"sink": sink, "options": sink_options}

Path("cot-to-tak.json").write_text(json.dumps(spec, indent=2) + "\n")
print("wrote cot-to-tak.json with current UTC event time")
PY

chaff validate cot-to-tak.json
chaff generate cot-to-tak.json
```

The latitude/longitude bounds are starting positions. The runbook reduces the
preset's simple movement step to `0.0002` degrees per tick so tracks stay near
that AOI; `CHAFF_TAK_STEP_DEG` can override it. This remains a map-integration
fixture, not a physical motion model: the separately generated CoT `speed`
field is not derived from positional displacement.

The preset advances one tick every five event-seconds and reports eight
entities per tick, so the script sends at `8 / 5 = 1.6` events per second.
Using one event per second would make event time fall behind wall time until
later reports arrived after their own 30-second stale deadline. The generated
run is capped at 480 records and 330 seconds; normal completion takes about
five minutes.

Verify the whole path, not just the chaff receipt:

1. Before sending, connect a TAK client that can see the input's assigned
   group and zoom to the selected AOI.
2. Watch the TAK input's read/message counters or server logs while chaff is
   running.
3. Expect eight stable `CHAFF-RAVEN-…` markers, each updated 60 times. The
   `<takv platform="chaff">` detail provides a second synthetic-data marker.
4. After the final report, expect clients to remove or age the markers after
   the 30-second stale window according to their own policy.

`sent 480 record(s)` means the local TCP socket accepted 480 encoded events;
it is not proof that TAK parsed, routed, persisted, or displayed them. With
UDP, even that transport-level signal is unavailable.

### Correlated scenes and VMTI KLV

Observer-aware entity specs separate a scene from what each sensor claims
about it. A run writes one file per observer plus `-truth.json`; the truth file
maps observer-local identifiers to scene entities, records scene positions per
tick, and carries each observer's declared error radius. It is an evaluation
artifact—never an input to the consumer being scored.

The binary sensor presets cover several distinct shapes:

| Preset | What it exercises |
|---|---|
| [vmti_targets](examples/vmti_targets.json) | Standalone MISB ST 0903.6 VMTI KLV, one target per packet. |
| [vmti_frames](examples/vmti_frames.json) | Multi-target frames and the detected-versus-reported culling ratio. |
| [vmti_embedded](examples/vmti_embedded.json) | VMTI as Item 74 in a minimal ST 0601 parent, using target offsets from the frame centre; some frames deliberately omit that required centre. |
| [correlated_multikind](examples/correlated_multikind.json) | The same scene as CoT XML and standalone VMTI KLV, with different sensor-local IDs and a truth key. |
| [displaced_parent](examples/displaced_parent.json) | A deliberately wrong ST 0601 frame centre that decodes cleanly but moves every embedded target away from truth. |

`klv` and `klv0601` are separate format IDs because their Universal Labels and
framing differ. The embedded encoder supplies the parent metadata VMTI needs;
it is not a general ST 0601 UAS Datalink encoder. Multi-target files cannot use
a streaming sink, and observer scenes are file/batch-only; isolate one
observer and remove `frame_column` when a consumer needs packet-at-a-time
delivery.

Repository tests pin KLV structure, checksums, mappings, and reference vectors.
The ADRs also report end-to-end decode measurements against a separate
consuming implementation, but that consumer and its interoperability gate are
not bundled with chaff or run by `make check`. Start with
[ADR-0034](docs/adr/0034-vmti-klv-and-mixed-scenes.md) and
[ADR-0036](docs/adr/0036-embedded-vmti.md) for the conformance boundary.

## Live streams

The UI's **Stream** tab offers two different paths:

- **View live:** `/stream` serves records to the browser over a WebSocket.
- **Push:** `/stream/jobs` starts a background HTTP/Kafka/MQTT/TCP/UDP job,
  with status polling and cooperative stop.

Server-side streams have record/time ceilings (defaults: 1,000,000 records and
300 seconds); push jobs require both bounds. There are eight active push-job
slots by default, plus separate WebSocket admission/handshake limits. Settings
are documented in [.env.example](.env.example). The CLI is a trusted local
operator path: give streaming specs explicit `sink.options.max_records` and
`duration` if you do not want an open-ended run.

Generation is lazy, and streaming can continue beyond a flat spec's `rows`.
Each record remains a deterministic prefix member; a time-limited run's final
record count depends on runtime speed. Job `sent` counts records handed to
the sink, not proof that a downstream application processed them. Stop and
elapsed-time limits cannot forcibly interrupt a sink blocked in I/O.

For disposable local broker fixtures:

```bash
docker compose --profile streaming up --build
```

This starts Kafka and anonymous MQTT test services. Their published broker
ports are not loopback-restricted like the web port; isolate them from
untrusted networks. They are development fixtures, not production brokers.

## Optional AI-assisted spec drafting

**Describe it in English** asks Anthropic, OpenAI, or Google to draft a spec
that you review and edit. The deterministic engine still generates the data.
No model key is needed for presets, manual authoring, or ordinary generation.

For a source install, add `.[nl]`, `.[nl-openai]`, or `.[nl-google]`. Docker
includes Anthropic/OpenAI SDKs by default; to add Google while retaining the
other capabilities, set this in `.env` and rebuild:

```dotenv
CHAFF_EXTRAS=api,formats-extra,streaming,nl,nl-openai,nl-google
```

You can paste a key into the UI or configure `ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, or `GOOGLE_API_KEY` on the server. Pasted keys persist in
browser `localStorage` and travel **through the chaff server to the provider**;
they are not a direct browser-to-provider call. chaff's request path does not
write them to disk. Use HTTPS for any remote deployment and avoid storing keys
in a shared browser profile.

[ADR-0030](docs/adr/0030-draft-cost-budget.md) adds these defaults:

| Control | Default | Setting |
|---|---:|---|
| Description length | 4,000 characters | `CHAFF_DRAFT_MAX_CHARS` |
| Draft requests per client address | 10 per minute | `CHAFF_DRAFT_RATE_PER_MINUTE` |
| Provider SDK timeout | 60 seconds | `CHAFF_DRAFT_TIMEOUT_SECONDS` |

Set the rate to `0` to disable drafting. This is not a dollar quota or
API-wide rate limiter: one invalid draft can trigger a second model call,
SDK retries may add latency/requests, and clients behind one proxy can share
an address bucket. A configured timeout is not a hard deadline for the whole
draft-and-retry operation.

## Trust model and remaining limits

### Access and destinations

With no `CHAFF_API_TOKEN`, functional API routes serve loopback callers only.
When a token is set, all callers—including loopback—must provide it. The
bootstrap page, favicon, and license routes deliberately remain public so
users can load the UI and enter the token.

To publish the Compose web port beyond loopback, set `CHAFF_BIND=0.0.0.0`
**and** a strong `CHAFF_API_TOKEN` in `.env`. Use a token behind a reverse
proxy too; do not rely on the backend seeing the original client address.
HTTP accepts `X-Chaff-Token` or bearer auth. Browser WebSockets use a query
parameter, so keep tokens out of proxy/access logs.

Push-job destinations are checked before launch. Token-enabled instances
default to strict public-egress rules; `CHAFF_STREAM_ALLOWED_HOSTS` restricts
hosts and can explicitly permit an internal destination. Metadata/link-local
addresses are blocked unless their dedicated escape hatch is enabled. The
check does not eliminate DNS rebinding between validation and connection, and
it does not gate CLI egress. See
[ADR-0026](docs/adr/0026-effective-egress-policy.md).

### Spec and output safety

Dataset/table names are validated as safe filename components. Saved specs
reject recognized literal sink secrets and redact them from reads of legacy
specs; rotate any credentials already written to disk or history. The CLI
engine resolves environment placeholders in sink options.

**Stream-job credential handling is still incomplete.** The job path does not
apply the saved-library credential rejection or the CLI's placeholder
resolution. Credential-bearing HTTP URLs can appear in job destination,
receipt, and error text. Do not embed credentials in URLs or treat job status
as a secret store; restrict this API to trusted operators until this path is
hardened.

CSV/TSV use a `smart` formula guard by default; `formula_guard: "strict"`
escapes every formula-leading string, while `"off"` disables the guard.
XLSX strings are typed as text. SQL names and values are quoted, but generated
SQL should still be inspected before execution. These are specific output
protections, not a guarantee that every possible consumer interprets a file
safely. See [ADR-0028](docs/adr/0028-output-injection-guards.md).

### Resource and operational boundaries

- Batch downloads materialize records and the encoded payload in memory.
  `CHAFF_API_MAX_ROWS` defaults to 100,000, including related-table totals;
  a row ceiling is not a memory or total-output-byte budget.
- Jobs and drafting-rate state are per-process and in-memory. There is no
  durable job history, multi-worker coordination, account/tenant model, or
  per-job user ownership. A blocked sink can retain its slot.
- Only drafting has a request-rate limiter. Derived-expression budgets and
  stream caps do not provide whole-service resource isolation.
- Raw TCP/UDP delivery has no TLS, client-certificate, login, or application
  acknowledgement support. Use a restricted TAK lab input or an authenticated
  relay; do not treat a successful socket write as receiver acceptance.
- Synthetic values, simple motion/state, and bounded uniform observer error do
  not establish measured sensor realism, load capacity, or operational
  TAK/broker interoperability. There is no sensor bias, correlated error,
  dropout, or missed-detection model. chaff does not anonymize a real dataset
  or exercise general malformed-input fuzzing paths.

### Reproducibility and build integrity

Seed determinism is tested byte-for-byte, including file metadata and ZIP
members. Preserve the spec, seed, chaff revision, and dependency versions for
exact replay. Version upgrades can deliberately change bytes: ADR-0032's CoT
fix is one such change, so review stored golden files when upgrading.

GitHub Actions are SHA-pinned, the application Docker base is digest-pinned,
workflow permissions default to read-only, and Dependabot proposes updates.
As written, PR packaging runs do not sign; non-PR builds sign only when the
required credentials are configured. Tests guard those workflow rules.

This is **not** a reproducible-build or supply-chain-verification guarantee:
Python dependencies still resolve version ranges, there is no dependency
lockfile/SBOM, and fixture images are not all digest-pinned. Workflow code
cannot enforce its own protection against someone who can edit it; protected
signing environments and required review are repository-configuration work.
See [ADR-0031](docs/adr/0031-build-input-integrity.md).

## Troubleshooting

| Symptom | What to check |
|---|---|
| Port 8000 is occupied | Change the Compose mapping to `"${CHAFF_BIND:-127.0.0.1}:8080:8000"`, then open port 8080. |
| The page loads but API calls return 401 | Enter the configured access token. Container/proxy networking may make local traffic appear remote; set a token rather than weakening the loopback rule. |
| XLSX, Parquet, or Avro fails with a missing dependency | Add `formats-extra` to the source install or Docker `CHAFF_EXTRAS` and rebuild. |
| AI drafting reports a missing SDK or key | Install the provider's extra and supply its key; Google is not in the default image. |
| Drafting returns 413, 429, or 503 | Shorten the description, wait for the rate window, or check whether drafting is disabled. |
| TCP to a TAK port is refused or reset immediately | Confirm the configured input protocol. Plain chaff TCP cannot negotiate the common mutual-TLS input; use the TLS relay path. |
| chaff reports `sent`, but TAK shows no markers | Check the input's read/message counters, server parsing logs, group visibility, event time/stale window, AOI, and client filters. A socket receipt is not end-to-end acceptance. |
| Early CoT markers appear but later updates disappear | Refresh `base_time` immediately before the run and pace at `entity.count / interval_seconds`; otherwise reports can arrive after their stale time. |
| UI Push rejects an internal TAK hostname | Add the exact destination to `CHAFF_STREAM_ALLOWED_HOSTS` on a token-enabled deployment. The source CLI does not apply the API egress policy. |
| Docker cannot reach a TAK server at `localhost` | `localhost` inside the chaff container is the container. Use a routable TAK/relay address on the container's network. |

## Engineering evidence and development

From the checkout, with the virtual environment active:

```bash
python -m pip install -e '.[dev,dev-browser,api,formats-extra,streaming,nl,nl-openai]'
python -m playwright install chromium
CHAFF_REQUIRE_BROWSER_TESTS=1 make check
make examples
```

`make check` runs tests and validates every preset. Tests also generate and
encode the shipped examples, exercise referential integrity and stateful
output, compare deterministic bytes, cover auth/egress/output guards, and
check drafting budgets and release-workflow configuration. The browser suite
drives the real page/server and checks the spec sent on the wire.

Without Playwright/Chromium, plain `make check` skips the browser tier. CI
installs Chromium and requires it, and separately builds the Docker image.
Desktop workflows smoke-test launch and shutdown on their target OS. These
checks do not establish visual-regression coverage, real-certificate signing,
production broker interoperability, or a throughput benchmark.

For bundled-dependency attribution, install `pip-licenses`, then run
`make notices` and `make license-check` in the packaging dependency environment
specified in the [Makefile](Makefile).

## Repository map and next work

| Path | Role |
|---|---|
| `src/chaff/` | Spec contract, engine, generator/format/sink/updater registries |
| `api/` | FastAPI transport, static UI, job lifecycle, access/egress/drafting controls |
| `examples/` | Reusable demo specs |
| `tests/` | Engine, API, security, browser, and packaging checks |
| `packaging/` | Desktop launcher, frozen-app configuration, installer tooling |
| `docs/adr/` | Design decisions and explicit residual risks |

Read [AGENTS.md](AGENTS.md) for the build invariants and
[ROADMAP.md](ROADMAP.md) for status. Shared-deployment credibility still needs
complete stream-secret handling, service-wide resource controls, release/version
alignment, and stronger build/signing governance. Sensor-format work now covers
standalone and embedded VMTI, multi-target frames, multi-observer scenes, and
geometry-bearing answer keys, but the sensor models and ST 0601 parent remain
deliberately narrow. gRPC/protobuf remain deferred.

## License

chaff is [MIT licensed](LICENSE). The desktop packaging configuration includes
[THIRD-PARTY-NOTICES.txt](THIRD-PARTY-NOTICES.txt); the app exposes license
routes for packaged attribution. Review the notices for bundled dependencies.
