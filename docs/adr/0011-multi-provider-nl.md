# ADR-0011: Multi-provider NL drafting (Anthropic / OpenAI / Google)

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-11

## Context
ADR-0010 shipped natural-language spec drafting on Anthropic. The drafting
contract is provider-agnostic — "a description in, one JSON `DatasetSpec`
out" — so nothing about it is Anthropic-specific. Teams have keys with
different providers; forcing one is needless friction.

## Decision
Support **Anthropic, OpenAI, and Google**, selected by **which API key is
present in the server environment**: `ANTHROPIC_API_KEY`, then
`OPENAI_API_KEY`, then `GOOGLE_API_KEY` (Anthropic wins ties). The browser
never sees a key.

- Each SDK is an **optional extra** and imported **lazily**: `nl`
  (anthropic), `nl-openai` (openai), `nl-google` (google-generativeai).
  Install only the one you use; a missing SDK fails with the exact
  `pip install 'chaff[...]'` hint.
- Per-provider default models are env-overridable
  (`CHAFF_ANTHROPIC_MODEL` = `claude-opus-4-8`, `CHAFF_OPENAI_MODEL` =
  `gpt-4o`, `CHAFF_GOOGLE_MODEL` = `gemini-1.5-flash`).
- OpenAI uses JSON mode (`response_format=json_object`) and Google uses
  `response_mime_type=application/json` to bias toward valid JSON, but the
  **same `load_spec` validation + one-retry** logic (ADR-0010) still gates
  every provider — a malformed draft never reaches the caller.

## Consequences
- Nothing else changes: still an interface that *produces a spec* (INV-1),
  still just drafting (INV-5), still a server-side key. `active_provider()`
  drives the `/draft` `503` (no key) and the UI message.
- **Verification boundary** unchanged: the selection, validation, and retry
  logic are unit-tested via an injected caller (no keys, no network); each
  provider's live call needs its key at runtime. (Note: in some sandboxes
  `google-generativeai` can't even import due to a broken native
  `cryptography` binding — the lazy import keeps that from affecting anyone
  who isn't using Google.)
- Adding a fourth provider later is one caller function + one extra.
