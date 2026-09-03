# Build DNA — chaff

> Instantiates the portable engineering standard for this repo. Tool-agnostic:
> Claude Code reads it via `CLAUDE.md`; any other agent can import it directly.
> Path-scoped refinements live in `src/chaff/generators/AGENTS.md`,
> `src/chaff/formats/AGENTS.md`, and `src/chaff/sinks/AGENTS.md`.

## 0. North Star

We are **Developing for Operations (D4O)**. chaff isn't done when it generates
rows on your machine — it's done when an average office Joe can open the UI,
build a dataset, and walk into a demo without asking an engineer for help.
Three back-pocket questions on every change:

1. **Day 2:** who operates this, and do they have what they need?
2. **Next person:** could someone pick this up cold?
3. **The proof:** can we show it does what we say? (Here: `make check`.)

## 1. Hard invariants (not up for "simplification")

These are the load-bearing walls. Future sessions: do not soften these into
warnings, defaults, or TODOs. If one must change, write an ADR first.

- **INV-1 — The spec is the product.** Every interface (UI, CLI, API) produces
  a `DatasetSpec`; only the engine consumes it. No generation logic outside
  `src/chaff/`. If the API or UI grows generation code, that's a defect.
- **INV-2 — Two axes, never merged.** Format (encoding) and sink (delivery)
  stay independent. Encoders are pure functions: no I/O, no network, no
  filesystem. Sinks never inspect or transform payload content.
- **INV-3 — Seed determinism is byte-for-byte.** Same spec + same seed =
  identical output, forever. All randomness flows from `GenContext.rng` /
  `GenContext.faker`. Never `random` module globals, never wall-clock entropy,
  never dict-ordering luck. `test_seed_determinism_is_byte_for_byte` is the
  tripwire — if it fails, stop and find the entropy leak.
- **INV-4 — Registries, not switch statements.** New generators, formats, and
  sinks register via decorator. Adding one never edits the engine.
- **INV-5 — Demo data only.** chaff generates synthetic demo/test data. It is
  not a training-data pipeline and does not grow features for AI/ML dataset
  production. Keep the README statement to that effect intact.

## 2. Architecture

Pipeline: `spec -> generate -> encode -> sink`. The engine (`engine.py`) walks
it and stays boring. Cleverness belongs in registry plugins. The service split
(API vs engine) is deliberate: the engine must remain importable as a plain
library so CLI and future headless uses are free.

Exceptions to the standard get an ADR with a named owner (`docs/adr/`).

## 3. Working the loop

```
make check     # tests + example-spec validation; the definition of green
make examples  # generate all preset datasets into out/
make run-api   # uvicorn dev server
```

`make check` includes browser-driven UI tests (ADR-0022). They **skip** when
Playwright or a Chromium build isn't present, so `make check` runs anywhere;
CI installs a browser and sets `CHAFF_REQUIRE_BROWSER_TESTS=1` so a skip
there is a failure. To run them locally:
`pip install -e '.[dev-browser]' && python -m playwright install chromium`.

Every change lands with: tests updated, `make check` green, ADR if a decision
was made, ROADMAP.md updated if scope moved. Don't batch phases — finish and
verify one before starting the next.

## 4. Roadmap discipline

Phases live in `ROADMAP.md`. Phase 2 = Parquet/Avro/Excel + Kafka/HTTP sinks +
rate control. Phase 3 = multi-table FKs, stateful entities, CoT, saved schemas,
preset library UI, natural-language spec building. Reserved seams already in
the code (`DatasetSpec.entity`, sink stubs) mark where those land — build into
the seams, don't re-architect around them.

## 5. Fun clause

Every release ships one small easter egg that costs nothing and harms nothing.
Standing suggestion: `chaff generate --seed 8675309` on any preset should emit
a comment/receipt line acknowledging Jenny. Log eggs in `docs/EGGS.md` as they
hatch. This clause is as binding as the invariants.
