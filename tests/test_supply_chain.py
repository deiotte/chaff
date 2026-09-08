"""Build-input integrity: F-11 — PR-triggered release workflows and mutable pins.

The red team's point was not that any of this had been exploited; it was that
the release workflows *could* hand a certificate to a pull-request run, and
that nothing in the build was pinned, so "the same commit" did not mean "the
same bytes".

These are config assertions rather than behaviour assertions, which makes them
easy to write hollow. Each one names the specific thing that would go wrong,
and each was checked by reintroducing it.

The last tier goes further than config and reads what each pinned action
*actually is* — see ADR-0041. It is the only part of this file that touches
the network, and it skips rather than fails when it cannot.
"""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.request
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW_DIR = Path(".github/workflows")
WORKFLOWS = sorted(WORKFLOW_DIR.glob("*.yml"))

#: A `uses:` value counts as pinned only at a full commit SHA. A tag — even a
#: patch tag like v4.1.1 — is a moving target the owner can repoint.
PINNED = re.compile(r"^[\w.-]+/[\w.-]+(?:/[\w.-]+)*@[0-9a-f]{40}(?:\s+#.*)?$")

#: Workflows that hold signing secrets, so must never sign on a pull request.
SIGNING_WORKFLOWS = ("windows-exe.yml", "macos-app.yml")


def uses_lines(text: str) -> list[str]:
    return [m.group(1).strip()
            for m in re.finditer(r"^\s*(?:-\s*)?uses:\s*(.+)$", text, re.MULTILINE)]


def test_there_are_workflows_to_check():
    # Guards the guards: a glob that silently matches nothing would make every
    # assertion below vacuously true.
    assert len(WORKFLOWS) >= 3, f"expected the CI + packaging workflows, found {WORKFLOWS}"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit(path):
    """A floating tag means a third party chooses what runs with our token."""
    unpinned = [u for u in uses_lines(path.read_text()) if not PINNED.match(u)]
    assert unpinned == [], (
        f"{path.name} uses actions at a mutable ref: {unpinned}. Pin to the "
        "full commit SHA with the version as a trailing comment.")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_pin_says_which_version_it_is(path):
    """A bare 40-hex SHA is unreadable and un-reviewable; the comment is how a
    human (and Dependabot) knows what the pin means."""
    uncommented = [u for u in uses_lines(path.read_text()) if "#" not in u]
    assert uncommented == [], (
        f"{path.name} has pins with no version comment: {uncommented}")


#: The expression that must guard the certificate the availability check reads.
EVENT_GUARD = "github.event_name != 'pull_request'"


@pytest.mark.parametrize("name", SIGNING_WORKFLOWS)
def test_signing_never_runs_on_a_pull_request(name):
    """The finding itself: a PR build must not be able to reach the cert.

    Every signing step gates on `steps.signing.outputs.available`, so the
    event check belongs in the step that computes it — one place, inherited by
    any signing step added later.

    The assertion is on the *certificate reference itself*, not on the word
    "pull_request" appearing somewhere in the step. The looser version passed
    with the guard removed, because the step also prints a notice mentioning
    pull requests — mutation testing is what surfaced that.
    """
    text = (WORKFLOW_DIR / name).read_text()
    block = text.split("Check for signing credentials", 1)
    assert len(block) == 2, f"{name} no longer has the signing-credentials step"
    step = block[1].split("- name:", 1)[0]

    secret_refs = [l.strip() for l in step.splitlines() if "secrets." in l]
    assert secret_refs, (
        f"{name}'s availability check no longer reads a certificate — either "
        "it moved, or the gate this test protects is gone")
    ungated = [l for l in secret_refs if EVENT_GUARD not in l]
    assert ungated == [], (
        f"{name} reads a signing secret without the {EVENT_GUARD!r} guard: "
        f"{ungated}. A pull-request run would receive the certificate.")


@pytest.mark.parametrize("name", SIGNING_WORKFLOWS)
def test_signing_secrets_are_not_interpolated_into_script_bodies(name):
    """A secret referenced inside `run:` is written into the runner's script
    file. Passing it through `env:` keeps it out of the script text."""
    text = (WORKFLOW_DIR / name).read_text()
    in_run = False
    offenders = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("run:"):
            in_run = True
            continue
        if stripped.startswith(("- name:", "env:", "with:", "uses:", "if:", "id:")):
            in_run = False
        if in_run and "secrets." in line:
            offenders.append(stripped)
    assert offenders == [], (
        f"{name} interpolates a secret into a script body: {offenders}")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_workflow_declares_least_privilege(path):
    """Without a `permissions:` block a job gets the repository default, which
    is usually far more than a build needs."""
    doc = yaml.safe_load(path.read_text())
    top = doc.get("permissions")
    jobs = doc.get("jobs", {})
    assert top is not None or all("permissions" in j for j in jobs.values()), (
        f"{path.name} declares no permissions, so its jobs inherit the "
        "repository default token scope")


def test_the_base_image_is_pinned_by_digest():
    """A tag is mutable: `python:3.12-slim` can be different bytes tomorrow."""
    froms = [l for l in Path("Dockerfile").read_text().splitlines()
             if l.strip().startswith("FROM ")]
    assert froms, "Dockerfile has no FROM line"
    unpinned = [f for f in froms if not re.search(r"@sha256:[0-9a-f]{64}", f)]
    assert unpinned == [], f"Dockerfile base image is not digest-pinned: {unpinned}"


