# E2H MCP verification server

E2H exposes a local MCP server for bounded evidence-memory queries, artifact verification, snapshot verification, and explicitly enabled task-capsule replay.

The server uses the stable MCP Python SDK 2.x line and serves over stdio. The filesystem trust boundary is an operator-selected root. Tool arguments can name only paths that resolve inside that root; returned status and verification records do not include absolute local paths.

## Start the server

```bash
uv sync --extra dev
uv run e2h-mcp --root . --store .e2h/evidence.duckdb
```

The default server registers these tools:

- `e2h_status` reports the configured capability boundary without revealing the absolute root path.
- `e2h_memory_query` reads only E2H's predefined DuckDB analytical views. Raw SQL is never accepted. Row counts are bounded by the operator-selected maximum, and each response includes a canonical SHA-256 digest over the returned rows and store metadata.
- `e2h_verify_artifact` hashes one regular file inside the configured root and evaluates an optional expected SHA-256 plus minimum/maximum byte bounds. The operator also sets a hard maximum number of bytes that any one call may hash.
- `e2h_verify_snapshot` runs E2H's full snapshot archive, manifest, member, size, and blob-digest verification and returns the content-addressed snapshot and archive identities.

A configured memory database must already exist. The MCP server does not create a store on behalf of a model call.

Verified memory queries do not pass the configured source pathname directly to DuckDB. Before opening the read-only DuckDB connection, E2H binds the store parent through descriptor-relative no-follow operations and copies the database plus DuckDB's `.wal`, `.wal.checkpoint`, and `.wal.recovery` sidecars, when present, into a private temporary directory. It revalidates the source file identities and sidecar membership after the copy, then lets DuckDB open only the private snapshot pathname. Platforms without the descriptor-relative/no-follow primitives required for that binding fail closed for MCP verified-memory queries instead of falling back to a pathname race. Large stores therefore require enough temporary disk space for one private snapshot per in-flight MCP memory query.

## Replay is opt-in

Replay executes capsule-declared commands and is therefore not registered unless the operator enables it when launching the server:

```bash
uv run e2h-mcp \
  --root . \
  --store .e2h/evidence.duckdb \
  --allow-replay \
  --backend local
```

`e2h_replay` accepts only a capsule path and workspace path relative to the configured root. The execution backend is server configuration, not a model-controlled tool argument.

On native Linux, effective local MCP replay binds the canonical workspace with no-follow directory opens before command execution. Each declared working directory is then opened relative to held directory descriptors and checked to remain beneath that bound workspace. The child process receives a `/proc/<server-pid>/fd/<check-directory-fd>` working directory while the server keeps that descriptor open for the full command. Rebinding the original workspace pathname after the handle is acquired therefore cannot redirect the local child to a different directory. Missing executables still use the runner's normal typed launch failures, and the existing POSIX process-group timeout cleanup remains in effect. Local MCP replay fails closed on platforms where this handle-bound Linux/procfs path is unavailable; direct library-level `run_capsule()` behavior is unchanged.

MCP container replay currently fails closed. An explicit `--allow-replay --backend container` configuration is rejected when the server is constructed, and an `auto` replay request is rejected if the loaded capsule would select the container backend. The normal library/CLI container runner remains available outside MCP. This temporary boundary prevents the remote MCP surface from claiming configured-root containment while Docker/Podman would still re-consult mutable host workspace and workdir pathnames. Re-enabling container replay requires a mechanism that binds both the container mount source and effective working directory to stable identities; that work remains tracked in issue #288.

By default replay results omit stdout and stderr and return their SHA-256 digests, character counts, truncation flags, status, failures, and an exact replay-result digest. This keeps routine MCP calls from automatically copying command output into model context. An operator can deliberately expose E2H's already bounded command output with `--expose-replay-output`.

Enabling MCP replay is not a sandbox by itself. The currently supported MCP replay path is the handle-bound local backend, so only enable it for commands you are prepared to execute directly on the MCP host. For container-isolated replay, use the normal trusted local E2H CLI/library path until the MCP container workspace boundary is re-enabled safely.

## Example host configuration

Any MCP host that can launch a stdio server can start E2H with an equivalent command. A generic configuration shape is:

```json
{
  "servers": {
    "e2h": {
      "command": "uv",
      "args": [
        "run",
        "e2h-mcp",
        "--root",
        ".",
        "--store",
        ".e2h/evidence.duckdb"
      ]
    }
  }
}
```

Resolve the working directory and command path according to the host's configuration rules. Do not place secrets in MCP tool arguments or in committed host configuration.

## Evidence semantics

The MCP layer does not claim that a SHA-256 digest proves an external statement is true. Digests bind the bytes or structured result E2H actually observed:

- experiment-store source rows retain the content hashes produced by E2H ingestion;
- memory responses receive a digest over the exact bounded query result and selected store metadata;
- artifact checks hash the exact stable file observed during the call;
- snapshot checks reuse E2H's manifest and blob verification;
- replay responses bind both the validated capsule and the exact full `RunResult`, even when raw command output is omitted from the MCP response.

These boundaries are intended to make agent-visible memory and verification results auditable without promoting hidden reasoning or unverified text into trusted executable state.
