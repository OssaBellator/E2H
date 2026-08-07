# Release toolchain evidence

E2H release manifests can bind distribution bytes to the reviewed toolchain context used to construct them. Production release workflows store this record under `metadata.release_toolchain` in the existing release-manifest schema.

The record includes:

- `source_commit`: the 40-character commit identity supplied by the trusted workflow;
- `runner_generation`: the GitHub-hosted runner generation, currently `ubuntu-24.04`;
- `python_version`: the Python patch observed by E2H at seal time;
- `uv_required_version`: the exact version required by `uv.toml`;
- `build_backend` and `build_backend_version`: the exact Hatchling backend required by `pyproject.toml`;
- `source_date_epoch`: the deterministic build epoch supplied by the workflow;
- SHA-256 identities for `uv.toml`, `pyproject.toml`, `uv.lock`, and `build-constraints.txt`.

Because manifest metadata is part of E2H's canonical manifest digest, changing any recorded toolchain field changes `manifest_sha256`. The production release flow also includes `release-manifest.json` in its release checksum file and immutable GitHub Release assets, so downstream release attestations bind the resulting manifest bytes.

## Inspecting a manifest

Use:

```bash
e2h release inspect release-manifest.json --json
```

The `metadata.release_toolchain` object describes both repository-controlled toolchain inputs and workflow context recorded at build time.

## Verifying repository-controlled toolchain inputs

Given an unpacked source tree for the release, run:

```bash
e2h release verify-toolchain release-manifest.json ./source --json
```

The verifier fails closed unless the manifest contains a valid `metadata.release_toolchain` object. It then:

- requires `uv.toml`, `pyproject.toml`, `uv.lock`, and `build-constraints.txt` to be bounded regular files rather than symlinks;
- recomputes all four SHA-256 identities;
- parses the exact uv version required by `uv.toml`;
- parses the exact Hatchling backend/version required by `pyproject.toml`;
- requires the Hatchling entry in `build-constraints.txt` to agree with `pyproject.toml`;
- compares those observed values with the manifest evidence.

This verification does not depend on the verifier's current Python patch. `python_version` in the manifest describes the release builder and is not compared with the consumer's interpreter.

If an independent source tells you the expected commit identity, bind that assertion too:

```bash
e2h release verify-toolchain \
  release-manifest.json \
  ./source \
  --expected-source-commit 0123456789abcdef0123456789abcdef01234567 \
  --json
```

With that option, `source_commit_verified` is true only when the supplied full lowercase SHA exactly matches the manifest. Without the option, source-controlled toolchain inputs can still verify, but `source_commit_verified` remains false because E2H does not execute Git or infer repository history from a directory.

A successful JSON proof contains the validated manifest evidence together with `source_inputs_verified: true`, `source_commit_verified`, and `verified: true`.

Production release CI performs this check automatically against `source-b`, an independently created `git archive HEAD` copy. The manifest itself is sealed from `source-a`. The resulting `release-toolchain-verification.json` is included in `release-checksums.txt` and shipped as an immutable GitHub Release asset alongside the manifest and inspection report.

## Trust boundary

This evidence establishes identities and relationships; it is not a general trust verdict.

- `runner_generation` identifies Ubuntu 24.04 as the selected hosted-runner generation, not an exact GitHub runner image build or VM byte identity.
- `python_version` records the builder interpreter observed at seal time. Consumer source verification deliberately does not claim to derive that value from source files.
- `source_date_epoch` is workflow-supplied build context, not a property that can be recovered from the four toolchain files.
- `source_commit` is workflow-supplied provenance. `verify-toolchain` checks it only when the consumer supplies `--expected-source-commit`; establishing that the source directory itself came from that commit remains the responsibility of the source/archive provenance mechanism.
- `uv_required_version` records the repository requirement. CI separately enforces that `setup-uv` discovers and installs that exact version.
- The Hatchling version and build-constraint digest identify the reviewed isolated build graph. Release CI separately enforces hashed build constraints and PEP 517 isolation.
- The runtime lock digest identifies `uv.lock`; the canonical CycloneDX SBOM separately describes the runtime dependency graph shipped with the release.
- GitHub attestations provide workflow/publisher provenance. The E2H manifest and toolchain verification report do not replace those attestations or claim that a dependency, runner, source archive, or workflow is intrinsically trustworthy.

Production workflows require the full toolchain evidence tuple when sealing release artifacts. The CLI still permits manifests without toolchain metadata for backwards compatibility with schema `0.1` and for non-production/local use. `verify-toolchain` intentionally rejects those legacy manifests because there is no toolchain evidence to verify.