def test_something_bumps_the_pins():
    """A pin with no update path freezes an unpatched base and a stale action.

    This is the assertion that keeps the previous three from aging into a
    liability, so it is deliberately part of the same suite: pinning without
    automated bumps trades one supply-chain risk for another.
    """
    config = Path(".github/dependabot.yml")
    assert config.exists(), (
        "the build pins actions and the base image by digest, so something has "
        "to propose bumps — add .github/dependabot.yml")
    doc = yaml.safe_load(config.read_text())
    ecosystems = {u["package-ecosystem"] for u in doc["updates"]}
    assert {"github-actions", "docker"} <= ecosystems, (
        f"dependabot covers {sorted(ecosystems)}; the pinned inputs are "
        "github-actions and docker")


# ── What the pin actually runs on (ADR-0041) ─────────────────────────
# ADR-0031 pinned the five actions at the versions already in use and parked
# the Node 20 deprecation as its own deliberate piece of work. Dependabot has
# since done the bumping. This tier is what stops it coming back: to every
# assertion above, a pin at a dead runtime is a perfectly well-formed SHA.

#: Node runtimes GitHub has deprecated. A step on one of these still runs
#: today and only warns — right up until the runner drops the runtime, at
#: which point every workflow using it fails at once, on a commit that
#: changed nothing.
#:
#: Deliberately a deny-list. An allow-list would fail this build the day
#: GitHub ships node28, which is a false alarm, and a guard that cries wolf
#: gets deleted. Add the next runtime here when GitHub announces it.
DEPRECATED_NODE_RUNTIMES = {"node12", "node16", "node20"}

#: Reading `runs.using` means reading the action's own action.yml at the
#: pinned SHA, which needs the network. Same bargain as the browser tests
#: (ADR-0022): skip by default so `make check` stays runnable offline, and
#: let CI set this so a skip there is a failure instead of a silent hole.
REQUIRE_RUNTIME_CHECK = os.environ.get("CHAFF_REQUIRE_ACTION_RUNTIME_TESTS") == "1"

RAW = "https://raw.githubusercontent.com"


class _Unreachable(Exception):
    """GitHub raw could not answer — a skip locally, a failure in CI."""


def pinned_actions() -> dict[str, str]:
    """Every distinct `owner/repo[/path]@sha` in the workflows, to its comment.

    Deduplicated: the same action appears in all three workflows, and the
    runtime is a property of the pin, not of the file it is written in.
    """
    pins: dict[str, str] = {}
    for path in WORKFLOWS:
        for line in uses_lines(path.read_text()):
            ref, _, comment = line.partition("#")
            ref = ref.strip()
            if "@" in ref:
                pins[ref] = comment.strip() or "no version comment"
    return pins


PINS = pinned_actions()


def _metadata_urls(ref: str) -> list[str]:
    """Both spellings of an action's metadata file, at the pinned commit.

    Handles an action living in a subdirectory (`owner/repo/path@sha`), which
    PINNED allows even though nothing here uses one today.
    """
    location, sha = ref.rsplit("@", 1)
    owner, repo, *subpath = location.split("/")
    base = "/".join([RAW, owner, repo, sha, *subpath])
    return [f"{base}/action.yml", f"{base}/action.yaml"]


def _read(url: str) -> str | None:
    """The body, or None on 404. Anything else means we could not look."""
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            return response.read().decode()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        # A rate limit or a 5xx did not tell us the runtime is *bad*, only
        # that we could not read it. That is unreachable, not a failure.
        raise _Unreachable(f"{url} answered HTTP {e.code}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise _Unreachable(f"cannot reach {RAW} ({e})") from e


def action_runtime(ref: str) -> str:
    """`runs.using` for an action, read at the commit we actually pinned."""
    for url in _metadata_urls(ref):
        body = _read(url)
        if body is not None:
            break
    else:
        raise AssertionError(
            f"{ref} has no action.yml or action.yaml at that commit, so the "
            "pin does not point at a usable action")
    runs = (yaml.safe_load(body) or {}).get("runs")
    if not isinstance(runs, dict) or "using" not in runs:
        raise AssertionError(f"{ref} declares no runs.using in {url}")
    return str(runs["using"])


def _unreachable(reason: str):
    """Fail when the environment promised network, else skip."""
    if REQUIRE_RUNTIME_CHECK:
        pytest.fail(
            f"{reason} — CHAFF_REQUIRE_ACTION_RUNTIME_TESTS=1 says this must "
            "run. A skip in CI leaves the runtime unchecked.")
    pytest.skip(
        f"{reason}; set CHAFF_REQUIRE_ACTION_RUNTIME_TESTS=1 to require it")


def test_there_are_pins_to_check():
    # Guards the guard, like test_there_are_workflows_to_check above: a
    # parametrize over an empty dict collects nothing and reports green.
    assert len(PINS) >= 5, f"expected the pinned actions, found {sorted(PINS)}"


@pytest.mark.parametrize("ref", sorted(PINS), ids=lambda r: r.rsplit("@", 1)[0])
def test_no_action_runs_on_a_deprecated_node_runtime(ref):
    """The other half of "pinned": a SHA can be immutable and still be dead.

    Proposing the bump is Dependabot's job. Noticing that a pin went
    *backwards* — a hand-edited SHA, a bad conflict resolution, a revert that
    took the workflow with it — is this one's.
    """
    try:
        using = action_runtime(ref)
    except _Unreachable as e:
        # Reported outside the handler so the failure reads as one line
        # instead of a chained urllib traceback.
        unreachable = str(e)
    else:
        unreachable = None
    if unreachable is not None:
        _unreachable(unreachable)
    assert using not in DEPRECATED_NODE_RUNTIMES, (
        f"{ref} ({PINS[ref]}) runs on {using}, which GitHub has deprecated. "
        "It only warns today and stops running when the runner drops the "
        "runtime. Bump the action to a major that runs on a current Node.")
