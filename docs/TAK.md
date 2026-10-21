# CoT into a TAK test environment

[Back to the README](../README.md) · [Access, egress, and operating limits](OPERATIONS.md)

Run these commands from the repository root after the README source setup.

## Generate CoT position feeds

[cot_tracks](../examples/cot_tracks.json) generates **8 entities × 60 ticks =
480 newline-delimited Cursor-on-Target XML events**:

```bash
chaff generate examples/cot_tracks.json
```

The preset separates device identity (`ANDROID-…` UIDs) from operator
callsigns (`RAVEN-…`). The encoder supports position/accuracy fields, speed
and course, battery status, and position-source metadata. Missing or unusable
optional `hae`, `ce`, and `le` values use the unknown-value sentinel
`9999999.0`; absent optional detail attributes are omitted. A default
`<takv platform="chaff">` identifies the generator inside the event.

[ADR-0032](adr/0032-cot-emitter-fidelity.md) records the fidelity change
and its output compatibility impact. It does not make chaff a physical sensor
simulator: movement is a degrees-per-tick model, and the preset's reported
speed is not derived from its positional displacement.

Times come from the spec/data, not the wall clock. The preset starts at
`2026-01-01T00:00:00Z`; a live receiver may consider it stale. Set an explicit
`output.options.base_time` appropriate to your test before sending it. The
synthetic marker is configurable, not an enforced isolation mechanism. Keep
fixtures separate from operational feeds and validate your receiving system.

## Point it at a TAK Server

First identify the exact input that TAK exposes. chaff's `tcp` and `udp` sinks
are raw sockets: they add no TLS, client certificate, authentication message,
or TAK protocol negotiation. TCP opens one connection and writes complete CoT
events head-to-tail; UDP sends one event per datagram. Every event ends with a
newline, but TAK's streaming CoT parser finds message boundaries at
`</event>`, so it does not depend on that newline.

| TAK input | Point chaff at | Operational meaning |
|---|---|---|
| Plain `protocol="tcp"` | `sink: "tcp"`, using that input's host and port | Recommended direct path for an isolated lab input. TCP connection failures are reported. |
| Plain `protocol="udp"` | `sink: "udp"`, using that input's host and port | One CoT event per datagram. UDP cannot confirm that the server or application received it. |
| `protocol="tls"` with X.509 client auth | A loopback TLS relay, then point chaff TCP at the relay | chaff cannot connect directly because its raw TCP sink has no TLS or certificate options. |
| CoT-over-protobuf, gRPC, QUIC, or another negotiated input | A compatible adapter | chaff emits XML CoT; it does not negotiate these protocols. |

