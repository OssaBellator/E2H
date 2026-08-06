"""Composed command-line application for the installed E2H entry point."""

from e2h.cli import app
from e2h.genome_cli import genome_app

app.add_typer(genome_app, name="genome")
