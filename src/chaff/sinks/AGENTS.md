# Path rules: sinks

A sink delivers bytes and returns a human-readable receipt string. It
never parses, transforms, or inspects payload content (INV-2).

- Register with `@sink("id")`. Options from `spec.sink.options` only.
- Streaming sinks (kafka, mqtt, http, tcp/udp) take a per-record iterator +
  rate control (records/sec) instead of one blob — a second sink signature
  negotiated by the engine (ADR-0007), NOT by making the file sink stream or
  by buffering streams into blobs. Run-mode (`max_records`, `duration`) and
  lazy per-record generation are engine-applied (ADR-0016); a sink just
  consumes the iterator it's handed.
- Serving a stream *to* a connecting consumer (the WebSocket `/stream`
  endpoint) is an API/interface concern, NOT a sink — a sink pushes bytes
  out; the socket inverts that (the consumer pulls). Don't model it here.
- Network sinks fail loudly with actionable messages (broker unreachable,
  endpoint 4xx) — a silent demo-data drop is a debugging séance.
- Secrets/credentials come from env or options, never hardcoded, never
  logged in receipts.
- A sink that imports a third-party client declares it at registration:
  `@stream_sink("kafka", requires=("confluent_kafka",), extra="kafka")`, and
  raises `missing_dependency(sink_id, package)` when the import fails. That
  feeds `chaff registry`, `/registry`'s `sink_status`, and the Stream tab's
  "not installed" state (ADR-0047). `tests/test_sink_requirements.py` fails
  if a lazy import isn't declared.
- Pure-Python, install-anywhere clients may be core dependencies; compiled,
  per-platform ones (confluent-kafka) get their own extra so they can never
  break the core install. Add any new extra to `all` in pyproject.toml.
