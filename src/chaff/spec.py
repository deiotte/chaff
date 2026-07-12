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

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

SPEC_VERSION = "1.0"


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

    @field_validator("columns")
    @classmethod
    def _cols_unique(cls, v: list[ColumnSpec]) -> list[ColumnSpec]:
        return _reject_duplicate_columns(v)


class UpdateSpec(BaseModel):
    """One per-tick update rule applied to an entity's state (ADR-0009)."""

    updater: str = Field(..., description="Registered updater id: movement, lifecycle, drift, ...")
    params: dict[str, Any] = Field(default_factory=dict, description="Updater-specific parameters.")


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
