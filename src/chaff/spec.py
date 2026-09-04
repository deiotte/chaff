"""chaff spec contract.

The spec IS the product. The UI builds specs, the CLI consumes specs,
the API transports specs. The engine only ever sees a validated DatasetSpec.

Design rules (see docs/adr/0001, 0002, 0003):
- Columns declare a *semantic generator*, not a physical type. Physical
  types are the format encoder's problem.
- `format` (how bytes are encoded) and `sink` (where bytes go) are
  independent axes. Any format may pair with any compatible sink.
- `seed` makes generation deterministic: same spec + same seed = same bytes.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SPEC_VERSION = "1.0"


# Dataset and table names become *filenames*: one file per table on disk, one
# member per table in the downloaded zip. Unvalidated, `../escaped` wrote
# outside the requested output directory (CLI) and produced a traversal entry
# in the archive (API). Gate it here, at the contract, so every interface —
# CLI, API, UI, anything later — inherits the same rule (INV-1).
_PATH_HOSTILE = re.compile(r"[\x00-\x1f\x7f/\\]")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")
# Windows refuses these as filenames regardless of extension.
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _reject_unsafe_name(value: str, what: str) -> str:
    """Reject a name that can't be used as a single, safe path component.

    Deliberately permissive about ordinary text — spaces, dots and unicode are
    fine, because office Joe names a dataset "Q3 sales" — and strict about the
    things that make a name stop being one path component.
    """
    if _PATH_HOSTILE.search(value):
        raise ValueError(
            f"{what} '{value}' may not contain path separators or control characters "
            "(it becomes a filename)")
    stripped = value.strip()
    if stripped in (".", "..") or set(stripped) == {"."}:
        raise ValueError(f"{what} '{value}' is not a usable filename")
    if _WINDOWS_DRIVE.match(value):
        raise ValueError(f"{what} '{value}' may not start with a drive letter")
    if stripped.split(".")[0].lower() in _WINDOWS_RESERVED:
        raise ValueError(f"{what} '{value}' is a reserved device name on Windows")
    if value != stripped:
        raise ValueError(f"{what} '{value}' has leading or trailing whitespace")
    return value


def _reject_duplicate_columns(cols: list["ColumnSpec"]) -> list["ColumnSpec"]:
    names = [c.name for c in cols]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError(f"duplicate column names: {sorted(dupes)}")
    return cols


class ColumnSpec(BaseModel):
    """One column: a name, a semantic generator, and its parameters."""

    name: str = Field(..., min_length=1, description="Column/field name in the output.")
    generator: str = Field(..., description="Registered generator id, e.g. 'full_name', 'choice_weighted'.")
    params: dict[str, Any] = Field(default_factory=dict, description="Generator-specific parameters.")
    null_rate: float = Field(0.0, ge=0.0, le=1.0, description="Probability [0,1] a value is null/empty.")

    @field_validator("name")
    @classmethod
    def name_is_identifier_friendly(cls, v: str) -> str:
        # Permissive on purpose (office Joe types spaces); encoders quote as needed.
        if v.strip() != v or not v:
            raise ValueError("column name must be non-empty with no leading/trailing whitespace")
        return v


class OutputSpec(BaseModel):
    """Format axis: how the rows are encoded."""

    format: str = Field(..., description="Registered format id: csv, tsv, json, ndjson, sql, ...")
    options: dict[str, Any] = Field(default_factory=dict, description="Format-specific options (e.g. sql dialect).")


class SinkSpec(BaseModel):
    """Sink axis: where the encoded bytes terminate."""

    sink: str = Field("file", description="Registered sink id: file, http, kafka, tcp, udp, ...")
    options: dict[str, Any] = Field(default_factory=dict, description="Sink-specific options (path, url, topic, rate...).")


class TableSpec(BaseModel):
    """One additional related table in a multi-table spec (Phase 3, ADR-0008).

    Same generation unit as the top-level table — a name, a row count, and
    columns — but no output/sink of its own: format and delivery are shared
    across all tables in the spec. Use the `fk` generator in a column to
    reference another table's column for foreign-key integrity.
    """

    name: str = Field(..., min_length=1, description="Table name; used for its filename and SQL table name.")
    rows: int = Field(..., ge=1, le=10_000_000, description="Record count for this table.")
    columns: list[ColumnSpec] = Field(..., min_length=1)

    @field_validator("name")
    @classmethod
    def _name_is_a_safe_filename(cls, v: str) -> str:
        return _reject_unsafe_name(v, "table name")

    @field_validator("columns")
    @classmethod
    def _cols_unique(cls, v: list[ColumnSpec]) -> list[ColumnSpec]:
        return _reject_duplicate_columns(v)


class UpdateSpec(BaseModel):
    """One per-tick update rule applied to an entity's state (ADR-0009)."""

    updater: str = Field(..., description="Registered updater id: movement, lifecycle, drift, ...")
    params: dict[str, Any] = Field(default_factory=dict, description="Updater-specific parameters.")


