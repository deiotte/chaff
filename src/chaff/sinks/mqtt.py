"""MQTT publish sink. See sinks/AGENTS.md.

Publishes one MQTT message per encoded record to a topic — the pub/sub
counterpart to the kafka sink, common for IoT/telemetry demo feeds. paho-mqtt
is optional, so it lives under the `streaming` extra and is imported lazily —
core `import chaff.sinks` stays dep-free.

INV-2: the sink delivers record-bytes as the message payload; it does not
parse or transform them. The topic is static (from options); it is never
derived from record content (that would require inspecting the payload).

Failures are loud (sinks/AGENTS.md): an unreachable broker raises rather than
silently dropping demo data. A publish the client can't queue (not connected)
surfaces via its return code; for qos > 0 each message is confirmed and a
missed confirmation raises.

options:
  topic (required), host (default 'localhost'), port (default 1883),
  qos (0|1|2, default 0), retain (default False), keepalive (s, default 60),
  client_id (optional), publish_timeout (s, default 10, qos>0 confirm wait),
  username / password (optional — also read from CHAFF_MQTT_USERNAME /
  CHAFF_MQTT_PASSWORD; never logged into the receipt).

The docker-compose `streaming` profile can carry a broker fixture for
end-to-end runs alongside kafka.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

from . import stream_sink
from ..spec import DatasetSpec


def _publish(client: Any, topic: str, records: Iterator[bytes],
             qos: int, retain: bool, timeout: float) -> int:
    """Publish every record, surfacing a not-connected/failed publish loudly.

    Dep-free and side-effect-isolated so it can be tested with a fake client.
    `client.publish` returns an info object whose `rc` is 0 on success; for
    qos > 0 we also wait for the broker's confirmation.
    """
    n = 0
    for rec in records:
        info = client.publish(topic, payload=rec, qos=qos, retain=retain)
        rc = getattr(info, "rc", 0)
        if rc != 0:
            raise RuntimeError(
                f"mqtt sink: publish failed (rc={rc}) — is the broker connected?"
            )
        if qos > 0:
            info.wait_for_publish(timeout)
            if not info.is_published():
                raise RuntimeError(
                    f"mqtt sink: message not confirmed within {timeout}s (qos={qos}; "
                    f"broker unreachable or overloaded?)"
                )
        n += 1
    return n


@stream_sink("mqtt")
def mqtt_sink(spec: DatasetSpec, records: Iterator[bytes]) -> str:
    try:
        import paho.mqtt.client as mqtt
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "mqtt sink needs paho-mqtt. Install it with: pip install 'chaff[streaming]'"
        ) from e

    opts = spec.sink.options
    topic = opts.get("topic")
    if not topic:
        raise ValueError("mqtt sink requires options.topic")
    host = str(opts.get("host", "localhost"))
    port = int(opts.get("port", 1883))
    qos = int(opts.get("qos", 0))
    retain = bool(opts.get("retain", False))
    keepalive = int(opts.get("keepalive", 60))
    timeout = float(opts.get("publish_timeout", 10))
    client_id = str(opts.get("client_id", ""))
    username = opts.get("username") or os.environ.get("CHAFF_MQTT_USERNAME")
    password = opts.get("password") or os.environ.get("CHAFF_MQTT_PASSWORD")

    client = _make_client(mqtt, client_id, username, password)
    try:
        client.connect(host, port, keepalive)
    except OSError as e:
        raise RuntimeError(f"mqtt sink: can't reach broker at {host}:{port} ({e})") from e

    client.loop_start()  # background thread drives the network + delivery
    try:
        n = _publish(client, topic, records, qos, retain, timeout)
    finally:
        client.loop_stop()
        client.disconnect()

    return f"published {n} record(s) to topic '{topic}' on {host}:{port}"


def _make_client(mqtt: Any, client_id: str, username: Any, password: Any) -> Any:
    """Seam for tests to inject a fake client without a live broker. Builds a
    paho client across the 1.x / 2.x API split (2.0 made the callback API
    version a required first argument)."""
    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except (AttributeError, TypeError):  # paho-mqtt 1.x has no CallbackAPIVersion
        client = mqtt.Client(client_id=client_id)
    if username:
        client.username_pw_set(username, password)
    return client
