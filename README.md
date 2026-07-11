# chaff

**Fake signals convincing enough to work with.**

chaff is a spec-driven synthetic data engine for **demo and test datasets**.
Describe your dataset once — columns, semantic generators, distributions,
row count, output format, destination — as a JSON spec; chaff generates it
identically every time (seeded), in the format you need, delivered where
you need it.

> chaff produces demo/test data only. It is not a training-data pipeline
> and will not become one.

## Quick start

```bash
# Library + CLI
pip install -e .
chaff registry                              # what can it generate?
chaff generate examples/crm_contacts.json   # 500 CRM contacts -> out/*.csv
chaff generate examples/case_records.json   # 1000 cases -> Postgres SQL
chaff generate examples/crm_contacts.json --seed 7 --rows 50 -o small.csv

# API + UI (dev) — open http://localhost:8000 for the form-based builder
pip install -e '.[api]'
make run-api

# Excel (.xlsx) output needs the formats-extra extra
pip install -e '.[formats-extra]'

# Docker (the intended distribution — pull, build, run anywhere)
docker compose up --build
# Phase 2 sink dev, with a Kafka broker:
docker compose --profile streaming up --build
```

## The idea

`spec -> generate -> encode -> sink`

- **Spec is the product** (ADR-0001): UI/CLI/API all just build specs.
- **Format ≠ sink** (ADR-0002): CSV/TSV/JSON/NDJSON/SQL/XLSX/XML/Parquet/Avro/CoT
  today; delivery via file or streaming **HTTP POST / Kafka / TCP / UDP**
  (ADR-0007) — any format, any compatible sink. Moving entities + CoT + a
  udp/tcp sink = a live synthetic **TAK** feed.
- **Semantic generators** (ADR-0003): "full_name", "pattern: DEA-####-?????",
  "70% Open / 20% Pending / 10% Closed" — not VARCHARs. Includes real-world
  **distributions** (lognormal, exponential, poisson, power_law) and
  **web/telemetry** types (ipv4, user_agent, http_status, ulid, …) so demo
  data behaves like the real thing. See `examples/web_access_logs.json`.
- **Seeded** (ADR-0004): same spec + seed = byte-identical dataset. When the
  demo works, you can have that exact data again.
- **Spec library**: pick a preset or a saved schema from the UI gallery, load
  it into the builder, tweak, and go. Saves persist under `CHAFF_LIBRARY_DIR`
  (a Docker volume); presets ship in `examples/`.
- **Describe it in English** (ADR-0010/0011): the UI can draft a spec from a
  plain sentence via **Anthropic, OpenAI, or Google** — picked by whichever
  server-side key is set (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
  `GOOGLE_API_KEY`; extras `nl` / `nl-openai` / `nl-google`). It drafts a
  *spec* you review and edit — the engine still generates the data
  deterministically. Not an AI/ML data pipeline (INV-5), just a spec-builder aid.
- **Multi-table** (ADR-0008): add related `tables` and an `fk` column that
  references another table's key; chaff generates them in dependency order
  with real referential integrity (customers → orders → lines), one file per
  table. See `examples/retail_orders.json`.
- **Derived columns** (ADR-0012): a `derived` column computes from other
  columns in the same row via a safe formula — `total = price * qty`,
  `tier = 'wholesale' if net > 500 else 'retail'`. No `eval` (formulas are
  parsed, not run), and it adds zero entropy so seeded output stays
  byte-identical. The realism unlock: the numbers actually add up. See
  `examples/orders_with_totals.json`.
- **Stateful entities** (ADR-0009): an `entity` block turns rows into
  `count × ticks` time-ordered snapshots whose state evolves each tick —
  `movement` (moving lat/lon tracks), `lifecycle` (state transitions),
  `drift` (sensor walk). Pairs with a streaming sink for a live feed. See
  `examples/moving_tracks.json`, `examples/order_lifecycle.json`.

## Repo map

```
src/chaff/          engine, spec contract, plugin registries
  generators/       semantic value generators (+ path rules)
  formats/          pure encoders (+ path rules)
  sinks/            delivery (+ path rules)
  updaters/         per-tick entity updaters (+ path rules)
  library.py        spec library: presets + saved schemas
api/                FastAPI transport (main.py) + static UI (static/index.html)
examples/           preset spec library
docs/adr/           the load-bearing decisions
AGENTS.md           Build DNA — read first
CLAUDE.md           Claude Code entry point
ROADMAP.md          phases and backlog
```

## Development

```bash
make check      # the definition of green
make examples   # regenerate all presets into out/
```
