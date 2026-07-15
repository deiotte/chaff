"""MQTT publish sink tests. The `_publish` helper runs against a fake client
(no dep, no broker). The full engine path uses the `_make_client` seam to
inject a fake, and importorskips paho-mqtt (the sink imports it). A true
broker round-trip is out of scope for unit tests — the docker-compose
`streaming` profile is that fixture."""

import json

import pytest

from chaff.sinks import is_stream_sink
from chaff.sinks import mqtt as mqtt_mod


class FakeInfo:
    """Stand-in for paho's MQTTMessageInfo."""

    def __init__(self, rc=0, published=True):
        self.rc = rc
        self._published = published
        self.waited = None

    def wait_for_publish(self, timeout=None):
        self.waited = timeout

    def is_published(self):
        return self._published


class FakeClient:
    """Configurable stand-in for paho.mqtt.client.Client."""

    def __init__(self, *, publish_rc=0, published=True, connect_error=None):
        self.msgs = []
        self.connected = None
        self.loop_running = False
        self.disconnected = False
        self.auth = None
        self._publish_rc = publish_rc
        self._published = published
        self._connect_error = connect_error

    def username_pw_set(self, username, password=None):
        self.auth = (username, password)

    def connect(self, host, port, keepalive=60):
        if self._connect_error is not None:
            raise self._connect_error
        self.connected = (host, port, keepalive)

    def loop_start(self):
        self.loop_running = True

    def loop_stop(self):
        self.loop_running = False

    def disconnect(self):
        self.disconnected = True

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.msgs.append((topic, payload, qos, retain))
        return FakeInfo(rc=self._publish_rc, published=self._published)


def test_mqtt_is_registered_as_streaming():
    assert is_stream_sink("mqtt")


def test_publish_delivers_all_records():
    c = FakeClient()
    n = mqtt_mod._publish(c, "sensors", iter([b"a\n", b"b\n", b"c\n"]), qos=0,
                          retain=False, timeout=10)
    assert n == 3
    assert [m[1] for m in c.msgs] == [b"a\n", b"b\n", b"c\n"]
    assert all(m[0] == "sensors" for m in c.msgs)


def test_publish_raises_on_bad_return_code():
    c = FakeClient(publish_rc=4)  # paho MQTT_ERR_NO_CONN
    with pytest.raises(RuntimeError, match="publish failed"):
        mqtt_mod._publish(c, "sensors", iter([b"a\n"]), qos=0, retain=False, timeout=10)


def test_publish_qos1_confirms_and_raises_when_unconfirmed():
    c = FakeClient(published=False)
    with pytest.raises(RuntimeError, match="not confirmed"):
        mqtt_mod._publish(c, "sensors", iter([b"a\n"]), qos=1, retain=False, timeout=5)


def test_engine_streams_ndjson_records_to_mqtt(monkeypatch):
    pytest.importorskip("paho.mqtt.client")
    from chaff.engine import run
    from chaff.spec import load_spec

    captured = {}

    def fake_make_client(mqtt, client_id, username, password):
        c = FakeClient()
        c.username_pw_set(username, password) if username else None
        captured["c"] = c
        return c

    monkeypatch.setattr(mqtt_mod, "_make_client", fake_make_client)
    spec = load_spec({
        "name": "ev", "seed": 1, "rows": 4,
        "columns": [{"name": "id", "generator": "row_id"},
                    {"name": "who", "generator": "full_name"}],
        "output": {"format": "ndjson"},
        "sink": {"sink": "mqtt", "options": {
            "topic": "demo/sensors", "host": "broker.local", "port": 1883,
            "username": "u", "password": "s3cret"}},
    })
    receipt = run(spec)

    c = captured["c"]
    assert len(c.msgs) == 4
    assert c.connected == ("broker.local", 1883, 60)
    assert c.msgs[0][0] == "demo/sensors"          # topic
    assert json.loads(c.msgs[0][1])["id"] == 1     # ndjson payload framing
    assert c.disconnected and not c.loop_running    # cleaned up
    assert "published 4 record(s) to topic 'demo/sensors'" in receipt
    assert "s3cret" not in receipt                  # secrets never in the receipt


def test_engine_unreachable_broker_fails_loud(monkeypatch):
    pytest.importorskip("paho.mqtt.client")
    from chaff.engine import run
    from chaff.spec import load_spec

    monkeypatch.setattr(mqtt_mod, "_make_client",
                        lambda *a: FakeClient(connect_error=ConnectionRefusedError("refused")))
    spec = load_spec({
        "name": "ev", "seed": 1, "rows": 2,
        "columns": [{"name": "id", "generator": "row_id"}],
        "output": {"format": "ndjson"},
        "sink": {"sink": "mqtt", "options": {"topic": "demo", "host": "127.0.0.1", "port": 1}},
    })
    with pytest.raises(RuntimeError, match="can't reach broker"):
        run(spec)
