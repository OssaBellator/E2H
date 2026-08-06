"""One-use fail-closed integration patch for the experiment store."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# CLI registration.
replace_once(
    "src/e2h/cli.py",
    "from e2h.snapshot_cli import snapshot_app\n",
    "from e2h.snapshot_cli import snapshot_app\nfrom e2h.store_cli import store_app\n",
)
replace_once(
    "src/e2h/cli.py",
    'app.add_typer(snapshot_app, name="snapshot")\n',
    'app.add_typer(snapshot_app, name="snapshot")\napp.add_typer(store_app, name="store")\n',
)

# Public API and package version.
replace_once(
    "src/e2h/__init__.py",
    "from e2h.snapshot import (\n",
    "from e2h.store import (\n"
    "    export_parquet,\n"
    "    ingest_artifact,\n"
    "    initialize_store,\n"
    "    query_store,\n"
    "    store_info,\n"
    ")\n"
    "from e2h.store_models import ArtifactKind, IngestResult, QueryView, StoreInfo\n"
    "from e2h.snapshot import (\n",
)
for name in (
    "ArtifactKind",
    "IngestResult",
    "QueryView",
    "StoreInfo",
    "export_parquet",
    "ingest_artifact",
    "initialize_store",
    "query_store",
    "store_info",
):
    replace_once(
        "src/e2h/__init__.py",
        '    "ArtifactOracle",\n',
        f'    "{name}",\n    "ArtifactOracle",\n',
    )
replace_once("src/e2h/__init__.py", '__version__ = "0.7.0"', '__version__ = "0.8.0"')

# Runtime dependencies and package version.
replace_once("pyproject.toml", 'version = "0.7.0"', 'version = "0.8.0"')
replace_once(
    "pyproject.toml",
    'dependencies = [\n  "pydantic>=2.10,<3",\n',
    'dependencies = [\n'
    '  "duckdb>=1.4,<2",\n'
    '  "pydantic>=2.10,<3",\n'
    '  "pytz>=2024,<2027",\n',
)

# Documentation and roadmap.
replace_once(
    "README.md",
    "The repository now contains seven connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, declarative **file, JSON, and artifact oracles**, content-addressed **workspace snapshots**, and an optional **container sandbox backend**.",
    "The repository now contains eight connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, declarative **file, JSON, and artifact oracles**, content-addressed **workspace snapshots**, an optional **container sandbox backend**, and a transactional **DuckDB/Parquet experiment store**.",
)
replace_once(
    "README.md",
    "- Backend selection for direct replay, matrices, and compiler mutation verification.\n",
    "- Backend selection for direct replay, matrices, and compiler mutation verification.\n"
    "- Transactional DuckDB ingestion for run and replay-matrix result artifacts.\n"
    "- Stable analytical views and compressed Parquet export without retaining raw command output.\n",
)
replace_once(
    "README.md",
    "uv run e2h run examples/sandbox/capsule.yaml --backend container --workspace .\n",
    "uv run e2h run examples/sandbox/capsule.yaml --backend container --workspace .\n"
    "uv run e2h store init .e2h/evidence.duckdb\n"
    "uv run e2h store ingest .e2h/evidence.duckdb .e2h/result.json .e2h/matrix.json\n"
    "uv run e2h store query .e2h/evidence.duckdb variants --json\n"
    "uv run e2h store export .e2h/evidence.duckdb .e2h/runs.parquet --view runs\n",
)
store_section = '''## Experiment store

`e2h store ingest` transactionally normalizes standalone `RunResult` artifacts and complete replay-matrix `ExperimentResult` artifacts into DuckDB. Source artifacts are identified by their SHA-256 bytes, so re-ingesting an identical file is idempotent. A failed validation or multi-row insertion rolls back without leaving a partial source, run, check, or summary record.

The normalized schema separates provenance sources, runs, per-check outcomes, and variant summaries. Stable query views expose runs, checks, failures, variants, capsule aggregates, and source provenance. Queries are bounded to 10,000 rows and selected from a fixed enum rather than accepting arbitrary SQL through the CLI.

For privacy, the store does not retain command stdout or stderr. Check rows contain character lengths, SHA-256 digests, truncation flags, exit status, duration, working directory, and canonical argument JSON. Source provenance records only the artifact basename, content digest, kind, schema version, and ingestion time; absolute source paths are not stored.

`e2h store export` uses DuckDB to write Zstandard-compressed Parquet for any stable view. This produces portable analytical datasets without requiring a separate Arrow dependency. The DuckDB file and Parquet outputs inherit the confidentiality of their source evidence and should be retained accordingly.

'''
replace_once(
    "README.md",
    "## Architecture direction\n",
    store_section + "## Architecture direction\n",
)
replace_once(
    "ROADMAP.md",
    "- [ ] DuckDB/Parquet experiment store and query layer.\n",
    "- [x] DuckDB/Parquet experiment store and query layer.\n",
)

Path(__file__).unlink()
