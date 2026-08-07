# PyPI trusted publishing

E2H publishes Python distributions through PyPI Trusted Publishing rather than a stored PyPI API token.

The release workflow is `.github/workflows/publish-pypi.yml`. It runs only for stable `vMAJOR.MINOR.PATCH` tag pushes and separates building, provenance/SBOM attestation, GitHub Release staging, package publication, and immutable GitHub Release publication into different jobs.

## One-time PyPI configuration

Configure a GitHub Actions Trusted Publisher for the `e2h` PyPI project with:

- owner: `OssaBellator`
- repository: `E2H`
- workflow filename: `publish-pypi.yml`
- environment: `pypi`

If the PyPI project does not exist yet, configure the same values as a pending Trusted Publisher. The first successful publication creates the project and converts the pending publisher into a normal publisher.

No `PYPI_TOKEN`, password, or long-lived publishing secret belongs in GitHub Actions.

## GitHub configuration

Create a repository environment named `pypi`. PyPI recommends using an environment because it can add restrictions around the trusted workflow. For a production release, configure environment protection appropriate to the repository's maintainer model, such as required approval before the publication job begins.

Also enable **Immutable releases** for the repository or organization. The release workflow checks this setting through the GitHub API before it creates a draft release. If immutable releases are disabled, the workflow stops before PyPI publication.

The workflow grants `id-token: write` only to the two jobs that require an ephemeral identity:

1. `attest` uses GitHub OIDC to create SLSA provenance and a CycloneDX SBOM attestation for the already-verified wheel and sdist.
2. `publish` uses GitHub OIDC inside the `pypi` environment so PyPI can mint a short-lived project-scoped publishing token.

The `build`, `release-draft`, and `release-publish` jobs have no OIDC permission.

## Release procedure

Before creating a release tag:

1. Merge the release version into `main`.
2. Confirm all permanent CI suites are green on that commit.
3. Confirm `pyproject.toml` contains the intended version.
4. Confirm the `pypi` environment, PyPI Trusted Publisher, and GitHub immutable-release setting are configured.
5. Create and push a stable tag with exactly the same version, for example `v0.27.0`.

The workflow rejects tags that do not match `vMAJOR.MINOR.PATCH`, versions that do not equal `pyproject.toml`, and tag commits that are not reachable from `origin/main`.

## Build, SBOM, and handoff integrity

The tag workflow does not publish the first build it happens to produce. It creates two independent clean source copies with `git archive`, builds each with the fixed release epoch, and requires the wheel and sdist to be byte-identical.

Each clean source copy also exports a CycloneDX 1.5 runtime SBOM directly from the locked `uv` resolution. The two SBOM files must be byte-identical, identify E2H, and exclude common development-only tools before one is admitted to the release bundle.

E2H's release-integrity commands seal build A and verify build B. The verified distributions, CycloneDX SBOM, manifest, verification proof, and a SHA-256 checksum file are uploaded together as one short-lived GitHub Actions artifact.

Every downstream job downloads that bundle and verifies the checksum file before doing anything with it. OIDC-bearing jobs never check out the repository and never execute E2H or other project code.

## GitHub Release staging

After provenance and SBOM attestations exist, `release-draft` creates a draft GitHub Release for the already-pushed tag. GitHub-generated release notes use `.github/release.yml` to group merged pull requests and contributors into stable changelog categories.

All release assets are attached while the release remains a draft. This is required because immutable release assets cannot be changed after publication.

The `publish` job then uploads the exact verified wheel and sdist to PyPI. Only after PyPI succeeds does `release-publish` publish the draft GitHub Release.

When immutable releases are enabled, publishing locks the tag and assets and GitHub automatically creates a cryptographically signed release attestation. The workflow requires `isImmutable=true`, verifies the release attestation with `gh release verify`, and verifies each attached asset with `gh release verify-asset`.

See `docs/immutable-releases.md` for the complete release-asset and SBOM trust model.

## Attestations

The `attest` job creates both GitHub SLSA provenance and a CycloneDX SBOM attestation for the exact wheel and sdist using `actions/attest`.

The PyPA publishing action is also configured with PEP 740 attestations enabled. PyPI therefore receives publication attestations in addition to GitHub provenance and SBOM records.

These records answer related but different questions:

- the E2H release manifest proves byte identity and embedded package identity;
- reproducible-build verification proves two clean builds from the tagged source produced the same bytes;
- the runtime SBOM describes the locked production dependency graph;
- GitHub provenance binds the released artifact digests to the authenticated GitHub workflow invocation;
- the SBOM attestation binds the dependency inventory to those artifact digests;
- the PyPI publish attestation binds the uploaded distributions to the configured Trusted Publisher;
- the immutable GitHub Release attestation binds the release tag, commit, and final release assets.

None of these mechanisms prove that the package source is safe or trustworthy. They make the release path auditable and materially reduce credential, tag-movement, and artifact-substitution risk.

## Action pinning

Every external action in the publishing workflow and release-integrity workflow is pinned to a full immutable commit SHA. Version comments record the reviewed upstream release. Updating an action requires an explicit workflow change and normal code review rather than silently following a mutable major-version tag.
