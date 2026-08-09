from __future__ import annotations

from pathlib import Path

import pytest

from e2h.compiler import CapsuleCompileError, load_compiler_spec


def test_compiler_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    source = tmp_path / "compiler.json"
    source.write_text(
        '{"id":"first","id":"second","checks":[{"id":"check","argv":["python","-V"]}]}',
        encoding="utf-8",
    )

    with pytest.raises(CapsuleCompileError, match="duplicate object key"):
        load_compiler_spec(source)


def test_compiler_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    source = tmp_path / "compiler.yaml"
    source.write_text(
        "\n".join(
            [
                "id: first",
                "id: second",
                "checks:",
                "  - id: check",
                "    argv: [python, -V]",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CapsuleCompileError, match="duplicate key"):
        load_compiler_spec(source)


def test_compiler_loader_rejects_nul_path_before_filesystem_access() -> None:
    with pytest.raises(CapsuleCompileError, match="path must not contain NUL"):
        load_compiler_spec(Path("bad\x00compiler.json"))


def test_compiler_loader_rejects_symlink_document(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text(
        '{"id":"compiler","checks":[{"id":"check","argv":["python","-V"]}]}',
        encoding="utf-8",
    )
    source = tmp_path / "compiler.json"
    try:
        source.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(CapsuleCompileError, match="regular file"):
        load_compiler_spec(source)
