# ADR-0025: Access control on every route, closed by default

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03
- Supersedes the scope decision in [ADR-0018](0018-streaming-surface-hardening.md)

## Context

ADR-0018 introduced `CHAFF_API_TOKEN` and scoped it to the streaming surface,
closing with:

> The same dependency can be hung on other routes later if the API's exposure
> model changes.

Nobody did. An external red-team assessment (F-02) found that with
`CHAFF_API_TOKEN` configured, `/registry`, `/library`, `/preview`, `/generate`
and `/draft` all still answered unauthenticated. Reproduced before writing any
code: 15 routes existed and 4 were protected.

The damage is not just the open routes. It is that **setting the token looked
like protection and wasn't** — the same defect shape as ADR-0020 (a UI that
silently dropped spec fields) and ADR-0024 (a Compose file that silently
dropped its own hardening). An operator who read `.env.example`, set a token,
and exposed the service believed the whole API was gated.

ADR-0024 bought time by binding Compose to loopback, and said so: it narrowed
*who could reach* the open routes without fixing that they were open. This is
the fix.

## Decision

**1. Two rules, composing to "fail closed without breaking localhost".**

- **A token is set** → required on every API route, from every client,
  *including loopback*.
- **No token is set** → loopback is served, everything else gets a 401 that
  says what to do.

Not exempting loopback when a token is configured is the load-bearing half.
Behind a reverse proxy — the standard way anyone would actually expose this —
every request arrives from 127.0.0.1. A loopback exemption would silently
disable authentication for precisely the deployment that needs it, which is
the bug this ADR exists to stop repeating.

The no-token case keeps the zero-config demo exactly as it was: `docker
compose up`, `http://localhost:8000`, no token, everything works. That is
chaff's product target (Build DNA §0) and it is not being traded away.

**2. Middleware, not a per-route dependency.** This is the structural point.
F-02 happened because routes were added and nobody remembered to hang
`require_token` on them. With a dependency, a new route is **open until
someone notices**; with middleware, a new route is **protected until someone
deliberately exempts it**. The failure mode being fixed is forgetting, so the
default has to be closed.

A test enumerates the app's real routes and fails on any that is neither in
the guarded list nor the deliberately-open list, so "I added a route and
didn't think about access" is now a build failure.

**3. Only bootstrap paths stay open**: the page itself (you cannot type a
token into a page you can't load) and `/licenses` (attribution has to remain
readable in a redistributed build). Neither returns user data.

**4. The UI wraps `fetch` rather than editing each call site.** Same reasoning
as the middleware: a request added later carries the token automatically.
Editing a dozen call sites and missing one is how the server-side hole
happened; the client should not repeat it.

**5. A refused page explains itself.** Before this, a token-protected server
gave the UI two unhandled 401s, a console full of `Cannot read properties of
undefined`, and a gallery stuck on "Loading…" forever. The page now shows what
is wrong ("this server needs an access token" vs "that token was refused") and
recovers fully when a valid token is entered — both bootstrap calls retry, not
just the registry.

## Consequences

- **Remote callers of an unprotected instance now get a 401 instead of data.**
  Intentional, and the message names the fix.
- **Setting a token now requires it from localhost too.** Someone who set one
  for network access will need to paste it into the UI's Access token box. The
  box moved from the Stream tab to the header, because it no longer gates only
  streaming; the previously-saved value is still read for back-compat.
- **The WebSocket reports the real reason.** It hardcoded "missing or invalid
  API token" even when the actual problem was that no token was configured
  server-side, sending users hunting for a token that didn't exist. Both paths
  now ask the same helper.
- **`ipaddress` classifies loopback**, not a set of three strings — 127.0.0.2
  and the IPv4-mapped `::ffff:127.0.0.1` are loopback, and a literal set would
  have silently treated them as remote.
- **The test suite now presents as a local operator.** Starlette's TestClient
  defaults its peer to the literal `"testclient"`, correctly classified as
  remote, which failed 61 existing tests. `conftest.py` defaults it to
  loopback at import time (several modules build their client at module
  scope). The remote and token paths get explicit peer addresses in
  `tests/test_access_control.py` rather than relying on that default.

  One existing test improved as a side effect:
  `test_shutdown_refuses_a_non_loopback_caller` had been passing because
  `"testclient"` happens not to parse as an IP — an incidental property, not
  an assertion. It now names the remote address it tests.

## Alternatives considered

- **Require a token always, no localhost exemption.** Strictly simpler to
  reason about, and it breaks the quick start the entire product is built
  around: Joe would have to generate and paste a token before seeing a form.
  Rejected on the North Star.
- **Exempt loopback even when a token is set.** Friendlier for the operator
  who never uses localhost, and it silently disables auth behind a reverse
  proxy. Rejected — that is the failure mode, not a convenience.
- **Infer the bind address and require auth only when non-loopback.** Cleaner
  in theory; the app doesn't reliably know its own bind address (uvicorn is
  configured outside it), and inferring it wrong fails open. Per-request peer
  address is a fact, not an inference.
- **Keep the per-route dependency and add it to the 11 missing routes.** The
  smallest diff, and it leaves the next route open by default — it fixes the
  instance and not the cause.

## Still open from the assessment

F-03/F-04 (egress not default-deny; the Kafka policy bypassable through the
nested `config` merge), F-05 (sink credentials in saved specs), F-07/F-08
(spreadsheet formula injection, T-SQL identifier escaping), F-09 (no cost
budget or active-job cap), F-10 (`/draft` cost abuse — now authenticated,
but still unmetered), F-11 (release workflow hardening). Tracked in ROADMAP
Phase 8.
