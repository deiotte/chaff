"""Build-input integrity: F-11 — PR-triggered release workflows and mutable pins.

The red team's point was not that any of this had been exploited; it was that
the release workflows *could* hand a certificate to a pull-request run, and
that nothing in the build was pinned, so "the same commit" did not mean "the
same bytes".

These are config assertions rather than behaviour assertions, which makes them
easy to write hollow. Each one names the specific thing that would go wrong,
and each was checked by reintroducing it.
"""

from __future__ import annotations

import re
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
