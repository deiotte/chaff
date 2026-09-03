# ADR-0031: A pull request builds, but never signs

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

The external assessment's F-11, confirmed by reading the workflows and then
watched happening on four consecutive pull requests in this very series —
`build chaff.exe` and `build chaff.app` ran on every one.

Both packaging workflows trigger on `pull_request` as well as on tags. Their
signing steps gate on **whether the secret exists**, not on **what event is
running**, so once a certificate is configured, a pull request would decrypt
it into the runner. The certificate was also interpolated directly into the
step's script body, which writes it into a file on the runner rather than
keeping it in the process environment.

Separately, nothing about the build was pinned: five actions at floating major
tags, and a `python:3.12-slim` base image whose tag can point at different
bytes tomorrow. "The same commit" did not mean "the same bytes".

## Decision

### 1. Keep building on pull requests; never sign on one

Removing the `pull_request` trigger would have been the simpler fix and the
wrong one — PR builds have already paid for themselves, catching three WiX
errors during ADR-0023 that would otherwise have failed a release.

So the build stays and the signing goes. Both workflows already funnel every
signing step through one `steps.signing.outputs.available` output, so the
event check goes in the step that computes it: one place, and a signing step
added later inherits it instead of having to remember. That is the same
reasoning as ADR-0025's middleware — the failure mode is forgetting, so the
check belongs where forgetting is impossible.

The certificate is now read through `env:` with an expression that yields an
empty string on a pull request, so on a PR the secret is not placed in the
runner at all — rather than placed there and then not used.

### 2. Pin every action to a commit SHA

An action runs with the workflow's token. A floating tag means its owner
chooses what that code is, at any time, retroactively. Each pin carries the
version as a trailing comment so a human can still read the file.

Pinned at the **versions already in use**, not the latest majors. Bumping
`checkout` v4 → v7 and friends in the same change would mean shipping breaking
changes I cannot test here, on the theory that newer is safer. The Node 20
deprecation warning those versions produce is real and is recorded in
ROADMAP.md as its own piece of work, to be done deliberately.

### 3. Pin the base image by digest

Same argument, and the same care: the digest is resolved from the registry,
not guessed.

### 4. Least privilege

No workflow declared `permissions`, so every job took the repository default.
Each now declares `contents: read` at the top; the two release jobs that
publish assets re-declare the `contents: write` they need.

### 5. Dependabot, because a pin without a bump is a liability

This is the part the finding does not ask for and the change needs. A pinned
digest stops a mutable tag from changing under you *and* stops security
patches from arriving. Without automation, "pinned" quietly means "stale", and
in a year the pin is the vulnerability.

`.github/dependabot.yml` covers github-actions, docker and pip: Dependabot
proposes the bump, CI proves it, a person merges it. `test_something_bumps_the_pins`
asserts it stays that way, deliberately in the same suite as the pinning
tests, because the two decisions only make sense together.

## Consequences

- PR builds continue and are unsigned, with a `::notice` saying why rather
  than a warning implying something is broken.
- A tag build signs exactly as before.
- Bumping an action or the base image now means editing a SHA, which is what
  Dependabot is for.
- `test_supply_chain.py` fails the build if a pin floats, a signing secret
  loses its event guard, a secret returns to a script body, a workflow drops
  its permissions block, or the Dependabot config stops covering the pinned
  ecosystems.

## Residual — the important one

**A contributor who can edit the workflow can remove the gate.** That is the
exact threat the report describes, and no in-workflow check can stop it: the
attacker's first commit is to the check. What actually stops it is repository
configuration, not code —

- a GitHub **Environment** holding the signing secrets, with required
  reviewers, so the signing job waits for a human;
- **branch protection** requiring review on `.github/workflows/**`.

Both are settings changes on the repository, outside what this PR can make.
This change removes the accidental exposure and raises the cost of the
deliberate one; it does not close it. Recorded here so nobody reads the guard
as more than it is.

**Pinning does not verify.** A SHA says "this exact code", not "this code is
good". Nothing here reviews what an action does, and there is no SBOM or
lockfile for the Python dependency set — `pip install -e .` still resolves
ranges at build time. That is the remaining half of the report's
"mutable build inputs" and it is a larger change.
