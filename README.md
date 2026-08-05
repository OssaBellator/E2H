# E2H — Evidence-to-Harness

E2H is an open-source capability flywheel for turning real AI-agent evidence into reproducible evaluations and validated harness improvements.

The repository now contains two connected vertical slices: deterministic **task capsule replay** and an observable **trace + replay-matrix layer**.

## What works today

- Strict JSON/YAML task capsule and experiment validation.
- Explicit command arguments with no shell interpolation.
- Capsule-declared working-directory boundary checks, including resolved symlink checks.
- Per-command timeouts, expected exit codes, fail-fast behavior, and bounded in-memory output capture.
- Normalized observable trace events for conversations, tools, artifacts, feedback, runs, and checks.
- Variant × repetition replay matrices with stable run IDs and per-variant reliability summaries.
- Atomic JSON experiment reports and deterministic JSONL trace evidence.
- CLI commands for validation, schema generation, replay, and experiments.
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

Each matrix cell receives stable `E2H_VARIANT_ID` and `E2H_REPETITION` environment variables. Results preserve the complete run report, while the JSONL trace contains only observable evidence—never hidden model reasoning.

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

Task capsules should be treated as code. The current runner verifies that capsule-declared working directories resolve within the selected workspace, avoids shell expansion, bounds retained output in memory, and terminates POSIX process groups on timeout. It does not restrict a command's filesystem access, provide OS-level isolation, or enforce the declared network policy. Run untrusted capsules only inside an external sandbox or disposable CI worker until sandbox backends land.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
