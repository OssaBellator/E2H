from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import e2h.oracles as oracles
from e2h.oracles import FileOracle, JsonOracle, compile_oracle, evaluate_oracle


def _rewrite_on_read(
    source: Path,
    replacement_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[os.stat_result, dict[str, bool]]:
    before = source.stat(follow_symlinks=False)
    original_fdopen = os.fdopen
    state = {"rewritten": False}

    def rewriting_fdopen(fd: int, *args: Any, **kwargs: Any) -> Any:
        if not state["rewritten"]:
            state["rewritten"] = True
            source.write_text(replacement_text, encoding="utf-8")
            os.utime(
                source,
                ns=(before.st_atime_ns, before.st_mtime_ns),
                follow_symlinks=False,
            )
        return original_fdopen(fd, *args, **kwargs)

    monkeypatch.setattr(oracles.os, "fdopen", rewriting_fdopen)
    return before, state


def test_json_oracle_rejects_nested_non_string_expected_key() -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        JsonOracle(
            id="ambiguous",
            path="result.json",
            pointer="/value",
            expected={"nested": {1: "integer", "1": "string"}},
        )


def test_compile_oracle_revalidates_mutated_expected() -> None:
    oracle = JsonOracle(
        id="mutated",
        path="result.json",
        pointer="/value",
        expected={"nested": {"value": "safe"}},
    )
    oracle.expected = {"nested": {1: "integer", "1": "string"}}

    with pytest.raises(ValueError, match="string keys"):
        compile_oracle(oracle)


@pytest.mark.skipif(
    not oracles._ORACLE_DIR_FD_SUPPORTED,
    reason="descriptor-relative oracle reads are unavailable",
)
def test_descriptor_oracle_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "result.txt"
    original_text = "inside-a\n"
    replacement_text = "inside-b\n"
    source.write_text(original_text, encoding="utf-8")
    before, state = _rewrite_on_read(source, replacement_text, monkeypatch)

    result = evaluate_oracle(
        FileOracle(id="rewrite", path=source.name, mode="text_equals", expected=original_text),
        root=tmp_path,
    )

    assert state["rewritten"] is True
    assert result.passed is False
    assert "changed while reading" in (result.error or "")
    after = source.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns


def test_fallback_oracle_rejects_same_inode_rewrite_with_restored_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "result.txt"
    original_text = "inside-a\n"
    replacement_text = "inside-b\n"
    source.write_text(original_text, encoding="utf-8")
    monkeypatch.setattr(oracles, "_ORACLE_DIR_FD_SUPPORTED", False)
    before, state = _rewrite_on_read(source, replacement_text, monkeypatch)

    result = evaluate_oracle(
        FileOracle(id="rewrite", path=source.name, mode="text_equals", expected=original_text),
        root=tmp_path,
    )

    assert state["rewritten"] is True
    assert result.passed is False
    assert "changed while reading" in (result.error or "")
    after = source.stat(follow_symlinks=False)
    assert after.st_ino == before.st_ino
    assert after.st_size == before.st_size
    assert after.st_mtime_ns == before.st_mtime_ns
    assert after.st_ctime_ns != before.st_ctime_ns
