# E2H — Evidence-to-Harness

E2H is an open-source capability flywheel for turning real AI-agent evidence into reproducible evaluations and validated harness improvements.

The repository now contains seven connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, declarative **file, JSON, and artifact oracles**, content-addressed **workspace snapshots**, and an optional **container sandbox backend**.

## What works today

- Strict JSON/YAML task capsule and experiment validation.
- Explicit command arguments with no shell interpolation.
- Capsule-declared working-directory boundary checks, including resolved symlink checks.
- Per-command timeouts, expected exit codes, fail-fast behavior, and bounded in-memory output capture.
- Normalized observable trace events for conversations, messages, spans, tools, artifacts, feedback, runs, and checks.
- Variant × repetition replay matrices with stable run IDs and per-variant reliability summaries.
- Canonical transcript JSON and OTLP/HTTP JSON ingestion.
- Explicit user-correction capture linked to earlier assistant messages.
- Default-on secret, email, and phone redaction with stable non-reversible placeholders.
- Content-addressed source provenance without exposing local filesystem paths.
- Immutable capsule proposals with evidence references and stable proposal IDs.
- Controlled mutation verification plus human approval/rejection gates.
- Capsule materialization only after matching review and verification evidence.
- Declarative file, RFC 6901 JSON, and artifact digest/size oracle templates.
- Automatic operator-specific oracle mutations for strong verification.
- Deterministic content-addressed workspace and artifact snapshot bundles.
- Snapshot verification, safe restoration, and portable compiler references.
- Optional immutable-image container execution with filesystem, network, user, and resource controls.
- Backend selection for direct replay, matrices, and compiler mutation verification.
- Atomic JSON reports and deterministic JSONL trace evidence.
- CLI commands for validation, replay, experiments, and evidence ingestion.
- Unit tests, coverage enforcement, linting, strict type checks, and end-to-end CI smoke workflows.

## Quick start

```bash
uv sync --extra dev
uv run e2h validate examples/smoke/capsule.yaml
uv run e2h run examples/smoke/capsule.yaml --workspace . --output .e2h/result.json
uv run e2h experiment validate examples/matrix/experiment.yaml
uv run e2h experiment run examples/matrix/experiment.yaml \
  --root . \
  --output .e2h/matrix.json \
  --traces .e2h/matrix.jsonl \
  --require-all-pass
uv run e2h ingest transcript examples/ingest/transcript.json \
  --output .e2h/transcript-bundle.json \
  --traces .e2h/transcript.jsonl
uv run e2h ingest otlp examples/ingest/otlp.json \
  --output .e2h/otlp-bundle.json \
  --traces .e2h/otlp.jsonl
uv run e2h compile proposal .e2h/transcript-bundle.json examples/compile/spec.yaml \
  --output .e2h/compiler-proposal.json
uv run e2h compile verify .e2h/compiler-proposal.json \
  --workspace . --output .e2h/compiler-verification.json --require-strong
uv run e2h compile review .e2h/compiler-proposal.json \
  --reviewer maintainer --decision approve --output .e2h/compiler-approved.json
uv run e2h compile materialize .e2h/compiler-approved.json \
  .e2h/compiler-verification.json --output .e2h/compiled-capsule.yaml
uv run e2h snapshot create examples .e2h/examples.e2hsnap \
  --include compile --include ingest
uv run e2h snapshot verify .e2h/examples.e2hsnap
uv run e2h snapshot restore .e2h/examples.e2hsnap .e2h/restored-examples
uv run e2h run examples/sandbox/capsule.yaml --backend container --workspace .
```

Run all checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

## Capsule example

```yaml
schema_version: "0.1"
id: billing-regression
goal: Preserve the legacy billing response contract.
initial_state:
  working_directory: .
allowed_actions:
  tools: [command]
  network: deny
limits:
  max_commands: 10
  default_timeout_seconds: 30
  max_output_chars: 20000
success:
  commands:
    - id: tests
      argv: [python, -m, pytest, -q]
    - id: contract
      argv: [python, scripts/check_contract.py]
```

