# ADR-0002: Format and sink are independent axes

- Status: Accepted
- Owner: Karl
- Date: 2026-07-10

## Context
The wishlist mixed encodings (CSV, Avro, Parquet) with transports (Kafka,
HTTP APIs). Kafka is not a format; Spark is not a format (it reads Parquet).

## Decision
Axis 1 (format): pure encoder `(spec, rows) -> bytes`. Axis 2 (sink):
delivery `(spec, payload) -> receipt`. Any format pairs with any compatible
sink. Encoders do no I/O; sinks do no content transformation.

## Consequences
- N formats + M sinks = N+M implementations, not N*M.
- "Spark support" = shipping Parquet. "Developer API support" = HTTP sink.
- Streaming sinks need per-record framing; NDJSON/Avro are the natural
  pairings, negotiated in Phase 2 via a second sink signature.
