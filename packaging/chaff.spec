# chaff.spec — PyInstaller onefile build of the chaff desktop launcher (ADR-0014).
#
# Build (from repo root, on the target OS — PyInstaller does NOT cross-compile):
#     pip install -e ".[api,formats-extra,nl,nl-openai]" pyinstaller
#     pyinstaller packaging/chaff.spec
# Produces dist/chaff.exe (Windows) / dist/chaff (other OS, for graph testing).
import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# SPECPATH is the dir holding this spec (packaging/); ROOT is the repo root.
ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))

# ── Data files ───────────────────────────────────────────────────────
datas = [
    # The static UI. Destination matches api/main.py's frozen branch
    # (_MEIPASS/api/static).
    (os.path.join(ROOT, "api", "static"), os.path.join("api", "static")),
    # Preset library. The launcher points CHAFF_PRESETS_DIR at _MEIPASS/examples.
    (os.path.join(ROOT, "examples"), "examples"),
]
# faker ships locale/provider data loaded via dynamic import — the module graph
# alone misses it, and it's on the core generate path.
datas += collect_data_files("faker")

# ── Native deps needing full collection ──────────────────────────────
# pyarrow bundles compiled Arrow libraries next to its extension modules; only
# collect_all reliably grabs the binaries + its large submodule tree. A bare
# hidden import would load the Python side but not the DLLs -> parquet download
# would ImportError.
pa_datas, pa_binaries, pa_hidden = collect_all("pyarrow")

# ── Hidden imports (what static analysis can't see) ──────────────────
hiddenimports = [
    # api/ is a namespace package; api.nl is imported lazily in the /draft route.
    "api", "api.main", "api.nl",
    # Lazy third-party imports inside the encoders / NL callers we bundle.
    "openpyxl",            # xlsx
    "pyarrow", "pyarrow.parquet",  # parquet
    "fastavro",            # avro
    "anthropic", "openai",  # NL drafting (Google intentionally excluded)
]
hiddenimports += pa_hidden
hiddenimports += collect_submodules("faker")  # faker discovers providers dynamically

# ── Excluded (unused / too heavy / not UI-reachable) ─────────────────
excludes = [
    "uvloop", "httptools",              # we force loop=asyncio/http=h11; no Win wheel
    "confluent_kafka",                  # kafka sink isn't reachable from the UI
    "google", "google.generativeai", "grpc", "grpcio",  # Google NL: grpcio too heavy
    "tkinter",                          # unused GUI toolkit; trims size
]

block_cipher = None

a = Analysis(
    [os.path.join(ROOT, "packaging", "chaff_desktop.py")],
    pathex=[ROOT, os.path.join(ROOT, "src")],  # resolve both `api.*` and `chaff.*`
    binaries=pa_binaries,
    datas=datas + pa_datas,
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
