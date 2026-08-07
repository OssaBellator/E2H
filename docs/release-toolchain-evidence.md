# Release toolchain evidence

E2H release manifests can bind distribution bytes to the reviewed source and toolchain context used to construct them. Production release workflows store this record under `metadata.release_toolchain` in the existing release-manifest schema.

The record includes:

- `source_commit`: the 40-character commit identity supplied by the trusted workflow;
- `source_tree_sha256`: the deterministic content identity of the complete archived release source tree;
- `runner_generation`: the GitHub-hosted runner generation, currently `ubuntu-24.04`;
- `python_version`: the Python patch observed by E2H at seal time;
- `uv_required_version`: the exact version required by `uv.toml`;
- `build_backend` and `build_backend_version`: the exact Hatchling backend required by `pyproject.toml`;
- `source_date_epoch`: the deterministic build epoch supplied by the workflow;
- SHA-256 identities for `uv.toml`, `pyproject.toml`, `uv.lock`, and `build-constraints.txt`.

Because manifest metadata is part of E2H's canonical manifest digest, changing any recorded source or toolchain field changes `manifest_sha256`. The production release flow also includes `release-manifest.json` in its release checksum file and immutable GitHub Release assets, so downstream release attestations bind the resulting manifest bytes.

## Source tree identity

`source_tree_sha256` reuses E2H's deterministic snapshot-core representation rather than hashing filesystem traversal order or tar container bytes. The identity covers:

- every included relative directory path;
- every included regular-file path;
- each file's SHA-256 and byte size;
- the executable bit for each file;
- total included file bytes.

Entries are sorted by relative POSIX path before hashing. Symbolic links and unsupported filesystem entry types are rejected. File reads are bounded and checked for changes while hashing; on platforms with `O_NOFOLLOW`, files are opened with that protection as well.

The same standard local-state exclusions used by E2H snapshots apply: `.git`, `.venv`, `.e2h`, Python bytecode, and `__pycache__` trees do not participate in the identity. Production sealing uses a clean `git archive HEAD` source tree, so those local-only paths are normally absent anyway.

This makes the identity portable across two independently unpacked copies of the same source while still detecting ordinary source edits, added or removed files/directories, path changes, and executable-bit changes.

## Checksum-bound release source snapshot

Production releases also ship `e2h-source.e2hsnap`, a restorable E2H snapshot containing the exact source representation bound by `source_tree_sha256`. Release CI creates the snapshot independently from both `source-a` and `source-b` and requires the two `.e2hsnap` archives to be byte-identical. It then requires the snapshot's `snapshot_id` to equal the release manifest's `source_tree_sha256`.

The release bundle includes:

- `e2h-source.e2hsnap` — deterministic source snapshot;
- `e2h-sbom.cdx.json` — canonical runtime CycloneDX SBOM;
- `release-source-inspection.json` — verified snapshot manifest summary;
- `release-manifest.json` — distribution and source/toolchain evidence;
- `release-verification.json` — cross-build distribution verification proof;
- `release-toolchain-verification.json` — independent `source-b` verification proof;
- `release-inspection.json` — manifest inspection summary;
- `release-checksums.txt` — SHA-256 handoff for all release evidence and distributions.

After checksum verification, the tag publication workflow creates one multi-subject GitHub provenance attestation covering the complete immutable release bundle: wheel, sdist, canonical SBOM, source snapshot, manifest, verification/inspection reports, and checksum file. The CycloneDX SBOM attestation remains separately scoped to the Python wheel/sdist because it describes their runtime dependency graph rather than the repository snapshot or evidence documents.

This means consumers can verify provenance for the evidence documents themselves instead of relying only on transitive trust through the checksum file. The checksum relationship is still useful: all handoff jobs validate `release-checksums.txt` before attestation, drafting, PyPI publication, and immutable GitHub Release publication.

## Verify the complete downloaded bundle

For a release directory containing the exact immutable bundle layout, use:

