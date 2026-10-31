# ADR-0047: Pure-Python sinks ship in core; Kafka stays opt-in

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-10-31
- Builds on: ADR-0005 (Docker-first, engine stays a plain pip library), ADR-0007 (streaming
  sink signature), ADR-0014 / ADR-0023 (desktop bundles), ADR-0031 (build-input integrity)

## Context
A fresh `pip install -e .` on a new Linux box could not stream anywhere but TCP/UDP. All three
client libraries (`httpx`, `paho-mqtt`, `confluent-kafka`) lived in one `streaming` extra. The
missing dependency only showed up when someone tried to stream, and `chaff registry` and the
UI's Stream tab listed `http`, `kafka` and `mqtt` even when none of them could run. That is the
"ask an engineer" moment the North Star rules out, and it caught the repo's own owner.

The three libraries are not alike:

| Library | Sink | Wheel | Install risk |
|---|---|---|---|
| `paho-mqtt` | mqtt | ~67 KB, pure Python, no deps | none |
| `httpx` | http | ~75 KB, pure Python, + 5 small pure-Python deps (~600 KB total) | none |
| `confluent-kafka` | kafka | ~5 MB, compiled, bundles librdkafka, per-platform | **real** |

Kafka's wheels cover common platforms (e.g. `manylinux_2_28_x86_64`). Elsewhere (glibc older
than 2.28, Alpine/musl, 32-bit ARM, a Python too new for a wheel yet) pip falls back to a C build
that needs a compiler and librdkafka headers. Putting Kafka in core would let one optional
destination break the whole install, CLI included, for people who will never use Kafka. That
would break ADR-0005's promise that the engine is a plain pip library.

## Decision
1. **`httpx` and `paho-mqtt` are core dependencies.** HTTP and MQTT streaming work after a plain
   `pip install chaff`. Both are still imported lazily inside their sink, so `import chaff.sinks`
   stays cheap.
2. **Kafka gets its own extra, `kafka`.** The old `streaming` extra stays as a back-compat alias
   with the same three packages, so existing commands, `CHAFF_EXTRAS` values and the Dockerfile
   default keep working unchanged.
3. **An `all` extra** installs everything a user can reach (API/UI, every format, every sink,
   every drafting provider). It is the one command in the README. Because it includes Kafka, the
   README also gives the no-Kafka fallback.
4. **Sinks declare what they need at registration**:
   `@stream_sink("kafka", requires=("confluent_kafka",), extra="kafka")`. The registry answers
   "can this run here?" with `sink_status()` / `list_sink_status()`, using
   `importlib.util.find_spec` (no heavy import). Every sink raises the same
   `missing_dependency()` error, so the install command can't drift between sinks. The engine is
   untouched (INV-4).
5. **The answer reaches people before they build a demo around it.**
   - `chaff registry` prints `kafka (not installed: pip install 'chaff[kafka]')`.
   - `/registry` returns `sink_status`. The Stream tab greys out a target that can't run and shows
     its install command.
   - `/stream/jobs` refuses an unavailable sink with a 422 and the install command, instead of
     launching a job that dies on its first record.
6. **Desktop bundles gain HTTP and MQTT, but not Kafka.** `httpx` and `paho.mqtt.client` are
   hidden imports. `confluent_kafka` stays excluded, so the desktop Stream tab shows Kafka as
   "not installed" rather than offering it and failing.
7. **The licence check reads `OR` as a choice.** paho-mqtt is `EPL-2.0 OR BSD-3-Clause`. The old
   check treated `OR` like `AND` and would have rejected it. It now parses SPDX-style
   expressions: `OR` needs one permissive branch, `AND` needs every term, and `AND` binds tighter
   than `OR`. We take paho-mqtt under BSD-3-Clause.

## Consequences
- A plain install can stream to HTTP, MQTT, TCP and UDP, which covers most demo targets with no
  extra step.
- Core gains seven small pure-Python packages to track (ADR-0031). Dependabot already covers
  them, and none of them can fail to install.
- Kafka still needs one extra command. That is deliberate, and it is now said up front in three
  places instead of discovered mid-demo.
- Determinism (INV-3) is unaffected. Sinks never touch payload bytes (INV-2), so these libraries
  cannot change what chaff generates.
- Adding a sink with a third-party client means passing `requires=` and `extra=`.
  `tests/test_sink_requirements.py` fails if a lazily-imported client isn't declared.
