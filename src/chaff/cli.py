"""chaff CLI. Headless mode is free because the spec is the product."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .engine import run as run_engine
from .formats import list_formats
from .generators import list_generators
from .sinks import list_sink_status
from .spec import load_spec

app = typer.Typer(help="chaff — synthetic demo data engine.", no_args_is_help=True)


@app.command()
def generate(
    spec_file: Path = typer.Argument(..., help="Path to a DatasetSpec JSON file."),
    out: Optional[Path] = typer.Option(None, "--out", "-o", help="Override sink file path."),
    seed: Optional[int] = typer.Option(None, "--seed", help="Override spec seed."),
    rows: Optional[int] = typer.Option(None, "--rows", "-n", help="Override row count."),
    fmt: Optional[str] = typer.Option(
        None, "--format", "-f",
        help="Override output format (e.g. csv), keeping the spec's other output options."),
):
    """Generate a dataset from a spec file."""
    data = json.loads(spec_file.read_text())
    if seed is not None:
        data["seed"] = seed
    if rows is not None:
        data["rows"] = rows
    if fmt is not None:
        data.setdefault("output", {})["format"] = fmt
    if out is not None:
        data.setdefault("sink", {}).setdefault("options", {})["path"] = str(out)
    spec = load_spec(data)
    typer.echo(run_engine(spec))


@app.command()
def registry():
    """List registered generators, formats, and sinks."""
    from .updaters import list_updaters

    typer.echo("generators: " + ", ".join(list_generators()))
    typer.echo("formats:    " + ", ".join(list_formats()))
    # A sink whose client library isn't installed is still registered, so say
    # so here — before someone builds a demo around it (ADR-0047).
    status = list_sink_status()
    typer.echo("sinks:      " + ", ".join(
        sid if st["available"] else f"{sid} (not installed: {st['install']})"
        for sid, st in status.items()))
    typer.echo("updaters:   " + ", ".join(list_updaters()))


@app.command()
def validate(spec_file: Path = typer.Argument(...)):
    """Validate a spec file without generating anything."""
    spec = load_spec(json.loads(spec_file.read_text()))
    if spec.entity:
        shape = f"{spec.entity.count} entities × {spec.entity.ticks} ticks"
    elif spec.tables:
        shape = f"{1 + len(spec.tables)} tables"
    else:
        shape = f"{spec.rows} rows"
    typer.echo(f"OK: '{spec.name}' — {shape}, {len(spec.columns)} columns, "
               f"{spec.output.format} -> {spec.sink.sink}")


@app.command()
def library(
    save: Optional[Path] = typer.Option(None, "--save", help="Save a spec file to the library."),
    name: Optional[str] = typer.Option(None, "--name", help="Name to save under (defaults to spec name)."),
):
    """List the spec library, or save a spec into it (--save FILE)."""
    from . import library as lib

    if save is not None:
        data = json.loads(save.read_text())
        saved = lib.save_named(name or data.get("name", ""), data)
        typer.echo(f"saved -> {saved}")
        return
    for s in lib.list_specs():
        typer.echo(f"[{s['source']:6}] {s['name']}  ({s['rows']} rows, "
                   f"{s['columns']} cols, {s['format']})")


@app.command()
def version():
    typer.echo(__version__)


if __name__ == "__main__":
    app()
