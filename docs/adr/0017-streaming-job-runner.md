# ADR-0017: A streaming job runner for push sinks (UI Model 2)

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-15

## Context
The Stream tab surfaces two ways to serve a feed to Office Joe:

- **Model 1 — live view (WebSocket, ADR-0016 + `/stream`).** The browser holds
  the socket; records stream into the page; closing the tab stops it. Already
  works, no server-side lifecycle needed.
- **Model 2 — push to a broker/endpoint (kafka/mqtt/http/tcp/udp).** The
  browser can't hold a Kafka/MQTT connection, and these sinks have only ever
  run from the CLI (`chaff generate`). ADR-0007 explicitly parked "running a
  long-lived streaming sink from the API" as a job-queue concern. This ADR
  builds the minimum of that: enough to Start, watch, and Stop a push from the
  UI, safely.

A push is long-lived, so it can't be a normal blocking request. And an
operator clicking "stream to Kafka" must never be able to start something that
runs forever unattended.

## Decision
A small **in-process job runner** in the API layer (`api/stream_jobs.py`), not
the engine — the engine stays a pure library (INV-1).

- `start_job(spec, max_records, max_seconds, rate)` validates and launches a
  background daemon thread, returns a job id.
- `GET /stream/jobs/{id}` reports `status` (running/done/stopped/error),
  `sent`, `elapsed`, and `ended_reason`.
- `DELETE /stream/jobs/{id}` sets a stop event; the worker halts **between
  records** (cooperative — it never interrupts a sink mid-write).

The worker consumes `engine.stream_encoded(...)` — the *same* encode+pace+bound
pipeline `run()` uses, refactored out so the two never drift — wrapped in a
counting/stop generator. The sink is untouched (INV-2): it just receives the
capped, paced, stoppable iterator and delivers it.

**The guardrail (mandatory, not advisory).** Every job must declare **both** a
record cap and a time cap, each clamped to a hard server ceiling
(`CHAFF_STREAM_MAX_RECORDS` default 1,000,000, `CHAFF_STREAM_MAX_SECONDS`
default 300). A missing or zero cap, or one over the ceiling, is a 422 before
anything starts. When a run ends by hitting a cap (`ended_reason` `limit` or
`duration` → `hit_limit`), the UI re-confirms ("run again?") rather than
silently rolling on. A `stopped` run needs no re-confirm.

In stream mode the **record cap is the stream length**: it extends past the
spec's `rows` (ADR-0016), so a normal run always ends `limit` or `duration`,
never "short." `rows` is a batch-mode notion; streaming ignores it (the UI
prefills the record cap from `rows` as a convenience).

## Consequences
- INV-1/INV-2 hold: lifecycle is an API concern; the engine still only encodes,
  and sinks still only deliver. `stream_encoded` is the single shared pipeline.
- Fail-fast preserved: non-streaming sinks, whole-file formats, and multi-table
  specs are rejected at `start_job` (422), before a thread spawns.
- **Single-process assumption.** Jobs live in an in-memory registry, so
  Start/Status/Stop must land on the worker that owns the job — fine for the
  dev server and the single-process Docker image. A multi-worker deployment
  would need shared job state (Redis, a DB) — deferred; not needed for the
  Office-Joe demo target.
- The registry self-prunes finished jobs beyond a cap so it can't grow without
  bound in a long-lived process.
- Cooperative stop means a job blocked in a slow sink call (e.g. a broker
  flush) stops at the next record boundary, not mid-call — acceptable and
  avoids corrupting a sink's in-flight write.
