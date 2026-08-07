from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = ROOT / "build-constraints.txt"
PYPROJECT = ROOT / "pyproject.toml"
REUSABLE = ROOT / ".github" / "workflows" / "reusable-release-build.yml"
EXPECTED = {
    "hatchling": "1.31.0",
    "packaging": "26.3",
    "pathspec": "1.1.1",
    "pluggy": "1.6.0",
    "trove-classifiers": "2026.6.1.19",
}
HASH_PATTERN = re.compile(r"--hash=sha256:[0-9a-f]{64}$")


def _workflow(path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    return parsed


def _constraint_blocks() -> dict[str, tuple[str, list[str]]]:
    blocks: dict[str, tuple[str, list[str]]] = {}
    current_name: str | None = None
    current_version = ""
    current_hashes: list[str] = []

    for raw_line in CONSTRAINTS.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not raw_line.startswith((" ", "\t")):
            if current_name is not None:
                blocks[current_name] = (current_version, current_hashes)
            requirement = line.removesuffix("\\").strip()
            name, separator, version = requirement.partition("==")
            assert separator == "==", requirement
            current_name = name
            current_version = version.strip()
            current_hashes = []
            continue
        if line.startswith("--hash="):
            current_hashes.append(line.removesuffix("\\").strip())

    if current_name is not None:
        blocks[current_name] = (current_version, current_hashes)
    return blocks


def _build_command() -> str:
    workflow = _workflow(REUSABLE)
    steps = workflow["jobs"]["build"]["steps"]
    matching = [step for step in steps if step.get("name") == "Build release artifacts twice"]
    assert len(matching) == 1
    command = matching[0]["run"]
    assert isinstance(command, str)
    return command


def test_build_constraint_graph_is_exact_and_fully_hashed() -> None:
    blocks = _constraint_blocks()
    assert {name: version for name, (version, _) in blocks.items()} == EXPECTED
    assert sum(len(hashes) for _, hashes in blocks.values()) == 10
    for name, (_, hashes) in blocks.items():
        assert len(hashes) == 2, name
        assert len(set(hashes)) == 2, name
        assert all(HASH_PATTERN.fullmatch(value) for value in hashes), name


def test_hatchling_constraint_matches_pep517_backend_pin() -> None:
    with PYPROJECT.open("rb") as handle:
        pyproject = tomllib.load(handle)
    assert pyproject["build-system"]["requires"] == [f"hatchling=={EXPECTED['hatchling']}"]


def test_reusable_release_build_requires_hashed_constraints_without_disabling_isolation() -> None:
    command = _build_command()
    assert command.count("uv build ") == 2
    assert command.count("--build-constraint build-constraints.txt") == 2
    assert command.count("--require-hashes") == 2
    assert "--no-build-isolation" not in command
    assert "--no-verify-hashes" not in command
