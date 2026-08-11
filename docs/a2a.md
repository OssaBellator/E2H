# E2H A2A verification agent

E2H exposes its bounded verification surface as an Agent2Agent (A2A) Protocol 1.0 agent. The agent is deterministic: it does not invoke an LLM, infer an operation from natural language, or promote remote agent text into executable state.

The implementation uses the official Python A2A SDK 1.x server interfaces and advertises one JSON-RPC 1.0 transport. Discovery is available through the standard Agent Card route.

## Start the agent

```bash
uv sync --extra dev
uv run e2h-a2a --root . --store .e2h/evidence.duckdb
```

By default the server listens on `127.0.0.1:41242` and advertises:

```text
http://127.0.0.1:41242/a2a/jsonrpc
```

Use `--public-url` when the externally reachable origin differs from the bind address, for example behind a reverse proxy:

```bash
uv run e2h-a2a \
  --root . \
  --store .e2h/evidence.duckdb \
  --host 0.0.0.0 \
  --port 41242 \
  --public-url https://verify.example/e2h
```

The public URL is used only for the Agent Card. It does not change the local route mount; a reverse proxy that adds a path prefix must map it accordingly.

## Message contract

Each `SendMessage` request must contain exactly one text or structured-data part representing a JSON object. Structured data is preferred. The object is validated with a strict operation-specific schema and has a maximum serialized size selected by the server operator.

Status:

```json
{
  "schema_version": "0.1",
  "operation": "status"
}
```

Verified experiment memory:

```json
{
  "schema_version": "0.1",
  "operation": "memory_query",
  "view": "sources",
  "limit": 10
}
```

Artifact verification:

```json
{
  "schema_version": "0.1",
  "operation": "verify_artifact",
  "artifact": ".e2h/result.json",
  "expected_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "min_bytes": 1,
  "max_bytes": 1048576
}
```

Snapshot verification:

```json
{
  "schema_version": "0.1",
  "operation": "verify_snapshot",
  "archive": ".e2h/environment.e2hsnap"
}
```

Every application-level reply is one structured A2A data message with this envelope:

```json
{
  "schema_version": "0.1",
  "operation": "verify_artifact",
  "ok": true,
  "result": {},
  "error": null,
  "response_sha256": "..."
}
```

`response_sha256` binds the canonical JSON representation of the response fields other than the digest itself. Operation-specific E2H results retain their own evidence digests, such as artifact SHA-256 values, snapshot identities, memory result digests, and replay result digests.

Malformed verification commands and expected verification failures are returned as `ok: false` application responses rather than being converted into opaque model-generated explanations. Unexpected internal failures return a generic error and are logged server-side.

## Capability discovery

The Agent Card always advertises status, artifact verification, and snapshot verification. It advertises memory queries only when the operator configured an existing E2H experiment store. It advertises replay only when replay was explicitly enabled and the selected server configuration has a supported replay path.

This matters because Agent Cards are capability declarations, not merely documentation: remote agents should not be told that E2H can execute replay when the local operator did not permit it. Remote replay uses either the handle-bound local path or the bounded read-only isolated-container path selected by the operator.

## Replay is opt-in

Replay executes capsule-declared commands and is disabled by default:

```bash
uv run e2h-a2a \
  --root . \
  --allow-replay \
  --backend auto
```

A replay request is then:

```json
{
  "schema_version": "0.1",
  "operation": "replay",
  "capsule": "examples/smoke/capsule.yaml",
  "workspace": "."
}
```

The remote A2A caller cannot select the execution backend, container-runtime binary, or isolated workspace limits. Those remain operator-side server settings. Replay output is digest-only by default; bounded stdout/stderr is included only when the operator also starts the server with `--expose-replay-output`.

A2A reuses the MCP verification service and therefore has the same replay semantics. Effective local replay is handle-bound on supported Linux hosts and executes against the caller workspace, so command mutations persist. Container replay first captures the requested workspace through descriptor-bound filesystem operations into a private temporary tree, then gives only that private path to the container runtime. The private tree is discarded after replay, so container mutations do not write back to the caller workspace.

Remote container replay accepts only capsules whose `sandbox.workspace_access` is `read_only`. The input snapshot is bounded by operator-selected byte and entry limits, but a writable bind mount could grow without bound after capture and consume host disk. A2A therefore fails closed for writable remote container workspaces until E2H has a quota-backed writable workspace primitive. The defaults are 100 MiB and 10,000 filesystem entries, configurable with `--max-replay-workspace-bytes` and `--max-replay-workspace-entries`.

Replay results explicitly report `execution_backend`, `workspace_mode`, and `workspace_mutations_persisted`. The replay digest binds those semantics together with the full E2H `RunResult`, so persistent local execution and discarded isolated execution cannot share a replay digest merely because command outputs happen to match.

Enabling remote replay is not equivalent to a universal sandbox guarantee. Local replay executes commands directly on the A2A host. Container replay applies the capsule's container sandbox policy plus the read-only isolated workspace boundary. Only enable replay for capsules and container images the operator is prepared to execute.

## Trust boundary

The A2A layer reuses the same root-bounded verification service as the MCP integration:

- filesystem paths must resolve inside the configured root;
- experiment memory uses predefined analytical views rather than caller-supplied SQL;
- artifact hashing has an operator hard byte limit independent of caller assertions;
- snapshot checks verify manifest structure, archive members, sizes, and content digests;
- replay is absent unless operator-enabled;
- supported local replay binds the workspace/check directory identities before process launch;
- supported container replay captures a bounded descriptor-bound private copy and mounts it read-only;
- writable remote container workspaces fail closed instead of exposing unbounded private-copy growth;
- the A2A response size is bounded to prevent large verification results from being copied automatically into another agent's context;
- local absolute root paths are scrubbed from expected verification errors before they are returned to a remote agent.

A2A peers are untrusted inputs. Agent Cards, messages, task metadata, and other remote content should never be treated as trusted instructions merely because they arrived over a standards-compliant transport.
