"""CLI for deterministic E2H release artifact sealing and verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from e2h.release import (
    ReleaseIntegrityError,
    ReleaseManifest,
    load_release_manifest,
    release_manifest_sha256,
    seal_release_artifacts,
    verify_release_artifacts,
)
from e2h.sbom import SbomCanonicalizationError, canonicalize_cyclonedx_sbom_file
from e2h.trace import write_json_atomic

release_app = typer.Typer(
    no_args_is_help=True,
    help="Seal and verify deterministic wheel/sdist release artifacts.",
)
console = Console()
error_console = Console(stderr=True)


def _load(path: Path) -> ReleaseManifest:
    try:
        return load_release_manifest(path)
    except ReleaseIntegrityError as exc:
        error_console.print(f"[red]Invalid release manifest:[/red] {exc}")
        raise typer.Exit(code=2) from exc


@release_app.command("seal")
def seal_release(
    directory: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", dir_okay=False)],
    project: Annotated[str | None, typer.Option("--project")] = None,
    version: Annotated[str | None, typer.Option("--version")] = None,
) -> None:
    """Seal an exact wheel/sdist pair into a deterministic JSON manifest."""
    try:
        manifest = seal_release_artifacts(
            directory,
            expected_project=project,
            expected_version=version,
        )
    except ReleaseIntegrityError as exc:
        error_console.print(f"[red]Unable to seal release artifacts:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    rendered = manifest.model_dump_json(indent=2) + "\n"
    write_json_atomic(output, rendered)
    console.print(
        f"Sealed {len(manifest.artifacts)} release artifacts for "
        f"{manifest.project} {manifest.version}: {release_manifest_sha256(manifest)}"
    )


@release_app.command("verify")
def verify_release(
    manifest_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    directory: Annotated[Path, typer.Argument(exists=True, file_okay=False)],
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write verification proof as JSON."),
    ] = False,
) -> None:
    """Verify a distribution directory against one exact release manifest."""
    manifest = _load(manifest_path)
    try:
        verification = verify_release_artifacts(manifest, directory)
    except ReleaseIntegrityError as exc:
        error_console.print(f"[red]Release verification failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    rendered = verification.model_dump_json(indent=2) + "\n"
    if json_stdout:
        typer.echo(rendered, nl=False)
        return
    console.print(
        f"[green]Verified[/green] {verification.project} {verification.version}: "
        f"{verification.artifact_count} artifacts, "
        f"manifest_sha256={verification.manifest_sha256}"
    )


@release_app.command("canonicalize-sbom")
def canonicalize_sbom(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option("--output", "-o", dir_okay=False)],
) -> None:
    """Remove per-generation CycloneDX identity fields and emit canonical JSON."""
    try:
        rendered = canonicalize_cyclonedx_sbom_file(source)
    except SbomCanonicalizationError as exc:
        error_console.print(f"[red]Unable to canonicalize SBOM:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    write_json_atomic(output, rendered)
    console.print(f"Wrote canonical CycloneDX SBOM to {output}")


@release_app.command("inspect")
def inspect_release(
    manifest_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    json_stdout: Annotated[
        bool,
        typer.Option("--json", help="Write manifest metadata as JSON."),
    ] = False,
) -> None:
    """Inspect artifact identities without reading distribution payload contents."""
    manifest = _load(manifest_path)
    payload = {
        "schema_version": manifest.schema_version,
        "project": manifest.project,
        "version": manifest.version,
        "manifest_sha256": release_manifest_sha256(manifest),
        "artifact_count": len(manifest.artifacts),
        "total_bytes": sum(artifact.size_bytes for artifact in manifest.artifacts),
        "artifacts": [
            {
                "filename": artifact.filename,
                "kind": artifact.kind.value,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            }
            for artifact in manifest.artifacts
        ],
    }
    if json_stdout:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True) + "\n", nl=False)
        return
    table = Table(title=f"E2H release: {manifest.project} {manifest.version}")
    table.add_column("Artifact")
    table.add_column("Kind")
    table.add_column("Bytes", justify="right")
    table.add_column("SHA-256")
    for artifact in manifest.artifacts:
        table.add_row(
            artifact.filename,
            artifact.kind.value,
            str(artifact.size_bytes),
            artifact.sha256,
        )
    console.print(table)


@release_app.command("schema")
def write_release_schema(
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
) -> None:
    """Print or write the release manifest JSON Schema."""
    rendered = json.dumps(ReleaseManifest.model_json_schema(), indent=2, sort_keys=True) + "\n"
    if output is None:
        typer.echo(rendered, nl=False)
        return
    write_json_atomic(output, rendered)
    console.print(f"Wrote release manifest schema to {output}")
