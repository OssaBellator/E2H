# E2H — Evidence-to-Harness

E2H is an open-source toolkit for turning observable AI-agent evidence into reproducible evaluations, controlled harness experiments, and verifiable release artifacts.

The project is built around a simple rule: **claims should be backed by replayable or content-addressed evidence rather than hidden reasoning or unverifiable state**. E2H therefore keeps execution, evidence capture, optimization, benchmark data, and release integrity explicit and machine-checkable.

Python 3.11, 3.12, and 3.13 are exercised in CI.

## What E2H provides

| Area | Capabilities |
| --- | --- |
| Replay | Versioned task capsules, deterministic command grading, bounded output capture, direct/container execution, variant × repetition matrices |
| Evidence | Canonical transcripts, OTLP/HTTP traces, OpenAI Responses, Anthropic Messages, Gemini GenerateContent, corrections, privacy redaction/review, content-addressed provenance |
| Harness compilation | Review-gated capsule proposals, controlled mutation verification, file/JSON/artifact oracles, workspace snapshots, human approval before materialization |
| Optimization | Typed harness genomes and patches, prompt/tool/context/routing/workflow variants, DSPy/GEPA adapters, train/validation/sealed-test partitions, promotion/rollback gates |
| Frontier integrations | OpenAI Responses, Anthropic Messages, and Gemini GenerateContent runtime adapters; credential-free request planning; MCP verification server; A2A verification agent; browser and VS Code capture clients |
| Community benchmark | Sanitized real-world failure patterns, long-horizon correction/constraint tasks, reproducible coding/research/browser environments |
| Distribution integrity | Reproducible wheel/sdist builds, deterministic release manifests, canonical CycloneDX runtime SBOMs, OIDC PyPI publication, provenance/SBOM attestations, immutable GitHub releases |

E2H records **observable events and artifacts only**. It does not attempt to capture or reconstruct hidden model chain-of-thought.

## Install from a source checkout

