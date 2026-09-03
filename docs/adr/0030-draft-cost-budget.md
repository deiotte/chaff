# ADR-0030: The one route that spends money gets a budget

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

The external assessment's F-10, whose headline was that `/draft` was
unauthenticated. **That half is already closed**: ADR-0025 moved access
control into middleware, so with `CHAFF_API_TOKEN` set the route is refused
like every other one, and with no token a remote caller gets a 401.

What ADR-0025 did not close is the *cost* half, and re-measuring on the merged
tree as a local operator shows it intact:

```
description of    10,000 chars -> 200, provider received    10,000 chars
description of 1,000,000 chars -> 200, provider received 1,000,000 chars
description of 5,000,000 chars -> 200, provider received 5,000,000 chars
40 rapid requests -> [200], provider calls made: 40
```

Every other route in chaff spends CPU on a machine the operator already owns.
This one reaches a paid API, so the failure mode is not a slow server — it is
an invoice that arrives later. And `draft_spec` retries once on an invalid
draft, so each request can be two calls.

Authentication does not fix this on its own. A token says *who* may spend;
nothing said *how much*.

## Decision

Three limits, in `api/draft_budget.py`, checked before anything reaches a
provider.

### 1. A prompt ceiling — 4,000 characters

A dataset description is a sentence. `CHAFF_DRAFT_MAX_CHARS` overrides it. The
refusal is a **413** naming the limit and saying what the field is for, rather
than a generic rejection.

### 2. A request rate — 10 per minute, per client

Drafting is interactive: someone tweaks a sentence and re-drafts several
times, so the limit has to fit a person iterating and not a loop. Refusals are
**429** and name both the cost and the way out.

Per **client address**, because that is the only identity the server has —
there are no accounts, and a shared token cannot tell two callers apart. This
is stated plainly rather than dressed up as per-user.

Counting and recording share one lock hold. Counting first and recording after
would let simultaneous requests all read the same under-cap count and all be
admitted — the same defect the stream-job cap had in ADR-0029, and worth
naming twice because it looks correct both times.

Setting the rate to `0` turns drafting off entirely (**503**), which is the
report's "make the feature opt-in or disabled" without breaking the documented
zero-config path where a key is present and it just works.

### 3. A wall-clock timeout — 60 seconds

Every provider call now carries one. Without it a hung connection pins a
worker thread for as long as the socket stays open, which costs the caller
nothing and the operator a worker.

### The budget applies to a pasted key too

Bring-your-own-key spends someone else's quota, but chaff is still the proxy,
and "an uncontrolled proxy for prompt traffic" is the shape of abuse the
report describes. The limits are about what the server will do, not about
whose card is on file.

## Consequences

- A description over 4,000 characters is refused. Nothing in the UI or docs
  ever produced one.
- Rapid drafting is throttled at 10/minute per address; interactive use is
  unaffected.
- Three new settings, all forwarded by Compose and documented in
  `.env.example`. The ADR-0024 guard failed until they were, which is the
  guard working — the second time this has happened, which is the point of it.

## Residual

**This rate-limits one route, not the API.** `/preview`, `/generate` and the
stream endpoints have their own caps (rows, records, seconds, active jobs) but
no request rate. A caller who can reach them can still hammer them. A shared
bucket across every route is a different change with a different shape, and
claiming this covers it would be worse than saying it doesn't.

**Per address is not per person.** Behind a reverse proxy every request can
share one address, and a determined caller can change theirs. The limit raises
the cost of abuse; it does not identify anyone.

**The retry still doubles a request.** An invalid draft costs two provider
calls, and the rate limit counts requests rather than calls. That is
deliberate — the retry is what makes drafting reliable — but the accounting is
worth knowing when setting the rate.
