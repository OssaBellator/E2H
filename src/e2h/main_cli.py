"""Composed command-line application for the installed E2H entry point."""

from e2h.cli import app as app
from e2h.genome_cli import genome_app
from e2h.variant_cli import variant_app

app.add_typer(genome_app, name="genome")
app.add_typer(variant_app, name="variant")
