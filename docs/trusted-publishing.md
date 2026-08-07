# PyPI trusted publishing

E2H publishes Python distributions through PyPI Trusted Publishing rather than a stored PyPI API token.

The release workflow is `.github/workflows/publish-pypi.yml`. It runs only for stable `vMAJOR.MINOR.PATCH` tag pushes and separates building, provenance attestation, and package publication into different jobs.

## One-time PyPI configuration

Configure a GitHub Actions Trusted Publisher for the `e2h` PyPI project with:

- owner: `OssaBellator`
- repository: `E2H`
- workflow filename: `publish-pypi.yml`
- environment: `pypi`

If the PyPI project does not exist yet, configure the same values as a pending Trusted Publisher. The first successful publication creates the project and converts the pending publisher into a normal publisher.

No `PYPI_TOKEN`, password, or long-lived publishing secret belongs in GitHub Actions.

## GitHub environment

Create a repository environment named `pypi`. PyPI recommends using an environment because it can add restrictions around the trusted workflow. For a production release, configure environment protection appropriate to the repository's maintainer model, such as required approval before the publication job begins.

The workflow grants `id-token: write` only to the two jobs that require an ephemeral identity:

1. `attest` uses GitHub OIDC to create SLSA provenance for the already-verified wheel and sdist.
2. `publish` uses GitHub OIDC inside the `pypi` environment so PyPI can mint a short-lived project-scoped publishing token.

The `build` job has no OIDC permission.

## Release procedure

Before creating a release tag:

1. Merge the release version into `main`.
2. Confirm all permanent CI suites are green on that commit.
3. Confirm `pyproject.toml` contains the intended version.
4. Create and push a stable tag with exactly the same version, for example `v0.26.0`.

The workflow rejects tags that do not match `vMAJOR.MINOR.PATCH`, versions that do not equal `pyproject.toml`, and tag commits that are not reachable from `origin/main`.

## Build and handoff integrity

The tag workflow does not publish the first build it happens to produce. It creates two independent clean source copies with `git archive`, builds each with the fixed release epoch, and requires the wheel and sdist to be byte-identical.

It then uses E2H's release-integrity commands to seal build A and verify build B. The verified distributions, manifest, verification proof, and a SHA-256 checksum file are uploaded together as one short-lived GitHub Actions artifact.

The `attest` and `publish` jobs do not check out the repository and do not execute E2H or other project code. They download that verified bundle and validate its checksum file before using it. This keeps OIDC-bearing jobs small and avoids giving tag contents a general-purpose credential-bearing execution surface.

## Attestations

The `attest` job creates GitHub SLSA provenance for the exact wheel and sdist using `actions/attest`.

The PyPA publishing action is also configured with PEP 740 attestations enabled. PyPI therefore receives publication attestations in addition to the GitHub provenance record.

These records answer related but different questions:

- the E2H release manifest proves byte identity and embedded package identity;
- reproducible-build verification proves two clean builds from the tagged source produced the same bytes;
- GitHub provenance binds the released artifact digests to the authenticated GitHub workflow invocation;
- the PyPI publish attestation binds the uploaded distributions to the configured Trusted Publisher.

None of these mechanisms prove that the package source is safe or trustworthy. They make the release path auditable and materially reduce credential and artifact-substitution risk.

## Action pinning

Every external action in the publishing workflow is pinned to a full immutable commit SHA. Version comments record the reviewed upstream release. Updating an action requires an explicit workflow change and normal code review rather than silently following a mutable major-version tag.