E2H uses [`uv`](https://docs.astral.sh/uv/) for its development and locked-environment workflow.

```bash
git clone https://github.com/OssaBellator/E2H.git
cd E2H
uv sync --extra dev
uv run e2h --help
```

The project requires Python 3.11 or newer.

## Quick start

Validate and replay the smoke capsule:

```bash
uv run e2h validate examples/smoke/capsule.yaml
uv run e2h run examples/smoke/capsule.yaml \
  --workspace . \
  --output .e2h/result.json
```

Run a variant × repetition experiment:

```bash
uv run e2h experiment validate examples/matrix/experiment.yaml
uv run e2h experiment run examples/matrix/experiment.yaml \
  --root . \
  --output .e2h/matrix.json \
  --traces .e2h/matrix.jsonl \
  --require-all-pass
```

Import and privacy-review a transcript:

```bash
uv run e2h ingest transcript examples/ingest/transcript.json \
  --redaction-policy examples/ingest/redaction-policy.yaml \
  --redaction-report .e2h/redaction-review.json \
  --output .e2h/transcript-bundle.json \
  --traces .e2h/transcript.jsonl
```

Compile sanitized evidence into a review-gated capsule:

```bash
uv run e2h compile proposal \
  .e2h/transcript-bundle.json \
  examples/compile/spec.yaml \
  --output .e2h/compiler-proposal.json

uv run e2h compile verify \
  .e2h/compiler-proposal.json \
  --workspace . \
  --output .e2h/compiler-verification.json \
  --require-strong

uv run e2h compile review \
  .e2h/compiler-proposal.json \
  --reviewer maintainer \
  --decision approve \
  --output .e2h/compiler-approved.json

uv run e2h compile materialize \
  .e2h/compiler-approved.json \
  .e2h/compiler-verification.json \
  --output .e2h/compiled-capsule.yaml
```

Create and verify a content-addressed workspace snapshot:

```bash
uv run e2h snapshot create examples .e2h/examples.e2hsnap \
  --include compile \
  --include ingest
uv run e2h snapshot verify .e2h/examples.e2hsnap
```

## Replay model

A task capsule declares the initial working directory, allowed tool/network boundary, resource limits, and deterministic success checks.

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

Commands are executed as argument vectors; E2H intentionally does not invoke a shell. Working-directory and path checks resolve symlinks before execution. Output capture and time/resource limits are bounded.

For untrusted workloads, use the container backend or an external sandbox appropriate to the threat model. A valid capsule describes the intended execution contract; it does not make arbitrary candidate code safe.

## Observable evidence

E2H normalizes visible messages, tool calls/results, traces, artifacts, feedback, runs, and checks into an observable trace model.

Supported evidence inputs include:

- canonical transcript JSON;
- OTLP/HTTP JSON traces;
- archived OpenAI Responses API request/response evidence;
- archived Anthropic Messages API evidence;
- archived Gemini GenerateContent evidence.

Provider adapters retain visible messages, tool/function activity, status, usage, and selected provider metadata. Hidden reasoning payloads, encrypted reasoning content, binary media bodies, and equivalent opaque internals are deliberately excluded.

### Corrections

Canonical transcript corrections are explicit rather than inferred from sentiment:

```json
{
  "schema_version": "0.1",
  "id": "conversation-123",
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

A correction must come from a user message and reference an earlier assistant message.

## Privacy and provenance

Evidence ingestion applies a configurable redaction policy for secrets, email addresses, phone numbers, trusted custom regular expressions, and exact allowlists.

Redaction review artifacts retain classes, rules, safe JSON-pointer locations, digests, counts, and warnings without reproducing raw matched values. `--no-redact` is a review-only mode for trusted local workflows; it preserves source evidence and reports residual sensitive-looking patterns rather than silently declaring the input safe.

Every ingestion bundle records content-addressed source provenance without writing absolute local filesystem paths.

Pattern matching cannot prove complete de-identification. Evidence intended to leave its original trust boundary should still receive human review.

## Capsule compiler and deterministic oracles

The compiler converts sanitized evidence plus trusted human-authored checks into an immutable capsule proposal.

A proposal cannot become an executable capsule merely because imported text suggests a command. Executable checks remain explicit trusted declarations. Materialization requires:

1. a matching immutable proposal;
2. baseline verification;
3. declared mutation probes that demonstrate the oracle detects controlled regressions;
4. an approval review bound to the same proposal and verification evidence.

Declarative oracle templates cover:

- file presence/absence, exact text, contained text, and SHA-256;
- RFC 6901 JSON-pointer equality/presence/absence;
- artifact size and SHA-256 constraints.

Generated operator-specific mutations make strong verification prove that each oracle fails under its intended regression.

## Snapshots and experiment store

Workspace snapshots are deterministic ZIP bundles with a canonical manifest, sorted path entries, executable bits, byte sizes, and content-addressed blobs. Creation/restoration reject unsafe paths, symlinks, duplicate or traversal-style archive members, and configured size limits.

Replay results can be persisted into the DuckDB/Parquet experiment store:

```bash
uv run e2h store init .e2h/evidence.duckdb
uv run e2h store ingest \
  .e2h/evidence.duckdb \
  .e2h/result.json \
  .e2h/matrix.json
uv run e2h store query .e2h/evidence.duckdb variants --json
uv run e2h store export \
  .e2h/evidence.duckdb \
  .e2h/runs.parquet \
  --view runs
```

The analytical store does not retain raw command output.

## Controlled optimization

E2H represents harness changes as typed variants and genomes rather than arbitrary source edits. Supported optimization surfaces include prompt, tool, context, routing, and workflow changes.

The optimization layer provides:

- typed genome/patch validation;
- DSPy and GEPA adapter artifacts;
- content-addressed train/validation/sealed-test partitions;
- label-free public sealed-test identities;
- aggregate-only sealed evaluation reports;
- statistical promotion gates and rollback metadata.

Use the installed command groups for the full schemas and workflows:

```bash
uv run e2h variant --help
uv run e2h genome --help
uv run e2h optimizer --help
uv run e2h partition --help
uv run e2h promotion --help
```

## Frontier integrations

### Provider runtimes

E2H executes the same verified typed harness contract through three live provider adapters while preserving provider-native request/result evidence:

- [OpenAI Responses](docs/openai-responses-runtime.md);
- [Anthropic Messages](docs/anthropic-messages-runtime.md);
- [Gemini GenerateContent](docs/gemini-generate-content-runtime.md).

All three runtimes are single-turn adapters: they map the provider-neutral prompt, context, routing, and custom-tool contract to the provider API, fail closed where a mapping cannot be represented faithfully, and never execute returned tool/function calls implicitly.

The [runtime request planner](docs/runtime-request-planning.md) materializes the exact provider request and deterministic request digest without reading credentials or opening a network connection.

See the installed runtime commands with:

```bash
uv run e2h runtime --help
```

### MCP

`e2h-mcp` exposes verified memory queries and artifact/snapshot checks. Replay is operator-gated rather than enabled implicitly.

```bash
uv run e2h-mcp --help
```

### A2A

`e2h-a2a` exposes deterministic verification operations through the A2A protocol. Replay capability is advertised only when enabled by the operator.

```bash
uv run e2h-a2a --help
```

### Capture clients

The browser Manifest V3 extension and VS Code extension capture only explicit user selections and export local E2H capture envelopes with per-selection SHA-256 digests. They do not perform background harvesting.

Validate exported captures with:

```bash
uv run e2h capture --help
```

## Community benchmark

E2H ships three complementary benchmark surfaces.

### Sanitized failure patterns

`benchmarks/failure-patterns/v0.1.json` contains paraphrased public failure reports mapped to E2H's failure taxonomy. Sanitized real-world claims require public-source provenance plus an explicit sanitization attestation and detector-backed privacy review.

```bash
uv run e2h benchmark validate \
  benchmarks/failure-patterns/v0.1.json
```

See [`docs/community-benchmark.md`](docs/community-benchmark.md).

### Long-horizon correction and retention

The long-horizon benchmark keeps machine-readable constraint updates private while exporting candidate-visible dialogue/probes with a separate public digest. Corrections and revocations explicitly supersede the active update they replace. Evaluation returns aggregate scores rather than expected labels.

```bash
uv run e2h benchmark long-horizon validate \
  benchmarks/long-horizon/v0.1.json
```

See [`docs/long-horizon-benchmark.md`](docs/long-horizon-benchmark.md).

### Reproducible environments

The coding, research, and browser benchmark environments are content-locked local source trees. Coding/research fixtures require no network; the browser fixture is localhost-only.

```bash
uv run e2h benchmark environments verify \
  benchmarks/environments/suite.json \
  benchmarks/environments/suite.lock.json \
  --root .
```

See [`docs/benchmark-environments.md`](docs/benchmark-environments.md).

## Release and supply-chain integrity

E2H applies the same verification principle to its own distributions.

A release build is created twice from independent clean `git archive` source copies. CI requires byte-identical wheel and sdist artifacts, seals build A into a deterministic release manifest, and verifies build B against that manifest.

The runtime dependency graph is exported from `uv.lock` as CycloneDX 1.5. Because raw CycloneDX generation includes optional per-generation UUID/timestamp fields, E2H validates the document, removes only those fields, and publishes canonical JSON whose dependency/tool/component data is byte-reproducible.

The tag-only production workflow is designed to provide:

- OIDC-only PyPI Trusted Publishing, with no stored PyPI token;
- SLSA provenance for the exact wheel/sdist;
- a CycloneDX SBOM attestation bound to those distributions;
- GitHub-generated release notes;
- draft release staging before PyPI publication;
- immutable GitHub release publication only after PyPI succeeds;
- native GitHub release/asset attestation verification after publication.

A release tag is a real external publication event and is intentionally not created by pull-request CI.

Operational documentation:

- [`docs/release-integrity.md`](docs/release-integrity.md)
- [`docs/trusted-publishing.md`](docs/trusted-publishing.md)
- [`docs/immutable-releases.md`](docs/immutable-releases.md)

## Security boundaries

E2H is designed to fail closed around common evidence and filesystem hazards, but it is not a general-purpose security sandbox.

Important boundaries:

- untrusted candidate code can still be malicious;
- custom privacy regexes are trusted code-like configuration and can be expensive or over-broad;
- redaction detectors reduce exposure risk but cannot prove de-identification;
- hashes prove byte identity, not that bytes are trustworthy;
- release attestations prove provenance/integrity relationships, not absence of vulnerabilities;
- network/identity/container restrictions depend on the selected runtime and host enforcement.

See [`SECURITY.md`](SECURITY.md) for vulnerability reporting.

## Development

Install development dependencies:

```bash
uv sync --extra dev
```

Run the core local checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

The repository also has dedicated CI for provider ingestion, privacy policy behavior, experiment-store behavior, capture clients, and release integrity. Security-sensitive changes require adversarial tests for the boundary being changed.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request.

## Project status

The implementation roadmap through distribution integrity is complete. [`ROADMAP.md`](ROADMAP.md) records the delivered milestones and remains the reference for any future explicitly scoped milestone work.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