```bash
e2h release verify-bundle ./release --json
```

If an independently trusted source provides the expected commit SHA, bind that assertion too:

```bash
e2h release verify-bundle \
  ./release \
  --expected-source-commit 0123456789abcdef0123456789abcdef01234567 \
  --json
```

`verify-bundle` is the end-to-end E2H content verifier. It fails closed unless the bundle has the exact expected top-level layout and exactly one wheel plus one sdist. It then:

1. parses `release-checksums.txt` with strict relative POSIX paths and rejects duplicate, absolute, traversal, backslash, missing, or unexpected entries;
2. copies every checksum-listed asset into a private staging directory while hashing it through a stable no-follow file descriptor where the platform supports `O_NOFOLLOW`;
3. performs all semantic verification only on those accepted staged bytes, avoiding a checksum-versus-parser time-of-check/time-of-use gap;
4. verifies the wheel and sdist against `release-manifest.json`;
5. requires `e2h-sbom.cdx.json` to be valid canonical CycloneDX JSON;
6. verifies `e2h-source.e2hsnap` and requires its `snapshot_id` to equal the manifest's `source_tree_sha256`;
7. restores the source snapshot into a private temporary tree and recomputes the source/toolchain proof;
8. checks the producer's stored `release-verification.json`, `release-toolchain-verification.json`, `release-inspection.json`, and `release-source-inspection.json` against recomputed facts;
9. separately applies the optional consumer `--expected-source-commit` assertion.

The returned proof includes the release project/version, manifest digest, source-tree digest, recorded source commit, whether the consumer-supplied commit matched, every checksum-bound asset identity, and the SHA-256 of the checksum manifest itself.

The producer workflow runs the same `verify-bundle` command after `release-checksums.txt` is finalized and before the release artifact is uploaded. Its CI proof is intentionally written outside the immutable `release/` directory; putting that proof inside the bundle would create a circular checksum relationship in which verifying the bundle changes the bundle.

`verify-bundle` does **not** verify GitHub artifact attestations or PyPI trusted-publisher identity. Those are external provenance relationships and should be checked separately using GitHub/PyPI verification mechanisms. E2H's command establishes that the downloaded bundle is internally self-consistent according to its deterministic hashes, source snapshot, manifest, SBOM, and stored verification reports.

## Manual source verification

The lower-level commands remain useful when inspecting individual relationships. A consumer can verify and restore the source asset directly:

```bash
sha256sum -c release-checksums.txt
e2h snapshot verify e2h-source.e2hsnap
e2h snapshot inspect e2h-source.e2hsnap --json
e2h snapshot restore e2h-source.e2hsnap ./release-source
```

GitHub provenance for an individual downloaded bundle asset can be checked with the GitHub CLI's artifact-attestation verification flow. The repository-issued provenance and E2H's checksum/content identities are complementary: the former establishes workflow provenance, while the latter establishes the byte/content relationships inside the release bundle.

The `snapshot_id` shown by `snapshot inspect` must equal `metadata.release_toolchain.source_tree_sha256` from:

```bash
e2h release inspect release-manifest.json --json
```

The restored tree can then be passed directly to the release verifier:

```bash
e2h release verify-toolchain \
  release-manifest.json \
  ./release-source \
  --expected-source-commit 0123456789abcdef0123456789abcdef01234567 \
  --json
```

## Inspecting a manifest

Use:

```bash
e2h release inspect release-manifest.json --json
```

The `metadata.release_toolchain` object describes both repository-controlled source/toolchain inputs and workflow context recorded at build time.

## Verifying repository-controlled source and toolchain inputs

Given an unpacked source tree for the release, run:

```bash
e2h release verify-toolchain release-manifest.json ./source --json
```

For current release evidence, the verifier:

- requires `uv.toml`, `pyproject.toml`, `uv.lock`, and `build-constraints.txt` to be bounded regular files rather than symlinks;
- recomputes all four dedicated SHA-256 identities;
- parses the exact uv version required by `uv.toml`;
- parses the exact Hatchling backend/version required by `pyproject.toml`;
- requires the Hatchling entry in `build-constraints.txt` to agree with `pyproject.toml`;
- recomputes the deterministic complete source-tree identity;
- compares all observed values with the manifest evidence.

A dirty checkout is therefore not equivalent to the archived release source if it contains ordinary untracked or modified files. For consumer verification, the checksum-bound `e2h-source.e2hsnap` release asset is the preferred source representation. Standard excluded local-state paths such as `.git` and `.venv` do not affect the tree identity.

This verification does not depend on the verifier's current Python patch. `python_version` in the manifest describes the release builder and is not compared with the consumer's interpreter.

If an independent source tells you the expected commit identity, bind that assertion too:

```bash
e2h release verify-toolchain \
  release-manifest.json \
  ./source \
  --expected-source-commit 0123456789abcdef0123456789abcdef01234567 \
  --json
```

With that option, `source_commit_verified` is true only when the supplied full lowercase SHA exactly matches the manifest. Without the option, source-controlled evidence can still verify, but `source_commit_verified` remains false because E2H does not execute Git or infer repository history from a directory.

A successful proof for current evidence contains the validated manifest evidence together with `source_inputs_verified: true`, `source_tree_verified: true`, `source_commit_verified`, and `verified: true`.

Production release CI performs this check automatically against `source-b`, an independently created `git archive HEAD` copy. The manifest itself is sealed from separate archived `source-a`. The resulting `release-toolchain-verification.json` and source snapshot evidence are included in `release-checksums.txt` and shipped as immutable GitHub Release assets.

## Legacy evidence

`source_tree_sha256` is additive within the existing nested toolchain-evidence schema. Toolchain records created before full-tree binding do not contain it. Those records continue to verify the original four dedicated source files and exact uv/Hatchling requirements; the verification proof reports `source_tree_verified: false` instead of pretending that the rest of the source tree was bound retrospectively.

Manifests with no `metadata.release_toolchain` object at all remain valid release-manifest schema `0.1` documents, but `verify-toolchain` rejects them because there is no source/toolchain evidence to verify.

## Trust boundary

This evidence establishes identities and relationships; it is not a general trust verdict.

- `source_tree_sha256` and the matching source snapshot prove deterministic content equality for the supplied source representation. They do not by themselves prove authorship or safety.
- `runner_generation` identifies Ubuntu 24.04 as the selected hosted-runner generation, not an exact GitHub runner image build or VM byte identity.
- `python_version` records the builder interpreter observed at seal time. Consumer source verification deliberately does not claim to derive that value from source files.
- `source_date_epoch` is workflow-supplied build context, not a property recoverable from source files.
- `source_commit` is workflow-supplied provenance. `verify-toolchain` and `verify-bundle` check it only when the consumer supplies `--expected-source-commit`; establishing that an independently obtained commit identity is trustworthy remains the caller's responsibility.
- `uv_required_version` records the repository requirement. CI separately enforces that `setup-uv` discovers and installs that exact version.
- The Hatchling version and build-constraint digest identify the reviewed isolated build graph. Release CI separately enforces hashed build constraints and PEP 517 isolation.
- The runtime lock digest identifies `uv.lock`; the canonical CycloneDX SBOM separately describes the runtime dependency graph shipped with the release.
- GitHub attestations provide workflow/publisher provenance. The E2H manifest, source snapshot, verification reports, and checksums do not replace those attestations or claim that a dependency, runner, source archive, or workflow is intrinsically trustworthy. Attesting an evidence document proves where that document was produced, not that every claim inside it is semantically correct.

Production workflows require the full source/toolchain evidence tuple when sealing release artifacts. The CLI still permits manifests without toolchain metadata for backwards compatibility with schema `0.1` and for non-production/local use.
