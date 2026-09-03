# chaff-macos.spec — PyInstaller .app bundle for macOS (ADR-0023).
#
# Build (on macOS; PyInstaller does NOT cross-compile):
#     pip install -e ".[api,formats-extra,nl,nl-openai]" pyinstaller
#     pyinstaller packaging/chaff-macos.spec
# Produces dist/chaff.app.
#
# Differs from chaff.spec (the Windows onefile) in three ways, all forced by
# what a .app actually is:
#   1. onedir, not onefile — a .app IS a directory, and onefile inside one
#      re-extracts to a temp dir on every launch for no benefit.
#   2. console=False — a bundle launched from Finder has no terminal. That's
#      why desktop mode grew a Quit button (ADR-0023); there is no window to
#      close here.
#   3. a BUNDLE step carrying Info.plist keys.
import os
import sys

# SPECPATH is the dir holding this spec (packaging/); ROOT is the repo root.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# Both specs must bundle the same app; the shared lists live in one place so a
# hidden import added for one platform can't go missing on the other.
sys.path.insert(0, SPECPATH)
from bundle_config import EXCLUDES, HIDDEN_IMPORTS, collect_extras, datas_for  # noqa: E402

extra_datas, extra_binaries, extra_hidden = collect_extras()
datas = datas_for(ROOT) + extra_datas
binaries = extra_binaries
hiddenimports = HIDDEN_IMPORTS + extra_hidden
excludes = EXCLUDES

a = Analysis(
    [os.path.join(ROOT, "packaging", "chaff_desktop.py")],
    pathex=[ROOT, os.path.join(ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,      # onedir: binaries go in COLLECT below
    name="chaff",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,              # Finder launch has no terminal (see header)
    disable_windowed_traceback=False,
    target_arch=None,           # build native to the runner's arch
    codesign_identity=None,     # signing happens in CI, after the build
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="chaff",
)

app = BUNDLE(
    coll,
    name="chaff.app",
    icon=None,
    bundle_identifier="dev.chaff.desktop",
    info_plist={
        # It's a background server that opens the user's browser: no dock
        # icon churn, and nothing to show if the user clicks it twice.
        "LSBackgroundOnly": False,
        "NSHighResolutionCapable": True,
        "CFBundleName": "chaff",
        "CFBundleDisplayName": "chaff",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        # No network *server* entitlement is needed for loopback, and the app
        # makes outbound calls only when a user pastes an AI key.
        "NSHumanReadableCopyright": "MIT licensed. See THIRD-PARTY-NOTICES.txt.",
    },
)