Commands are executed directly as argument vectors. E2H intentionally does not invoke a shell.

## Experiment example

```yaml
schema_version: "0.1"
id: billing-harness-comparison
capsule: benchmarks/billing/capsule.yaml
workspace: .
repetitions: 5
variants:
  - id: baseline
    env:
      HARNESS_PROFILE: baseline
  - id: verify-first
    env:
      HARNESS_PROFILE: verify-first
    metadata:
      change: require-artifact-verification
```

Environment overlays are the first typed variant mechanism. They make the matrix immediately useful for command-driven harnesses while leaving room for prompt, tool, context, routing, and workflow variant types in later schema versions.

Each matrix cell receives protected `E2H_VARIANT_ID` and `E2H_REPETITION` environment variables. Results preserve the complete run report, while the JSONL trace contains only observable evidence—never hidden model reasoning.

## Transcript ingestion

The canonical transcript format is intentionally provider-neutral:

```json
{
  "schema_version": "0.1",
  "id": "conversation-123",
  "capsule_id": "billing-regression",
  "messages": [
    {
      "id": "m1",
      "role": "assistant",
      "content": "The migration passed.",
      "timestamp": "2026-08-05T12:00:00Z"
    },
    {
      "id": "m2",
      "role": "user",
      "content": "That is incorrect; the contract test failed.",
      "timestamp": "2026-08-05T12:00:01Z",
      "correction_of": "m1"
    }
  ]
}
```

Corrections must be explicit, must come from a user message, and must reference an earlier assistant message. This avoids unreliable sentiment or phrase heuristics.

## Privacy and provenance

Ingestion redacts recognized secrets, email addresses, and phone numbers by default. Placeholders include a truncated SHA-256 digest, so repeated values remain comparable without storing the original value. Redaction records contain only the class, safe JSON-pointer location, digest, and placeholder.

Every ingestion bundle records the source filename, byte length, SHA-256 content hash, importer format, and whether redaction was enabled. Absolute source paths are never written to the bundle. Use `--no-redact` only for trusted local workflows where raw evidence is intentionally retained.

The OTLP importer accepts OTLP/HTTP JSON `resourceSpans`, preserves resource, scope, span, event, status, and parent identifiers, and retains nanosecond ordering even though normalized timestamps use Python's microsecond-resolution datetime representation.

## Capsule compilation

The compiler turns a sanitized ingestion bundle plus a human-authored compiler specification into an immutable proposal. Evidence may supply the goal and provenance, but executable checks and mutation probes remain explicit trusted declarations; imported text is never silently promoted into a command.

Each proposal ID is the SHA-256 digest of its immutable core. Verification binds to that exact capsule and ordered mutation plan. A strong report requires the baseline capsule to pass and every declared controlled environment mutation to make the oracle fail. Human reviews are append-only, and the latest approval or rejection determines whether materialization is allowed.

`e2h compile materialize` rejects stale or mismatched reports, weak verification, and unapproved proposals by default. Mutation verification executes the proposed commands, so it has the same security boundary as ordinary task capsule replay and should run in an external sandbox for untrusted workloads.

## Declarative oracles

Compiler specifications may declare `file`, `json`, and `artifact` oracles alongside command checks. Oracles are compiled into ordinary bounded `CommandCheck` entries that execute without shell interpolation, so materialized capsules remain compatible with the replay runner and trace model.

File oracles support presence, absence, exact UTF-8 text, contained text, and SHA-256 checks. JSON oracles use RFC 6901 pointers with equality, presence, and absence modes. Artifact oracles enforce byte-size bounds and optional SHA-256 digests. All paths remain relative, reject parent traversal, and are resolved against the check working directory to prevent symlink escapes.

