# ADR-0026: Egress policy checks the effective configuration

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03
- Extends [ADR-0018](0018-streaming-surface-hardening.md)

## Context

The red-team assessment raised two findings against the destination policy.
Both were reproduced against the merged tree first.

### F-04 — the policy did not check what the sink would use

Three bypasses, all confirmed:

- **Only the first broker was vetted.** `bootstrap.servers` is a
  comma-separated list; the parser took `.split(",")[0]`. So
  `safe.example:9092,169.254.169.254:9092` passed.
- **Bracketed IPv6 was not canonicalised.** `[fe80::1]` does not parse as an
  address with the brackets attached, so it resolved to nothing, and "resolves
  to nothing" meant "no blocked IP found".
- **The nested Kafka config could replace the checked value.**
  `options.config` is a passthrough to confluent-kafka and can set
  `bootstrap.servers`. The sink merged it *after* the policy ran. A spec named
  a safe broker, passed policy, and the producer was constructed with the
  metadata address. This one is decisive: the check and the connection
  disagreed about the destination.

### F-03 — egress is not default-deny

Loopback and RFC1918 destinations are permitted, so the job runner can send
traffic into services reachable only from the chaff host.

## Decision

**1. Policy reads the effective configuration.** `kafka.effective_config()`
builds the exact dict the `Producer` is constructed with, and both the sink
and the policy call it. They cannot disagree about the destination because
there is only one place that computes it — the same shape as `table_views()`
in ADR-0020.

**2. Every endpoint is vetted, canonicalised.** `sink_hosts()` returns a
*list*: all brokers, the URL host, the `host` option. Brackets are stripped in
the one function that parses `host:port`, so there is no second code path to
forget.

**3. Strict mode is expressed as an allow-rule, not a deny-list.** Strict
permits only globally-routable unicast: `is_global and not is_multicast`.
Writing it as a list of denied ranges misses things, and demonstrably does —
CGNAT (`100.64.0.0/10`) is **not** `is_private` in the stdlib, and multicast
reports `is_global == True`. An allow-rule cannot silently gain a hole when a
new special-purpose range is assigned.

**4. Strict is the default when chaff is a network service, not always.**
This is where the implementation departs from the assessment's remediation
("default-deny egress ... require an explicit allowlist for every sink"), and
the reason is the product:

The push sinks exist so an operator can send demo data to *their own*
destination. In practice that destination is `localhost:9092`, a container
name like `kafka:9092` (the shipped compose fixture), or an internal `10.x`
broker. Blanket default-deny would break the feature's primary use and the
repo's own documented dev fixture, requiring configuration before the feature
works at all.

So `CHAFF_STREAM_EGRESS` takes `strict` or `permissive`, and when unset it
follows the same signal ADR-0025 uses to decide whether this instance is a
*service* or a *tool*: a configured `CHAFF_API_TOKEN` means service, and a
service should not be usable to reach the host's own network. No token means
a single operator on their own machine, where reaching their own broker is
the point.

This is a judgement call, stated plainly: an operator who exposes chaff
without setting a token gets the loopback bind from ADR-0024 and no strict
egress. That combination is already refused at the auth layer (ADR-0025
returns 401 to every remote caller when no token is set), so there is no
window where an unauthenticated remote caller reaches a permissive policy.

**5. Strict mode fails closed on an unresolvable host.** If the address cannot
be checked, the decision is not handed to the sink's connect-time resolution.

**6. The allowlist satisfies the range check, not the metadata block.** Naming
a host in `CHAFF_STREAM_ALLOWED_HOSTS` is an explicit operator decision about
that destination, so it passes strict mode. Cloud metadata stays blocked
regardless; overriding that is a separate switch
(`CHAFF_STREAM_ALLOW_LINK_LOCAL`) because it is a separate decision.

## Consequences

- **An exposed instance loses private-network egress by default.** Setting a
  token now also tightens egress. An operator who wants both adds the
  destination to `CHAFF_STREAM_ALLOWED_HOSTS` — which is already the
  documented step when exposing chaff.
- **The local demo is untouched.** `localhost:9092`, `kafka:9092`, the
  compose streaming fixture and the TCP/UDP local-listener tests all still
  work, and there are tests asserting exactly that so a future tightening
  can't quietly break them.
- **The DNS-rebinding caveat from ADR-0018 still stands**, and strict mode
  narrows it: a name must resolve to a public address at check time.
- **`sink_hosts()` is public API of the policy module** now, because the
  endpoint list is useful to assert against directly in tests.

## Mistakes made building this, kept as tests

- Inserting `effective_config` above `kafka_sink` detached the
  `@stream_sink("kafka")` decorator, silently registering the *wrong function*
  as the kafka sink. Caught by an existing kafka test;
  `test_kafka_is_still_registered_as_a_stream_sink` now guards it.
- A first version had a separate `_strip_brackets` helper that was
  **unreachable** — `_split_host_port` already unbrackets every form. The
  bracket mutation test passed with the helper deleted, which is what exposed
  it as dead code. Removed; the bracket test is now parametrised over the
  ported and unported forms.
- The test fixture originally reloaded `netpolicy` to pick up env changes.
  That rebinds `DestinationBlocked` to a new class object, so the job
  runner's `except DestinationBlocked` stopped matching and an unrelated
  test failed with the exception escaping as a 500. Every setting is read at
  call time, so no reload is needed. Reloading a module that other code holds
  references into is a trap.

## Alternatives considered

- **Blanket default-deny, as the assessment recommends.** Correct for a pure
  SSRF threat model, wrong for this product — see decision 4. Available with
  one setting for anyone whose threat model wants it.
- **Pin the resolved IP into the socket** to close DNS rebinding fully. That
  reaches into the sinks and would make them destination-aware, breaking the
  format ⟂ sink separation (INV-2). Still deferred, still documented.
- **Validate `options.config` by rejecting security-sensitive keys.** Cheaper
  than computing the effective config, and it would need a maintained list of
  which confluent-kafka keys are sensitive — a deny-list with the same
  failure mode as decision 3. Computing what the producer actually gets has
  no list to keep current.
