# ADR-0029: A small request may not buy a large amount of work

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

The external assessment's F-09, which is really two findings that share a
shape: **a cheap input buying expensive work.**

Both reproduced on the merged tree.

### Amplification in derived formulas

The evaluator bounded exponentiation and nothing else. Measured:

| formula | result | time |
|---|---|---|
| `a * 1000000` | 1,000,000 chars | 4 ms |
| `a * 1000000 * 100` | **100,000,000 chars** | 441 ms |
| `a * b` (`b` from a column) | 10,000,000 chars | 5 ms |
| `(10 ** 1000) ** 1000` | a 10^1,000,000 integer | — |

Three things make this worse than the headline number:

1. **It is per row.** The API caps inline generation at 100,000 rows, which
   does nothing here: even a 100-row *preview* of a 100 MB field is 10 GB.
2. **The multiplier can come from data.** `a * b` reads `b` from a generated
   column, so the spec carries no suspicious literal.
3. **The exponent guard was per-node.** `(10 ** 1000) ** 1000` has an exponent
   of exactly 1000 at each step and walks straight through.

### Unlimited concurrent stream jobs

`start_job` never counted running jobs, and `_prune_locked` only ever dropped
*finished* ones. The existing `CHAFF_STREAM_MAX_RECORDS` / `MAX_SECONDS`
ceilings bound how **long** each job runs, not how **many** start. Proof: 70
jobs started, 70 threads alive, zero refusals.

## Decision

### 1. Predict the blowup; never build it

A guard that checks the *result* has already paid for the megabyte, and
paying for it is most of the damage. So every operator that can amplify is
judged from its operands, before it runs:

| operator | predicted from |
|---|---|
| `*` (repeat) | `size(seq) × n` |
| `+` (join) | `size(a) + size(b)` |
| `**` | `bits(base) × exponent` |
| `%` on text | refused outright — see below |

Refusals now cost ~0 ms and allocate nothing.

### 2. Size counts nesting, not just length

`len` is not a budget. `[[x] * 1000] * 1000` has a length of 1000 and a
million elements; nesting it twice more is a gigabyte. So `_size` charges a
sequence for its own length **plus** everything inside it, which is what
bounds the repetition *product* rather than only the outermost repeat.

### 3. `%` on text is refused

`"%1000000d" % 5` is a megabyte, and the width lives inside a format string —
there is nothing in the operand sizes to predict it from. printf formatting
has no documented use in a derived column; numeric modulo (`id % 10`) is the
real one and is untouched.

This one is worth recording honestly: **the backstop found it, not the
design.** The predictions were written from an enumeration of amplifying
operators, that enumeration was wrong, and the post-check caught `%` after
allocating 100 MB. That is exactly the argument for keeping a backstop that
does not depend on the enumeration being right.

### 4. A backstop that doesn't trust the list

After every binary operation the result is measured anyway. `int * int` is
deliberately *not* predicted — a wide integer is cheap, so predicting it
would cost more than it saves — and the backstop is what keeps the budget
honest for it.

### 5. Limits

| limit | value | why |
|---|---|---|
| `_MAX_VALUE_SIZE` | 100,000 units | a derived cell is a field, not a payload |
| `_MAX_INT_BITS` | 8,192 | Python itself won't render an int past ~4,300 digits |
| `_MAX_EXPR_CHARS` | 2,000 | parsing is work; a 1 MB formula is its own vector |

These are constants, not settings. A knob here invites "just raise it", and
100 KB for one demo field is already far past legible.

### 6. An admission cap on stream jobs

`CHAFF_STREAM_MAX_ACTIVE_JOBS`, default **8** — enough for the several feeds
a real demo runs (a broker, a TAK feed, an HTTP endpoint) while putting the
ceiling somewhere a person chose. A 9th start is refused.

**The count and the insert share one lock hold.** Counting first and
inserting after would let N simultaneous requests all read the same under-cap
count and all be admitted; a cap that isn't indivisible isn't a cap. Verified
with 70 concurrent starts: exactly 8 admitted.

The refusal is **429, not 422**. The spec is fine; the server is busy. That
distinction is what tells a client to retry rather than to edit and resubmit,
and the message names the way out (stop one, or raise the ceiling).

## Consequences

- Formulas that build very large values now fail with a message naming the
  limit. No example spec is affected — they compute totals and tiers.
- String formatting via `%` stops working in formulas. Nothing in the repo
  used it.
- A 9th concurrent push job is refused until one finishes or is stopped. The
  UI already surfaces the message verbatim, so no UI change was needed.
- `CHAFF_STREAM_MAX_ACTIVE_JOBS` is forwarded by Compose and documented in
  `.env.example` — the ADR-0024 guard failed until it was, which is the guard
  working.

## Residual

**A hung sink still holds its slot.** `max_seconds` bounds the record
*generator*, not a sink blocked on a socket write. Eight stuck jobs would
therefore wedge the feature until the process restarts — better than
unbounded threads, but not fixed here. Sink-level timeouts are their own
change.

**The budget is per value, not per dataset.** A spec can still ask for
100,000 rows × many columns × 100 KB. The row cap and this cap together bound
the product far below what one row could previously produce, but there is no
single accounting of total bytes across a run. That is the "shared cost
budget" the report asks for in full, and it is a larger change than this one.

**Nothing here rate-limits requests.** A caller who can start jobs can still
start, stop, and restart them in a loop. Request-level throttling belongs
with F-10.