class ObserverSpec(BaseModel):
    """One viewpoint onto an entity scene (ADR-0033).

    An entity scene is ground truth: where each thing actually was, tick by
    tick. An observer is a **sensor's account of it** — the same scene, seen
    imperfectly, under identifiers of its own choosing.

    Two observers over one scene produce what a cross-source consumer needs
    and a single feed can never supply: two independent reports of the same
    real-world object, arriving under different ids, in slightly different
    places. Whether that consumer can tell they are one thing is the question
    the pair exists to ask; if they shared an id there would be nothing to ask.

    Each observer renders every `(entity, tick)` snapshot into its own output
    file. Format and sink stay shared, so this is a property of the scene and
    not a third axis (INV-2).
    """

    name: str = Field(..., min_length=1, description="Observer id; also its output filename.")
    id_pattern: Optional[str] = Field(
        None,
        description="Pattern for this observer's entity ids (see the `pattern` generator). "
                    "Omit to reuse the scene's own ids — which makes the observers agree on "
                    "identity, and is usually not what you want.",
    )
    position_error_m: float = Field(
        0.0, ge=0.0, le=100_000.0,
        description="How far this observer may misplace a thing, in metres. Bounded, not Gaussian: "
                    "every report lands within this radius of the truth, so a fixture's expectations "
                    "are exact rather than probabilistic.",
    )
    lat_column: str = Field("lat", description="Column carrying latitude, perturbed by position_error_m.")
    lon_column: str = Field("lon", description="Column carrying longitude, perturbed by position_error_m.")
    reports: dict[str, Any] = Field(
        default_factory=dict,
        description="Constant columns this observer adds to every row — what the sensor says "
                    "about itself rather than about the scene. A unit that knows its horizontal "
                    "error is six metres reports `{\"ce\": 6.0}`, and two observers with "
                    "different error radii then disagree honestly instead of both claiming "
                    "the same accuracy.",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="Format options overlaid on `output.options` for this observer's file only. "
                    "Where a per-observer difference is a property of the encoding rather than of "
                    "the scene — a clock offset via `base_time`, a different reported type — it "
                    "belongs here, so the observer stays format-agnostic.",
    )

    @field_validator("name")
    @classmethod
    def _name_is_a_safe_filename(cls, v: str) -> str:
        return _reject_unsafe_name(v, "observer name")


