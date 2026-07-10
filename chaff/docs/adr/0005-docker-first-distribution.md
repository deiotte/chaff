# ADR-0005: Docker-first distribution

- Status: Accepted
- Owner: Karl
- Date: 2026-07-10

## Context
This runs wherever Karl lands: homelab, cloud sandboxes, customer demo
environments. "Pull from GitHub and build" must be the whole install story.

## Decision
Single Dockerfile serving the API+UI; docker-compose with an optional
`streaming` profile carrying a Kafka broker for Phase 2 sink development.
Engine stays pip-installable as a plain library for CLI/headless use.

## Consequences
- No host-machine assumptions in code; config via env vars and spec options.
- The compose Kafka broker is a dev fixture, not a product dependency.
- CI (when added) builds the image as part of `check`.
