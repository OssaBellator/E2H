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
{"schema_version":"0.1","operation":"status"}
```

Verified experiment memory:

```json
{"schema_version":"0.1","operation":"memory_query","view":"sources","limit":10}
```

Artifact verification:

```json
{"schema_version":"0.1","operation":"verify_artifact","artifact":".e2h/result.json"}
```

Snapshot verification:

```json
{"schema_version":"0.1","operation":"verify_snapshot","archive":".e2h/environment.e2hsnap"}
```

Every application-level reply is one structured A2A data message. `response_sha256` binds the canonical JSON representation of the response fields other than the digest itself. Operation-specific E2H results retain their own evidence digests.

Malformed verification commands and expected verification failures are returned as `ok: false` application responses rather than being converted into opaque model-generated explanations. Unexpected internal failures return a generic error and are logged server-side.

## Capability discovery

The Agent Card always advertises status, artifact verification, and snapshot verification. It advertises memory queries only when the operator configured an existing E2H experiment store. It advertises replay only when replay was explicitly enabled and the selected server configuration has a supported replay path.

Effective remote replay currently means the handle-bound local backend only. Docker-backed MCP/A2A replay remains fail-closed while the sealed-volume candidate is validated and the Docker control-plane deployment boundary is established.

## Replay is opt-in

Replay executes capsule-declared commands and is disabled by default:

```bash
uv run e2h-a2a \
  --root . \
  --allow-replay \
  --backend local
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

The remote A2A caller cannot select the execution backend, container-runtime binary, or Docker control-plane trust declaration. Those remain operator-side server settings. Replay output is digest-only by default; bounded stdout/stderr is included only when the operator also starts the server with `--expose-replay-output`.

A2A reuses the MCP verification service and therefore has the same replay semantics. Effective local replay is handle-bound on supported Linux hosts and executes against the caller workspace, so command mutations persist. Before either local workspace binding or future Docker work, the shared replay boundary also rejects capsules with more than 50 checks, more than 2,000,000 aggregate retained stdout/stderr characters at the capsule output ceiling, or more than 1,800 seconds of aggregate declared check timeout.

### Container replay candidate remains disabled

The current remote Docker candidate replaces the superseded mutable-host-path workspace design. It descriptor-captures the caller workspace into a bounded sealed Linux memfd tar, validates the tar before Docker import, copies it into a fresh Docker-managed volume, and mounts that volume read-only for retained-container replay. The candidate also applies the branch's Docker/runc/resource capability gates, immutable-image `VOLUME` rejection, explicit non-root identity policy, exact argv behavior, bounded writable-memory resources, inspected container state transitions, and fail-closed cleanup semantics.

Public A2A container replay is still disabled: the underlying isolated-container capability remains false. `--allow-replay --backend container` therefore does not become usable merely because the candidate code exists, and `auto` cannot launch a sandbox capsule through that path.

Before re-enablement, the committed real-Docker integration targets must pass on an actual supported Linux Docker daemon, and the deployment must independently establish the Docker API/socket as a trusted operator boundary or mediate it through a suitably narrow helper. A principal with unrestricted Docker control-plane authority can interfere with daemon-managed containers and volumes and is not an untrusted peer that this candidate claims to isolate from.

`--trusted-container-control-plane` is an operator **attestation** wired into the shared MCP service. It declares that the deployment has already established that control-plane boundary; E2H does not verify socket ACLs, daemon access, peer privileges, or surrounding workload isolation. The option remains default-off, is required before a future container replay can advance past backend selection, and does not override the current fail-closed capability gate.

Replay results explicitly report `execution_backend`, `workspace_mode`, and `workspace_mutations_persisted`. Effective A2A replay currently reports the persistent handle-bound local mode.

Enabling remote replay is not equivalent to sandboxing. The supported A2A replay path executes commands directly on the A2A host, so enable it only for capsules you are prepared to execute there. The normal direct trusted E2H CLI/library container runner remains separate from this remote fail-closed boundary.

## Trust boundary

The A2A layer reuses the same root-bounded verification service as the MCP integration:

- filesystem paths must resolve inside the configured root;
- experiment memory uses predefined analytical views rather than caller-supplied SQL;
- artifact hashing has an operator hard byte limit independent of caller assertions;
- snapshot checks verify manifest structure, archive members, sizes, and content digests;
- replay is absent unless operator-enabled;
- supported local replay binds the workspace/check directory identities before process launch;
- all replay backends enforce the shared host command/output/timeout budget before execution-specific work;
- remote container replay remains disabled pending real-daemon evidence and an operator-attested Docker control-plane boundary;
- the A2A response size is bounded to prevent large verification results from being copied automatically into another agent's context;
- local absolute root paths are scrubbed from expected verification errors before they are returned to a remote agent.

A2A peers are untrusted inputs. Agent Cards, messages, task metadata, and other remote content should never be treated as trusted instructions merely because they arrived over a standards-compliant transport.
