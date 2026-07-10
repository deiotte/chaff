# Path rules: sinks

A sink delivers bytes and returns a human-readable receipt string. It
never parses, transforms, or inspects payload content (INV-2).

- Register with `@sink("id")`. Options from `spec.sink.options` only.
- Streaming sinks (kafka, http, tcp/udp — Phase 2) will take a per-record
  iterator + rate control (records/sec) instead of one blob. Design that
  as a second sink signature negotiated by the engine, NOT by making file
  sink stream or by buffering streams into blobs.
- Network sinks fail loudly with actionable messages (broker unreachable,
  endpoint 4xx) — a silent demo-data drop is a debugging séance.
- Secrets/credentials come from env or options, never hardcoded, never
  logged in receipts.
