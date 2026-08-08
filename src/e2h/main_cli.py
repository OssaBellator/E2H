"""Composed command-line application for the installed E2H entry point."""

from e2h.benchmark_cli import benchmark_app
from e2h.capture_cli import capture_app
from e2h.cli import app as app
from e2h.gemini_runtime_cli import runtime_app
from e2h.genome_cli import genome_app
from e2h.optimizer_cli import optimizer_app
from e2h.partition_cli import partition_app
from e2h.promotion_cli import promotion_app
from e2h.release_cli import release_app
from e2h.runtime_plan_cli import plan_runtime_request_command
from e2h.variant_cli import variant_app

app.add_typer(benchmark_app, name="benchmark")
app.add_typer(capture_app, name="capture")
app.add_typer(genome_app, name="genome")
app.add_typer(optimizer_app, name="optimizer")
app.add_typer(partition_app, name="partition")
app.add_typer(promotion_app, name="promotion")
app.add_typer(release_app, name="release")
runtime_app.command("plan")(plan_runtime_request_command)
app.add_typer(runtime_app, name="runtime")
app.add_typer(variant_app, name="variant")
