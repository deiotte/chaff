# ADR-0018: Hardening the network-facing streaming surface

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-16

## Context
Phase 6 (ADR-0016/0017) added chaff's first *network-facing* surface: the
`/stream` WebSocket and the `/stream/jobs` push-job runner. A security review of
PRs #27/#28 found four issues that are blocking the moment chaff is reachable by
anyone but a single trusted operator on localhost — and the default Docker image
binds `0.0.0.0`, so "reachable" is the norm, not the exception:

1. **Unauthenticated job disclosure / hijack.** `GET /stream/jobs` lists every
   job with its destination (internal host:port); `DELETE /stream/jobs/{id}`
   lets anyone stop anyone's job. There is no auth anywhere on the API.
2. **SSRF / blind port-scan oracle.** `POST /stream/jobs` lets an unauthenticated
   caller aim raw TCP/UDP/HTTP/MQTT writes at any host — including the cloud
   metadata endpoint (169.254.169.254) via the http sink — and read
   connect-success/refused/timeout back off the job's `error` field: a
   network-mapping primitive against internal infra.
3. **No WS ceiling once the client sets the bound.** The push-job runner clamps
   every cap to a hard server ceiling, but the `/stream` WS applied the client's
   `max_records`/`duration` verbatim and had *no* time bound at all — a couple of
   held-open sockets DoS the single-process server.
4. **Unbounded entity materialization blocks the event loop.** A spec with
   `entity.count = 1_000_000` and `max_records = 1` still materialized all 1M
   entities synchronously before the cap was checked — one WS message freezes the
   whole server.

The tension: chaff's north star (Build DNA §0) is *Office Joe opens the UI on
localhost and it just works* — zero config. Bolting on mandatory multi-tenant
auth would break that, and there are no tenants to authenticate anyway (chaff has
one operator). The fix has to keep the localhost demo zero-config while making
network exposure *safe to opt into*, not a foot-gun by default.

## Decision
Four proportionate, mostly opt-in controls. Defaults leave the localhost demo
byte-for-byte unchanged; each control is what an operator turns **on** when they
expose chaff.

- **Opt-in shared-secret auth (`api/auth.py`).** `CHAFF_API_TOKEN`, unset by
  default. When set, `/stream/jobs*` (via a FastAPI dependency) and the `/stream`
  WS require a matching token — `Authorization: Bearer …` or `X-Chaff-Token`, and
  for the browser-held WS a `?token=` query param (browsers can't set WS
  headers). A single operator secret, not a user model; gating the whole
  streaming surface is the right grain because there is no per-user ownership to
  express. Constant-time compare. Addresses findings 1 & 2's *unauthenticated*.

- **Egress policy on job destinations (`api/netpolicy.py`).** Vetted at
  `start_job`, before any thread/socket exists, so a blocked host is a clean 422.
  Cloud-metadata / link-local addresses (IPv4 169.254.0.0/16, IPv6 fe80::/10, and
  fd00:ec2::254) are **always** blocked — no legitimate demo streams there, and
  it's the highest-value SSRF target. Loopback and private ranges stay allowed so
  `localhost:1883` / `kafka:9092` brokers keep working. `CHAFF_STREAM_ALLOWED_HOSTS`
  is an opt-in allowlist for operators who expose chaff; `CHAFF_STREAM_ALLOW_LINK_LOCAL=1`
  is the escape hatch for the rare trusted case. Addresses finding 2. This is an
  API-layer concern, not the engine's — the CLI operator streams from their own
  machine to wherever they like and is deliberately not gated (INV-1/INV-2 hold).

- **WS applies the same ceilings as the job runner.** `/stream` now clamps
  `max_records` to `CHAFF_STREAM_MAX_RECORDS` and bounds every socket by
  `CHAFF_STREAM_MAX_SECONDS` — even a client that sent no `duration` is cut at the
  ceiling, so a held-open connection can't stream unattended. Invalid query params
  now error explicitly instead of silently degrading to unlimited (the LOW
  finding). Addresses finding 3.

- **Entity materialization bounded by the cap.** `iter_entity_rows` materializes
  only `min(count, limit)` entities. When the cap is smaller than the entity
  count the run ends inside tick 0, before any per-tick update, so the skipped
  entities are never observed — output stays byte-identical (INV-3) while
  `count = 1_000_000, max_records = 1` now materializes one entity, not a million.
  Addresses finding 4.

## Consequences
- **Zero-config localhost is unchanged.** With no env vars set, auth is off, the
  allowlist is empty, and only the (universally safe) metadata block is active —
  the demo behaves exactly as before. The UI gains one optional "Access token"
  field, blank by default.
- **Invariants hold.** Auth and egress policy live in the API layer; the engine
  still only encodes and sinks still only deliver (INV-1/INV-2). The entity fix
  is determinism-preserving (INV-3) — the eager `limit=None` path is untouched.
- **Known residual — DNS rebinding.** The egress check resolves the host at
  job-start; the sink resolves again at connect-time, so a name that rebinds
  between the two isn't fully closed. Pinning the resolved IP into the socket
  would cross into the sink (INV-2) and is deferred. The always-on metadata block
  plus an allowlist of names you control cover the realistic exposure.
- **Known residual — duration-bounded entity streams.** A duration-only WS
  stream over an entity spec can still materialize up to the record ceiling of
  entities up front. That's now bounded by the operator's declared ceiling
  (default/env) rather than the spec's arbitrary count; fully chunked per-entity
  materialization is a larger refactor, deferred.
- **Single-process assumption (unchanged from ADR-0017).** The token is a single
  shared secret held in the process env; a multi-worker deploy with real users
  would need a different model. Out of scope for the Office-Joe target.
