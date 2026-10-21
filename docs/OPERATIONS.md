# Operating chaff

[Back to the README](../README.md) · [TAK integration](TAK.md) · [Releases](RELEASING.md)

## Live streams

The UI's **Stream** tab offers two different paths:

- **View live:** `/stream` serves records to the browser over a WebSocket.
- **Push:** `/stream/jobs` starts a background HTTP/Kafka/MQTT/TCP/UDP job,
  with status polling and cooperative stop.

Server-side streams have record/time ceilings (defaults: 1,000,000 records and
300 seconds); push jobs require both bounds. There are eight active push-job
slots by default, plus separate WebSocket admission/handshake limits. Settings
are documented in [.env.example](../.env.example). The CLI is a trusted local
operator path: give streaming specs explicit `sink.options.max_records` and
`duration` if you do not want an open-ended run.

Generation is lazy, and streaming can continue beyond a flat spec's `rows`.
Each record remains a deterministic prefix member; a time-limited run's final
record count depends on runtime speed. Job `sent` counts records handed to
the sink, not proof that a downstream application processed them. For
`cps_feed`, one generated snapshot can produce several event lines; record
limits and receipts count snapshots, not individual lines. Stop and
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

[ADR-0030](adr/0030-draft-cost-budget.md) adds these defaults:

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
[ADR-0026](adr/0026-effective-egress-policy.md).

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
safely. See [ADR-0028](adr/0028-output-injection-guards.md).

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
  TAK/broker interoperability. Explicit frame-centre, scale, and clock faults
  are available, but there is no calibrated sensor model, correlated noise,
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
See [ADR-0031](adr/0031-build-input-integrity.md).

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
