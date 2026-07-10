# CLAUDE.md

@AGENTS.md

You are working on **chaff**, a spec-driven synthetic demo-data engine.
The canonical standard is `AGENTS.md` (above) — internalize the five hard
invariants before touching code. Path-scoped rules refine it:

- `src/chaff/generators/AGENTS.md` — adding semantic generators
- `src/chaff/formats/AGENTS.md` — adding format encoders
- `src/chaff/sinks/AGENTS.md` — adding sinks

Loop: change -> `make check` -> green before moving on. Never batch phases.
Current state and next work: `ROADMAP.md`.
