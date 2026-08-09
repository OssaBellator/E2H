from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from e2h.oracles import (
    FileOracle,
    JsonOracle,
    compile_oracle,
    evaluate_oracle,
)


@pytest.mark.parametrize(
    "expected",
    [
        {"nested": (1, 2)},
        {"nested": {1: "coerced key"}},
    ],
)
def test_json_oracle_rejects_json_coercible_expected(expected: Any) -> None:
    with pytest.raises(ValidationError, match="JSON-serializable"):
        JsonOracle(id="oracle", path="data.json", pointer="/value", expected=expected)


def test_compile_oracle_revalidates_mutated_expected() -> None:
    oracle = JsonOracle(id="oracle", path="data.json", pointer="/value", expected=[1, 2])
    oracle.expected = (1, 2)

    with pytest.raises(ValueError, match="invalid oracle template"):
        compile_oracle(oracle)


def test_evaluate_oracle_revalidates_mutated_path() -> None:
    oracle = FileOracle(id="oracle", path="result.txt", mode="exists")
    oracle.path = "bad\x00path"

    with pytest.raises(ValueError, match="invalid oracle template"):
        evaluate_oracle(oracle)


def test_oracle_rejects_nul_path_at_construction() -> None:
    with pytest.raises(ValidationError, match="path must not contain NUL"):
        FileOracle(id="oracle", path="bad\x00path", mode="exists")


def test_evaluate_oracle_normalizes_nul_root_error() -> None:
    oracle = FileOracle(id="oracle", path="result.txt", mode="exists")

    evaluation = evaluate_oracle(oracle, root=Path("bad\x00root"))

    assert not evaluation.passed
    assert evaluation.error is not None
    assert "oracle root must not contain NUL" in evaluation.error


def test_valid_json_oracle_compiles_without_changing_expected() -> None:
    expected = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}
    oracle = JsonOracle(
        id="oracle",
        path="data.json",
        pointer="/value",
        expected=expected,
    )

    check = compile_oracle(oracle)

    assert check.id == "oracle"
    assert check.argv[:3] == ["python", "-m", "e2h.oracle_cli"]
    assert oracle.expected == expected
