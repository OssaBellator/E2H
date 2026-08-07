# Contributing

E2H accepts focused contributions that preserve its core evidence rule: externally visible claims should be backed by observable, reproducible, or content-addressed artifacts rather than hidden reasoning or implicit state.

## Development setup

Use Python 3.11 or newer and install the locked development environment with `uv`:

```bash
uv sync --extra dev
```

Run the core local checks before opening a pull request:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

The repository additionally runs dedicated CI for provider ingestion, privacy-policy behavior, the experiment store, capture clients, and release integrity. A pull request is not ready to merge while any relevant permanent suite is failing.

## Contribution shape

Prefer small, coherent branches and pull requests. Each behavioral change should include tests that demonstrate both the intended behavior and important rejection/failure cases.

When changing a public artifact or protocol:

- preserve backwards compatibility within an existing schema version, or introduce an explicit new schema version;
- keep additive provider payload handling separate from strict core identity/ordering requirements;
- update generated JSON Schema behavior and documentation when the public contract changes;
- avoid introducing shell interpolation where E2H currently uses explicit argument vectors;
- keep observable evidence separate from hidden model reasoning or opaque provider internals.

## Generated and content-addressed artifacts

Do not hand-edit generated identities merely to make a test pass.

Examples include:

- `uv.lock`;
- benchmark environment locks;
- snapshot/release digests;
- partition/public dataset identities;
- release manifests and canonical SBOM output.

Regenerate them using the corresponding E2H or `uv` command, then review the resulting diff. A generated digest changing unexpectedly is evidence to investigate, not formatting noise.

## Tests for security-sensitive changes

Changes to execution, path handling, archive processing, redaction, permissions, protocol boundaries, capture clients, sandbox configuration, or release workflows require explicit adversarial tests.

Useful rejection cases include:

- absolute paths, parent traversal, symlink escapes, and raced filesystem changes;
- duplicate IDs/keys, malformed ordering, unsupported archive members, and oversized inputs;
- residual secrets/PII and unsafe review/report output;
- stale or mismatched content digests and provenance identities;
- credential-bearing jobs executing repository-controlled code;
- mutable third-party action references in supply-chain workflows;
- missing/extra release artifacts and tampered handoffs.

See [`SECURITY.md`](SECURITY.md) for vulnerability reporting. Do not put active exploit details or sensitive evidence into a public pull request merely to demonstrate a security problem.

## Privacy-sensitive benchmark contributions

Sanitized real-world benchmark patterns require public provenance, a sanitization attestation, detector-backed review, and human inspection. Do not copy raw issue logs, usernames, local paths, tokens, emails, phone numbers, or other unnecessary source identifiers into the benchmark corpus.

Synthetic benchmark fixtures should remain clearly labeled as synthetic rather than being presented as real-world evidence.

## Dependency and GitHub Action updates

Dependabot monitors two independent update streams in `.github/dependabot.yml`:

- the `uv` ecosystem for `pyproject.toml` and `uv.lock`;
- the `github-actions` ecosystem for external workflow actions.

Python dependency pull requests must preserve the locked workflow. If a dependency change affects `pyproject.toml`, the corresponding reviewed `uv.lock` change must be present and all release-integrity/SBOM checks must pass.

External GitHub Actions remain pinned to full immutable commit SHAs in every permanent workflow. Dependabot can propose updates to SHA-pinned actions and their same-line release comments, but `tests/test_workflow_action_pins.py` deliberately contains the currently reviewed commit map. A bot PR that advances an action is expected to remain red until a maintainer:

1. reviews the upstream release or security-fix commit;
2. confirms the proposed SHA is the intended immutable revision;
3. updates the workflow pin and its version/security context comment;
4. updates the reviewed-pin map in the policy test;
5. runs all relevant permanent suites.

`actions/setup-node` is temporarily excluded from automated action updates because E2H currently pins a reviewed post-v7.0.0 security-fix commit that is not a tagged release. Re-enable automated updates for it only after a reviewed tagged release contains that fix.

## Supply-chain workflow changes

The tag-only publication workflow must not be exercised from a pull request by creating a real release tag. Normal CI validates release policy, reproducible builds, manifests, canonical SBOMs, and workflow action pins without publishing externally.

Supply-chain workflow changes must preserve job-scoped permissions, immutable action pins, checksum-bound artifact handoffs, and the separation between repository-controlled build code and OIDC-bearing publication/attestation jobs.

## Documentation

Update the README or focused docs when a contribution changes a user-visible command, security boundary, benchmark contract, integration capability, or release procedure.

Keep limitations explicit. Hashes establish byte identity, attestations establish provenance/integrity relationships, and redaction detectors reduce exposure risk; none of those mechanisms proves that arbitrary content or code is trustworthy.
