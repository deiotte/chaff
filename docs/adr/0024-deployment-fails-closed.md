# ADR-0024: The shipped deployment fails closed, and names can't be paths

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

An external red-team assessment of `e7bfc04` reported eleven findings. Every
one checked reproduced exactly as written, so the report is being treated as
accurate rather than re-litigated finding by finding. This ADR covers the two
addressed first; the rest are tracked in ROADMAP Phase 8.

They were chosen first because they are the two that need no product tradeoff:
one is a documented protection that silently did nothing, the other is an
arbitrary file write. Both are small. The larger item — authentication on
every route — trades directly against the Docker quick start and is being
designed separately.

### F-01: the deployment silently discarded its own hardening

`docker-compose.yml` published `0.0.0.0:8000` and forwarded exactly three
environment variables, all AI provider keys. `.env.example` documents
`CHAFF_API_TOKEN`, `CHAFF_STREAM_ALLOWED_HOSTS` and the stream ceilings — and
the application reads all of them from the *container's* environment. A value
in the host `.env` file reached Compose's interpolation and never reached the
process.

So the operator who read the docs, set a token, and believed the streaming
surface was authenticated had an unauthenticated one, published to their whole
network, with nothing anywhere reporting a problem. This is the same shape as
the ADR-0020 defect: a thing that looks configured and isn't.

### F-06: a table name was a path

`DatasetSpec.name` and `TableSpec.name` were length-checked and otherwise
unconstrained, and both become filenames — one file per table on disk, one
member per table in the downloaded zip. Confirmed locally on the merged tree:

- CLI: a table named `../escaped` wrote `escaped.csv` **outside** the
  requested output directory. Arbitrary file write, bounded only by the
  process's permissions.
- API: the same name produced a `../../outside/pwn.csv` member in the zip —
  dangerous to whoever extracts it.

This one is ours specifically. ADR-0020 added the multi-table zip and
sanitized the `Content-Disposition` filename and the `X-Chaff-Tables` header —
but not the member names, and not the sink paths. The lesson is that
sanitizing *one* place a hostile name surfaces isn't a fix; the name itself
had to stop being able to hold a path.

## Decision

**1. Compose forwards every setting the app reads, and a test enforces it.**
The list is derived by scanning the source for `CHAFF_*`; anything found that
is neither forwarded nor in an explicit `DELIBERATELY_NOT_FORWARDED` map (with
a stated reason per entry) fails the suite. The reverse is checked too: a
forwarded name that nothing reads is a setting the user can set with no
effect. Adding a setting to the code without wiring the deployment is now a
build failure rather than a silent hole.

**2. Compose binds to loopback by default.** `127.0.0.1:8000:8000`, overridable
with `CHAFF_BIND`. `http://localhost:8000` behaves exactly as before, so the
quick start in the README is untouched — but the container is no longer
reachable from the network. chaff's defaults (no auth on the generation
routes) assume one operator on localhost, and the deployment now matches that
assumption instead of contradicting it. Exposing it stays a deliberate act.

This is a stopgap, deliberately: it narrows *who can reach* the open routes
without fixing the fact that they are open. F-02 is the real fix.

**3. Names are validated at the contract, not at the write.** `DatasetSpec`
and `TableSpec` reject path separators, control characters, `.`/`..`
components, drive-letter prefixes, Windows device names, and surrounding
whitespace. Placing this in the spec means the CLI, the API, the UI and
anything built later inherit one rule (INV-1: the spec is the product) — a
check bolted onto the zip writer would have left the CLI file write open,
which was the more damaging half.

Validation is deliberately permissive about ordinary text: spaces, dots and
unicode all pass, because office Joe names a dataset `Q3 sales`.

**4. The write points check again anyway.** `_safe_member_name()` requires a
single path component, and `_run_multi` resolves each target and requires it to
stay under the requested directory. This should never fire. It is tested
independently — via `model_construct`, which skips validators the way a future
internal caller might — because an untested second layer is an assumption, not
a defence.

## Consequences

- **A previously-legal spec can now be rejected.** A dataset or table named
  with a slash, a leading dot, or `con` will 422 where it used to generate.
  No shipped preset or example uses such a name, and the alternative is
  keeping a file-write primitive.
- **`docker compose up` is unchanged for the documented flow** and now
  genuinely applies the documented settings.
- **Someone running Compose on a remote host loses network access on
  upgrade** until they set `CHAFF_BIND=0.0.0.0`. That break is the point, and
  `.env.example` explains it next to the token they should also be setting.
- **F-01's blast radius was wider than the streaming surface** it appears to
  guard: because auth only covers streaming (F-02), the loopback bind is
  currently the only thing standing between a network and `/generate`,
  `/library` and `/draft`. Recorded in `.env.example` so nobody reads the
  token setting as more protection than it gives.

## What this does not fix

F-02 through F-11 remain open and are listed in ROADMAP Phase 8 with their
reproduction status. In particular: authentication still covers streaming
routes only, egress is not default-deny, the Kafka policy is bypassable
through the nested `config` merge, saved specs still store sink credentials in
clear JSON, and there is still no cost budget or active-job cap.

## Alternatives considered

- **Slugify unsafe names instead of rejecting.** Silently renaming a user's
  table is its own small lie, and the failure mode of a slug collision is two
  tables writing to one file. Rejecting is louder and matches how the spec
  already treats unusable input.
- **Sanitize only the zip arcname**, as the report's remediation suggests as
  one option. That fixes the archive and leaves the CLI file write — the worse
  of the two — untouched.
- **Require a token by default in Compose.** Strictly safer, and it breaks the
  quick start the whole product is built around. The loopback bind gets most
  of the protection at none of that cost; the real answer is F-02's
  network-mode auth.