class EntitySpec(BaseModel):
    """Stateful entities that evolve over time (Phase 3, ADR-0009).

    When present, the engine stops generating independent rows and instead
    creates `count` entities, each with initial state from the spec's
    `columns`, then advances them `ticks` times applying `updates`. Output
    is one snapshot row per (tick, entity): `entities × ticks` rows, time
    ordered. The top-level `rows` is ignored in this mode.
    """

    count: int = Field(..., ge=1, le=1_000_000, description="Number of entities.")
    ticks: int = Field(..., ge=1, le=1_000_000, description="Snapshots per entity (time steps).")
    id_column: str = Field("entity_id", description="Output column carrying the entity id.")
    id_pattern: Optional[str] = Field(
        None, description="Pattern for entity ids (see the `pattern` generator); default is sequential ints."
    )
    tick_column: str = Field("tick", description="Output column carrying the 0-based tick number.")
    updates: list[UpdateSpec] = Field(default_factory=list, description="Per-tick update rules, applied in order.")
    observers: list[ObserverSpec] = Field(
        default_factory=list,
        description="Viewpoints onto this scene (ADR-0033). Empty = one feed of the truth itself; "
                    "two or more = one file per observer, each an imperfect account of the same "
                    "entities. Adding observers never changes the underlying scene.",
    )

    @model_validator(mode="after")
    def _observer_names_unique(self) -> "EntitySpec":
        names = [o.name for o in self.observers]
        dupes = {n for n in names if names.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate observer names: {sorted(dupes)}")
        return self


class DatasetSpec(BaseModel):
    """The whole contract. Serialize to JSON, save it, share it, version it."""

    spec_version: str = Field(SPEC_VERSION, description="Contract version for forward migration.")
    name: str = Field(..., min_length=1, description="Dataset/table name; used by SQL encoder and filenames.")
    description: Optional[str] = Field(None, description="Human context; shown in the preset library UI.")
    seed: Optional[int] = Field(None, description="RNG seed. Set it and the dataset is reproducible byte-for-byte.")
    rows: Optional[int] = Field(None, ge=1, le=10_000_000, description="Record count. Not used when `entity` is set.")
    columns: list[ColumnSpec] = Field(..., min_length=1)
    output: OutputSpec
    sink: SinkSpec = Field(default_factory=SinkSpec)

    # Additional related tables (Phase 3, ADR-0008). The top-level name/
    # columns/rows are the primary table; `tables` holds the rest. Absent =>
    # a single-table spec, byte-for-byte identical to before.
    tables: Optional[list[TableSpec]] = Field(
        None, description="Additional related tables; use `fk` columns for referential integrity."
    )

    # Stateful entities that evolve over time (Phase 3, ADR-0009). Presence
    # switches the engine from independent-row generation to per-entity
    # update loops (tracks, lifecycles, sensors). Absent => unchanged.
    entity: Optional[EntitySpec] = Field(None, description="Stateful entity config (ADR-0009).")

    @field_validator("name")
    @classmethod
    def _name_is_a_safe_filename(cls, v: str) -> str:
        return _reject_unsafe_name(v, "dataset name")

    @field_validator("columns")
    @classmethod
    def column_names_unique(cls, v: list[ColumnSpec]) -> list[ColumnSpec]:
        return _reject_duplicate_columns(v)

    @model_validator(mode="after")
    def _rows_required_without_entity(self) -> "DatasetSpec":
        # `rows` drives independent-row generation; entity specs derive their
        # length from count × ticks instead, so rows is optional there.
        if self.entity is None and self.rows is None:
            raise ValueError("rows is required (except for entity specs, which use count × ticks)")
        return self

    @model_validator(mode="after")
    def _entity_and_tables_are_exclusive(self) -> "DatasetSpec":
        """`entity` and `tables` describe incompatible generation modes.

        The engine takes the multi-table path first and never looks at
        `entity`, so a spec carrying both silently loses the entity config —
        and with `rows` omitted (legal for an entity spec) the table path has
        no row count and crashes deep in generation. Reject the combination
        here so the CLI, API and UI all get one clear error at load time.
        """
        if self.entity and self.tables:
            raise ValueError(
                "a spec cannot set both `entity` and `tables`: stateful entities "
                "and multi-table generation are different modes. Use one or the other."
            )
        return self

    @model_validator(mode="after")
    def _table_names_unique(self) -> "DatasetSpec":
        if self.tables:
            names = [self.name] + [t.name for t in self.tables]
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                raise ValueError(f"duplicate table names: {sorted(dupes)}")
        return self

    @model_validator(mode="after")
    def _validate_derived_formulas(self) -> "DatasetSpec":
        """Fail early (at load) if a `derived` column's formula is malformed
        or references a column that isn't declared before it — so the UI/CLI
        shows a precise error instead of a surprise at generation time. The
        evaluator import is lazy to keep the spec module free of engine deps."""
        from .generators._expr import FormulaError, validate_expr

        def check(cols: list[ColumnSpec], where: str) -> None:
            seen: set[str] = set()
            for c in cols:
                if c.generator == "derived":
                    expr = c.params.get("expr") or c.params.get("formula")
                    if not expr:
                        raise ValueError(f"{where} column '{c.name}': derived needs an 'expr'")
                    try:
                        refs = validate_expr(expr)
                    except FormulaError as e:
                        raise ValueError(f"{where} column '{c.name}': {e}") from None
                    missing = refs - seen
                    if missing:
                        raise ValueError(
                            f"{where} column '{c.name}' formula references {sorted(missing)}, "
                            "which must be column(s) declared before it")
                seen.add(c.name)

        check(self.columns, "dataset")
        for t in self.tables or []:
            check(t.columns, f"table '{t.name}'")
        return self

    @model_validator(mode="after")
    def _validate_geo_links(self) -> "DatasetSpec":
        """Fail early if a column's {'from': X} doesn't point at a linked
        `country` column declared before it (ADR-0015) — a precise error
        instead of a silent independent fallback at generation time."""
        linkable = {"city", "timezone", "lat", "lon", "currency_code"}

        def check(cols: list[ColumnSpec], where: str) -> None:
            declared: set[str] = set()
            anchors: set[str] = set()  # names of linked `country` columns seen so far
            for c in cols:
                src = c.params.get("from")
                if src is not None:
                    if c.generator not in linkable:
                        raise ValueError(
                            f"{where} column '{c.name}': 'from' is only valid on "
                            f"{sorted(linkable)}, not '{c.generator}'")
                    if src not in declared:
                        raise ValueError(
                            f"{where} column '{c.name}': 'from' references '{src}', "
                            "which must be a column declared before it")
                    if src not in anchors:
                        raise ValueError(
                            f"{where} column '{c.name}': 'from' references '{src}', which "
                            "must be a `country` column with {\"link\": true}")
                declared.add(c.name)
                if c.generator == "country" and c.params.get("link"):
                    anchors.add(c.name)

        check(self.columns, "dataset")
        for t in self.tables or []:
            check(t.columns, f"table '{t.name}'")
        return self


def load_spec(data: dict[str, Any] | str) -> DatasetSpec:
    """Parse and validate a spec from a dict or JSON string."""
    if isinstance(data, str):
        return DatasetSpec.model_validate_json(data)
    return DatasetSpec.model_validate(data)
