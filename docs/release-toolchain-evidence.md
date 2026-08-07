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

The `metadata.release_toolchain` object can then be compared with the source tree named by `source_commit`. The four recorded file hashes allow a verifier to confirm that the repository toolchain inputs at that commit match the inputs asserted by the release manifest.

## Trust boundary

This evidence establishes identities and relationships; it is not a general trust verdict.

- `runner_generation` identifies Ubuntu 24.04 as the selected hosted-runner generation, not an exact GitHub runner image build or VM byte identity.
- `uv_required_version` records the repository requirement. CI separately enforces that `setup-uv` discovers and installs that exact version.
- The Hatchling version and build-constraint digest identify the reviewed isolated build graph. Release CI separately enforces hashed build constraints and PEP 517 isolation.
- The runtime lock digest identifies `uv.lock`; the canonical CycloneDX SBOM separately describes the runtime dependency graph shipped with the release.
- GitHub attestations provide workflow/publisher provenance. The E2H manifest does not replace those attestations or claim that a dependency, runner, or workflow is intrinsically trustworthy.

Production workflows require the full toolchain evidence tuple when sealing release artifacts. The CLI still permits manifests without toolchain metadata for backwards compatibility with schema `0.1` and for non-production/local use.
