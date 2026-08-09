from __future__ import annotations

from pathlib import Path

import pytest

import e2h.benchmark_env as benchmark_env
from e2h.benchmark_env import BenchmarkEnvironmentError


def test_canonical_json_rejects_non_finite_number() -> None:
    with pytest.raises(ValueError, match="canonical JSON data"):
        benchmark_env._canonical_json_bytes({"value": float("nan")})


@pytest.mark.parametrize("value", ["", "bad\\path", "/absolute", "../parent"])
def test_relative_path_validation_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError):
        benchmark_env._validate_relative_path(value, "test path")


def test_safe_root_rejects_file(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_text("file\n", encoding="utf-8")

    with pytest.raises(BenchmarkEnvironmentError, match="root is not a directory"):
        benchmark_env._safe_root(root)


def test_resolve_relative_normalizes_validation_error(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(BenchmarkEnvironmentError, match="must not contain"):
        benchmark_env._resolve_relative(root, "../outside", "test path")


def test_resolve_relative_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(BenchmarkEnvironmentError, match="escapes the benchmark root"):
        benchmark_env._resolve_relative(root, "escape", "test path")


def test_entrypoint_validator_rejects_empty_argument() -> None:
    with pytest.raises(ValueError, match="entrypoint arguments"):
        benchmark_env.BenchmarkEnvironmentSpec.entrypoint_must_be_safe([""])
