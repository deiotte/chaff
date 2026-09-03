"""Spec library: named presets (read-only) and saved schemas (read-write).

The spec is the product (INV-1); this is storage for specs, not generation
logic, so it lives in the package where both the CLI and API can use it.

- **Presets** are the specs shipped in the presets directory (the repo's
  `examples/`, copied into the Docker image). Read-only.
- **Saved schemas** are user saves in a writable library directory, so an
  office Joe can name a dataset in the UI and recall it later.

Both directories are env-configurable (Docker-friendly, per ADR-0005):
`CHAFF_PRESETS_DIR` (default `examples`) and `CHAFF_LIBRARY_DIR` (default
`spec-library`). Names are validated to a safe slug — no path traversal,
no writing outside the library dir.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from .spec import DatasetSpec, load_spec

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def presets_dir() -> Path:
    return Path(os.environ.get("CHAFF_PRESETS_DIR", "examples"))


def library_dir() -> Path:
    return Path(os.environ.get("CHAFF_LIBRARY_DIR", "spec-library"))


def _safe(name: str) -> str:
    """Reject anything that isn't a plain slug — blocks path traversal."""
    if not _SAFE_NAME.match(name):
        raise ValueError(
            f"invalid library name '{name}': use letters, digits, '-' or '_' "
            "(must start alphanumeric)"
        )
    return name


def _summary(name: str, source: str, data: dict[str, Any]) -> dict[str, Any]:
    """Gallery card fields.

    `rows` must describe what the spec actually emits, not just its `rows`
    key: an entity spec's length is count × ticks, and a multi-table spec
    emits every table. A card that advertises the primary table's row count
    for a 3-table spec is a card that lies to the person clicking it.
    """
    entity = data.get("entity") or None
    tables = data.get("tables") or None

    if entity:
        rows = (entity.get("count") or 0) * (entity.get("ticks") or 0) or None
    elif tables:
        rows = (data.get("rows") or 0) + sum(t.get("rows") or 0 for t in tables)
    else:
        rows = data.get("rows")

    return {
        "name": name,
        "source": source,
        "description": data.get("description"),
        "rows": rows,
        "format": (data.get("output") or {}).get("format"),
        "columns": len(data.get("columns") or []),
        # Shape hints so the card can say *why* the row count looks like it
        # does. None for a plain flat spec (the common case).
        "tables": 1 + len(tables) if tables else None,
        "entity": {"count": entity["count"], "ticks": entity["ticks"]} if entity else None,
    }


def list_specs() -> list[dict[str, Any]]:
    """Summaries for the gallery: saved schemas first, then presets."""
    out: list[dict[str, Any]] = []
    for source, directory in (("saved", library_dir()), ("preset", presets_dir())):
        if not directory.is_dir():
            continue
        for f in sorted(directory.glob("*.json")):
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue  # skip unreadable/garbage files rather than 500
            out.append(_summary(f.stem, source, data))
    return out


def load_named(name: str) -> dict[str, Any]:
    """Return a spec dict by name. A saved schema shadows a preset."""
    _safe(name)
    for directory in (library_dir(), presets_dir()):
        f = directory / f"{name}.json"
        if f.is_file():
            return json.loads(f.read_text())
    raise KeyError(f"no spec named '{name}' in the library")


def save_named(name: str, spec: dict[str, Any] | DatasetSpec) -> Path:
    """Validate then persist a spec to the writable library directory.

    Only valid specs enter the library — validation happens before any
    bytes hit disk (the spec is the product).
    """
    _safe(name)
    validated = spec if isinstance(spec, DatasetSpec) else load_spec(spec)
    directory = library_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(validated.model_dump_json(indent=2))
    return path


def delete_named(name: str) -> None:
    """Delete a saved schema. Presets are read-only and cannot be deleted."""
    _safe(name)
    path = library_dir() / f"{name}.json"
    if not path.is_file():
        raise KeyError(f"no saved spec named '{name}'")
    path.unlink()
