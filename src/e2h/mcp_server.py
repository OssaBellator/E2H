"""MCP server exposing bounded, verifiable E2H operations."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from e2h.genome import capsule_sha256
from e2h.loader import CapsuleLoadError, load_capsule
from e2h.runner import ExecutionBackend, RunnerError, run_capsule
from e2h.snapshot import SnapshotError, archive_sha256, verify_snapshot
from e2h.store import StoreError, query_store, store_info
from e2h.store_models import MAX_QUERY_ROWS, QueryView

_DEFAULT_MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
_READ_CHUNK_BYTES = 1024 * 1024
_SHA256_PATTERN = r"^[0-9a-f]{64}$"


class MCPServiceError(ValueError):
    """Raised when an MCP-facing operation cannot be completed safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise MCPServiceError("value must contain canonical JSON data") from exc
    return rendered.encode("utf-8")


def _digest_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _package_version() -> str:
    try:
        return importlib.metadata.version("e2h")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _safe_relative(root: Path, value: str, *, noun: str) -> tuple[Path, str]:
    if not value or "\x00" in value:
        raise MCPServiceError(f"{noun} must be a non-empty path without NUL")
    candidate = Path(value)
    if candidate.is_absolute():
        raise MCPServiceError(f"{noun} must be relative to the configured MCP root")
    resolved = (root / candidate).resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise MCPServiceError(f"{noun} escapes the configured MCP root") from exc
    return resolved, relative.as_posix() or "."


def _stable_file_sha256(path: Path, *, max_bytes: int) -> tuple[int, str]:
    try:
        before = path.stat()
    except OSError as exc:
        raise MCPServiceError(f"unable to stat artifact: {exc}") from exc
    if not path.is_file():
        raise MCPServiceError("artifact is not a regular file")
    if before.st_size > max_bytes:
        raise MCPServiceError(f"artifact exceeds configured max bytes ({max_bytes})")
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_READ_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise MCPServiceError(f"artifact exceeds configured max bytes ({max_bytes})")
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise MCPServiceError(f"unable to hash artifact: {exc}") from exc
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if identity_before != identity_after or total != before.st_size:
        raise MCPServiceError("artifact changed while it was being verified")
    return total, digest.hexdigest()


@dataclass(frozen=True)
class MCPServerConfig:
    """Operator-controlled trust boundary for the E2H MCP server."""

    root: Path
    store: Path | None = None
    allow_replay: bool = False
    replay_backend: ExecutionBackend = ExecutionBackend.AUTO
    container_runtime: str | None = None
    expose_replay_output: bool = False
    max_artifact_bytes: int = _DEFAULT_MAX_ARTIFACT_BYTES
    max_memory_rows: int = 1000


class MCPStatus(StrictModel):
    schema_version: str = "0.1"
    server_version: str
    root_label: str
    memory_enabled: bool
    replay_enabled: bool
    replay_backend: str
    replay_output_exposed: bool
    max_artifact_bytes: int = Field(ge=1)
    max_memory_rows: int = Field(ge=1, le=MAX_QUERY_ROWS)


class VerifiedMemoryResult(StrictModel):
    schema_version: str = "0.1"
    view: QueryView
    rows: list[dict[str, Any]]
    row_count: int = Field(ge=0)
    store_schema_version: str
    store_source_count: int = Field(ge=0)
    memory_sha256: str = Field(pattern=_SHA256_PATTERN)


class ArtifactVerification(StrictModel):
    schema_version: str = "0.1"
    artifact: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=_SHA256_PATTERN)
    expected_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    min_bytes: int | None = Field(default=None, ge=0)
    max_bytes: int | None = Field(default=None, ge=0)
    digest_matches: bool | None = None
    size_matches: bool = True
    verified: bool


class SnapshotVerification(StrictModel):
    schema_version: str = "0.1"
    archive: str
    snapshot_id: str = Field(pattern=_SHA256_PATTERN)
    archive_sha256: str = Field(pattern=_SHA256_PATTERN)
    entries: int = Field(ge=0)
    total_bytes: int = Field(ge=0)
    verified: bool = True


