"""Kafka streaming sink. See sinks/AGENTS.md.

Produces one Kafka message per encoded record to a topic. confluent-kafka
is heavy/optional, so it lives under the `streaming` extra and is imported
lazily — core `import chaff.sinks` stays dep-free.

INV-2: the sink delivers record-bytes as message values; it does not parse
or transform them. It therefore does not derive per-record message keys
from column values (that would require inspecting content) — an optional
static `key` from options is the only keying.

Failures are loud (sinks/AGENTS.md): a delivery error or any message left
undelivered after flush raises, rather than a silent demo-data drop.

options:
  topic (required), bootstrap_servers (default 'localhost:9092'),
  key (optional static message key), flush_timeout (s, default 10),
  config (dict passed through to the confluent-kafka Producer, e.g.
  security/SASL settings — never logged).

The docker-compose `streaming` profile carries a broker fixture for
end-to-end runs: `docker compose --profile streaming up`.
"""

from __future__ import annotations

from typing import Any, Callable, Iterator

from . import stream_sink
from ..spec import DatasetSpec


def _produce(producer: Any, topic: str, records: Iterator[bytes],
             key: bytes | None, flush_timeout: float) -> int:
    """Produce every record, handling local-queue backpressure, then flush.

    Kept dep-free and side-effect-isolated so it can be tested with a fake
    producer. Raises on any delivery error or undelivered remainder.
    """
    errors: list[str] = []

    def on_delivery(err: Any, _msg: Any) -> None:
        if err is not None:
            errors.append(str(err))

    n = 0
    for rec in records:
        while True:
            try:
                producer.produce(topic, value=rec, key=key, on_delivery=on_delivery)
                break
            except BufferError:
                # Local queue full: serve delivery callbacks to drain, retry.
                producer.poll(0.5)
        producer.poll(0)
        n += 1

    remaining = producer.flush(flush_timeout)
    if errors:
        raise RuntimeError(f"kafka sink: {len(errors)} delivery error(s); first: {errors[0]}")
    if remaining:
        raise RuntimeError(
            f"kafka sink: {remaining} message(s) undelivered after {flush_timeout}s "
            f"(broker unreachable or topic missing?)"
        )
    return n


@stream_sink("kafka")
def kafka_sink(spec: DatasetSpec, records: Iterator[bytes]) -> str:
    try:
        from confluent_kafka import Producer
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "kafka sink needs confluent-kafka. Install it with: pip install 'chaff[streaming]'"
        ) from e

    opts = spec.sink.options
    topic = opts.get("topic")
    if not topic:
        raise ValueError("kafka sink requires options.topic")
    servers = opts.get("bootstrap_servers", "localhost:9092")
    flush_timeout = float(opts.get("flush_timeout", 10))
    key = opts.get("key")
    if isinstance(key, str):
        key = key.encode("utf-8")

    config = {"bootstrap.servers": servers}
    config.update(opts.get("config", {}))

    producer = _make_producer(Producer, config)
    n = _produce(producer, topic, records, key, flush_timeout)
    return f"produced {n} record(s) to topic '{topic}' on {servers}"


def _make_producer(producer_cls: Callable[[dict], Any], config: dict) -> Any:
    """Seam for tests to inject a fake producer without a live broker."""
    return producer_cls(config)
