from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.oracles import (
    ORACLE_ADAPTER,
    ORACLE_MUTATION_ENV,
    ArtifactOracle,
    FileOracle,
    JsonOracle,
    compile_oracle,
    evaluate_oracle,
    oracle_mutation_id,
    oracle_mutation_operator,
)


def test_file_oracle_modes_and_mutations(tmp_path: Path) -> None:
    path = tmp_path / "result.txt"
    path.write_text("alpha beta\n", encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    cases = [
        FileOracle(id="exists", path="result.txt", mode="exists"),
        FileOracle(
            id="equals",
            path="result.txt",
            mode="text_equals",
            expected="alpha beta\n",
        ),
        FileOracle(
            id="contains",
            path="result.txt",
            mode="text_contains",
            expected="beta",
        ),
        FileOracle(id="digest", path="result.txt", mode="sha256", expected=digest),
        FileOracle(id="absent", path="missing.txt", mode="absent"),
    ]
    for oracle in cases:
        assert evaluate_oracle(oracle, root=tmp_path).passed is True
        mutated = evaluate_oracle(
            oracle,
            root=tmp_path,
            mutation_operator=oracle_mutation_operator(oracle),
        )
        assert mutated.passed is False


def test_json_pointer_modes_and_escaping(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text(
        json.dumps({"a/b": {"~key": [1, {"value": None}]}}),
        encoding="utf-8",
    )
    equal = JsonOracle(
        id="json-value",
        path="result.json",
        pointer="/a~1b/~0key/1/value",
        expected=None,
    )
    assert evaluate_oracle(equal, root=tmp_path).passed is True
    assert (
        evaluate_oracle(
            equal,
            root=tmp_path,
            mutation_operator=oracle_mutation_operator(equal),
        ).passed
        is False
    )
    assert (
        evaluate_oracle(
            JsonOracle(id="present", path="result.json", pointer="/a~1b", mode="exists"),
            root=tmp_path,
        ).passed
        is True
    )
    assert (
        evaluate_oracle(
            JsonOracle(id="absent", path="result.json", pointer="/missing", mode="absent"),
            root=tmp_path,
        ).passed
        is True
    )


def test_artifact_oracle_digest_and_size(tmp_path: Path) -> None:
    path = tmp_path / "artifact.bin"
    path.write_bytes(b"artifact")
    digest = hashlib.sha256(b"artifact").hexdigest()
    oracle = ArtifactOracle(
        id="artifact",
        path="artifact.bin",
        sha256=digest,
        min_bytes=8,
        max_bytes=8,
    )
    result = evaluate_oracle(oracle, root=tmp_path)
    assert result.passed is True
    assert result.observed == {"sha256": digest, "bytes": 8}
    assert (
        evaluate_oracle(
            oracle,
            root=tmp_path,
            mutation_operator=oracle_mutation_operator(oracle),
        ).passed
        is False
    )


def test_oracle_paths_and_json_are_strict(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="relative"):
        FileOracle(id="bad", path="/etc/passwd", mode="exists")
    with pytest.raises(ValidationError, match="parent traversal"):
        FileOracle(id="bad", path="../secret", mode="exists")
    with pytest.raises(ValidationError, match="invalid escape"):
        JsonOracle(id="bad", path="x.json", pointer="/~2")
    (tmp_path / "link").symlink_to(tmp_path.parent)
    result = evaluate_oracle(
        FileOracle(id="escape", path="link/outside", mode="exists"),
        root=tmp_path,
    )
    assert result.passed is False
    assert "escapes" in (result.error or "")


def test_compile_oracle_is_deterministic() -> None:
    oracle = JsonOracle(id="contract", path="result.json", pointer="/ok", expected=True)
    first = compile_oracle(oracle)
    second = compile_oracle(ORACLE_ADAPTER.validate_python(oracle.model_dump(mode="json")))
    assert first == second
    assert first.argv[:3] == ["python", "-m", "e2h.oracle_cli"]
    assert oracle_mutation_id(oracle) == "oracle-contract"
    assert ORACLE_MUTATION_ENV not in first.env


def test_cli_exit_codes(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text('{"ok":true}', encoding="utf-8")
    oracle = JsonOracle(id="contract", path="result.json", pointer="/ok", expected=True)
    check = compile_oracle(oracle)
    passed = subprocess.run(
        check.argv,
        cwd=tmp_path,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert passed.returncode == 0
    assert json.loads(passed.stdout)["passed"] is True
    failed = subprocess.run(
        check.argv,
        cwd=tmp_path,
        env={**os.environ, ORACLE_MUTATION_ENV: oracle_mutation_operator(oracle)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["passed"] is False


def test_invalid_operator_is_reported(tmp_path: Path) -> None:
    oracle = FileOracle(id="exists", path="x", mode="absent")
    result = evaluate_oracle(oracle, root=tmp_path, mutation_operator="wrong")
    assert result.passed is False
    assert "unsupported mutation" in (result.error or "")
