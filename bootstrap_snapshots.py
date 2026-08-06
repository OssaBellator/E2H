"""One-use fail-closed integration patch for workspace snapshots."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Proactive strict-lint corrections in the isolated snapshot core.
replace_once(
    "src/e2h/snapshot.py",
    '    schema_version: Literal["0.1"] = SNAPSHOT_SCHEMA_VERSION\n',
    '    schema_version: Literal["0.1"] = "0.1"\n',
)
replace_once(
    "src/e2h/snapshot.py",
    '''        if self.kind == "directory":
            if self.sha256 is not None or self.size_bytes != 0 or self.executable:
                raise ValueError("directory entries cannot declare file attributes")
        elif self.sha256 is None:
            raise ValueError("file entries require sha256")
''',
    '''        if self.kind == "directory" and (
            self.sha256 is not None or self.size_bytes != 0 or self.executable
        ):
            raise ValueError("directory entries cannot declare file attributes")
        if self.kind == "file" and self.sha256 is None:
            raise ValueError("file entries require sha256")
''',
)
replace_once(
    "src/e2h/snapshot.py",
    '''    if destination.exists():
        if not destination.is_dir():
            raise SnapshotError("restore destination exists and is not a directory")
        try:
''',
    '''    if destination.exists() and not destination.is_dir():
        raise SnapshotError("restore destination exists and is not a directory")
    if destination.exists():
        try:
''',
)

# CLI registration.
replace_once(
    "src/e2h/cli.py",
    "from e2h.compiler_cli import compiler_app\n",
    "from e2h.compiler_cli import compiler_app\nfrom e2h.snapshot_cli import snapshot_app\n",
)
replace_once(
    "src/e2h/cli.py",
    'app.add_typer(compiler_app, name="compile")\n',
    'app.add_typer(compiler_app, name="compile")\napp.add_typer(snapshot_app, name="snapshot")\n',
)

# Compiler snapshot references.
replace_once(
    "src/e2h/compiler.py",
    "from e2h.runner import RunResult, RunStatus, run_capsule\n",
    "from e2h.runner import RunResult, RunStatus, run_capsule\n"
    "from e2h.snapshot import SnapshotReference\n",
)
replace_once(
    "src/e2h/compiler.py",
    "    oracles: list[OracleTemplate] = Field(default_factory=list, max_length=100)\n"
    "    auto_mutate_oracles: bool = True\n",
    "    oracles: list[OracleTemplate] = Field(default_factory=list, max_length=100)\n"
    "    snapshots: list[SnapshotReference] = Field(default_factory=list, max_length=100)\n"
    "    auto_mutate_oracles: bool = True\n",
)
replace_once(
    "src/e2h/compiler.py",
    '        _ensure_json(self.metadata, "compiler metadata")\n'
    "        compiled_oracles = [compile_oracle(oracle) for oracle in self.oracles]\n",
    '        _ensure_json(self.metadata, "compiler metadata")\n'
    "        snapshot_ids = [snapshot.snapshot_id for snapshot in self.snapshots]\n"
    "        if len(snapshot_ids) != len(set(snapshot_ids)):\n"
    '            raise ValueError("snapshot ids must be unique")\n'
    "        compiled_oracles = [compile_oracle(oracle) for oracle in self.oracles]\n",
)
replace_once(
    "src/e2h/compiler.py",
    '            "oracles": [oracle.model_dump(mode="json") for oracle in spec.oracles],\n',
    '            "oracles": [oracle.model_dump(mode="json") for oracle in spec.oracles],\n'
    '            "snapshots": [snapshot.model_dump(mode="json") for snapshot in spec.snapshots],\n',
)

# Public API and version.
replace_once(
    "src/e2h/__init__.py",
    "from e2h.runner import RunResult, run_capsule\n",
    "from e2h.runner import RunResult, run_capsule\n"
    "from e2h.snapshot import (\n"
    "    SnapshotLimits,\n"
    "    SnapshotManifest,\n"
    "    SnapshotReference,\n"
    "    create_snapshot,\n"
    "    restore_snapshot,\n"
    "    snapshot_reference,\n"
    "    verify_snapshot,\n"
    ")\n",
)
for name in (
    "SnapshotLimits",
    "SnapshotManifest",
    "SnapshotReference",
    "create_snapshot",
    "restore_snapshot",
    "snapshot_reference",
    "verify_snapshot",
):
    replace_once(
        "src/e2h/__init__.py",
        '    "TaskCapsule",\n',
        f'    "{name}",\n    "TaskCapsule",\n',
    )
replace_once("src/e2h/__init__.py", '__version__ = "0.5.0"', '__version__ = "0.6.0"')
replace_once("pyproject.toml", 'version = "0.5.0"', 'version = "0.6.0"')

# Documentation and roadmap.
replace_once(
    "README.md",
    "The repository now contains five connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, and declarative **file, JSON, and artifact oracles**.",
    "The repository now contains six connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, declarative **file, JSON, and artifact oracles**, and content-addressed **workspace snapshots**.",
)
replace_once(
    "README.md",
    "- Automatic operator-specific oracle mutations for strong verification.\n",
    "- Automatic operator-specific oracle mutations for strong verification.\n"
    "- Deterministic content-addressed workspace and artifact snapshot bundles.\n"
    "- Snapshot verification, safe restoration, and portable compiler references.\n",
)
replace_once(
    "README.md",
    "uv run e2h compile materialize .e2h/compiler-approved.json \\\n  .e2h/compiler-verification.json --output .e2h/compiled-capsule.yaml\n",
    "uv run e2h compile materialize .e2h/compiler-approved.json \\\n  .e2h/compiler-verification.json --output .e2h/compiled-capsule.yaml\n"
    "uv run e2h snapshot create examples .e2h/examples.e2hsnap \\\n  --include compile --include ingest\n"
    "uv run e2h snapshot verify .e2h/examples.e2hsnap\n"
    "uv run e2h snapshot restore .e2h/examples.e2hsnap .e2h/restored-examples\n",
)
snapshot_section = '''## Workspace snapshots

`e2h snapshot create` records selected workspace or artifact trees as deterministic ZIP bundles. A canonical `manifest.json` contains sorted directory and file entries, executable bits, byte lengths, and SHA-256 digests; identical file content is stored once under `blobs/<sha256>`. Fixed archive timestamps, modes, and member ordering make equivalent snapshots byte-for-byte reproducible.

Creation rejects symbolic links, special filesystem entries, unsafe include paths, and configured entry or byte limits. Verification rejects duplicate, unexpected, oversized, or traversal-style archive members and recomputes every blob digest. Restoration first verifies the complete archive, writes into a sibling staging directory, and atomically moves the result into a new or empty destination. It never extracts ZIP member paths directly.

`e2h snapshot reference` emits a portable `SnapshotReference` containing the manifest-derived snapshot ID, archive SHA-256, locator, and workspace/artifact role. Compiler specifications may attach these references under `snapshots`; they become immutable `e2h_compiler.snapshots` metadata and therefore participate in capsule and proposal identity without embedding archive bytes in the proposal.

Default creation excludes `.git`, `.venv`, `.e2h`, Python caches, and bytecode. Supplying `--exclude` replaces that default list, so trusted workflows can define an explicit capture policy.

'''
replace_once(
    "README.md",
    "## Architecture direction\n",
    snapshot_section + "## Architecture direction\n",
)
replace_once(
    "ROADMAP.md",
    "- [ ] Environment and artifact snapshots.\n",
    "- [x] Environment and artifact snapshots.\n",
)

Path(__file__).unlink()
