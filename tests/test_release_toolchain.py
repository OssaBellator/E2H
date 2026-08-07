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
from e2h.release_cli import release_app
from e2h.release_toolchain import (
    ReleaseToolchainError,
    collect_release_toolchain_evidence,
    release_toolchain_metadata,
)

runner = CliRunner()


def _write_toolchain_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
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
    return root


def _write_release_pair(tmp_path: Path) -> Path:
    directory = tmp_path / "dist"
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
    return directory


def test_collect_release_toolchain_evidence_binds_reviewed_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    monkeypatch.setattr(toolchain.platform, "python_version", lambda: "3.13.14")
    evidence = collect_release_toolchain_evidence(
        root,
        source_commit="a" * 40,
        runner_generation="ubuntu-24.04",
        source_date_epoch=1_704_067_200,
    )
    assert evidence.python_version == "3.13.14"
    assert evidence.uv_required_version == "0.12.2"
    assert evidence.build_backend == "hatchling.build"
    assert evidence.build_backend_version == "1.31.0"
    assert evidence.source_commit == "a" * 40
    assert evidence.runner_generation == "ubuntu-24.04"
    assert evidence.source_date_epoch == 1_704_067_200
    for filename, field in (
        ("uv.toml", "uv_toml_sha256"),
        ("pyproject.toml", "pyproject_sha256"),
        ("uv.lock", "uv_lock_sha256"),
        ("build-constraints.txt", "build_constraints_sha256"),
    ):
        expected = hashlib.sha256((root / filename).read_bytes()).hexdigest()
        assert getattr(evidence, field) == expected
    assert release_toolchain_metadata(evidence) == {
        "release_toolchain": evidence.model_dump(mode="json")
    }


def test_toolchain_rejects_unpinned_or_inconsistent_build_inputs(tmp_path: Path) -> None:
    root = _write_toolchain_root(tmp_path)
    (root / "uv.toml").write_text('required-version = ">=0.12"\n', encoding="utf-8")
    with pytest.raises(ReleaseToolchainError, match="exact == version"):
        collect_release_toolchain_evidence(
            root,
            source_commit="a" * 40,
            runner_generation="ubuntu-24.04",
            source_date_epoch=1,
        )

    (root / "uv.toml").write_text('required-version = "==0.12.2"\n', encoding="utf-8")
    (root / "build-constraints.txt").write_text(
        "hatchling==1.30.0 \\\n    --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ReleaseToolchainError, match="must match pyproject"):
        collect_release_toolchain_evidence(
            root,
            source_commit="a" * 40,
            runner_generation="ubuntu-24.04",
            source_date_epoch=1,
        )


def test_toolchain_rejects_unsafe_inputs_and_invalid_identity(tmp_path: Path) -> None:
    root = _write_toolchain_root(tmp_path)
    target = root / "real.lock"
    target.write_text("version = 1\n", encoding="utf-8")
    (root / "uv.lock").unlink()
    (root / "uv.lock").symlink_to(target)
    with pytest.raises(ReleaseToolchainError, match="regular file"):
        collect_release_toolchain_evidence(
            root,
            source_commit="a" * 40,
            runner_generation="ubuntu-24.04",
            source_date_epoch=1,
        )

    (root / "uv.lock").unlink()
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    with pytest.raises(ReleaseToolchainError, match="invalid release toolchain evidence"):
        collect_release_toolchain_evidence(
            root,
            source_commit="not-a-commit",
            runner_generation="ubuntu-24.04",
            source_date_epoch=1,
        )


def test_cli_seal_embeds_and_inspect_exposes_toolchain_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    directory = _write_release_pair(tmp_path)
    manifest_path = tmp_path / "release-manifest.json"
    monkeypatch.setattr(toolchain.platform, "python_version", lambda: "3.13.14")
    result = runner.invoke(
        release_app,
        [
            "seal",
            str(directory),
            "--output",
            str(manifest_path),
            "--project",
            "e2h",
            "--version",
            "0.27.0",
            "--toolchain-root",
            str(root),
            "--source-commit",
            "a" * 40,
            "--runner-generation",
            "ubuntu-24.04",
            "--source-date-epoch",
            "1704067200",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    evidence = payload["metadata"]["release_toolchain"]
    assert evidence["python_version"] == "3.13.14"
    assert evidence["uv_required_version"] == "0.12.2"
    assert evidence["build_backend_version"] == "1.31.0"

    inspected = runner.invoke(release_app, ["inspect", str(manifest_path), "--json"])
    assert inspected.exit_code == 0
    assert json.loads(inspected.stdout)["metadata"] == payload["metadata"]


def test_cli_rejects_partial_toolchain_evidence(tmp_path: Path) -> None:
    directory = _write_release_pair(tmp_path)
    result = runner.invoke(
        release_app,
        [
            "seal",
            str(directory),
            "--output",
            str(tmp_path / "release-manifest.json"),
            "--source-commit",
            "a" * 40,
        ],
    )
    assert result.exit_code == 2
    assert "Incomplete toolchain evidence" in result.stderr
