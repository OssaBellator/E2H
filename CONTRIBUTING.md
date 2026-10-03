# Contributing

E2H treats evaluation evidence, replayability, privacy boundaries, and release integrity as part of the product—not as optional test scaffolding.

## Expectations

- Preserve observable-evidence semantics; do not introduce hidden-chain-of-thought capture or reconstruction.
- Keep runtime/provider inputs strictly revalidated at trust boundaries.
- Preserve fail-closed filesystem, snapshot, store, and replay behavior when identity or containment becomes uncertain.
- Add or update focused tests when changing validation, provider ingestion, privacy, snapshot, promotion, store, or replay behavior.
- Prefer stronger validation and explicit error messages over silently normalizing ambiguous state.

## Local quality gate

The repository pins its toolchain. Use the project lock rather than substituting arbitrary tool versions.

```sh
uv sync --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

CI exercises Python 3.11, 3.12 and 3.13, plus provider-ingestion, privacy-policy, release-integrity, experiment-store, dependency-audit, and CodeQL workflows.

Security-sensitive reports should follow [SECURITY.md](./SECURITY.md) rather than a public issue.
