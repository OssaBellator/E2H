# E2H — Evidence-to-Harness

E2H is an open-source capability flywheel for turning real AI-agent evidence into reproducible evaluations and validated harness improvements.

This repository currently contains the first vertical slice: a deterministic **task capsule replay core**.

## What works today

- Strict JSON/YAML task capsule validation.
- Explicit command arguments with no shell interpolation.
- Capsule-declared working-directory boundary checks, including resolved symlink checks.
- Per-command timeouts, expected exit codes, fail-fast behavior, and bounded in-memory output capture.
- Structured JSON run reports suitable for CI and later optimization loops.
- A CLI for validation, schema generation, and replay.
- Unit tests, coverage enforcement, linting, type checks, and a smoke workflow.

## Quick start

```bash
uv sync --extra dev
uv run e2h validate examples/smoke/capsule.yaml
uv run e2h run examples/smoke/capsule.yaml --workspace . --output .e2h/result.json
```

Run all checks:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
uv run e2h run examples/smoke/capsule.yaml --workspace .
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

## Architecture direction

The replay core is the foundation for the broader loop:

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

Task capsules should be treated as code. The current runner verifies that capsule-declared working directories resolve within the selected workspace, avoids shell expansion, and bounds retained output in memory. It does not restrict a command's filesystem access, provide OS-level isolation, or enforce the declared network policy. Run untrusted capsules only inside an external sandbox or disposable CI worker until sandbox backends land.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
