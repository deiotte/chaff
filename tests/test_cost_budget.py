"""Cost budget: F-09 — resource amplification and unlimited active jobs.

Two independent halves of one finding:

* A derived formula could turn one cell into a megabyte, once *per row*. The
  evaluator bounded exponentiation and nothing else.
* A stream job could always be started. The existing ceilings bound how long
  each job runs, not how many run at once, so 70 launched with no refusal.

Every attack here is the report's proof or a variant that reaches the same
place by a route the first fix would have missed.
"""

from __future__ import annotations

import threading
import time

import pytest

from chaff.generators._expr import (
    _MAX_EXPR_CHARS,
    _MAX_VALUE_SIZE,
    FormulaError,
    safe_eval,
    validate_expr,
)

# ── the formula cost budget ──────────────────────────────────────────

AMPLIFIERS = [
    # (formula, row values, what makes it interesting)
    ("a * 1000000", {"a": "x"}, "the report's proof: a megabyte from one cell"),
    ("[a] * 1000000", {"a": 1}, "list repetition, not just string"),
    ("a * 1000000 * 100", {"a": "x"}, "compounded — 100 MB in 441ms unguarded"),
    ("a * b", {"a": "x", "b": 10 ** 7}, "multiplier comes from data, so the spec looks innocent"),
    ("[[a] * 1000] * 1000", {"a": 1}, "nested: len() is 1000, element count is a million"),
    ("[[[a]*100]*100]*100", {"a": 1}, "nested three deep"),
    ("a * 60000 + a * 60000", {"a": "x"}, "each half is legal; the sum is not"),
    ("(10 ** 1000) ** 1000", {}, "chained pow walks past a per-node exponent check"),
    ("'%1000000d' % a", {"a": 5}, "printf width: the size hides inside the format string"),
    ("'%100000000d' % a", {"a": 5}, "same, 100 MB — only the backstop caught this one"),
]


@pytest.mark.parametrize("formula,names,why", AMPLIFIERS,
                         ids=[f[0] for f in AMPLIFIERS])
def test_an_amplifying_formula_is_refused(formula, names, why):
    validate_expr(formula)  # shape is fine; the cost is the problem
    with pytest.raises(FormulaError):
        safe_eval(formula, names)


LEGITIMATE = [
    ("price * qty", {"price": 9.99, "qty": 3}, 29.97),
    ("round(a / b, 2)", {"a": 10, "b": 3}, 3.33),
    ("'wholesale' if net > 500 else 'retail'", {"net": 900}, "wholesale"),
    ("a * 100", {"a": "x"}, "x" * 100),
    ("len(a) + 1", {"a": "hello"}, 6),
    ("a + b", {"a": "Jane ", "b": "Doe"}, "Jane Doe"),
    ("-x", {"x": 5}, -5),
]


@pytest.mark.parametrize("formula,names,expected", LEGITIMATE,
                         ids=[f[0] for f in LEGITIMATE])
def test_ordinary_formulas_still_evaluate(formula, names, expected):
    # The budget is worthless if it costs the feature. These are the shapes
    # the docs and the example specs actually use.
    assert safe_eval(formula, names) == expected


class ExplodingStr(str):
    """A string that refuses to be built on, so a test can tell the difference
    between predicting a blowup and performing one.

    Without this, a test asserting only `FormulaError` passes either way: the
    backstop catches what the prediction misses, so removing a prediction
    fails nothing while quietly restoring the allocation it existed to avoid.
    """

    def __mul__(self, n):  # pragma: no cover - the assertion is the point
        raise AssertionError("the multiplication was actually performed")

    def __add__(self, other):  # pragma: no cover - ditto
        raise AssertionError("the concatenation was actually performed")

    __rmul__ = __mul__
    __radd__ = __add__


class ExplodingInt(int):
    """An int that refuses to be raised to a power. Same purpose."""

    def __pow__(self, other, mod=None):  # pragma: no cover
        raise AssertionError("the exponentiation was actually performed")

    __rpow__ = __pow__


