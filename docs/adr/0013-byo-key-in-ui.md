# ADR-0013: Bring-your-own-key from the UI

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-11
- Amends: ADR-0011 (multi-provider NL drafting)

## Context
ADR-0010/0011 put the LLM key in a **server** env var, so turning on
"Describe it in English" meant `cp .env.example .env`, editing the file,
and `docker compose up --build`. For a non-engineer that's three unfamiliar
steps and a rebuild — the exact friction chaff exists to remove (D4O: an
office Joe shouldn't need an engineer). ADR-0011 stated "the browser never
sees a key"; that was the safe default but it made the marquee feature
effectively unreachable without editing files.

## Decision
Let the user **paste their key straight into the UI**. The `/draft` request
gains optional `api_key` and `provider` fields:

- The key is **stored only in the browser** (`localStorage`) and sent with
  each draft request. It is used for that call only — **never written to
  disk, never logged** server-side. `draft_spec(provider=, api_key=)` threads
  it to the chosen provider's client; all three SDKs already accept an
  explicit key, so this shares one code path with the env-var flow.
- **Provider auto-detect:** if the user doesn't pick one, the server infers
  it from the key shape (`sk-ant-` → Anthropic, `AIza` → Google, other
  `sk-` → OpenAI). An explicit dropdown overrides. Unknown shape with no
  pick → `400` asking them to choose.
- **Precedence:** a pasted key wins over any server key. With no pasted key,
  the server env key is used exactly as before (`active_provider()`), so
  existing deployments are unchanged.
- The default Docker image now bundles the **Anthropic + OpenAI** SDKs
  (`api,nl,nl-openai`) so a pasted Claude or GPT key works with no rebuild.
  Google stays opt-in via `CHAFF_EXTRAS` (heavier native deps); a pasted
  Google key without it returns the clear `pip install chaff[nl-google]`
  hint.

## Consequences
- **INV-1/INV-5 unchanged:** still an interface that *drafts a spec* the user
  edits; the engine still generates deterministically. This is not an ML
  pipeline.
- **Security posture, stated plainly:** the browser now holds the user's own
  key (their choice, their key) and sends it to their own chaff server, which
  forwards it to the provider and forgets it. chaff never persists or logs
  it. On a shared/hosted deployment the operator should terminate TLS so the
  key isn't sent in the clear — a deployment concern, noted in the README.
  The server-side env-var path (ADR-0011) remains for teams who prefer the
  key never touch the browser.
- The `.env` flow still works and is documented as the alternative for
  server-managed keys.
