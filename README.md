# chaff

**Fake signals convincing enough to work with.**

chaff is a spec-driven synthetic data engine for **demo and test datasets**.
Describe your dataset once — columns, semantic generators, distributions,
row count, output format, destination — as a JSON spec; chaff generates it
identically every time (seeded), in the format you need, delivered where
you need it.

> chaff produces demo/test data only. It is not a training-data pipeline
> and will not become one.

## Windows: just download chaff.exe (no Docker, no Python)

The simplest path if you're on Windows. Grab **`chaff.exe`** from the
[latest release](https://github.com/deiotte/chaff/releases/latest) and
double-click it.

1. A small console window opens and your browser pops to the chaff UI.
2. First time only: Windows may show a blue **"Windows protected your PC"**
   screen (the app isn't code-signed yet) — click **More info → Run anyway**.
3. Build a dataset, download it. Close the console window to stop chaff.

No install, no terminal. AI drafting ("Describe it in English") works with an
Anthropic (Claude) or OpenAI (GPT) key pasted into the UI. Every output format
is included — CSV, Excel, JSON, Parquet, Avro, and more. (See
[ADR-0014](docs/adr/0014-windows-exe-distribution.md) for the details and
limitations.)

## Get it running with Docker (any OS, 2 minutes, no coding)

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/)
installed and running. That's it — no Python, no setup.

```bash
git clone https://github.com/deiotte/chaff.git
cd chaff
docker compose up --build          # first run downloads + builds; give it a minute
```

Then open **http://localhost:8000** in your browser. You'll see a form.

1. Click any card in **Library** (e.g. *crm_contacts*) to load a ready-made dataset.
2. Click **Preview** to see sample rows.
3. Click **Download** to save the file.

That's the whole loop: pick something, preview, download. Tweak the columns,
change the row count, switch the format — no code, no engineer.

When you're done, press `Ctrl+C` in the terminal to stop it.

### Turn on "Describe it in English" (optional)

The **Describe it** box at the top can draft a dataset from a plain sentence
(e.g. *"500 CRM contacts with name, email, company, and a weighted lead
status"*). It needs an AI API key — and you just **paste it into the box**:

1. Get a key from Anthropic (Claude), OpenAI (GPT), or Google (Gemini).
2. Paste it into the **Your AI API key** field under the Describe box.
3. Type what you want and click **Draft with AI**.

That's it — no files, no restart. Your key is stored **only in your browser**
and sent straight to the model; chaff never saves it to disk. Claude and GPT
keys work out of the box; a Google key needs one extra (see below).

<details>
<summary>Prefer a server-managed key instead? (optional)</summary>

If you'd rather the key live on the server and never touch the browser, set
it once and skip the paste field:

```bash
cp .env.example .env               # make your own settings file
# open .env and paste your key after ANTHROPIC_API_KEY=
docker compose up --build          # restart to pick it up
```

For a Google key (either way), add its SDK to the image: set
`CHAFF_EXTRAS=api,nl,nl-openai,nl-google` in `.env` and rebuild.

> On a shared/hosted deployment, put chaff behind HTTPS so pasted keys aren't
> sent in the clear.
</details>

### Troubleshooting

| What you see | What's wrong / what to do |
|---|---|
| `docker: command not found` | Docker Desktop isn't installed or isn't running. Install it, open it, try again. |
| `port is already allocated` / page won't load | Something else is on port 8000. Stop it, or change `"8000:8000"` to `"8080:8000"` in `docker-compose.yml` and open http://localhost:8080. |
| **Describe it** says *"needs an LLM API key"* | No key available. Paste one into the **Your AI API key** field (see "Turn on Describe it in English"), or set one on the server. |
| **Describe it** says *"needs its SDK"* | You pasted a **Google** key, whose SDK isn't in the default image. Set `CHAFF_EXTRAS=api,nl,nl-openai,nl-google` in `.env` and `docker compose up --build`. (Claude/GPT keys work with no rebuild.) |
| Downloaded a preset but the numbers look the same every time | That's the point — same spec + seed = identical data, on purpose (see "Seeded" below). Change the **Seed** field for different data. |

## For developers (CLI + library)

Prefer the command line, or want to script it? chaff is also a plain Python
library and CLI — no server needed.

```bash
pip install -e .                            # core library + CLI
chaff registry                              # what can it generate?
chaff generate examples/crm_contacts.json   # 500 CRM contacts -> out/*.csv
chaff generate examples/case_records.json   # 1000 cases -> Postgres SQL
chaff generate examples/crm_contacts.json --seed 7 --rows 50 -o small.csv

pip install -e '.[api]'                      # then `make run-api` for the UI
pip install -e '.[formats-extra]'            # Excel (.xlsx), Parquet, Avro
docker compose --profile streaming up --build  # + Kafka & MQTT brokers for sink dev
```

## The idea

`spec -> generate -> encode -> sink`

- **Spec is the product** (ADR-0001): UI/CLI/API all just build specs.
- **Format ≠ sink** (ADR-0002): CSV/TSV/JSON/NDJSON/SQL/XLSX/XML/Parquet/Avro/CoT
  today; delivery via file or streaming **HTTP POST / Kafka / MQTT / TCP / UDP**
  (ADR-0007) — any format, any compatible sink. Moving entities + CoT + a
  udp/tcp sink = a live synthetic **TAK** feed.
- **Serve a live stream, don't just download one** (ADR-0016/0017): the UI has
  a **Stream tab** next to Batch. Watch records arrive live in the page over the
  **`/stream` WebSocket**, or **push to a broker/endpoint** (Kafka/MQTT/HTTP/
  TCP/UDP) as a bounded, stoppable server-side job (`/stream/jobs`). Every run
  is capped (records + seconds, hard-ceilinged) and re-confirms before going
  again — no runaway feeds. Generation is lazy and can run past the row count;
  determinism holds per-record: the i-th record is always the same, whatever
  the stream's length.
- **Exposing chaff beyond localhost?** (ADR-0018) chaff's defaults assume a
  single operator on localhost. The streaming surface is safe to put on a
  network once you set two knobs: `CHAFF_API_TOKEN` requires a shared token on
  `/stream/jobs` and the `/stream` WebSocket (paste it into the UI's **Access
  token** field), and `CHAFF_STREAM_ALLOWED_HOSTS` restricts where push jobs may
  connect. Cloud-metadata / link-local addresses (169.254.x, the SSRF classic)
  are **always** blocked, no config needed. The `/stream` socket is also bounded
  against connection-exhaustion out of the box (ADR-0019): an idle client that
  never sends a spec is dropped after a short handshake window, and concurrent
  live sockets are capped — tune with `CHAFF_STREAM_HANDSHAKE_TIMEOUT` and
  `CHAFF_STREAM_MAX_SESSIONS` if you expose it. Left unset, everything behaves as
  the zero-config local demo it's always been.
- **Semantic generators** (ADR-0003): "full_name", "pattern: ABC-####-?????",
  "70% Open / 20% Pending / 10% Closed" — not VARCHARs. Includes real-world
  **distributions** (lognormal, exponential, poisson, power_law),
  **web/telemetry** types (ipv4, user_agent, http_status, ulid, …), and
  **geo/finance/people** types (country, timezone, currency_code, test
  credit_card/iban, job_title, age, gender) so demo data behaves like the
  real thing. See `examples/web_access_logs.json`, `examples/employees.json`.
- **Seeded** (ADR-0004): same spec + seed = byte-identical dataset. When the
  demo works, you can have that exact data again.
- **Spec library**: pick a preset or a saved schema from the UI gallery, load
  it into the builder, tweak, and go. Saves persist under `CHAFF_LIBRARY_DIR`
  (a Docker volume); presets ship in `examples/`.
- **Related tables, from the UI** (ADR-0008/0020/0021): a spec can carry extra
  tables whose columns reference real parent keys (customers -> orders ->
  lines). Hit **+ Related table** to add one — it gets its own name, row count
  and column editor, and you link it with the `fk` generator. Preview shows
  every table side by side so you can see the keys line up; Download gives you
  a `.zip` with one file per table. See `examples/retail_orders.json`.
- **Things that change over time** (ADR-0009/0021): entity specs generate
  `count` entities and advance them over `ticks` — moving tracks, order
  lifecycles, sensor drift — one row per entity per tick. Hit **+ Time
  series**, set entities × ticks, and add per-tick rules (movement, lifecycle,
  drift); each rule's params box fills in with a working example. See
  `examples/moving_tracks.json`.
- **Describe it in English** (ADR-0010/0011/0013): the UI can draft a spec
  from a plain sentence via **Anthropic, OpenAI, or Google**. Paste your key
  straight into the box (stored only in your browser, never saved on the
  server) — or set a server-side key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY`
  / `GOOGLE_API_KEY`). It drafts a *spec* you review and edit — the engine
  still generates the data deterministically. Not an AI/ML data pipeline
  (INV-5), just a spec-builder aid.
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
- **Correlated columns** (ADR-0015): mark a `country` column `{"link": true}`
  and give `city`/`timezone`/`currency_code`/`lat`/`lon` a `{"from": "country"}`
  — they fan out consistently (Russia → a Russian city, Europe/Moscow, RUB,
  matching coordinates) instead of clashing. Deterministic; opt-in (unmarked
  columns stay independent). See `examples/crm_contacts_geo.json`.
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
make check          # the definition of green
make examples       # regenerate all presets into out/
make notices        # regenerate THIRD-PARTY-NOTICES.txt (bundled-deps attribution)
make license-check  # fail if any bundled dependency is non-permissive
```

## License

chaff is **MIT licensed** — see [`LICENSE`](LICENSE). Use it, fork it, ship it.

Bundled/redistributed builds (the Windows `chaff.exe`) include
[`THIRD-PARTY-NOTICES.txt`](THIRD-PARTY-NOTICES.txt) with the license and NOTICE
attributions for chaff's open-source dependencies (all permissive — MIT / BSD /
Apache-2.0 / MPL-2.0). The running app also serves them at `/licenses`, and the
release attaches the notices file next to the exe. All runtime dependencies are
permissive; PyInstaller (build-only, GPL with a bootloader exception) does not
affect the license of the produced binary.
