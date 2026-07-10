# ADR-0006: UI is a static, build-free page served by the API

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-07-10

## Context
Phase 1 needs a web UI: a form-based spec builder that reads the registry,
shows a live preview, and downloads a dataset. The roadmap left the
location open ("`api/static` or a `ui/` dir — your call, ADR it"). The
North Star user is an average office Joe, and the standing bias is
"simple > clever" with Day-2 operability in mind.

Two options:
1. A single static HTML + vanilla-JS page served by the API process.
2. A framework SPA (React/Svelte) in a `ui/` dir with an npm build step.

## Decision
Option 1. The UI lives in `api/static/` as one `index.html` (inline CSS
+ vanilla JS, no dependencies, no bundler) and is served by the same
FastAPI process via `StaticFiles(..., html=True)` mounted at `/`. It talks
to the existing `/registry`, `/preview`, and `/generate` endpoints and
does nothing but assemble a `DatasetSpec` and post it (INV-1: no
generation logic in the UI).

## Consequences
- One process, one container, one `docker compose up` — no node toolchain,
  no build stage in the Dockerfile, nothing extra to operate on Day 2.
- The mount is guarded (`if static dir exists`) and mounted last so it
  never shadows the API routes.
- Dropdowns are populated from `/registry` at load time, never hardcoded,
  so new generators/formats/sinks appear in the UI for free (INV-4).
- Trade-off: no rich component ecosystem. Accepted — the form is small and
  the audience wants obvious, not slick. If the UI ever outgrows a single
  file, revisit with a follow-up ADR rather than bolting a build step on.
