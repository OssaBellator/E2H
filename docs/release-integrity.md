# Release artifact integrity

E2H release integrity binds built Python distributions to an offline, deterministic JSON manifest before publication.

The manifest is intentionally narrower than a signing or provenance system. It proves that a directory contains the same wheel/sdist bytes that were sealed earlier, and that every artifact embeds the expected package name and version. It does not prove who built or published those bytes.

## Seal a release

Build into a clean directory containing only Python distribution files:

```bash
SOURCE_DATE_EPOCH=1704067200 uv build --out-dir dist
uv run e2h release seal dist \
  --output release-manifest.json \
  --project e2h \
  --version 0.25.0
```

The release directory must contain only regular `.whl` and `.tar.gz` files. Symlinks, directories, unsupported files, empty artifacts, oversized artifacts, malformed archives, and inconsistent package metadata are rejected.

The manifest records, for each artifact:

- portable filename;
- wheel or sdist kind;
- byte length;
- SHA-256;
- embedded package name;
- embedded package version.

The manifest itself contains no clock time or machine-specific path, so the same artifact set produces the same canonical manifest digest.

## Verify a release

```bash
uv run e2h release verify release-manifest.json dist --json
```

Verification rescans every artifact, rereads the wheel `METADATA` or sdist `PKG-INFO`, and requires the current directory to match the manifest exactly. Missing, extra, renamed, modified, or metadata-inconsistent artifacts fail verification.

`e2h release inspect` reports filenames, kinds, sizes, hashes, and the manifest digest without extracting package payload files.

## Reproducible-build CI

`.github/workflows/release-integrity.yml` builds the project twice with the same `SOURCE_DATE_EPOCH`, requires both output directories to be byte-identical, seals the first build, and verifies the second build against that manifest.

This catches nondeterministic build output before package publication. The workflow uploads the verified artifacts, manifest, and verification proof as CI artifacts for review.

## Security boundary

A SHA-256 manifest establishes byte identity, not publisher authenticity. An attacker who can replace both an artifact and its unsigned manifest can create a new internally consistent pair.

For that reason, registry publication and provenance attestations remain a separate Milestone 6 item. A later publication workflow should bind these already-verified artifact digests to the repository/ref and an authenticated OIDC publishing identity rather than weakening or replacing the offline manifest check.