def test_repetition_is_predicted_not_performed():
    # The whole value of the guard is that the megabyte is never allocated.
    # If the check ran on the *result*, this raises AssertionError instead.
    with pytest.raises(FormulaError):
        safe_eval("a * 1000000", {"a": ExplodingStr("x" * 100)})


def test_concatenation_is_predicted_not_performed():
    big = ExplodingStr("x" * 60_000)
    with pytest.raises(FormulaError):
        safe_eval("a + b", {"a": big, "b": big})


def test_exponentiation_is_predicted_not_performed():
    # (10 ** 1000) ** 1000 slips past the per-node exponent cap, so the width
    # has to be predicted from the base. Computing it first would be the cost.
    with pytest.raises(FormulaError):
        safe_eval("a ** 1000", {"a": ExplodingInt(10 ** 300)})


def test_the_backstop_catches_an_operator_no_prediction_covers():
    # int * int is deliberately not predicted — a wide integer is cheap, so
    # predicting it would cost more than it saves. The backstop is what makes
    # the budget hold anyway. `%` on text was found exactly this way: the
    # backstop caught it, which is how the missing prediction became visible.
    huge = 2 ** 500_000                      # 62,500 units: under the ceiling
    with pytest.raises(FormulaError, match="over the"):
        safe_eval("a * a", {"a": huge})      # 125,000 units: over it


def test_a_value_just_under_the_ceiling_is_allowed():
    # The boundary is a real boundary, not a vague discouragement.
    assert len(safe_eval("a * 1000", {"a": "x" * 99})) == 99_000


def test_a_value_just_over_the_ceiling_is_refused():
    with pytest.raises(FormulaError, match="over the"):
        safe_eval("a * 1001", {"a": "x" * 100})


def test_the_refusal_names_the_limit_and_what_to_do():
    with pytest.raises(FormulaError) as e:
        safe_eval("a * 1000000", {"a": "x"})
    msg = str(e.value)
    assert f"{_MAX_VALUE_SIZE:,}" in msg
    assert "reduce the repetition" in msg


def test_an_overlong_formula_is_refused_before_parsing():
    with pytest.raises(FormulaError, match="over the"):
        validate_expr("1+" * _MAX_EXPR_CHARS + "1")


def test_a_modest_exponent_still_works():
    assert safe_eval("2 ** 10", {}) == 1024


def test_numeric_modulo_is_untouched():
    # Only `%` on *text* is refused. Numeric modulo is the documented use and
    # cannot amplify anything.
    assert safe_eval("id % 10", {"id": 47}) == 7
    assert safe_eval("a % b", {"a": 10.5, "b": 3}) == pytest.approx(1.5)


def test_string_formatting_is_refused_by_prediction_not_by_the_backstop():
    # The width lives inside the format string, so there is nothing to predict
    # from operand sizes — the operator itself has to go. If this were left to
    # the backstop, the 100 MB would be built first and then thrown away.
    with pytest.raises(FormulaError, match="string formatting"):
        safe_eval("'%1000000d' % a", {"a": 5})


# ── the active-job cap ───────────────────────────────────────────────

pytest.importorskip("fastapi")

from api import netpolicy, stream_jobs  # noqa: E402
from chaff.sinks import _STREAM_REGISTRY  # noqa: E402
from chaff.spec import load_spec  # noqa: E402


@pytest.fixture
def blocking_sink(monkeypatch):
    """A sink that never returns, so every started job stays `running`.

    The cap is about jobs that are *in flight*; a sink that finishes
    immediately would let a hundred starts through without ever proving
    anything about concurrency.
    """
    release = threading.Event()

    def sink(spec, records):
        for _ in records:
            release.wait(10)
        return "released"

    monkeypatch.setitem(_STREAM_REGISTRY, "blocking_test", sink)
    # The destination guard is a different finding; stub it so a failure here
    # can only mean the job cap.
    monkeypatch.setattr(netpolicy, "check_destination", lambda spec: None)
    monkeypatch.setattr(stream_jobs, "check_destination", lambda spec: None)
    monkeypatch.setattr(stream_jobs, "_JOBS", {})
    yield release
    release.set()


