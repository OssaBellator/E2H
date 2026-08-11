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
  --backend auto
```

`e2h_replay` accepts only a capsule path and workspace path relative to the configured root. The execution backend is server configuration, not a model-controlled tool argument. `auto` selects container replay for capsules with a sandbox and local replay otherwise.

On native Linux, local MCP replay binds the canonical workspace with no-follow directory opens before command execution. Each declared working directory is then opened relative to held directory descriptors and checked to remain beneath that bound workspace. The child process receives a `/proc/<server-pid>/fd/<check-directory-fd>` working directory while the server keeps that descriptor open for the full command. Rebinding the original workspace pathname after the handle is acquired therefore cannot redirect the local child to a different directory. Local replay runs against the caller workspace, so successful command mutations persist. Missing executables still use the runner's normal typed launch failures, and the existing POSIX process-group timeout cleanup remains in effect. Local MCP replay fails closed on platforms where this handle-bound Linux/procfs path is unavailable; direct library-level `run_capsule()` behavior is unchanged.

Container MCP replay uses a different workspace contract. Before the runtime is invoked, E2H binds the requested source directory by descriptor and captures a private temporary copy while enforcing byte and entry limits. Regular files and directories are revalidated during capture, unsupported special files are rejected, and only relative symlinks whose lexical target remains inside the isolated tree are preserved. The container runtime receives only the private copy path, so rebinding the caller's original workspace pathname after capture cannot redirect the runtime to another source tree.

Remote container replay is intentionally stricter than the direct container runner. The capsule must use `sandbox.workspace_access: read_only`, `sandbox.read_only_root: true`, and `sandbox.pull_policy: never`. The input capture limits cannot bound arbitrary growth after a writable bind mount or writable container root is handed to the runtime, and allowing a runtime-triggered image pull would make a remote replay capable of expanding host image storage. The direct trusted CLI/library runner retains its existing policy choices; the remote isolated adapter fails closed on these three settings.

E2H also invokes containers with `--log-driver none`. Attached stdout/stderr still flows through E2H's bounded capture, while Docker is prevented from separately persisting an unbounded per-container daemon log. The private workspace is mounted read-only and discarded after replay, so container checks cannot write back to the caller workspace.

The isolated capture limit defaults are 100 MiB and 10,000 filesystem entries. Operators can change them with `--max-replay-workspace-bytes` and `--max-replay-workspace-entries`. Hosts that do not provide every descriptor primitive required by the capture path fail closed for container replay instead of silently falling back to pathname-based copying.

By default replay results omit stdout and stderr and return their SHA-256 digests, character counts, truncation flags, status, failures, and an exact replay-result digest. Each result also reports `execution_backend`, `workspace_mode`, and `workspace_mutations_persisted`, so callers can distinguish persistent handle-bound local execution from discarded isolated-copy execution. `replay_sha256` binds those workspace semantics together with the full `RunResult`; the validated capsule has its own `capsule_sha256`. An operator can deliberately expose E2H's already bounded command output with `--expose-replay-output`.

Enabling MCP replay is not a general sandbox guarantee. Local replay executes declared commands directly on the MCP host. Container replay uses the capsule's existing container sandbox policy plus the additional remote restrictions described above. Only enable replay for capsules and container images the operator is prepared to execute.

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
- replay responses return a capsule digest plus a replay digest over the full `RunResult` and the backend/workspace persistence semantics, even when raw command output is omitted from the MCP response.

These boundaries are intended to make agent-visible memory and verification results auditable without promoting hidden reasoning or unverified text into trusted executable state.
