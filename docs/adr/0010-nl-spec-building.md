# ADR-0010: Natural-language spec drafting

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-11

## Context
Phase 3 calls for a "describe the dataset in plain English, get a draft
spec to review/edit" box in the UI. Two invariants shape it:

- **INV-1** — every interface produces a `DatasetSpec`; only the engine
  consumes one. Natural-language drafting is one more spec-*producing*
  interface, so it must not live in the engine.
- **INV-5** — chaff generates demo/test data and is **not** an AI/ML
  dataset pipeline. Using an LLM to *draft a spec* is a spec-building
  convenience, not dataset production: the engine still generates the data
  deterministically from the (reviewed, edited) spec.

## Decision
- Lives in the **API layer** (`api/nl.py`), not `src/chaff` — it produces a
  spec like the UI/CLI/API do (INV-1). The engine is untouched.
- Uses the **Anthropic SDK** with a **server-side key** (`ANTHROPIC_API_KEY`);
  the browser never sees it. Model: `claude-opus-4-8`. `anthropic` is an
  optional dependency under the `nl` extra, imported lazily so the API runs
  without it.
- The model returns a JSON `DatasetSpec`, which is **validated with
  `load_spec`**; on failure the error is fed back for **one correction
  round-trip** before giving up. A malformed draft never reaches the caller —
  the spec is still the product, and only valid specs leave this boundary.
- `POST /draft` → `400` (empty), `503` (no key configured), `502`
  (undraftable after retry), else the validated spec dict. The UI loads it
  into the builder for review and edit — it is a **draft**, not a commit.

## Consequences
- **INV-5 intact**: the LLM drafts a spec; generation stays deterministic and
  seeded. The README's demo-data-only statement is unchanged. This is the
  one sanctioned LLM touchpoint (a Phase 3 roadmap item), and it stops at
  drafting.
- **Verification boundary**: the drafting logic (JSON extraction, validation,
  retry) is unit-tested via an injected caller — no key, no network. The
  live model call itself needs a real key at runtime and is not exercised in
  CI (same honest boundary as the Kafka broker round-trip).
- **Day-2**: no key configured → the endpoint returns a clear `503` and the
  UI shows it; the rest of chaff works unchanged. The key is a server env
  var (ADR-0005 config style), so Docker deployments set it once.