The upstream TAK Server example enables TLS on 8089 and leaves plaintext TCP
and UDP on 8087 commented out. Those are examples, not a promise about your
server: confirm the live input in **Configuration → Input Definitions** or
with its administrator. See the official
[TAK Server input example](https://github.com/TAK-Product-Center/Server/blob/main/src/takserver-core/example/CoreConfig.example.docker.xml)
and its
[streaming CoT parser](https://github.com/TAK-Product-Center/Server/blob/main/src/takserver-core/src/main/java/com/bbn/marti/nio/protocol/connections/StreamingCotProtocol.java).

If you administer an isolated TAK test environment, a dedicated plaintext
input can look like this in `CoreConfig.xml` (or be created in the input UI):

```xml
<input _name="chaff-lab-tcp" protocol="tcp" port="18087" auth="anonymous"/>
```

Use a unique port, restrict it at the host/network boundary to the chaff
sender, and apply the TAK group/filter rules your test clients require. Do not
expose an anonymous plaintext input to an untrusted network. Input changes and
restart requirements depend on how your TAK Server is managed.

For a normal mutual-TLS input, put TLS outside chaff. For example, a local
[`stunnel` client](https://www.stunnel.org/howto.html) can own the TAK
certificate while chaff sends plain CoT only to loopback:

```ini
; chaff-tak.conf -- stunnel 5 client mode
foreground = yes
client = yes

[chaff-to-tak]
accept = 127.0.0.1:19089
connect = tak.example.test:8089
cert = /secure/path/chaff-client-cert.pem
key = /secure/path/chaff-client-key.pem
CAfile = /secure/path/tak-ca.pem
verifyChain = yes
checkHost = tak.example.test
```

Start the relay with `stunnel ./chaff-tak.conf`, then use
`127.0.0.1:19089` as chaff's TCP destination. Obtain a test client identity,
private key, and CA chain from the TAK administrator; the hostname in
`checkHost` must match the server certificate. If the credentials were issued
as PKCS#12, use the administrator's approved PEM-export procedure. Keep keys,
passwords, and relay configuration out of the repository and saved specs.

Now create a run-specific spec. Run this immediately before each test so its
event times are current. The four AOI variables are optional; without them the
tracks appear in the preset's Los Angeles rectangle.

```bash
# Direct lab TCP example. For the TLS relay, use 127.0.0.1 and 19089.
export CHAFF_TAK_HOST=tak-lab.example.test
export CHAFF_TAK_PORT=18087
export CHAFF_TAK_SINK=tcp       # tcp or udp

# Optional test area of interest:
# export CHAFF_TAK_MIN_LAT=34.00 CHAFF_TAK_MAX_LAT=34.05
# export CHAFF_TAK_MIN_LON=-118.30 CHAFF_TAK_MAX_LON=-118.20
# export CHAFF_TAK_STEP_DEG=0.0002

python - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

spec = json.loads(Path("examples/cot_tracks.json").read_text())
spec["name"] = "cot-to-tak"

columns = {column["name"]: column for column in spec["columns"]}
overrides = {
    "CHAFF_TAK_MIN_LAT": ("lat", "min"),
    "CHAFF_TAK_MAX_LAT": ("lat", "max"),
    "CHAFF_TAK_MIN_LON": ("lon", "min"),
    "CHAFF_TAK_MAX_LON": ("lon", "max"),
}
for variable, (column, bound) in overrides.items():
    if variable in os.environ:
        columns[column]["params"][bound] = float(os.environ[variable])

# Make the test identities obvious and keep event time aligned with delivery.
columns["callsign"]["params"]["pattern"] = "CHAFF-RAVEN-##"
spec["entity"]["id_pattern"] = "CHAFF-####"
movement = next(update for update in spec["entity"]["updates"]
                if update["updater"] == "movement")
movement["params"]["speed"] = float(
    os.environ.get("CHAFF_TAK_STEP_DEG", "0.0002"))
options = spec["output"]["options"]
options["base_time"] = datetime.now(timezone.utc).replace(
    microsecond=0).isoformat().replace("+00:00", "Z")

entity_count = int(spec["entity"]["count"])
ticks = int(spec["entity"]["ticks"])
interval = float(options["interval_seconds"])
sink = os.environ.get("CHAFF_TAK_SINK", "tcp").lower()
if sink not in {"tcp", "udp"}:
    raise SystemExit("CHAFF_TAK_SINK must be tcp or udp")

sink_options = {
    "host": os.environ["CHAFF_TAK_HOST"],
    "port": int(os.environ["CHAFF_TAK_PORT"]),
    "rate": entity_count / interval,
    "max_records": entity_count * ticks,
    "duration": ticks * interval + float(options["stale_seconds"]),
}
if sink == "tcp":
    sink_options["timeout"] = 10
spec["sink"] = {"sink": sink, "options": sink_options}

Path("cot-to-tak.json").write_text(json.dumps(spec, indent=2) + "\n")
print("wrote cot-to-tak.json with current UTC event time")
PY

chaff validate cot-to-tak.json
chaff generate cot-to-tak.json
```

The latitude/longitude bounds are starting positions. The runbook reduces the
preset's simple movement step to `0.0002` degrees per tick so tracks stay near
that AOI; `CHAFF_TAK_STEP_DEG` can override it. This remains a map-integration
fixture, not a physical motion model: the separately generated CoT `speed`
field is not derived from positional displacement.

The preset advances one tick every five event-seconds and reports eight
entities per tick, so the script sends at `8 / 5 = 1.6` events per second.
Using one event per second would make event time fall behind wall time until
later reports arrived after their own 30-second stale deadline. The generated
run is capped at 480 records and 330 seconds; normal completion takes about
five minutes.

Verify the whole path, not just the chaff receipt:

1. Before sending, connect a TAK client that can see the input's assigned
   group and zoom to the selected AOI.
2. Watch the TAK input's read/message counters or server logs while chaff is
   running.
3. Expect eight stable `CHAFF-RAVEN-…` markers, each updated 60 times. The
   `<takv platform="chaff">` detail provides a second synthetic-data marker.
4. After the final report, expect clients to remove or age the markers after
   the 30-second stale window according to their own policy.

`sent 480 record(s)` means the local TCP socket accepted 480 encoded events;
it is not proof that TAK parsed, routed, persisted, or displayed them. With
UDP, even that transport-level signal is unavailable.
