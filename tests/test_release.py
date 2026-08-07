from __future__ import annotations

import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from e2h.release import (
    ReleaseArtifact,
    ReleaseArtifactKind,
    ReleaseIntegrityError,
    ReleaseManifest,
    load_release_manifest,
    release_manifest_sha256,
    seal_release_artifacts,
    verify_release_artifacts,
)
from e2h.release_cli import release_app

runner = CliRunner()


def _metadata(name: str = "e2h", version: str = "0.25.0") -> bytes:
    return (
        "Metadata-Version: 2.4\n"
        f"Name: {name}\n"
        f"Version: {version}\n"
        "Summary: release integrity fixture\n"
        "\n"
    ).encode()


def _write_wheel_metadata(directory: Path, payload: bytes) -> Path:
    path = directory / "e2h-0.25.0-py3-none-any.whl"
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("e2h-0.25.0.dist-info/METADATA", payload)
    return path


def _write_wheel(directory: Path, *, name: str = "e2h", version: str = "0.25.0") -> Path:
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    path = directory / filename
    dist_info = f"{name.replace('-', '_')}-{version}.dist-info"
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        metadata_info = zipfile.ZipInfo(f"{dist_info}/METADATA")
        metadata_info.date_time = (2024, 1, 1, 0, 0, 0)
        archive.writestr(metadata_info, _metadata(name, version))
        wheel_info = zipfile.ZipInfo(f"{dist_info}/WHEEL")
        wheel_info.date_time = (2024, 1, 1, 0, 0, 0)
        archive.writestr(
            wheel_info,
            b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n",
        )
    return path


def _write_sdist(directory: Path, *, name: str = "e2h", version: str = "0.25.0") -> Path:
    path = directory / f"{name}-{version}.tar.gz"
    payload = _metadata(name, version)
    with tarfile.open(path, mode="w:gz") as archive:
        info = tarfile.TarInfo(f"{name}-{version}/PKG-INFO")
        info.size = len(payload)
        info.mtime = 1_704_067_200
        archive.addfile(info, io.BytesIO(payload))
    return path


def _release_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "dist"
    directory.mkdir()
    _write_wheel(directory)
    _write_sdist(directory)
    return directory


def _artifact_pair() -> tuple[ReleaseArtifact, ReleaseArtifact]:
    wheel = ReleaseArtifact(
        filename="e2h-0.25.0-py3-none-any.whl",
        kind="wheel",
        size_bytes=10,
        sha256="a" * 64,
        package_name="e2h",
        package_version="0.25.0",
    )
    sdist = ReleaseArtifact(
        filename="e2h-0.25.0.tar.gz",
        kind="sdist",
        size_bytes=10,
        sha256="b" * 64,
        package_name="e2h",
        package_version="0.25.0",
    )
    return wheel, sdist


