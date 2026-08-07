from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import e2h.release_toolchain as toolchain
from e2h.release import ReleaseArtifact, ReleaseArtifactKind, ReleaseManifest
from e2h.release_cli import release_app
from e2h.release_toolchain import (
    ReleaseToolchainError,
    collect_release_toolchain_evidence,
    release_toolchain_metadata,
    verify_release_toolchain_evidence,
)

runner = CliRunner()


def _write_toolchain_root(tmp_path: Path) -> Path:
    root = tmp_path / "source"
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
    (root / "README.md").write_text("release source\n", encoding="utf-8")
    return root


def _metadata(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(toolchain.platform, "python_version", lambda: "3.13.14")
    evidence = collect_release_toolchain_evidence(
        root,
        source_commit="a" * 40,
        runner_generation="ubuntu-24.04",
        source_date_epoch=1_704_067_200,
    )
    return release_toolchain_metadata(evidence)


def _manifest(metadata: dict[str, object]) -> ReleaseManifest:
    return ReleaseManifest(
        project="e2h",
        version="0.27.0",
        artifacts=[
            ReleaseArtifact(
                filename="e2h-0.27.0-py3-none-any.whl",
                kind=ReleaseArtifactKind.WHEEL,
                size_bytes=1,
                sha256="c" * 64,
                package_name="e2h",
                package_version="0.27.0",
            ),
            ReleaseArtifact(
                filename="e2h-0.27.0.tar.gz",
                kind=ReleaseArtifactKind.SDIST,
                size_bytes=1,
                sha256="d" * 64,
                package_name="e2h",
                package_version="0.27.0",
            ),
        ],
        metadata=metadata,
    )


def test_verify_toolchain_matches_source_inputs_and_optional_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    metadata = _metadata(root, monkeypatch)
    monkeypatch.setattr(toolchain.platform, "python_version", lambda: "3.11.9")

    verification = verify_release_toolchain_evidence(
        metadata,
        root,
        expected_source_commit="a" * 40,
    )

    assert verification.verified is True
    assert verification.source_inputs_verified is True
    assert verification.source_tree_verified is True
    assert verification.source_commit_verified is True
    assert verification.evidence.python_version == "3.13.14"
    assert verification.evidence.uv_required_version == "0.12.2"
    assert verification.evidence.build_backend_version == "1.31.0"
    assert verification.evidence.source_tree_sha256 is not None
    assert (
        verification.evidence.uv_lock_sha256
        == hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest()
    )


def test_verify_toolchain_without_expected_commit_marks_commit_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    verification = verify_release_toolchain_evidence(_metadata(root, monkeypatch), root)
    assert verification.source_inputs_verified is True
    assert verification.source_tree_verified is True
    assert verification.source_commit_verified is False


def test_verify_toolchain_rejects_non_toolchain_source_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    metadata = _metadata(root, monkeypatch)
    (root / "README.md").write_text("changed source\n", encoding="utf-8")

    with pytest.raises(ReleaseToolchainError, match="source_tree_sha256"):
        verify_release_toolchain_evidence(metadata, root)


def test_legacy_toolchain_evidence_keeps_four_file_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    metadata = _metadata(root, monkeypatch)
    raw = metadata["release_toolchain"]
    assert isinstance(raw, dict)
    legacy = {
        "release_toolchain": {
            key: value for key, value in raw.items() if key != "source_tree_sha256"
        }
    }
    (root / "README.md").write_text("legacy extra source change\n", encoding="utf-8")

    verification = verify_release_toolchain_evidence(legacy, root)
    assert verification.source_inputs_verified is True
    assert verification.source_tree_verified is False
    assert verification.source_commit_verified is False
    assert verification.evidence.source_tree_sha256 is None


def test_verify_toolchain_rejects_source_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    metadata = _metadata(root, monkeypatch)
    (root / "uv.lock").write_text("version = 2\n", encoding="utf-8")

    with pytest.raises(ReleaseToolchainError, match="uv_lock_sha256"):
        verify_release_toolchain_evidence(metadata, root)


def test_verify_toolchain_rejects_version_drift_and_unsafe_source_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    metadata = _metadata(root, monkeypatch)
    (root / "uv.toml").write_text('required-version = "==0.12.3"\n', encoding="utf-8")
    with pytest.raises(ReleaseToolchainError, match="uv_required_version"):
        verify_release_toolchain_evidence(metadata, root)

    (root / "uv.toml").write_text('required-version = "==0.12.2"\n', encoding="utf-8")
    target = root / "real.lock"
    target.write_text("version = 1\n", encoding="utf-8")
    (root / "uv.lock").unlink()
    (root / "uv.lock").symlink_to(target)
    with pytest.raises(ReleaseToolchainError, match="regular file"):
        verify_release_toolchain_evidence(metadata, root)


def test_verify_toolchain_rejects_symlink_in_full_source_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    metadata = _metadata(root, monkeypatch)
    (root / "readme-link").symlink_to(root / "README.md")

    with pytest.raises(ReleaseToolchainError, match="symbolic links are not supported"):
        verify_release_toolchain_evidence(metadata, root)


def test_verify_toolchain_rejects_missing_malformed_or_wrong_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    metadata = _metadata(root, monkeypatch)

    with pytest.raises(ReleaseToolchainError, match=r"no metadata\.release_toolchain"):
        verify_release_toolchain_evidence({}, root)
    with pytest.raises(ReleaseToolchainError, match=r"invalid metadata\.release_toolchain"):
        verify_release_toolchain_evidence({"release_toolchain": {"schema_version": "0.1"}}, root)
    with pytest.raises(ReleaseToolchainError, match="lowercase 40-character SHA"):
        verify_release_toolchain_evidence(metadata, root, expected_source_commit="A" * 40)
    with pytest.raises(ReleaseToolchainError, match="does not match expected source commit"):
        verify_release_toolchain_evidence(metadata, root, expected_source_commit="b" * 40)


def test_cli_verify_toolchain_emits_machine_readable_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(
        _manifest(_metadata(root, monkeypatch)).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    result = runner.invoke(
        release_app,
        [
            "verify-toolchain",
            str(manifest_path),
            str(root),
            "--expected-source-commit",
            "a" * 40,
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["verified"] is True
    assert payload["source_inputs_verified"] is True
    assert payload["source_tree_verified"] is True
    assert payload["source_commit_verified"] is True
    assert payload["evidence"]["source_commit"] == "a" * 40
    assert len(payload["evidence"]["source_tree_sha256"]) == 64


def test_cli_verify_toolchain_fails_closed_on_legacy_or_tampered_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_toolchain_root(tmp_path)
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(
        _manifest({}).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    missing = runner.invoke(release_app, ["verify-toolchain", str(manifest_path), str(root)])
    assert missing.exit_code == 1
    normalized_stderr = " ".join(missing.stderr.split())
    assert "no metadata.release_toolchain" in normalized_stderr

    manifest_path.write_text(
        _manifest(_metadata(root, monkeypatch)).model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 99\n", encoding="utf-8")
    tampered = runner.invoke(release_app, ["verify-toolchain", str(manifest_path), str(root)])
    assert tampered.exit_code == 1
    assert "uv_lock_sha256" in tampered.stderr