def blocking_spec():
    return load_spec({
        "name": "j", "rows": 1, "seed": 1,
        "columns": [{"name": "id", "generator": "row_id"}],
        "output": {"format": "ndjson", "options": {}},
        "sink": {"sink": "blocking_test", "options": {"host": "example.com"}},
    })


def start(spec):
    return stream_jobs.start_job(spec, max_records=1000, max_seconds=30)


def test_the_cap_refuses_the_job_past_the_ceiling(blocking_sink):
    spec = blocking_spec()
    cap = stream_jobs._max_active_jobs()
    for _ in range(cap):
        start(spec)
    with pytest.raises(stream_jobs.TooManyJobs, match="already running"):
        start(spec)
    assert stream_jobs.active_job_count() == cap


def test_seventy_concurrent_starts_admit_exactly_the_cap(blocking_sink):
    """The report's proof: 70 blocking jobs started with no refusal."""
    spec = blocking_spec()
    cap = stream_jobs._max_active_jobs()
    outcomes: list[str] = []
    lock = threading.Lock()

    def racer():
        try:
            start(spec)
            with lock:
                outcomes.append("admitted")
        except stream_jobs.TooManyJobs:
            with lock:
                outcomes.append("refused")

    threads = [threading.Thread(target=racer) for _ in range(70)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Counting and inserting must share one lock hold; if they don't, several
    # racers read the same under-cap count and all get in.
    assert outcomes.count("admitted") == cap
    assert outcomes.count("refused") == 70 - cap
    assert stream_jobs.active_job_count() == cap


def test_a_finished_job_frees_its_slot(blocking_sink):
    spec = blocking_spec()
    cap = stream_jobs._max_active_jobs()
    for _ in range(cap):
        start(spec)
    blocking_sink.set()  # let them all finish

    deadline = time.monotonic() + 5
    while stream_jobs.active_job_count() > 0 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert stream_jobs.active_job_count() == 0, "jobs never finished"
    start(spec)  # the cap is a live count, not a lifetime quota


def test_the_ceiling_is_configurable(blocking_sink, monkeypatch):
    monkeypatch.setenv("CHAFF_STREAM_MAX_ACTIVE_JOBS", "2")
    spec = blocking_spec()
    start(spec)
    start(spec)
    with pytest.raises(stream_jobs.TooManyJobs):
        start(spec)


def test_a_nonsense_ceiling_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("CHAFF_STREAM_MAX_ACTIVE_JOBS", "eight")
    assert stream_jobs._max_active_jobs() == stream_jobs.DEFAULT_MAX_ACTIVE_JOBS


# ── the API surface ──────────────────────────────────────────────────

from fastapi.testclient import TestClient  # noqa: E402

from api.main import app  # noqa: E402

client = TestClient(app)


def test_a_full_queue_answers_429_not_422(blocking_sink):
    """429 says retry; 422 says fix your spec. The spec is fine."""
    spec_json = {
        "name": "j", "rows": 1, "seed": 1,
        "columns": [{"name": "id", "generator": "row_id"}],
        "output": {"format": "ndjson"},
        "sink": {"sink": "blocking_test", "options": {"host": "example.com"}},
    }
    body = {"spec": spec_json, "max_records": 1000, "max_seconds": 30}
    for _ in range(stream_jobs._max_active_jobs()):
        assert client.post("/stream/jobs", json=body).status_code == 200
    r = client.post("/stream/jobs", json=body)
    assert r.status_code == 429
    assert "already running" in r.json()["detail"]


def test_a_genuinely_bad_request_is_still_422(blocking_sink):
    # The new 429 branch must not swallow the errors that were already there.
    bad = {
        "spec": {
            "name": "j", "rows": 1, "seed": 1,
            "columns": [{"name": "id", "generator": "row_id"}],
            "output": {"format": "xlsx"},          # whole-file format can't stream
            "sink": {"sink": "blocking_test", "options": {"host": "example.com"}},
        },
        "max_records": 10, "max_seconds": 5,
    }
    assert client.post("/stream/jobs", json=bad).status_code == 422
