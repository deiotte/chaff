# chaff.spec — PyInstaller onefile build of the chaff desktop launcher (ADR-0014).
#
# Build (from repo root, on the target OS — PyInstaller does NOT cross-compile):
#     pip install -e ".[api,formats-extra,nl,nl-openai]" pyinstaller
#     pyinstaller packaging/chaff.spec
# Produces dist/chaff.exe (Windows) / dist/chaff (other OS, for graph testing).
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

block_cipher = None

a = Analysis(
    [os.path.join(ROOT, "packaging", "chaff_desktop.py")],
    pathex=[ROOT, os.path.join(ROOT, "src")],  # resolve both `api.*` and `chaff.*`
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="chaff",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,        # UPX compression is a common antivirus false-positive trigger
    console=True,     # keep the "chaff is running..." window; closing it stops the app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
