from __future__ import annotations

import hashlib
import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

import e2h.release_toolchain as toolchain
from e2h.release import (
    ReleaseManifest,
    release_manifest_sha256,
    seal_release_artifacts,
    verify_release_artifacts,
)
from e2h.release_bundle import ReleaseBundleError, verify_release_bundle
from e2h.release_cli import release_app
from e2h.release_toolchain import (
    collect_release_toolchain_evidence,
    release_toolchain_metadata,
    verify_release_toolchain_evidence,
)
from e2h.sbom import canonicalize_cyclonedx_sbom
from e2h.snapshot import create_snapshot

runner = CliRunner()


def _write_source(root: Path) -> None:
    root.mkdir()
    (root / "uv.toml").write_text('required-version = "==0.12.2"\n', encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling==1.31.0"]\nbuild-backend = "hatchling.build"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / "build-constraints.txt").write_text(
        "hatchling==1.31.0 \\\n"
        "    --hash=sha256:" + "a" * 64 + " \\\n"
        "    --hash=sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    package = root / "src" / "e2h"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.27.0"\n', encoding="utf-8")


def _write_release_pair(directory: Path) -> None:
    directory.mkdir()
    metadata = b"Metadata-Version: 2.4\nName: e2h\nVersion: 0.27.0\n\n"
    wheel = directory / "e2h-0.27.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr("e2h-0.27.0.dist-info/METADATA", metadata)
    sdist = directory / "e2h-0.27.0.tar.gz"
    with tarfile.open(sdist, mode="w:gz") as archive:
        info = tarfile.TarInfo("e2h-0.27.0/PKG-INFO")
        info.size = len(metadata)
        archive.addfile(info, io.BytesIO(metadata))


def _release_inspection(manifest: ReleaseManifest) -> dict[str, object]:
    # Keep this fixture independent from the bundle verifier's private helper.
    return {
        "schema_version": manifest.schema_version,
        "project": manifest.project,
        "version": manifest.version,
        "manifest_sha256": release_manifest_sha256(manifest),
        "artifact_count": len(manifest.artifacts),
        "total_bytes": sum(artifact.size_bytes for artifact in manifest.artifacts),
        "metadata": manifest.metadata,
        "artifacts": [
            {
                "filename": artifact.filename,
                "kind": artifact.kind.value,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            }
            for artifact in manifest.artifacts
        ],
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _checksum_paths(bundle: Path) -> list[str]:
    return [
        *sorted(f"dist/{path.name}" for path in (bundle / "dist").iterdir()),
        "e2h-sbom.cdx.json",
        "e2h-source.e2hsnap",
        "release-manifest.json",
        "release-verification.json",
        "release-toolchain-verification.json",
        "release-inspection.json",
        "release-source-inspection.json",
    ]


def _rewrite_checksums(bundle: Path) -> None:
    lines = []
    for relative in _checksum_paths(bundle):
        digest = hashlib.sha256((bundle / relative).read_bytes()).hexdigest()
        lines.append(f"{digest}  {relative}\n")
    (bundle / "release-checksums.txt").write_text("".join(lines), encoding="utf-8")


def _write_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    source = tmp_path / "source"
    _write_source(source)
    monkeypatch.setattr(toolchain.platform, "python_version", lambda: "3.13.14")
    evidence = collect_release_toolchain_evidence(
        source,
        source_commit="a" * 40,
        runner_generation="ubuntu-24.04",
        source_date_epoch=1_704_067_200,
    )

    bundle = tmp_path / "release"
    bundle.mkdir()
    dist = bundle / "dist"
    _write_release_pair(dist)
    manifest = seal_release_artifacts(
        dist,
        expected_project="e2h",
        expected_version="0.27.0",
        metadata=release_toolchain_metadata(evidence),
    )
    (bundle / "release-manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    release_verification = verify_release_artifacts(manifest, dist)
    (bundle / "release-verification.json").write_text(
        release_verification.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    _write_json(bundle / "release-inspection.json", _release_inspection(manifest))

    source_manifest = create_snapshot(source, bundle / "e2h-source.e2hsnap")
    (bundle / "release-source-inspection.json").write_text(
        source_manifest.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    toolchain_verification = verify_release_toolchain_evidence(
        manifest.metadata,
        source,
        expected_source_commit="a" * 40,
    )
    (bundle / "release-toolchain-verification.json").write_text(
        toolchain_verification.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    sbom = canonicalize_cyclonedx_sbom(
        {
            "bomFormat": "CycloneDX",
            "specVersion": "1.5",
            "version": 1,
            "metadata": {
                "component": {
                    "type": "library",
                    "name": "e2h",
                    "version": "0.27.0",
                }
            },
            "components": [],
            "dependencies": [],
        }
    )
    (bundle / "e2h-sbom.cdx.json").write_text(sbom, encoding="utf-8")
    _rewrite_checksums(bundle)
    return bundle, source


def test_verify_release_bundle_checks_every_evidence_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)

    verification = verify_release_bundle(
        bundle,
        expected_source_commit="a" * 40,
    )

    assert verification.verified is True
    assert verification.project == "e2h"
    assert verification.version == "0.27.0"
    assert verification.artifact_count == 2
    assert verification.checksum_count == 9
    assert verification.source_commit == "a" * 40
    assert verification.source_commit_verified is True
    assert len(verification.source_tree_sha256) == 64
    assert set(verification.checksums) == set(_checksum_paths(bundle))


def test_verify_release_bundle_keeps_consumer_commit_assertion_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    verification = verify_release_bundle(bundle)
    assert verification.verified is True
    assert verification.source_commit_verified is False


def test_verify_release_bundle_rejects_checksum_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    (bundle / "e2h-sbom.cdx.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="checksum mismatch"):
        verify_release_bundle(bundle)


def test_verify_release_bundle_rejects_unsafe_duplicate_or_missing_checksums(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    checksum_path = bundle / "release-checksums.txt"
    original = checksum_path.read_text(encoding="utf-8")

    checksum_path.write_text(original + f"{'a' * 64}  ../escape\n", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="unsafe checksum path"):
        verify_release_bundle(bundle)

    first = original.splitlines()[0]
    checksum_path.write_text(original + first + "\n", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="duplicate release checksum path"):
        verify_release_bundle(bundle)

    checksum_path.write_text(
        "\n".join(
            line for line in original.splitlines() if "release-source-inspection.json" not in line
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ReleaseBundleError, match="missing evidence"):
        verify_release_bundle(bundle)


def test_verify_release_bundle_rejects_extra_files_and_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    (bundle / "extra.txt").write_text("extra\n", encoding="utf-8")
    with pytest.raises(ReleaseBundleError, match="layout does not match"):
        verify_release_bundle(bundle)
    (bundle / "extra.txt").unlink()

    sbom = bundle / "e2h-sbom.cdx.json"
    target = tmp_path / "real-sbom.json"
    target.write_bytes(sbom.read_bytes())
    sbom.unlink()
    sbom.symlink_to(target)
    with pytest.raises(ReleaseBundleError, match="regular file"):
        verify_release_bundle(bundle)


def test_verify_release_bundle_rejects_semantically_wrong_report_even_with_new_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    report = bundle / "release-verification.json"
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["artifact_count"] = 1
    _write_json(report, payload)
    _rewrite_checksums(bundle)

    with pytest.raises(ReleaseBundleError, match="does not match bundle contents"):
        verify_release_bundle(bundle)


def test_verify_release_bundle_rejects_noncanonical_sbom_even_with_new_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    sbom = bundle / "e2h-sbom.cdx.json"
    payload = json.loads(sbom.read_text(encoding="utf-8"))
    sbom.write_text(json.dumps(payload, indent=4) + "\n", encoding="utf-8")
    _rewrite_checksums(bundle)

    with pytest.raises(ReleaseBundleError, match="SBOM is not canonical"):
        verify_release_bundle(bundle)


def test_verify_release_bundle_rejects_sbom_version_mismatch_with_new_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    sbom = bundle / "e2h-sbom.cdx.json"
    payload = json.loads(sbom.read_text(encoding="utf-8"))
    payload["metadata"]["component"]["version"] = "999.0.0"
    sbom.write_text(canonicalize_cyclonedx_sbom(payload), encoding="utf-8")
    _rewrite_checksums(bundle)

    with pytest.raises(ReleaseBundleError, match="SBOM component does not match release manifest"):
        verify_release_bundle(bundle)


def test_verify_release_bundle_rejects_different_source_snapshot_even_with_new_checksum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, source = _write_bundle(tmp_path, monkeypatch)
    (source / "src" / "e2h" / "__init__.py").write_text(
        '__version__ = "tampered"\n',
        encoding="utf-8",
    )
    create_snapshot(source, bundle / "replacement.e2hsnap")
    (bundle / "e2h-source.e2hsnap").unlink()
    (bundle / "replacement.e2hsnap").rename(bundle / "e2h-source.e2hsnap")
    _rewrite_checksums(bundle)

    with pytest.raises(ReleaseBundleError, match="does not match source_tree_sha256"):
        verify_release_bundle(bundle)


def test_verify_release_bundle_rejects_wrong_expected_source_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    with pytest.raises(ReleaseBundleError, match="does not match expected source commit"):
        verify_release_bundle(bundle, expected_source_commit="b" * 40)


def test_verify_bundle_cli_emits_json_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle, _ = _write_bundle(tmp_path, monkeypatch)
    result = runner.invoke(
        release_app,
        [
            "verify-bundle",
            str(bundle),
            "--expected-source-commit",
            "a" * 40,
            "--json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verified"] is True
    assert payload["source_commit_verified"] is True
    assert payload["checksum_count"] == 9

    (bundle / "release-checksums.txt").write_text("bad\n", encoding="utf-8")
    failed = runner.invoke(release_app, ["verify-bundle", str(bundle)])
    assert failed.exit_code == 1
    assert "Release bundle verification failed" in failed.stderr
