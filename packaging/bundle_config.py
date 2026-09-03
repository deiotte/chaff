"""What goes into a frozen chaff app — shared by every PyInstaller spec.

There are two specs (Windows onefile, macOS .app) and they must bundle the
*same application*. Duplicating these lists is how a format ends up working
on one platform and raising ImportError on the other, months later, with no
test to catch it: the graph analysis can't see any of this, which is exactly
why it's listed by hand.

Imported by packaging/chaff.spec and packaging/chaff-macos.spec.
"""

from __future__ import annotations

import os

# NOTE: PyInstaller is imported inside `collect_extras()`, not here. The lists
# below are plain data and are what the guards in tests/test_packaging.py
# check — importing PyInstaller at module scope made this whole module
# unimportable without it, so those five guards silently *skipped* in CI (the
# make-check job doesn't install PyInstaller) while passing locally. Tests
# that skip in CI are the hole ADR-0022 is about; keep this import lazy.


def datas_for(root: str) -> list[tuple[str, str]]:
    """Data files every build needs, as (source, dest-in-bundle)."""
    return [
        # The static UI. Destination matches api/main.py's frozen branch
        # (_MEIPASS/api/static).
        (os.path.join(root, "api", "static"), os.path.join("api", "static")),
        # Preset library. The launcher points CHAFF_PRESETS_DIR at
        # _MEIPASS/examples.
        (os.path.join(root, "examples"), "examples"),
        # Third-party attribution (MIT/BSD/Apache-2.0 NOTICE), served at
        # /licenses. CI regenerates this before freezing; the committed copy
        # covers local builds.
        (os.path.join(root, "THIRD-PARTY-NOTICES.txt"), "."),
        (os.path.join(root, "LICENSE"), "."),
    ]


# What static analysis can't see.
HIDDEN_IMPORTS = [
    # api/ is a namespace package; api.nl is imported lazily in /draft.
    "api", "api.main", "api.nl",
    # Lazy third-party imports inside the encoders / NL callers we bundle.
    "openpyxl",                     # xlsx
    "pyarrow", "pyarrow.parquet",   # parquet
    "fastavro",                     # avro
    "anthropic", "openai",          # NL drafting (Google intentionally excluded)
]

# Unused / too heavy / not UI-reachable.
EXCLUDES = [
    "uvloop", "httptools",              # we force loop=asyncio/http=h11
    "confluent_kafka",                  # kafka sink isn't reachable from the UI
    "google", "google.generativeai", "grpc", "grpcio",  # grpcio too heavy
    "tkinter",                          # unused GUI toolkit; trims size
]


def collect_extras() -> tuple[list, list, list]:
    """(datas, binaries, hiddenimports) for dependencies that need more than a
    hidden import.

    - **pyarrow** bundles compiled Arrow libraries next to its extension
      modules; only `collect_all` reliably grabs the binaries plus its large
      submodule tree. A bare hidden import loads the Python side but not the
      shared libs, so a parquet download would ImportError at runtime.
    - **faker** ships locale/provider data loaded by dynamic import, and
      discovers providers the same way, so the module graph misses both. It's
      on the core generate path.
    """
    from PyInstaller.utils.hooks import (
        collect_all,
        collect_data_files,
        collect_submodules,
    )

    pa_datas, pa_binaries, pa_hidden = collect_all("pyarrow")
    datas = pa_datas + collect_data_files("faker")
    hidden = list(pa_hidden) + collect_submodules("faker")
    return datas, list(pa_binaries), hidden
