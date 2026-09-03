"""Egress policy: every endpoint, effective config, strict mode (F-03/F-04).

Each bypass below was reproduced against the merged tree before being fixed.
The decisive one was F-04c: the policy approved `safe.example` while the
producer was configured with the metadata IP — the check and the connection
disagreed about the destination.
"""

from __future__ import annotations

import pytest

import api.netpolicy as netpolicy
from chaff.spec import load_spec

METADATA = "169.254.169.254"


def spec(sink: str, options: dict):
    return load_spec({
        "name": "t", "rows": 1, "output": {"format": "ndjson"},
        "columns": [{"name": "a", "generator": "row_id"}],
        "sink": {"sink": sink, "options": options}})


@pytest.fixture
def policy(monkeypatch):
    """Set the policy environment. Deliberately does NOT reload the module.

    Every setting here is read from `os.environ` at call time, so monkeypatch
    alone is enough. A first version of this fixture reloaded `netpolicy`,
    which rebinds `DestinationBlocked` to a *new class object* — the job
    runner's `except DestinationBlocked` then stopped matching it and an
    unrelated test in test_stream_security.py failed with the exception
    escaping as a 500. Reloading a module other code holds references into is
    a trap; don't.
    """
    def _configure(**env):
        for key in ("CHAFF_API_TOKEN", "CHAFF_STREAM_EGRESS",
                    "CHAFF_STREAM_ALLOWED_HOSTS", "CHAFF_STREAM_ALLOW_LINK_LOCAL"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        return netpolicy

    return _configure


def blocked(mod, sink, options):
    try:
        mod.check_destination(spec(sink, options))
        return False
    except mod.DestinationBlocked:
        return True


# ── F-04: the policy must check what the sink will actually use ──────

def test_metadata_as_a_later_broker_is_caught(policy):
    """Only the first comma-separated broker was vetted, so putting the
    metadata address second walked straight through."""
    mod = policy()
    assert blocked(mod, "kafka", {
        "topic": "t", "bootstrap_servers": f"safe.example:9092,{METADATA}:9092"})


def test_every_broker_in_a_long_list_is_vetted(policy):
    mod = policy()
    servers = ",".join(["a.example:9092", "b.example:9092", f"{METADATA}:9092"])
    assert blocked(mod, "kafka", {"topic": "t", "bootstrap_servers": servers})


@pytest.mark.parametrize("endpoint", ["[fe80::1]:9092", "[fe80::1]"])
def test_bracketed_ipv6_is_canonicalised(policy, endpoint):
    """`[fe80::1]` doesn't parse as an address with brackets attached, so the
    old parser produced a host that resolved to nothing and was allowed.

    Both forms are covered — with and without a port — because a first
    attempt at this fix had a separate bracket-stripping helper that was
    unreachable for the ported form, and only the parametrised case showed it
    was dead code.
    """
    mod = policy()
    assert blocked(mod, "kafka", {"topic": "t", "bootstrap_servers": endpoint})


def test_bare_ipv6_literal_is_handled(policy):
    mod = policy()
    assert blocked(mod, "tcp", {"host": "fe80::1", "port": 9999})


def test_nested_kafka_config_cannot_replace_the_checked_broker(policy):
    """The decisive bypass: options.config is a passthrough to
    confluent-kafka and can set bootstrap.servers, so the spec named a safe
    broker, passed policy, and the producer connected to the metadata IP."""
    mod = policy()
    assert blocked(mod, "kafka", {
        "topic": "t", "bootstrap_servers": "safe.example:9092",
        "config": {"bootstrap.servers": f"{METADATA}:9092"}})


def test_policy_and_producer_read_the_same_config():
    """They can't drift: both call kafka.effective_config."""
    from chaff.sinks.kafka import effective_config

    opts = {"bootstrap_servers": "safe.example:9092",
            "config": {"bootstrap.servers": f"{METADATA}:9092"}}
    assert effective_config(opts)["bootstrap.servers"] == f"{METADATA}:9092"
    assert netpolicy.sink_hosts(spec("kafka", {"topic": "t", **opts})) == [METADATA]


def test_kafka_is_still_registered_as_a_stream_sink():
    """Guards a mistake made while writing this: inserting effective_config
    above kafka_sink detached the @stream_sink decorator, silently registering
    the wrong function as the sink."""
    from chaff.sinks import get_stream_sink, is_stream_sink

    assert is_stream_sink("kafka")
    assert get_stream_sink("kafka").__name__ == "kafka_sink"


# ── the local demo must keep working (permissive default) ────────────

@pytest.mark.parametrize("sink,options", [
    ("kafka", {"topic": "t", "bootstrap_servers": "localhost:9092"}),
    ("kafka", {"topic": "t", "bootstrap_servers": "kafka:9092"}),   # compose fixture
    ("tcp", {"host": "127.0.0.1", "port": 9999}),
    ("udp", {"host": "127.0.0.1", "port": 9999}),
])
def test_local_destinations_are_allowed_without_a_token(policy, sink, options):
    """Pushing demo data to your own broker is the feature. With no token
    configured this is a local tool, and blanket default-deny would break the
    documented compose fixture along with the primary use case."""
    mod = policy()
    assert not blocked(mod, sink, options)


def test_metadata_is_blocked_even_in_permissive_mode(policy):
    mod = policy()
    assert blocked(mod, "http", {"url": f"http://{METADATA}/latest/meta-data"})


# ── F-03: strict mode for a network service ──────────────────────────

@pytest.mark.parametrize("sink,options,what", [
    ("tcp", {"host": "127.0.0.1", "port": 9999}, "loopback"),
    ("tcp", {"host": "10.0.0.5", "port": 9999}, "RFC1918"),
    ("tcp", {"host": "192.168.1.1", "port": 9999}, "RFC1918"),
    ("tcp", {"host": "100.64.0.1", "port": 9999}, "CGNAT"),
    ("udp", {"host": "224.0.0.1", "port": 9999}, "multicast"),
    ("tcp", {"host": "0.0.0.0", "port": 9999}, "unspecified"),
])
def test_strict_mode_refuses_non_public_destinations(policy, sink, options, what):
    """CGNAT is not `is_private` in the stdlib and multicast reports
    `is_global == True`, so the rule is expressed as allow-only-global-unicast
    rather than a deny-list that would miss both."""
    mod = policy(CHAFF_STREAM_EGRESS="strict")
    assert blocked(mod, sink, options), f"{what} was allowed in strict mode"


def test_strict_mode_allows_a_public_destination(policy):
    mod = policy(CHAFF_STREAM_EGRESS="strict")
    assert not blocked(mod, "http", {"url": "https://example.com/ingest"})


def test_strict_mode_refuses_an_unresolvable_host(policy):
    """Fail closed: an address that can't be checked isn't handed to the
    sink's connect-time resolution to decide."""
    mod = policy(CHAFF_STREAM_EGRESS="strict")
    assert blocked(mod, "tcp", {"host": "no-such-host.invalid", "port": 9999})


def test_allowlist_is_the_escape_hatch_in_strict_mode(policy):
    """An operator naming a host explicitly has made the decision."""
    mod = policy(CHAFF_STREAM_EGRESS="strict", CHAFF_STREAM_ALLOWED_HOSTS="10.0.0.5")
    assert not blocked(mod, "tcp", {"host": "10.0.0.5", "port": 9999})


def test_allowlisting_does_not_unblock_metadata(policy):
    """The allowlist satisfies the range check, not the metadata block —
    those are separate decisions with separate switches."""
    mod = policy(CHAFF_STREAM_ALLOWED_HOSTS=METADATA)
    assert blocked(mod, "tcp", {"host": METADATA, "port": 80})


# ── the default follows exposure ─────────────────────────────────────

def test_default_is_permissive_without_a_token(policy):
    mod = policy()
    assert mod._egress_mode() == "permissive"


def test_default_is_strict_when_a_token_is_configured(policy):
    """A configured token means this is a network service (ADR-0025), and a
    service shouldn't be usable to reach the host's own network."""
    mod = policy(CHAFF_API_TOKEN="s3cret")
    assert mod._egress_mode() == "strict"


def test_explicit_setting_overrides_the_inferred_default(policy):
    mod = policy(CHAFF_API_TOKEN="s3cret", CHAFF_STREAM_EGRESS="permissive")
    assert mod._egress_mode() == "permissive"
    assert not blocked(mod, "tcp", {"host": "127.0.0.1", "port": 9999})


def test_a_hostless_sink_passes_untouched(policy):
    mod = policy(CHAFF_STREAM_EGRESS="strict")
    assert not blocked(mod, "file", {"path": "out/x.ndjson"})
