# E2H — Evidence-to-Harness Case Study

## Problem

AI-agent evaluations become difficult to trust when success depends on opaque transcripts, mutable workspaces, hidden state, or evaluation logic that cannot be replayed independently.

E2H turns observable evidence into versioned harnesses, deterministic checks, controlled experiments, and verifiable release artifacts.

## Constraints

- Hidden model reasoning is not an acceptable evidence dependency.
- Imported transcripts and traces may contain sensitive data.
- Untrusted evidence must not be able to smuggle executable commands into a trusted harness.
- Workspaces and benchmark fixtures must be reproducible enough to compare runs.
- Provider-specific APIs differ, but the verification contract should remain inspectable.
- Release integrity claims should be tied to concrete artifacts and provenance, not branding.

## Design decisions

1. **Observable evidence only.** E2H records events and artifacts; it does not try to capture or reconstruct hidden chain-of-thought.
2. **Content-addressed provenance.** Evidence, snapshots, and release artifacts use deterministic identities where practical.
3. **Review-gated compilation.** Imported evidence can propose a harness, but executable checks remain trusted declarations and require verification plus approval before materialization.
4. **Mutation-tested oracles.** Strong verification demonstrates that an oracle fails under its intended controlled regression.
5. **Typed experiment surfaces.** Variants and genomes constrain optimization changes instead of treating arbitrary source edits as an experiment API.
6. **Release integrity as another evidence problem.** Reproducible builds, SBOMs, manifests, provenance, and publication gates are part of the same verification philosophy.

## Implementation

Reviewer entry points:

- [`src/e2h/workspace_snapshot.py`](./src/e2h/workspace_snapshot.py) — deterministic workspace snapshots.
- [`src/e2h/runtime_plan.py`](./src/e2h/runtime_plan.py) — credential-free provider request planning.
- [`tests/test_compiler_boundaries.py`](./tests/test_compiler_boundaries.py) — representative compiler-boundary tests.
- [`docs/provider-runtime-conformance.md`](./docs/provider-runtime-conformance.md) — provider mapping/conformance.
- [`docs/release-integrity.md`](./docs/release-integrity.md) — release evidence model.
- [`.github/workflows/release-integrity.yml`](./.github/workflows/release-integrity.yml) — release-integrity CI.
- [`.github/workflows/privacy-ci.yml`](./.github/workflows/privacy-ci.yml) — privacy-policy CI.

The project covers replay, evidence ingestion, privacy review, harness compilation, workspace snapshots, experiment storage, typed optimization, provider runtimes, MCP/A2A verification surfaces, capture clients, community benchmarks, and release integrity.

## Verification

The public proof card was captured from an actual green GitHub Actions `main` run on 2026-10-03.

Observed Python 3.13 job result:

- **2035 passed**;
- **49 warnings**;
- **33.66 seconds**;
- workflow conclusion: **success**.

The repair that restored the suite was merged in PR #371. The repair reduced Ruff findings from 90 to 0, restored Ruff format and Linux mypy, kept dependency audit green, and reduced behavioral failures through successive fixes from 30 to 0 rather than weakening the stricter fail-closed behavior.

At the same verification point, `main` was checked green across the Python 3.11/3.12/3.13 CI jobs plus Provider Ingestion, Privacy Policy, Locked Dependency Audit, Release Integrity, Experiment Store, Capture Clients, and CodeQL.

## Limitations

- A valid capsule or release artifact proves the defined evidence relationship; it is not a general-purpose security sandbox.
- Candidate code can still be malicious.
- Privacy detectors reduce exposure risk but cannot prove complete de-identification.
- Hashes establish byte identity, not trustworthiness.
- Release attestations establish provenance/integrity relationships, not absence of vulnerabilities.
- A production release tag remains a separate external publication event and is intentionally not created by pull-request CI.

## What this demonstrates for a client

This project demonstrates reproducible evaluation engineering, evidence provenance, privacy-aware ingestion, deterministic harnesses, adversarial verification, provider integration, CI repair without weakening safety semantics, and supply-chain/release integrity work.