# ADR-0022: Browser-driven UI tests

- Status: Accepted
- Owner: Karl (implemented by Claude Code)
- Date: 2026-09-03

## Context

ADR-0020 and ADR-0021 both shipped with the same caveat: CI had no way to
execute the page, so the UI contract was guarded by *parsing `index.html` as
text* — asserting that `loadSpecIntoForm` mentions `spec.entity`, that
`baseSpec` calls `syncAdvancedFromEditors(true)`, and so on.

Those guards were honest about what they were. Every one was mutation-tested,
and they did fail when the original bug was reintroduced. But they assert on
the *shape of the fix*, not the behaviour, which has three consequences:

- **They break on restructuring.** Renaming a function fails the test with
  nothing actually wrong. This happened during ADR-0021: adding an argument
  to `syncAdvancedFromEditors` broke a guard that was testing for
  `syncAdvancedFromEditors()` literally.
- **They can pass while the page is broken.** A syntax error anywhere in the
  script blanks the entire UI, and every regex assertion still passes.
- **They can be hollow without anyone noticing.** Writing this ADR's test
  suite turned up exactly that: the source guard for per-table column
  scoping had a browser-based sibling that asserted
  `offered <= {"qty"}` — which an *empty* set satisfies. The datalist only
  populates on focus, the test never focused, and it passed with the bug
  deliberately reintroduced. A subset assertion with no non-emptiness check
  is not a test.

The real bug in ADR-0020 was a page that looked correct in source and put
the wrong spec on the wire. Only executing it can catch that class.

## Decision

**1. Drive the real page against the real server.** `tests/test_ui_browser.py`
runs Playwright against Chromium, pointed at the actual FastAPI app on a real
port (a `live_server` fixture, uvicorn in a thread). `TestClient` is not
enough: the page is served as static files and talks to the API over
`fetch()`, so it needs a genuine HTTP origin.

**2. Assert on the spec the page puts on the wire.** A `sent` fixture records
every POST body. Tests check what the form *produced*, because that is the
product (INV-1) — the UI's only job is to build a `DatasetSpec`. Asserting on
rendered DOM alone would have missed ADR-0020 exactly as the source guards
did.

**3. A JavaScript error fails the test.** The `page` fixture collects
`pageerror` and asserts none at teardown. A silent console exception is how
the original bug hid; no test here may pass while the page is throwing.

**4. Skip locally, mandatory in CI.** `make check` is the definition of green
for every contributor (AGENTS.md §3), so it must stay runnable with no
browser installed — the suite skips cleanly. That makes a *silent hole*
possible in CI: if the browser install broke, everything would skip and
`make check` would still pass with zero coverage of the page. CI therefore
sets `CHAFF_REQUIRE_BROWSER_TESTS=1`, which turns any skip in the browser
fixtures into a failure naming the cause. Both paths are verified.

**5. Retire the superseded source guards, keep the two that aren't.** The
round-trip and editor assertions are deleted — a browser test proves each of
them better. Two remain because driving the page genuinely cannot show them:

- **`test_ui_never_hardcodes_updater_ids`** — INV-4 compliance is a property
  of the source. A hardcoded list that happens to match the registry renders
  identically in a browser; it only diverges later, when someone registers a
  new updater and it silently fails to appear.
- **`test_ui_javascript_parses`** (`node --check`) — a syntax error would fail
  every browser test, but they *skip* without a browser. This runs anywhere,
  costs milliseconds, and reports the offending line.

## Consequences

- **The ADR-0020 and ADR-0021 defects are now caught by execution.** Verified
  by mutation: removing the `entity`/`tables` emit fails 5 tests, removing
  the editor read-back fails 4, unscoping `columnsBefore` fails 1.
- **Coverage is reduced for a contributor with no browser.** This is a real
  cost of retiring the source guards, accepted deliberately: the alternative
  is maintaining two suites where the weaker one dictates how the code may be
  named. `pip install -e '.[dev-browser]' && playwright install chromium`
  restores it, and CI enforces it on every push regardless.
- **`make check` grows by ~30s** where a browser is present. Worth it; this is
  the only tier that tests the thing users actually touch.
- **A Chromium build that doesn't match Playwright's pin** (some CI and dev
  images ship their own) is handled: `CHAFF_TEST_CHROMIUM` takes an explicit
  path, and the fixture otherwise probes `PLAYWRIGHT_BROWSERS_PATH`.

## What these tests are not

They are not a visual-regression suite — no screenshots are compared, and
styling is unasserted. They test that the form builds the right spec and that
its invariants (mode exclusivity, per-table column scoping, validation
timing) hold when a person actually clicks things. Layout is still reviewed
by eye.

## Alternatives considered

- **Keep both tiers.** Rejected: the weaker tier ends up constraining how the
  stronger one's code may be written (the rename breakage above), and two
  suites asserting one behaviour drift.
- **jsdom / a headless DOM instead of a real browser.** Lighter, and it would
  run everywhere without a browser install. But it cannot exercise `fetch()`
  against the real API, which is where the ADR-0020 bug lived, and the whole
  point of this tier is to stop trusting a simulation of the page.
- **A separate CI job for browser tests.** Cleaner failure attribution, but it
  splits "the definition of green" in two. Keeping them inside `make check`
  means one command still answers the question.
