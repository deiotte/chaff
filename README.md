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
- **Format ≠ sink** (ADR-0002): CSV/TSV/JSON/NDJSON/SQL/XLSX/XML/Parquet/Avro
  today; delivery via file or streaming **HTTP POST / Kafka / TCP / UDP**
  (ADR-0007) — any format, any compatible sink.
- **Semantic generators** (ADR-0003): "full_name", "pattern: DEA-####-?????",
  "70% Open / 20% Pending / 10% Closed" — not VARCHARs.
- **Seeded** (ADR-0004): same spec + seed = byte-identical dataset. When the
  demo works, you can have that exact data again.
- **Multi-table** (ADR-0008): add related `tables` and an `fk` column that
  references another table's key; chaff generates them in dependency order
  with real referential integrity (customers → orders → lines), one file per
  table. See `examples/retail_orders.json`.

## Repo map

```
src/chaff/          engine, spec contract, plugin registries
  generators/       semantic value generators (+ path rules)
  formats/          pure encoders (+ path rules)
  sinks/            delivery (+ path rules)
api/                FastAPI transport (main.py) + static UI (static/index.html)
examples/           preset spec library
docs/adr/           the five load-bearing decisions
AGENTS.md           Build DNA — read first
CLAUDE.md           Claude Code entry point
ROADMAP.md          phases and backlog
```

## Development

```bash
make check      # the definition of green
make examples   # regenerate all presets into out/
```
