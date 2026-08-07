# Immutable GitHub releases and runtime SBOMs

E2H's final distribution-integrity stage publishes a GitHub Release only after the same wheel and sdist have passed reproducible-build verification, E2H's release manifest check, provenance attestation, and PyPI Trusted Publishing.

The release boundary uses GitHub immutable releases instead of a repository-managed GPG signing key. When immutable releases are enabled, GitHub locks the release tag and attached assets after publication and automatically creates a cryptographically verifiable release attestation that binds the tag, commit SHA, and release assets.

This is intentionally different from claiming that the Git tag object itself contains a maintainer GPG signature. E2H relies on the native immutable-release attestation because it covers both the release identity and the bytes users actually download.

## Required repository setting

Before pushing a production release tag, enable **Immutable releases** for the repository or organization.

The tag workflow performs a GitHub API preflight against the repository's immutable-release setting. If immutability is not enabled, the release stops before PyPI publication.

The workflow then follows the recommended immutable-release sequence:

1. build and verify all release assets;
2. create a draft GitHub Release;
3. attach every asset while the release is still mutable;
4. publish the package to PyPI;
5. publish the draft GitHub Release, making its tag and assets immutable;
6. verify the resulting release attestation and every attached asset.

A failed PyPI upload therefore leaves only a draft GitHub Release. An immutable public GitHub Release is not created until PyPI publication succeeds.

## Generated release notes

`.github/release.yml` configures GitHub's generated release notes. The tag workflow creates the draft with `gh release create --generate-notes`, so merged pull requests and contributors are incorporated by GitHub and grouped into:

- Security and release integrity;
- Features and improvements;
- Fixes;
- Documentation;
- Dependencies;
- Other changes.

Pull requests carrying `skip-changelog` are omitted. The catch-all category ensures unlabeled changes are still represented rather than silently disappearing from release notes.

## CycloneDX runtime SBOM

E2H does not scan the development virtual environment for its release SBOM. Instead, `uv export --format cyclonedx1.5` exports the project's locked runtime dependency graph directly from `uv.lock`.

Both clean source copies used for reproducible-build verification export the SBOM independently. CI requires the two CycloneDX JSON documents to be byte-identical and verifies that common development-only tools such as pytest, mypy, pytest-cov, and Ruff are absent.

The verified SBOM is published as `e2h-sbom.cdx.json` alongside the wheel, sdist, release manifest, release verification proof, and checksum file.

## SBOM attestation

Before release staging, `actions/attest` creates two independent statements for the exact wheel and sdist:

- SLSA build provenance;
- a CycloneDX SBOM attestation whose predicate is `e2h-sbom.cdx.json`.

The SBOM file itself is also checksum-bound into the release bundle and becomes an immutable GitHub Release asset.

Consumers can verify GitHub artifact attestations with `gh attestation verify`. SBOM attestations require the CycloneDX predicate type when querying them explicitly.

## Release attestation verification

After the draft is published, the workflow requires GitHub to report `isImmutable=true`. It then retries `gh release verify` for a short bounded interval to allow the native release attestation to become available.

Once the release attestation verifies, every attached local asset is checked with `gh release verify-asset` against the published release:

- wheel;
- sdist;
- CycloneDX SBOM;
- E2H release manifest;
- E2H release verification proof;
- SHA-256 checksum file.

The JSON result of `gh release verify` is retained as a GitHub Actions artifact for operational review.

## Trust layers

The resulting release has several complementary checks:

- `uv.lock` binds the dependency resolution used to create the runtime SBOM;
- clean double-build verification demonstrates deterministic wheel/sdist bytes;
- the E2H release manifest binds artifact filenames, sizes, digests, and embedded package identity;
- SLSA provenance binds distribution digests to the authenticated GitHub build workflow;
- the CycloneDX SBOM attestation binds the dependency inventory to those distribution digests;
- PyPI Trusted Publishing binds uploads to the configured GitHub OIDC publisher;
- the immutable GitHub Release attestation binds the release tag, commit, and final attached assets.

These mechanisms establish provenance and integrity. They do not establish that the source code or dependencies are free of vulnerabilities.
