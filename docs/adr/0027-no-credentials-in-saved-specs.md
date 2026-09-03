# ADR-0027: A saved spec holds references, not secrets

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

The external assessment's F-05: `src/chaff/library.py` serialised complete
specs including `sink.options`, and `/library` returned those records
directly. Reproduced on the merged tree — a spec saved with
`Authorization: Bearer SUPER_SECRET_TOKEN` was read back with the value
intact.

The problem is what a spec *is*. INV-1 makes it the product: a document you
save, list over the API, load in the UI, commit to a repo, paste into an
issue, attach to a support bundle. Every one of those is a way for a
credential to travel. "The library file is only readable by the operator" is
true of the file and false of everything the file becomes.

## Decision

**1. Saving a spec with a literal secret is refused.** Loudly, at
`save_named`, before any bytes reach disk, with a message naming every
offending field and the exact replacement. "This spec contains credentials"
alone would leave someone guessing at what a spec may contain.

**2. A saveable way to express the same thing.** `"${MY_SINK_TOKEN}"` in a
sink option is resolved from the environment at run time by the engine,
before the sink is dispatched. Without this, refusal would be a functional
regression: an authenticated HTTP sink would become unsaveable, and the honest
description of that change would be "we removed a feature."

Resolution happens once in `engine.run()` rather than in each sink, so every
sink gets it and none has to know the convention. It resolves the sink's own
*configuration*, not the payload — encoders and sinks are untouched (INV-2).

An unset variable is deliberately left as the literal placeholder, so the sink
fails naming its destination instead of silently sending an empty credential
and returning an opaque 401 that reads like a server fault.

**3. A curated list, not a heuristic.** `sink.options.key` is Kafka's static
*message* key. "Contains the substring key" would have flagged it, and a check
that cries wolf is a check people learn to route around. `username` is
likewise not blocked: half a credential is not one, and blocking it stops
people saving otherwise-shareable specs for no security gain.

**4. Reads are redacted for files written before this rule.** `load_named`
and `list_specs` strip secrets on the way out and report the removed paths as
`_redacted`, which the UI surfaces along with the advice to rotate. Values are
*removed* rather than replaced with a placeholder, because a
`"***REDACTED***"` string under a `password` key would itself be refused on
the next save — which would strand the spec.

## Consequences

- **Saving an existing spec that embeds a credential now fails.** That break
  is the point. The error names the field and the replacement.
- **The secret is still on disk in files already saved.** This is the residual
  risk and it is not fixable by reading: redaction protects the API and the
  UI, not a backup, a git history, or a support bundle made from
  `spec-library/`. **Any credential that was ever saved must be rotated.**
  A test asserts we do not silently rewrite those files, so this stays a
  deliberate operator action rather than something that looks handled.
- **`${VAR}` reads operator-chosen names**, which Compose cannot pre-enumerate
  — its `environment:` block lists variables explicitly. `.env.example` says
  so, because a placeholder that silently never resolves is the failure mode
  ADR-0024 was written about.
- **The generic suggestion carries no `CHAFF_` prefix** (`MY_SINK_TOKEN`, not
  `CHAFF_MY_SECRET`): it is the operator's variable, not a chaff setting. This
  also stopped the deployment guard in `tests/test_deployment_config.py`
  reading the placeholder as a setting Compose had forgotten to forward —
  which it did, correctly, on the first attempt.
- **`CHAFF_MQTT_PASSWORD` / `CHAFF_MQTT_USERNAME` keep working** and are what
  the error suggests for MQTT, since chaff already reads them.

## Alternatives considered

- **Strip silently on save.** Gentler, and it is the failure mode this
  codebase keeps getting caught by: silently altering what someone saved
  (ADR-0020's dropped spec fields, ADR-0024's inert Compose settings). If the
  spec cannot hold the secret, the person saving it needs to know.
- **Redact on read only, leave saving alone.** The smallest change, and the
  secret keeps landing on disk with every save — the leak continues, just
  through backups instead of the API.
- **Encrypt secrets at rest in the library.** Real key management (where does
  the key live, who rotates it) for a tool whose product target is one
  operator on a laptop. A reference to the environment achieves the same
  outcome with no key to manage.
- **A broad heuristic on option names.** Would have caught more, and flagged
  Kafka's message key on day one. A false positive on a core option teaches
  users the check is noise.

## Still open from the assessment

F-07 (CSV/XLSX formula injection), F-08 (T-SQL identifier escaping), F-09
(no cost budget or active-job cap), F-10 (`/draft` is authenticated as of
ADR-0025 but still unmetered), F-11 (release workflow hardening). Tracked in
ROADMAP Phase 8.
