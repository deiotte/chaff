# ADR-0019: Closing the `/stream` WebSocket connection-exhaustion DoS

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-17
- Follows: ADR-0018 (streaming-surface hardening)

## Context
ADR-0018 hardened the network-facing streaming surface — opt-in token auth, an
SSRF egress policy, WS record/second ceilings, bounded entity materialization.
A re-verification of that work (not trusting the description, re-running the
attacks) found the *record/second ceilings did not actually close the DoS the
ADR set out to close*, because they only bound a socket **after** the client
sends a spec. Two holes remained, both on the same connection-exhaustion class:

- **C-1 (the one that matters) — no bound on the opening handshake.** The
  `/stream` handler `await`s `websocket.receive_text()` for the client's first
  frame with **no timeout**. A client can connect and simply never send a spec;
  the coroutine parks there forever, holding a socket + task. The record/second
  ceilings never engage — they gate the *stream loop*, which this connection
  never reaches. `CHAFF_API_TOKEN` is off by default and the default Docker
  image binds `0.0.0.0`, so in the **supported default config** an
  unauthenticated remote client opens unlimited sockets and holds them open
  indefinitely. This is the slow-loris variant of the exact DoS ADR-0018 named.

- **C-3 — no ceiling on concurrent sockets.** Even with C-1 fixed, a socket
  that *does* send a valid spec is bounded per-connection (to `sec_ceiling`,
  default 300s) but nothing caps how many run at once. `stream_jobs` prunes its
  *finished*-job registry but never bounds *live* sessions; the WS path had no
  registry at all. A flood of valid-but-slow (`rate` low) streams still
  exhausts the single-process server for the full ceiling window, re-openable.

The same tension as ADR-0018 governs the fix: the north star (Build DNA §0) is a
single Office-Joe operator on localhost with zero config. The controls must
leave that path byte-for-byte unchanged and only bite abuse.

## Decision
Two small, always-on bounds on the `/stream` handler, both env-tunable. Neither
introduces a new concept — they are the missing time/space bounds on the socket
lifecycle that the ADR-0018 ceilings assumed but didn't provide.

- **Bounded opening handshake (C-1).** The wait for the client's first frame is
  wrapped in `asyncio.wait_for(..., CHAFF_STREAM_HANDSHAKE_TIMEOUT)` (default
  **10s** — generous for one JSON frame, tight against a held-open idle socket).
  On timeout the server sends a readable `{"error": "timed out …"}` frame and
  closes `1008`. This is the only place the server waits on the client — once
  streaming starts the server drives, so bounding the handshake bounds the whole
  idle-hold vector.

- **Concurrent-session ceiling (C-3).** A process-wide counter of live `/stream`
  sockets, capped at `CHAFF_STREAM_MAX_SESSIONS` (default **64**). A connection
  over the cap gets `{"error": "… at its live-stream capacity …"}` and a `1013`
  (try-again-later) close. The counter is a plain int: the WS handlers are
  coroutines on the one event loop, and the check-then-increment has no `await`
  between the two statements, so admissions can't interleave (no lock needed).
  It is incremented only for admitted sessions and released in a `finally` on
  every exit path — handshake timeout, bad spec, disconnect, or clean
  end-of-stream. Push jobs run in threads and are counted separately by
  `stream_jobs`; this ceiling is only the browser-held sockets.

Both live in the API layer (INV-1/INV-2 hold — the engine still only encodes,
sinks still only deliver). The handler body moved into a `_serve_stream` helper
so the admission gate (auth → capacity → count) wraps it cleanly.

## Consequences
- **Zero-config localhost is unchanged.** No env vars set → 10s handshake window
  (a real client sends in milliseconds) and 64 concurrent sockets (a single
  operator opens one or two). The demo behaves exactly as before; the bounds
  only engage under a flood.
- **The DoS class is now actually closed.** An idle socket is dropped after the
  handshake window (C-1); a flood of live sockets is refused past the ceiling
  (C-3). Together with the per-connection second/record ceilings from ADR-0018,
  no unauthenticated client can hold unbounded server resources.
- **Operator knobs.** `CHAFF_STREAM_HANDSHAKE_TIMEOUT` and
  `CHAFF_STREAM_MAX_SESSIONS` join the ADR-0018/0017 env ceilings; an operator
  exposing chaff tunes them to their host. Auth (`CHAFF_API_TOKEN`) still short-
  circuits *before* the capacity gate, so a wrong token never consumes a slot.

## Known residuals (re-verified, deliberately not changed here)
- **C-2 — WS token in the query string (LOW, accepted).** Browsers can't set
  headers on `new WebSocket`, so ADR-0018 accepts a `?token=` fallback; `auth.py`
  already prefers the `Authorization`/`X-Chaff-Token` headers and only falls back
  to the query param. The residual is that a browser-driven token can surface in
  proxy/server logs and browser history. No clean browser-side fix exists (it is
  the WebSocket protocol's own gap); non-browser clients should use the header
  forms. Left as documented behavior, not a code change.
- **C-4 — DNS-rebinding TOCTOU (INFO, still open by design).** ADR-0018 already
  disclosed that the egress check resolves at job-start while the sink resolves
  again at connect-time; pinning the resolved IP into the socket would cross into
  the sink (INV-2) and stays deferred. Re-verification confirmed it is still
  open, and separately ruled out two *additional* bypass hypotheses: an
  IPv4-mapped-IPv6 form (`::ffff:169.254.169.254`) is caught on the shipped
  Python (3.11) because `ipaddress.is_link_local` delegates to the mapped IPv4
  — a behavior fixed in modern CPython, so this bypass is not exploitable here —
  and a sink-option-key bypass (naming the host under an unvetted option) does not
  exist — `_sink_host` covers `url` / `host` / `bootstrap.servers` /
  `bootstrap_servers`, the full set every stream sink reads. So the residual is
  exactly the one disclosed, no wider.