class ReplayCheck(StrictModel):
    id: str
    status: str
    exit_code: int | None
    duration_seconds: float = Field(ge=0)
    stdout_chars: int = Field(ge=0)
    stderr_chars: int = Field(ge=0)
    stdout_sha256: str = Field(pattern=_SHA256_PATTERN)
    stderr_sha256: str = Field(pattern=_SHA256_PATTERN)
    stdout_truncated: bool
    stderr_truncated: bool
    stdout: str | None = None
    stderr: str | None = None
    error: str | None = None
    failure: dict[str, Any] | None = None


class ReplayVerification(StrictModel):
    schema_version: str = "0.1"
    capsule_id: str
    capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    replay_sha256: str = Field(pattern=_SHA256_PATTERN)
    status: str
    duration_seconds: float = Field(ge=0)
    checks: list[ReplayCheck]
    failure_summary: dict[str, Any]
    output_exposed: bool


class E2HMCPService:
    """Path-bounded implementation behind the MCP tool surface."""

    def __init__(self, config: MCPServerConfig) -> None:
        root = config.root.resolve()
        if not root.is_dir():
            raise MCPServiceError(f"MCP root is not a directory: {config.root}")
        if config.max_artifact_bytes < 1:
            raise MCPServiceError("max_artifact_bytes must be positive")
        if not 1 <= config.max_memory_rows <= MAX_QUERY_ROWS:
            raise MCPServiceError(
                f"max_memory_rows must be between 1 and {MAX_QUERY_ROWS}"
            )
        try:
            backend = ExecutionBackend(config.replay_backend)
        except ValueError as exc:
            raise MCPServiceError(f"unknown replay backend: {config.replay_backend}") from exc

        store: Path | None = None
        if config.store is not None:
            raw_store = config.store
            if raw_store.is_absolute():
                try:
                    store = raw_store.resolve()
                    store.relative_to(root)
                except ValueError as exc:
                    raise MCPServiceError("memory store must be inside the configured MCP root") from exc
            else:
                store, _ = _safe_relative(root, str(raw_store), noun="memory store")
        self.config = replace(config, root=root, store=store, replay_backend=backend)

    def status(self) -> MCPStatus:
        """Return the operator-selected MCP capability boundary without local absolute paths."""
        return MCPStatus(
            server_version=_package_version(),
            root_label=self.config.root.name or ".",
            memory_enabled=self.config.store is not None,
            replay_enabled=self.config.allow_replay,
            replay_backend=self.config.replay_backend.value,
            replay_output_exposed=self.config.expose_replay_output,
            max_artifact_bytes=self.config.max_artifact_bytes,
            max_memory_rows=self.config.max_memory_rows,
        )

    def memory_query(self, view: QueryView, *, limit: int = 100) -> VerifiedMemoryResult:
        """Query one predefined E2H analytical view and bind the returned memory to a digest."""
        if self.config.store is None:
            raise MCPServiceError("verified memory is not configured for this MCP server")
        if not self.config.store.is_file():
            raise MCPServiceError("configured memory store does not exist")
        if not 1 <= limit <= self.config.max_memory_rows:
            raise MCPServiceError(
                f"limit must be between 1 and configured max_memory_rows "
                f"({self.config.max_memory_rows})"
            )
        try:
            selected = QueryView(view)
            info = store_info(self.config.store)
            rows = query_store(self.config.store, selected, limit=limit)
        except (StoreError, ValueError) as exc:
            raise MCPServiceError(str(exc)) from exc
        material = {
            "view": selected.value,
            "rows": rows,
            "store_schema_version": info.schema_version,
            "store_source_count": info.sources,
        }
        return VerifiedMemoryResult(
            view=selected,
            rows=rows,
            row_count=len(rows),
            store_schema_version=info.schema_version,
            store_source_count=info.sources,
            memory_sha256=_digest_json(material),
        )

    def verify_artifact(
        self,
        artifact: str,
        *,
        expected_sha256: str | None = None,
        min_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> ArtifactVerification:
        """Hash one bounded file and evaluate caller-supplied digest and size expectations."""
        path, relative = _safe_relative(self.config.root, artifact, noun="artifact")
        if expected_sha256 is not None:
            expected_sha256 = expected_sha256.casefold()
            if len(expected_sha256) != 64 or any(
                char not in "0123456789abcdef" for char in expected_sha256
            ):
                raise MCPServiceError("expected_sha256 must be a lowercase hexadecimal SHA-256")
        if min_bytes is not None and min_bytes < 0:
            raise MCPServiceError("min_bytes must be non-negative")
        if max_bytes is not None and max_bytes < 0:
            raise MCPServiceError("max_bytes must be non-negative")
        if min_bytes is not None and max_bytes is not None and min_bytes > max_bytes:
            raise MCPServiceError("min_bytes must not exceed max_bytes")
        size, digest = _stable_file_sha256(
            path,
            max_bytes=self.config.max_artifact_bytes,
        )
        digest_matches = None if expected_sha256 is None else digest == expected_sha256
        size_matches = (min_bytes is None or size >= min_bytes) and (
            max_bytes is None or size <= max_bytes
        )
        verified = size_matches and digest_matches is not False
        return ArtifactVerification(
            artifact=relative,
            size_bytes=size,
            sha256=digest,
            expected_sha256=expected_sha256,
            min_bytes=min_bytes,
            max_bytes=max_bytes,
            digest_matches=digest_matches,
            size_matches=size_matches,
            verified=verified,
        )

    def verify_snapshot(self, archive: str) -> SnapshotVerification:
        """Run E2H's complete snapshot structure and blob verification for one archive."""
        path, relative = _safe_relative(self.config.root, archive, noun="snapshot archive")
        if not path.is_file():
            raise MCPServiceError("snapshot archive does not exist or is not a file")
        try:
            manifest = verify_snapshot(path)
            digest = archive_sha256(path)
        except SnapshotError as exc:
            raise MCPServiceError(str(exc)) from exc
        return SnapshotVerification(
            archive=relative,
            snapshot_id=manifest.snapshot_id,
            archive_sha256=digest,
            entries=len(manifest.core.entries),
            total_bytes=manifest.core.total_bytes,
        )

    def replay(self, capsule: str, *, workspace: str = ".") -> ReplayVerification:
        """Execute one capsule only when replay was enabled by the server operator."""
        if not self.config.allow_replay:
            raise MCPServiceError("replay is disabled by the MCP server operator")
        capsule_path, _ = _safe_relative(self.config.root, capsule, noun="capsule")
        workspace_path, _ = _safe_relative(self.config.root, workspace, noun="workspace")
        if not capsule_path.is_file():
            raise MCPServiceError("capsule does not exist or is not a file")
        if not workspace_path.is_dir():
            raise MCPServiceError("workspace does not exist or is not a directory")
        try:
            loaded = load_capsule(capsule_path)
            result = run_capsule(
                loaded,
                workspace_path,
                backend=self.config.replay_backend,
                container_runtime=self.config.container_runtime,
            )
        except (CapsuleLoadError, RunnerError) as exc:
            raise MCPServiceError(str(exc)) from exc

        raw_result = result.model_dump(mode="json")
        checks = [
            ReplayCheck(
                id=check.id,
                status=check.status.value,
                exit_code=check.exit_code,
                duration_seconds=check.duration_seconds,
                stdout_chars=len(check.stdout),
                stderr_chars=len(check.stderr),
                stdout_sha256=hashlib.sha256(check.stdout.encode("utf-8")).hexdigest(),
                stderr_sha256=hashlib.sha256(check.stderr.encode("utf-8")).hexdigest(),
                stdout_truncated=check.stdout_truncated,
                stderr_truncated=check.stderr_truncated,
                stdout=check.stdout if self.config.expose_replay_output else None,
                stderr=check.stderr if self.config.expose_replay_output else None,
                error=check.error,
                failure=(
                    check.failure.model_dump(mode="json") if check.failure is not None else None
                ),
            )
            for check in result.checks
        ]
        return ReplayVerification(
            capsule_id=result.capsule_id,
            capsule_sha256=capsule_sha256(loaded),
            replay_sha256=_digest_json(raw_result),
            status=result.status.value,
            duration_seconds=result.duration_seconds,
            checks=checks,
            failure_summary=result.failure_summary.model_dump(mode="json"),
            output_exposed=self.config.expose_replay_output,
        )


def create_mcp_server(config: MCPServerConfig) -> MCPServer:
    """Create a stdio-oriented MCP server with tools matching the operator capability policy."""
    service = E2HMCPService(config)
    mcp = MCPServer(
        "E2H Verification",
        version=_package_version(),
        instructions=(
            "Use E2H tools for bounded evidence-memory queries, artifact verification, and "
            "operator-enabled deterministic replay. Treat returned SHA-256 values as evidence "
            "bindings, not as claims about data outside the configured E2H root."
        ),
    )

    @mcp.tool()
    def e2h_status() -> MCPStatus:
        """Show the configured E2H MCP trust boundary and enabled capabilities."""
        return service.status()

    @mcp.tool()
    def e2h_memory_query(view: QueryView, limit: int = 100) -> VerifiedMemoryResult:
        """Query a predefined, bounded E2H experiment-store view with a result digest."""
        return service.memory_query(view, limit=limit)

    @mcp.tool()
    def e2h_verify_artifact(
        artifact: str,
        expected_sha256: str | None = None,
        min_bytes: int | None = None,
        max_bytes: int | None = None,
    ) -> ArtifactVerification:
        """Verify one file inside the configured root by SHA-256 and optional size bounds."""
        return service.verify_artifact(
            artifact,
            expected_sha256=expected_sha256,
            min_bytes=min_bytes,
            max_bytes=max_bytes,
        )

    @mcp.tool()
    def e2h_verify_snapshot(archive: str) -> SnapshotVerification:
        """Verify a deterministic E2H snapshot archive and all content blobs."""
        return service.verify_snapshot(archive)

    if service.config.allow_replay:

        @mcp.tool()
        def e2h_replay(capsule: str, workspace: str = ".") -> ReplayVerification:
            """Replay a capsule under the operator-selected backend and return evidence digests."""
            return service.replay(capsule, workspace=workspace)

    return mcp


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e2h-mcp",
        description="Serve bounded E2H verification tools over MCP stdio.",
    )
    parser.add_argument("--root", type=Path, default=Path("."), help="Allowed filesystem root.")
    parser.add_argument(
        "--store",
        type=Path,
        default=None,
        help="DuckDB experiment store inside --root for verified memory queries.",
    )
    parser.add_argument(
        "--allow-replay",
        action="store_true",
        help="Register the command-executing replay tool. Disabled by default.",
    )
    parser.add_argument(
        "--backend",
        choices=[item.value for item in ExecutionBackend],
        default=ExecutionBackend.AUTO.value,
        help="Operator-selected replay backend when --allow-replay is enabled.",
    )
    parser.add_argument(
        "--container-runtime",
        default=None,
        help="Container runtime binary override for replay. Never exposed as a tool argument.",
    )
    parser.add_argument(
        "--expose-replay-output",
        action="store_true",
        help="Include bounded stdout/stderr in replay results instead of digest-only output.",
    )
    parser.add_argument(
        "--max-artifact-bytes",
        type=int,
        default=_DEFAULT_MAX_ARTIFACT_BYTES,
        help="Maximum bytes hashed by one artifact verification call.",
    )
    parser.add_argument(
        "--max-memory-rows",
        type=int,
        default=1000,
        help=f"Maximum rows returned by one memory query (hard limit {MAX_QUERY_ROWS}).",
    )
    return parser


def main() -> None:
    """Run the configured E2H MCP server over stdio."""
    args = _parser().parse_args()
    try:
        server = create_mcp_server(
            MCPServerConfig(
                root=args.root,
                store=args.store,
                allow_replay=args.allow_replay,
                replay_backend=ExecutionBackend(args.backend),
                container_runtime=args.container_runtime,
                expose_replay_output=args.expose_replay_output,
                max_artifact_bytes=args.max_artifact_bytes,
                max_memory_rows=args.max_memory_rows,
            )
        )
    except MCPServiceError as exc:
        print(f"Unable to start E2H MCP server: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    server.run()


if __name__ == "__main__":
    main()
