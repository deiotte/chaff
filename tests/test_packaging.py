"""Packaging config guards (ADR-0014, ADR-0023).

Frozen builds fail at *runtime*, on a user's machine, when a lazily imported
module didn't make it into the bundle — the module graph can't see any of it,
which is why the lists are written by hand. CI builds and smoke-tests the real
artifacts; these are the cheap checks that run everywhere and catch the
drift that causes those failures.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

PACKAGING = Path("packaging")
WINDOWS_SPEC = (PACKAGING / "chaff.spec").read_text()
MACOS_SPEC = (PACKAGING / "chaff-macos.spec").read_text()


@pytest.fixture(scope="module")
def config():
    """The shared bundle config — importable without PyInstaller on purpose.

    It used to import PyInstaller at module scope, so every guard below
    skipped in CI (the make-check job doesn't install it) while passing
    locally: five inert tests reporting success. No skip here now — if this
    module can't be imported that is a real failure, not a reason to pass.
    """
    sys.path.insert(0, str(PACKAGING.resolve()))
    try:
        import bundle_config
    finally:
        sys.path.pop(0)
    return bundle_config


def test_bundle_config_imports_without_pyinstaller():
    """The property that keeps the guards above running in CI.

    PyInstaller is a build-time dependency; the lists these tests check are
    plain data. If someone moves the import back to module scope, every guard
    silently starts skipping again — so assert the import stays lazy.
    """
    source = (PACKAGING / "bundle_config.py").read_text()
    header = source.split("def ", 1)[0]
    assert "from PyInstaller" not in header and "import PyInstaller" not in header, (
        "PyInstaller must be imported inside collect_extras(), not at module "
        "scope — a top-level import makes every guard in this file skip in CI")


# ── the two specs must bundle the same app ───────────────────────────

def test_both_specs_share_one_bundle_config():
    """Duplicated lists are how a format works on Windows and ImportErrors on
    macOS months later, with nothing to catch it."""
    for name, spec in (("chaff.spec", WINDOWS_SPEC), ("chaff-macos.spec", MACOS_SPEC)):
        assert "from bundle_config import" in spec, f"{name} must use the shared config"
        # A spec that re-derives these locally has started to drift.
        assert "collect_all(" not in spec, f"{name} should collect via bundle_config"


def test_every_bundled_optional_dependency_is_a_hidden_import(config):
    """Each of these is imported lazily (inside an encoder or the NL route),
    so nothing in the graph references it. Miss one and that format's
    download raises ImportError on a user's machine."""
    for module in ("openpyxl", "pyarrow", "fastavro", "anthropic", "openai"):
        assert module in config.HIDDEN_IMPORTS, f"{module} would be missing from the bundle"


def test_api_submodules_are_hidden_imports(config):
    """`api` is a namespace package and api.nl is imported lazily in /draft."""
    for module in ("api", "api.main", "api.nl"):
        assert module in config.HIDDEN_IMPORTS


def test_heavy_and_unreachable_deps_stay_excluded(config):
    """These are deliberate size/compat decisions (ADR-0014); dropping one
    silently re-adds tens of MB or breaks the Windows build."""
    for module in ("uvloop", "httptools", "grpcio", "tkinter"):
        assert module in config.EXCLUDES


def test_bundled_data_covers_the_ui_and_presets(config):
    """Without these the app starts and then 404s its own page."""
    dests = {dest for _src, dest in config.datas_for("/repo")}
    assert any("static" in d for d in dests), "the UI would be missing"
    assert "examples" in dests, "the preset gallery would be empty"


def test_notices_are_bundled(config):
    """MIT/BSD/Apache-2.0 attribution ships with any redistributed build."""
    srcs = {Path(src).name for src, _dest in config.datas_for("/repo")}
    assert "THIRD-PARTY-NOTICES.txt" in srcs
    assert "LICENSE" in srcs


# ── macOS bundle specifics ───────────────────────────────────────────

def test_macos_bundle_is_windowless_and_onedir():
    """A .app IS a directory, and one launched from Finder has no terminal —
    which is the whole reason desktop mode grew a Quit button (ADR-0023)."""
    assert "BUNDLE(" in MACOS_SPEC, "no .app bundle stage"
    assert "COLLECT(" in MACOS_SPEC, "a .app must be onedir, not onefile"
    assert "console=False" in MACOS_SPEC
    assert "bundle_identifier=" in MACOS_SPEC


def test_windows_build_keeps_its_console():
    """Closing the console window is still the Windows quit path; the Quit
    button is an addition, not a replacement."""
    assert "console=True" in WINDOWS_SPEC


def test_upx_stays_off_in_both():
    """UPX compression is a common antivirus false-positive trigger, and an
    unsigned binary is already suspect enough (ADR-0014)."""
    assert "upx=False" in WINDOWS_SPEC and "upx=False" in MACOS_SPEC


# ── the MSI ──────────────────────────────────────────────────────────

def test_msi_installs_a_start_menu_shortcut_and_uninstaller():
    """The reason the MSI exists: a bare .exe lands in Downloads and is lost."""
    wxs = (PACKAGING / "msi" / "chaff.wxs").read_text()
    assert "<Shortcut" in wxs and "ChaffMenuFolder" in wxs
    assert "MajorUpgrade" in wxs, "upgrades would stack Add/Remove entries"
    assert 'Scope="perUser"' in wxs, "a machine-wide install would prompt for admin"


def test_msi_license_is_generated_from_the_real_license():
    """A hand-copied licence in the installer would drift from LICENSE."""
    script = (PACKAGING / "msi" / "make_license_rtf.py").read_text()
    assert '"LICENSE"' in script or "'LICENSE'" in script


def test_generated_license_rtf_contains_the_licence_text(tmp_path):
    import subprocess
    out = tmp_path / "license.rtf"
    subprocess.run([sys.executable, str(PACKAGING / "msi" / "make_license_rtf.py"),
                    str(out)], check=True, capture_output=True)
    text = out.read_text()
    assert text.startswith("{\\rtf1")
    assert "Permission is hereby granted" in text


# ── signing is gated, not assumed ────────────────────────────────────

@pytest.mark.parametrize("workflow", ["windows-exe.yml", "macos-app.yml"])
def test_signing_is_optional_and_says_so_when_skipped(workflow):
    """A code-signing certificate is a purchase (ADR-0023). The build must
    still produce a working artifact without one — and must not look like it
    signed when it didn't."""
    text = Path(".github/workflows") / workflow
    src = text.read_text()
    assert "steps.signing.outputs.available == 'true'" in src, \
        "signing must be conditional on credentials being present"
    assert "::warning" in src, "an unsigned build must announce itself"


@pytest.mark.parametrize("workflow", ["windows-exe.yml", "macos-app.yml"])
def test_signing_material_is_cleaned_up(workflow):
    """The private key must not outlive its step, including on failure."""
    src = (Path(".github/workflows") / workflow).read_text()
    assert ("finally" in src) or ("trap cleanup EXIT" in src), \
        "signing key cleanup must run even when signing fails"


@pytest.mark.parametrize("workflow", ["windows-exe.yml", "macos-app.yml"])
def test_ci_smoke_tests_the_real_artifact(workflow):
    """'The file exists' doesn't catch a missing hidden import — only starting
    the thing does."""
    src = (Path(".github/workflows") / workflow).read_text()
    assert "/registry" in src, f"{workflow} must prove the build serves the UI"
    assert "/shutdown" in src, f"{workflow} must prove the Quit path works"


# ── WiX invocation (each of these actually broke a CI build) ──────────
# None of this can be run on Linux, so it is asserted on the source. Each
# assertion below is a failure that happened, not a hypothetical.

WINDOWS_WORKFLOW = Path(".github/workflows/windows-exe.yml").read_text()
WXS = (PACKAGING / "msi" / "chaff.wxs").read_text()


def _commands(text: str) -> str:
    """Workflow lines with `#` comments stripped.

    The comments explain the wrong syntax these guards forbid, so matching
    against the raw file would flag the explanation instead of a real call.
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


WINDOWS_COMMANDS = _commands(WINDOWS_WORKFLOW)


def test_wix_extension_version_is_exact_and_matches_the_tool():
    """`wix extension add` rejects a wildcard — "Invalid extension version in
    WixToolset.UI.wixext/5.*" — even though `dotnet tool install` accepts one.
    They must also be the same version: an extension built against a different
    wix is unsupported."""
    tool = re.search(r"dotnet tool install --global wix --version (\S+)", WINDOWS_COMMANDS)
    ext = re.search(r"wix extension add -g WixToolset\.UI\.wixext/(\S+)", WINDOWS_COMMANDS)
    assert tool and ext, "the wix install/extension steps moved"

    # Both should reference one pinned variable, or two identical literals.
    versions = set()
    for match in (tool.group(1), ext.group(1)):
        if match.startswith("$"):
            # re.escape already handles the leading `$` of a PowerShell var.
            pinned = re.search(re.escape(match) + r'\s*=\s*"([^"]+)"', WINDOWS_COMMANDS)
            assert pinned, f"{match} is used but never assigned"
            versions.add(pinned.group(1))
        else:
            versions.add(match)

    assert len(versions) == 1, f"wix tool and extension versions differ: {versions}"
    version = versions.pop()
    assert "*" not in version, "wix extension add rejects wildcard versions"
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), f"pin an exact version, got {version!r}"


def test_named_bindpaths_use_the_supported_syntax():
    """WiX v4/v5 takes `-b name=path`. `-bindpath:name value` parses as an
    unnamed bind path, and every !(bindpath.name) in the .wxs then fails to
    resolve."""
    assert "-bindpath:" not in WINDOWS_COMMANDS, \
        "use `-b name=path`, not `-bindpath:name value`"

    declared = set(re.findall(r"-b\s+(\w+)=", WINDOWS_COMMANDS))
    referenced = set(re.findall(r"!\(bindpath\.(\w+)\)", WXS))
    missing = referenced - declared
    assert not missing, f"the .wxs references bind paths the build never defines: {missing}"


def test_components_do_not_use_the_removed_star_guid():
    """WiX v4 removed Component/@Guid='*'; auto-generated GUIDs are now the
    default and the literal is an error."""
    assert 'Guid="*"' not in WXS, 'WiX v4+ rejects Guid="*" — omit the attribute'


def test_macos_smoke_test_cannot_pass_on_a_crashed_bundle():
    """A bundle that crashes on launch leaves no process — identical, to a
    naive wait loop, to one that stopped on request. The check must prove the
    app *served* something before it can interpret the exit as success."""
    src = Path(".github/workflows/macos-app.yml").read_text()
    assert "SERVED=1" in src, "the smoke test must record that the app answered"
    assert re.search(r'if \[ "\$SERVED" -ne 1 \]', src), \
        "the smoke test must fail when the app never served"


def test_windows_smoke_test_cannot_pass_on_a_crashed_build():
    """Same invariant on the PowerShell side."""
    assert 'if (-not $up) { throw' in WINDOWS_COMMANDS, \
        "the smoke test must fail when the exe never served"
