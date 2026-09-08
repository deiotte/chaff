# ADR-0041: Guard the runtime, not just the pin

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-08

## Context

ADR-0031 pinned all five actions to commit SHAs and deliberately left them at
the versions then in use:

> Pinned at the **versions already in use**, not the latest majors. [...] The
> Node 20 deprecation warning those versions produce is real and is recorded
> in ROADMAP.md as its own piece of work, to be done deliberately.

That work is done, and nobody did it as a piece of work: Dependabot bumped
each action one PR at a time, CI proved each bump, and a person merged it —
exactly the loop ADR-0031 §5 set up. `checkout` v4→v7, `setup-python` v5→v7,
`upload-artifact`→v7, `download-artifact`→v8, `action-gh-release` v2→v3. Every
pin now resolves to `using: node24`, and every one sits at the tip of its
major.

So the roadmap item closes with no code change. What it leaves behind is a
gap: **nothing in the repo can tell whether that is still true tomorrow.**
`test_supply_chain.py` asserts a pin is a 40-hex SHA with a version comment,
which a Node 20 pin satisfies perfectly. The suite would have read green
through the entire deprecation it was written alongside.

The failure mode is not hypothetical and not exotic. A pin goes backwards
through a bad conflict resolution, a revert that takes the workflow with it,
or a hand-edited SHA copied from a stale README. It warns for months and then
every workflow fails at once, on a commit that changed nothing — the worst
kind of build break, because the diff that caused it is nowhere near the day
it fires.

## Decision

### 1. Read what the pin actually is, not what the comment says

`test_no_action_runs_on_a_deprecated_node_runtime` fetches each pinned
action's own `action.yml` **at the pinned commit** and asserts `runs.using` is
not a runtime GitHub has retired.

Reading the trailing `# vN` comment instead would have been offline and free,
and it would assert the thing we already know how to get wrong: the comment is
prose, maintained by the same bump that could be wrong. The SHA is the only
part of a pin that is load-bearing, so the SHA is what gets dereferenced.

### 2. A deny-list, not an allow-list

`DEPRECATED_NODE_RUNTIMES = {"node12", "node16", "node20"}`.

An allow-list (`{"node24", "docker", "composite"}`) catches the *next*
deprecation for free, which is the whole appeal. It also fails this build the
day GitHub ships node28 and Dependabot bumps into it — a red CI on a change
that is strictly correct. A guard that cries wolf gets deleted, and then it
catches nothing at all. The deny-list needs a human to extend it; the comment
above it says so.

### 3. Skip offline, fail in CI

This is the first test in the suite that touches the network, which is a real
cost: `make check` is the definition of green for every contributor
(AGENTS.md §3), and a check that needs GitHub is a check that fails on a
plane.

Same bargain ADR-0022 struck for the browser tests, and the same mechanism, so
there is one idiom to learn rather than two: the tier skips when it cannot
reach `raw.githubusercontent.com`, and CI sets
`CHAFF_REQUIRE_ACTION_RUNTIME_TESTS=1` so a skip there is a failure. A rate
limit or a 5xx counts as *unreachable*, not as a bad runtime — we did not
learn the pin is wrong, only that we could not look.

## Consequences

- Five extra unauthenticated GETs to `raw.githubusercontent.com` per CI run,
  one per distinct pin — deduplicated across the three workflows, since the
  runtime is a property of the pin and not of the file it appears in.
- A contributor offline sees five skips and a green `make check`.
- Bumping an action stays Dependabot's job. Noticing a bump that went
  backwards is now the suite's.
- The Node 20 roadmap item closes as done-by-Dependabot, with the guard as the
  thing that keeps it closed.

## Residual

**A composite action's steps are not inspected.** `runs.using: composite`
passes this check, and such an action can itself call a Node 20 action. None
of the five are composite today. Recursing into them is a bigger change than
the risk currently justifies, and is written down here rather than left for
someone to discover.

**A deny-list only knows what it was told.** When GitHub deprecates node24,
this test passes until a human adds it to the set. That is the deliberate
trade in §2, not an oversight — but it is the one thing about this guard that
will quietly stop being true.

**This checks the runtime, not the code.** ADR-0031's point stands unchanged:
a SHA says "this exact code", not "this code is good", and nothing here
reviews what an action does.
