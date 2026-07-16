"""Outbound destination policy for the streaming job runner (ADR-0018).

The push sinks (http/tcp/udp/mqtt/kafka) fire packets at a host the caller
names — that's their whole job. On an unauthenticated, network-exposed API
that same capability is an **SSRF / port-scan primitive**: a caller could aim
a sink at the cloud-metadata endpoint (169.254.169.254) to exfiltrate creds,
or sweep internal hosts and read connect-success/refused back off the job's
`error` field. This module vets a spec's destination *before* a job launches.

An API-layer concern, not the engine's (INV-1/INV-2): the engine encodes and
sinks deliver; egress policy about *who the served API may reach* lives here.
The CLI operator streams from their own machine to wherever they like and is
deliberately **not** gated — only the served API job runner is.

Defaults keep the localhost demo zero-config:
  - **Always blocked:** cloud-metadata / link-local addresses — IPv4
    169.254.0.0/16 (covers the AWS/GCP/Azure 169.254.169.254 metadata IP),
    IPv6 fe80::/10, and the fd00:ec2::254 metadata address. No legitimate demo
    streams there, and it's the single highest-value SSRF target. Escape hatch
    for the rare real case: `CHAFF_STREAM_ALLOW_LINK_LOCAL=1`.
  - **Allowed:** loopback and private ranges, so `localhost:1883`,
    `kafka:9092`, `192.168.x` brokers, etc. all keep working untouched.
  - **Opt-in allowlist:** `CHAFF_STREAM_ALLOWED_HOSTS` (comma-separated) — when
    set, the destination host must match one of the listed names/IPs. This is
    the positive control an operator flips on when exposing chaff on a network.

Caveat (noted in the ADR): the check resolves the host at job-start, while the
sink resolves again at connect-time, so a DNS name that rebinds between the two
isn't fully closed. Pinning the resolved IP into the socket would cross into
the sink (INV-2); it's deferred. The always-on metadata block plus an allowlist
of names you control cover the realistic exposure.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import Optional
from urllib.parse import urlsplit

from chaff.spec import DatasetSpec

# Metadata addresses outside the link-local /16 (IPv6 ULA form used by some
# clouds). `is_link_local` catches 169.254.0.0/16 and fe80::/10 on its own.
_METADATA_IPS = {"fd00:ec2::254"}


class DestinationBlocked(ValueError):
    """The spec's sink points at a destination egress policy forbids."""


def _sink_host(spec: DatasetSpec) -> Optional[str]:
    """The host a streaming sink will connect to, or None when the sink carries
    no host (e.g. a broker-free in-memory test sink)."""
    o = spec.sink.options
    url = o.get("url")
    if url:
        return urlsplit(str(url)).hostname
    host = o.get("host")
    if host:
        return str(host)
    servers = o.get("bootstrap.servers") or o.get("bootstrap_servers")
    if servers:  # kafka: "host:port,host:port" — vet the first broker
        first = str(servers).split(",")[0].strip()
        return first.rsplit(":", 1)[0] if ":" in first else first
    return None


def _allowlist() -> set[str]:
    raw = os.environ.get("CHAFF_STREAM_ALLOWED_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _allow_link_local() -> bool:
    return os.environ.get("CHAFF_STREAM_ALLOW_LINK_LOCAL", "").strip().lower() in (
        "1", "true", "yes", "on")


def _resolved_ips(host: str) -> list:
    """Every IP `host` resolves to (a literal IP resolves to itself). A name
    that doesn't resolve yields [] — it can't be a metadata hit, and the sink
    will surface the real connection error when it tries."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0].split("%")[0]))
        except ValueError:
            continue
    return out


def _blocked_ip(ip) -> bool:
    return str(ip) in _METADATA_IPS or ip.is_link_local


def check_destination(spec: DatasetSpec) -> None:
    """Raise `DestinationBlocked` if the spec's sink points somewhere egress
    policy forbids. A sink with no host (test/in-memory) passes untouched."""
    host = _sink_host(spec)
    if not host:
        return

    allow = _allowlist()
    if allow and host.strip().lower() not in allow:
        raise DestinationBlocked(
            f"destination host '{host}' is not in CHAFF_STREAM_ALLOWED_HOSTS")

    if _allow_link_local():
        return
    for ip in _resolved_ips(host):
        if _blocked_ip(ip):
            raise DestinationBlocked(
                f"destination '{host}' resolves to link-local/metadata address "
                f"{ip}, which is blocked to prevent SSRF against cloud metadata "
                f"and internal infrastructure (set CHAFF_STREAM_ALLOW_LINK_LOCAL=1 "
                f"to override for a trusted deployment)")