By default, each oracle receives a generated mutation probe. Presence checks are inverted, JSON equality values are structurally changed, and content/artifact checks receive a digest mismatch. Strong verification therefore proves that the baseline passes and every declared oracle detects its operator-specific regression. Set `auto_mutate_oracles: false` only when mutations are supplied by another trusted workflow.

## Workspace snapshots

`e2h snapshot create` records selected workspace or artifact trees as deterministic ZIP bundles. A canonical `manifest.json` contains sorted directory and file entries, executable bits, byte lengths, and SHA-256 digests; identical file content is stored once under `blobs/<sha256>`. Fixed archive timestamps, modes, and member ordering make equivalent snapshots byte-for-byte reproducible.

Creation rejects symbolic links, special filesystem entries, unsafe include paths, and configured entry or byte limits. Verification rejects duplicate, unexpected, oversized, or traversal-style archive members and recomputes every blob digest. Restoration first verifies the complete archive, writes into a sibling staging directory, and atomically moves the result into a new or empty destination. It never extracts ZIP member paths directly.

`e2h snapshot reference` emits a portable `SnapshotReference` containing the manifest-derived snapshot ID, archive SHA-256, locator, and workspace/artifact role. Compiler specifications may attach these references under `snapshots`; they become immutable `e2h_compiler.snapshots` metadata and therefore participate in capsule and proposal identity without embedding archive bytes in the proposal.

Default creation excludes `.git`, `.venv`, `.e2h`, Python caches, and bytecode. Supplying `--exclude` replaces that default list, so trusted workflows can define an explicit capture policy.

## Container sandbox

Capsules may declare a `sandbox` policy with an immutable `name@sha256:<digest>` image. The default `auto` backend selects container execution when that policy is present and otherwise preserves the existing local runner. Operators may explicitly choose `--backend local` or `--backend container`; replay matrices and compiler mutation verification expose the same selection. `--container-runtime` is a trusted-administrator override for a Docker-compatible CLI binary.

The Docker adapter invokes the runtime directly as an argument vector—never through a shell. It bind-mounts the selected workspace read-only by default, uses `/workspace` as the container root, maps capsule working directories into that mount, disables networking when `allowed_actions.network` is `deny`, drops all Linux capabilities, sets `no-new-privileges`, requires a non-root numeric user, bounds PIDs, memory, CPUs, and `/tmp`, and makes the image root filesystem read-only by default. Workspace write access and bridge networking require explicit capsule declarations.

Each container run uses a private CID file. When the attached runtime process exceeds the command timeout, E2H terminates that client process and then force-removes the recorded container. Cleanup failures are promoted to infrastructure errors rather than hidden behind an ordinary timeout result.

The Docker daemon, runtime binary, image registry, and host kernel remain trusted infrastructure. Do not allow untrusted capsule authors to choose the runtime binary or Docker socket. An immutable image reference prevents tag drift but does not establish that the image itself is safe; curate and scan permitted images separately.

## Architecture direction

```text
observable evidence
  -> privacy-safe trace normalization
  -> executable task capsules
  -> counterfactual replay matrix
  -> structured failure diagnosis
  -> typed harness patches
  -> sealed promotion gates
  -> MCP / A2A / API runtime bundles
```

See [`ROADMAP.md`](ROADMAP.md) for planned milestones.

## Security model

Task capsules should be treated as code. The local runner verifies that capsule-declared working directories resolve within the selected workspace, avoids shell expansion, bounds retained output in memory, and terminates POSIX process groups on timeout, but it does not provide OS-level isolation or enforce network policy. The optional container backend adds declared filesystem, network, identity, and resource controls; its Docker daemon, image supply chain, and host kernel remain trusted boundaries. Use disposable workers and curated immutable images for untrusted capsules.

Evidence importers parse data rather than execute it, but imported content can still contain sensitive or adversarial text. Redaction is pattern-based and cannot guarantee removal of every possible identifier. Review sanitized evidence before publishing it or using it outside the original trust boundary.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
