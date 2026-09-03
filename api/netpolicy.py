"""Outbound destination policy for the streaming job runner (ADR-0018).

The push sinks (http/tcp/udp/mqtt/kafka) fire packets at a host the caller
names — that's their whole job. On a network-exposed API that same capability
is an **SSRF / port-scan primitive**: a caller could aim a sink at the
cloud-metadata endpoint (169.254.169.254) to exfiltrate creds, or sweep
internal hosts and read connect-success/refused back off the job's `error`
field. This module vets a spec's destination *before* a job launches.

Since ADR-0025 there is no *unauthenticated* network caller — a remote request
needs the operator token. That reduces the threat but does not remove it: the
token is one shared operator secret with no per-caller scope, it can leak, and
a reverse-proxied deployment makes every request look local. So this stays a
control in its own right rather than something auth made redundant.

An API-layer concern, not the engine's (INV-1/INV-2): the engine encodes and
sinks deliver; egress policy about *who the served API may reach* lives here.
The CLI operator streams from their own machine to wherever they like and is
deliberately **not** gated — only the served API job runner is.

What is checked (ADR-0026): **every** endpoint the sink will contact, derived
from the sink's **effective** configuration. Both matter — the previous version
vetted only the first kafka broker and only the pre-merge options, so a spec
could name a safe destination, pass the check, and connect somewhere else.

  - **Always blocked:** cloud-metadata / link-local addresses — IPv4
    169.254.0.0/16 (covers the AWS/GCP/Azure 169.254.169.254 metadata IP),
    IPv6 fe80::/10, and the fd00:ec2::254 metadata address. No legitimate demo
    streams there, and it's the single highest-value SSRF target. Escape hatch
    for the rare real case: `CHAFF_STREAM_ALLOW_LINK_LOCAL=1`.
  - **Egress mode:** `CHAFF_STREAM_EGRESS` is `strict` (only globally-routable
    unicast) or `permissive` (only the metadata block above). Unset, it follows
    ADR-0025's service-vs-tool signal: strict when `CHAFF_API_TOKEN` is set,
    permissive otherwise. A local operator pushing to `localhost:9092` or the
    compose `kafka:9092` fixture is the feature working, so that is not
    default-denied.
  - **Opt-in allowlist:** `CHAFF_STREAM_ALLOWED_HOSTS` (comma-separated) — when
    set, every destination host must match one of the listed names/IPs. It also
    satisfies strict mode's range check (naming a host is an operator decision
    about that host); it does **not** unblock cloud metadata.

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


def _split_host_port(endpoint: str) -> str:
    """The host part of a `host:port` / `[v6]:port` / `[v6]` / bare-host
    endpoint, with any IPv6 brackets removed.

    Brackets matter: `[fe80::1]` does not parse as an address with them
    attached, so the previous parser produced a "host" that resolved to
    nothing and sailed past the metadata block.
    """
    endpoint = endpoint.strip()
    if endpoint.startswith("["):
        close = endpoint.find("]")
        if close != -1:
            return endpoint[1:close]
    # A bare IPv6 literal contains several colons and no port.
    if endpoint.count(":") > 1:
        return endpoint
    return endpoint.rsplit(":", 1)[0] if ":" in endpoint else endpoint


def sink_hosts(spec: DatasetSpec) -> list[str]:
    """Every host the sink will contact, canonicalised (ADR-0026).

    Returns a list, not one host: a kafka `bootstrap.servers` is a
    comma-separated broker list and the old code vetted only the first entry,
    so `safe.example:9092,169.254.169.254:9092` passed. Empty when the sink
    carries no host (a broker-free in-memory test sink).
    """
    o = spec.sink.options
    raw: list[str] = []

    url = o.get("url")
    if url:
        hostname = urlsplit(str(url)).hostname   # already unbracketed
        if hostname:
            raw.append(hostname)

    host = o.get("host")
    if host:
        raw.append(str(host))

    if spec.sink.sink == "kafka":
        # The *effective* producer config, so a nested `options.config` that
        # replaces bootstrap.servers is vetted rather than bypassing the check.
        from chaff.sinks.kafka import effective_config

        servers = effective_config(o).get("bootstrap.servers", "")
        raw.extend(str(servers).split(","))
    else:
        servers = o.get("bootstrap.servers") or o.get("bootstrap_servers")
        if servers:
            raw.extend(str(servers).split(","))

    hosts = []
    for entry in raw:
        candidate = _split_host_port(str(entry))
        if candidate:
            hosts.append(candidate)
    return hosts


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


def _egress_mode() -> str:
    """'strict' or 'permissive' (ADR-0026).

    Explicit `CHAFF_STREAM_EGRESS` wins. Unset, it follows the same signal
    ADR-0025 uses to decide whether this instance is a network *service* or a
    local *tool*: a configured API token means service, and a service should
    not be usable to reach the host's own network. A local operator pushing
    demo data to their own broker — almost always loopback or RFC1918, and the
    shipped compose fixture is `kafka:9092` — is the feature working as
    intended, so that stays permissive.
    """
    raw = os.environ.get("CHAFF_STREAM_EGRESS", "").strip().lower()
    if raw in ("strict", "permissive"):
        return raw
    from . import auth
    return "strict" if auth.configured_token() is not None else "permissive"


def _link_local_reason(ip) -> Optional[str]:
    """Always blocked (unless explicitly overridden): cloud metadata."""
    if str(ip) in _METADATA_IPS or ip.is_link_local:
        return (f"link-local/metadata address {ip}, blocked to prevent SSRF "
                f"against cloud metadata and internal infrastructure (set "
                f"CHAFF_STREAM_ALLOW_LINK_LOCAL=1 to override for a trusted "
                f"deployment)")
    return None


def _non_global_reason(ip) -> Optional[str]:
    """Strict mode: allow only globally-routable unicast.

    Expressed as an allow-rule rather than a list of denied ranges because a
    deny-list misses things: CGNAT (100.64.0.0/10) is *not* `is_private` in
    the stdlib, and multicast reports `is_global == True`. "Globally routable
    and not multicast" covers private, loopback, link-local, CGNAT,
    unspecified, reserved and multicast in one condition that cannot silently
    gain a hole when a new special-purpose range is assigned.
    """
    if ip.is_multicast:
        return f"multicast address {ip}"
    if not ip.is_global:
        return (f"non-public address {ip} (private, loopback, CGNAT or "
                f"reserved). This instance runs a strict egress policy: add "
                f"the host to CHAFF_STREAM_ALLOWED_HOSTS to permit it, or set "
                f"CHAFF_STREAM_EGRESS=permissive")
    return None


def check_destination(spec: DatasetSpec) -> None:
    """Raise `DestinationBlocked` if any destination the sink will contact is
    forbidden. A sink with no host (test/in-memory) passes untouched.

    Checks *every* endpoint of the *effective* configuration — the two gaps
    that made the previous version approve destinations the sink then used
    (ADR-0026).
    """
    hosts = sink_hosts(spec)
    if not hosts:
        return

    allow = _allowlist()
    strict = _egress_mode() == "strict"
    skip_link_local = _allow_link_local()

    for host in hosts:
        listed = host.strip().lower() in allow
        if allow and not listed:
            raise DestinationBlocked(
                f"destination host '{host}' is not in CHAFF_STREAM_ALLOWED_HOSTS")

        ips = _resolved_ips(host)
        if strict and not ips and not listed:
            # Can't verify where this goes. Fail closed rather than hand the
            # decision to the sink's connect-time resolution.
            raise DestinationBlocked(
                f"destination '{host}' does not resolve, so its address can't "
                f"be checked against this instance's strict egress policy")

        for ip in ips:
            if not skip_link_local:
                reason = _link_local_reason(ip)
                if reason:
                    raise DestinationBlocked(f"destination '{host}' resolves to {reason}")
            # An explicitly allowlisted host is an operator decision naming
            # that destination, so it satisfies the range check. The
            # metadata block above still applies.
            if strict and not listed:
                reason = _non_global_reason(ip)
                if reason:
                    raise DestinationBlocked(f"destination '{host}' resolves to {reason}")
