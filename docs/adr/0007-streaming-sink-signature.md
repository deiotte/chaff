# ADR-0007: A second, streaming sink signature

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-11

## Context
Phase 1 sinks are blob sinks: `(spec, payload: bytes) -> str`. The whole
dataset is encoded to one `bytes` and handed over. That's right for `file`,
but wrong for delivery that runs over time — HTTP POST, Kafka, TCP/UDP —
where records go out one at a time, paced. `sinks/AGENTS.md` is explicit:
build streaming as a *second sink signature negotiated by the engine*, not
by making the file sink stream and not by buffering a stream back into a
blob.

## Decision
Two sink shapes, two registries, one negotiator:

- **Blob sink** `@sink` → `(spec, payload: bytes) -> str`. Unchanged.
- **Streaming sink** `@stream_sink` → `(spec, records: Iterator[bytes]) -> str`.
  Receives already-encoded record-bytes, one at a time.

`engine.run()` negotiates: if the sink id is in the streaming registry, the
engine encodes **per record** and feeds the sink; otherwise it encodes the
whole payload and calls the blob sink. Per-record framing comes from a new
`@record_encoder(fmt)` registry in `formats` — only record-oriented formats
(ndjson, json, csv) register one. Whole-file formats (sql, xlsx, parquet,
avro, xml) register none, so pairing them with a streaming sink fails fast
with a clear message rather than emitting nonsense.

**Rate control** (records/sec, from `spec.sink.options.rate`) is applied
once, by the engine, wrapping the record iterator (`rate_limited`) before it
reaches the sink. Every streaming sink is paced uniformly; none reimplement
it. Pacing is delivery-time only — it never touches generation or payload
bytes, so INV-3 is unaffected.

## Consequences
- Format ⟂ sink holds (INV-2): the engine encodes; the sink only groups
  (batching) and delivers bytes. A sink still never parses payload content.
- New streaming sinks are pure registry additions (INV-4): `http` this
  chunk; `kafka`, `tcp`, `udp` slot into the same signature next.
- Rows are still materialized by `generate_rows` before streaming; the
  streaming contract here is per-record *delivery* + pacing, not lazy
  generation. The record-iterator seam makes lazy generation a later
  drop-in (yield rows instead of returning a list) with no signature change.
- The API's synchronous `/generate` remains the blob/download path; running
  a long-lived streaming sink from the API is a job-queue concern (still a
  Phase 2 API TODO), out of scope here.