def test_seal_and_verify_release_pair(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(
        directory,
        expected_project="e2h",
        expected_version="0.25.0",
    )
    assert manifest.project == "e2h"
    assert manifest.version == "0.25.0"
    assert len(manifest.artifacts) == 2
    assert [artifact.filename for artifact in manifest.artifacts] == sorted(
        artifact.filename for artifact in manifest.artifacts
    )
    assert {artifact.kind for artifact in manifest.artifacts} == {
        ReleaseArtifactKind.WHEEL,
        ReleaseArtifactKind.SDIST,
    }
    assert len(release_manifest_sha256(manifest)) == 64

    verification = verify_release_artifacts(manifest, directory)
    assert verification.verified is True
    assert verification.artifact_count == 2
    assert verification.total_bytes > 0
    assert verification.manifest_sha256 == release_manifest_sha256(manifest)


def test_project_normalization_accepts_equivalent_names(tmp_path: Path) -> None:
    directory = tmp_path / "dist"
    directory.mkdir()
    _write_wheel(directory, name="e2h_test")
    _write_sdist(directory, name="e2h_test")
    manifest = seal_release_artifacts(directory, expected_project="E2H-Test")
    assert manifest.project == "E2H-Test"


def test_expected_project_and_version_must_match(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    with pytest.raises(ReleaseIntegrityError, match="expected project"):
        seal_release_artifacts(directory, expected_project="other")
    with pytest.raises(ReleaseIntegrityError, match="expected version"):
        seal_release_artifacts(directory, expected_version="9.9.9")


def test_inconsistent_archive_metadata_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "dist"
    directory.mkdir()
    _write_wheel(directory, version="0.25.0")
    _write_sdist(directory, version="0.24.0")
    with pytest.raises(ReleaseIntegrityError, match="inconsistent"):
        seal_release_artifacts(directory)


def test_tampered_and_renamed_artifacts_fail_verification(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    wheel = next(path for path in directory.iterdir() if path.suffix == ".whl")
    original = wheel.read_bytes()
    wheel.write_bytes(original + b"tampered")
    with pytest.raises(ReleaseIntegrityError, match="does not match manifest"):
        verify_release_artifacts(manifest, directory)

    wheel.write_bytes(original)
    renamed = directory / "e2h-0.25.0-1-py3-none-any.whl"
    wheel.rename(renamed)
    with pytest.raises(ReleaseIntegrityError, match="artifact set does not match"):
        verify_release_artifacts(manifest, directory)


def test_extra_or_unsupported_distribution_file_is_rejected(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    (directory / "notes.txt").write_text("not a distribution", encoding="utf-8")
    with pytest.raises(ReleaseIntegrityError, match="unsupported release artifact"):
        verify_release_artifacts(manifest, directory)


def test_manifest_requires_sorted_unique_consistent_artifacts() -> None:
    wheel, sdist = _artifact_pair()
    with pytest.raises(ValidationError, match="sorted"):
        ReleaseManifest(project="e2h", version="0.25.0", artifacts=[sdist, wheel])
    with pytest.raises(ValidationError, match="unique"):
        ReleaseManifest(project="e2h", version="0.25.0", artifacts=[wheel, wheel, sdist])
    with pytest.raises(ValidationError, match="package version"):
        ReleaseManifest(
            project="e2h",
            version="0.24.0",
            artifacts=sorted([wheel, sdist], key=lambda item: item.filename),
        )
    other_sdist = ReleaseArtifact(
        filename=sdist.filename,
        kind=sdist.kind,
        size_bytes=sdist.size_bytes,
        sha256=sdist.sha256,
        package_name="other",
        package_version=sdist.package_version,
    )
    with pytest.raises(ValidationError, match="package name"):
        ReleaseManifest(
            project="e2h",
            version="0.25.0",
            artifacts=sorted([wheel, other_sdist], key=lambda item: item.filename),
        )
    with pytest.raises(ValidationError, match="wheel and an sdist"):
        ReleaseManifest(project="e2h", version="0.25.0", artifacts=[wheel])
    with pytest.raises(ValidationError, match="portable basename"):
        ReleaseArtifact(
            filename="../e2h.whl",
            kind="wheel",
            size_bytes=10,
            sha256="a" * 64,
            package_name="e2h",
            package_version="0.25.0",
        )
    with pytest.raises(ValidationError, match="canonical JSON"):
        ReleaseManifest(
            project="e2h",
            version="0.25.0",
            artifacts=sorted([wheel, sdist], key=lambda item: item.filename),
            metadata={"bad": float("nan")},
        )


def test_invalid_wheel_and_sdist_archives_are_rejected(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    (wheel_dir / "e2h-0.25.0-py3-none-any.whl").write_bytes(b"not a zip")
    _write_sdist(wheel_dir)
    with pytest.raises(ReleaseIntegrityError, match="invalid wheel archive"):
        seal_release_artifacts(wheel_dir)

    sdist_dir = tmp_path / "sdist"
    sdist_dir.mkdir()
    _write_wheel(sdist_dir)
    (sdist_dir / "e2h-0.25.0.tar.gz").write_bytes(b"not a tarball")
    with pytest.raises(ReleaseIntegrityError, match="invalid sdist archive"):
        seal_release_artifacts(sdist_dir)


def test_invalid_wheel_and_sdist_metadata_are_rejected(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    wheel = wheel_dir / "e2h-0.25.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w") as archive:
        archive.writestr("e2h-0.25.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
    _write_sdist(wheel_dir)
    with pytest.raises(ReleaseIntegrityError, match=r"exactly one \.dist-info/METADATA"):
        seal_release_artifacts(wheel_dir)

    missing_name = tmp_path / "missing-name"
    missing_name.mkdir()
    _write_wheel_metadata(missing_name, b"Metadata-Version: 2.4\nVersion: 0.25.0\n\n")
    _write_sdist(missing_name)
    with pytest.raises(ReleaseIntegrityError, match="missing Name"):
        seal_release_artifacts(missing_name)

    missing_version = tmp_path / "missing-version"
    missing_version.mkdir()
    _write_wheel_metadata(missing_version, b"Metadata-Version: 2.4\nName: e2h\n\n")
    _write_sdist(missing_version)
    with pytest.raises(ReleaseIntegrityError, match="missing Version"):
        seal_release_artifacts(missing_version)

    sdist_dir = tmp_path / "sdist"
    sdist_dir.mkdir()
    _write_wheel(sdist_dir)
    bad_sdist = sdist_dir / "e2h-0.25.0.tar.gz"
    with tarfile.open(bad_sdist, mode="w:gz") as archive:
        info = tarfile.TarInfo("e2h-0.25.0/README.md")
        payload = b"missing metadata"
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(ReleaseIntegrityError, match="exactly one top-level PKG-INFO"):
        seal_release_artifacts(sdist_dir)


def test_release_directory_safety_checks(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ReleaseIntegrityError, match="directory is empty"):
        seal_release_artifacts(empty)

    not_directory = tmp_path / "artifact.whl"
    not_directory.write_bytes(b"not a directory")
    with pytest.raises(ReleaseIntegrityError, match="real directory"):
        seal_release_artifacts(not_directory)

    nested = tmp_path / "nested"
    nested.mkdir()
    _write_wheel(nested)
    _write_sdist(nested)
    (nested / "subdir").mkdir()
    with pytest.raises(ReleaseIntegrityError, match="regular distribution files"):
        seal_release_artifacts(nested)

    empty_artifact = tmp_path / "empty-artifact"
    empty_artifact.mkdir()
    (empty_artifact / "e2h-0.25.0-py3-none-any.whl").touch()
    _write_sdist(empty_artifact)
    with pytest.raises(ReleaseIntegrityError, match="must not be empty"):
        seal_release_artifacts(empty_artifact)


def test_loader_rejects_invalid_documents(tmp_path: Path) -> None:
    wrong_extension = tmp_path / "manifest.yaml"
    wrong_extension.write_text("{}", encoding="utf-8")
    with pytest.raises(ReleaseIntegrityError, match=r"must use \.json"):
        load_release_manifest(wrong_extension)

    wrong_root = tmp_path / "manifest.json"
    wrong_root.write_text("[]", encoding="utf-8")
    with pytest.raises(ReleaseIntegrityError, match="root must be an object"):
        load_release_manifest(wrong_root)

    wrong_root.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(ReleaseIntegrityError, match="non-standard JSON constant"):
        load_release_manifest(wrong_root)

    wrong_root.write_bytes(b"\xff\xfe")
    with pytest.raises(ReleaseIntegrityError, match="must be UTF-8"):
        load_release_manifest(wrong_root)

    wrong_root.write_text("{", encoding="utf-8")
    with pytest.raises(ReleaseIntegrityError, match="invalid release manifest JSON"):
        load_release_manifest(wrong_root)


def test_cli_seal_verify_inspect_and_schema(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest_path = tmp_path / "release-manifest.json"
    sealed = runner.invoke(
        release_app,
        [
            "seal",
            str(directory),
            "--output",
            str(manifest_path),
            "--project",
            "e2h",
            "--version",
            "0.25.0",
        ],
    )
    assert sealed.exit_code == 0
    assert manifest_path.is_file()

    verified = runner.invoke(
        release_app,
        ["verify", str(manifest_path), str(directory), "--json"],
    )
    assert verified.exit_code == 0
    verification = json.loads(verified.stdout)
    assert verification["verified"] is True
    assert verification["artifact_count"] == 2

    inspected = runner.invoke(release_app, ["inspect", str(manifest_path), "--json"])
    assert inspected.exit_code == 0
    inspection = json.loads(inspected.stdout)
    assert inspection["project"] == "e2h"
    assert inspection["artifact_count"] == 2

    schema = runner.invoke(release_app, ["schema"])
    assert schema.exit_code == 0
    assert json.loads(schema.stdout)["type"] == "object"


def test_cli_failures_return_expected_exit_codes(tmp_path: Path) -> None:
    directory = _release_dir(tmp_path)
    manifest = seal_release_artifacts(directory)
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    wheel = next(path for path in directory.iterdir() if path.suffix == ".whl")
    wheel.write_bytes(wheel.read_bytes() + b"tampered")
    result = runner.invoke(release_app, ["verify", str(manifest_path), str(directory)])
    assert result.exit_code == 1
    assert "verification failed" in result.stderr

    unsupported = tmp_path / "unsupported"
    unsupported.mkdir()
    (unsupported / "notes.txt").write_text("no distributions", encoding="utf-8")
    seal = runner.invoke(
        release_app,
        ["seal", str(unsupported), "--output", str(tmp_path / "out.json")],
    )
    assert seal.exit_code == 2
    assert "Unable to seal" in seal.stderr
