"""Kafka streaming sink tests. The `_produce` helper is exercised with a
fake producer (no dep, no broker). The full engine path uses the
`_make_producer` seam to inject a fake, and importorskips confluent-kafka
(the sink imports it). A true broker round-trip is out of scope for unit
tests — the docker-compose `streaming` profile is that fixture."""

import json

import pytest

from chaff.sinks import is_stream_sink
from chaff.sinks import kafka as kafka_mod


class FakeProducer:
    """Configurable stand-in for confluent_kafka.Producer."""

    def __init__(self, config=None, *, flush_remaining=0, fail_delivery=False,
                 buffer_fail_first=False):
        self.config = config or {}
        self.msgs = []
        self.polls = 0
        self.flushed_with = None
        self._flush_remaining = flush_remaining
        self._fail_delivery = fail_delivery
        self._buffer_fail_first = buffer_fail_first

    def produce(self, topic, value=None, key=None, on_delivery=None):
        if self._buffer_fail_first:
            self._buffer_fail_first = False
            raise BufferError("queue full")
        self.msgs.append((topic, value, key))
        if on_delivery is not None:
            on_delivery("boom" if self._fail_delivery else None, None)

    def poll(self, timeout=0):
        self.polls += 1
        return 0

    def flush(self, timeout=None):
        self.flushed_with = timeout
        return self._flush_remaining


def test_kafka_is_registered_as_streaming():
    assert is_stream_sink("kafka")


def test_produce_delivers_all_and_flushes():
    p = FakeProducer()
    n = kafka_mod._produce(p, "topic", iter([b"a\n", b"b\n", b"c\n"]), None, 10)
    assert n == 3
    assert [m[1] for m in p.msgs] == [b"a\n", b"b\n", b"c\n"]
    assert p.flushed_with == 10


def test_produce_raises_on_delivery_error():
    p = FakeProducer(fail_delivery=True)
    with pytest.raises(RuntimeError, match="delivery error"):
        kafka_mod._produce(p, "topic", iter([b"a\n"]), None, 10)


def test_produce_raises_when_messages_remain_undelivered():
    p = FakeProducer(flush_remaining=2)
    with pytest.raises(RuntimeError, match="undelivered"):
        kafka_mod._produce(p, "topic", iter([b"a\n", b"b\n"]), None, 10)


def test_produce_handles_backpressure_buffererror():
    p = FakeProducer(buffer_fail_first=True)
    n = kafka_mod._produce(p, "topic", iter([b"a\n"]), None, 10)
    assert n == 1
    assert p.msgs == [("topic", b"a\n", None)]
    assert p.polls >= 1  # polled to drain the full queue before retrying


def test_engine_streams_ndjson_records_to_kafka(monkeypatch):
    pytest.importorskip("confluent_kafka")
    from chaff.engine import run
    from chaff.spec import load_spec

    captured = {}
    monkeypatch.setattr(kafka_mod, "_make_producer",
                        lambda cls, config: captured.setdefault("p", FakeProducer(config)))
    spec = load_spec({
        "name": "ev", "seed": 1, "rows": 4,
        "columns": [{"name": "id", "generator": "row_id"},
                    {"name": "who", "generator": "full_name"}],
        "output": {"format": "ndjson"},
        "sink": {"sink": "kafka", "options": {"topic": "demo",
                 "bootstrap_servers": "broker:9092", "key": "k1"}},
    })
    receipt = run(spec)

    p = captured["p"]
    assert len(p.msgs) == 4
    assert p.config["bootstrap.servers"] == "broker:9092"
    assert p.msgs[0][0] == "demo"          # topic
    assert p.msgs[0][2] == b"k1"           # static key, encoded
    assert json.loads(p.msgs[0][1])["id"] == 1  # ndjson value framing
    assert "produced 4 record(s) to topic 'demo'" in receipt
