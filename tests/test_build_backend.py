from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
EXPECTED_BACKEND = "hatchling==1.31.0"


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def test_isolated_build_backend_is_exactly_pinned() -> None:
    build_system = _pyproject()["build-system"]
    assert isinstance(build_system, dict)
    assert build_system["build-backend"] == "hatchling.build"
    assert build_system["requires"] == [EXPECTED_BACKEND]


def test_build_backend_pin_is_not_a_range() -> None:
    requirement = EXPECTED_BACKEND
    assert requirement.startswith("hatchling==")
    version = requirement.removeprefix("hatchling==")
    assert version == "1.31.0"
    assert all(marker not in version for marker in (">", "<", "~", "^", "*", ","))


def test_build_backend_is_not_published_as_runtime_or_dev_dependency() -> None:
    pyproject = _pyproject()
    project = pyproject["project"]
    assert isinstance(project, dict)
    dependencies = project["dependencies"]
    optional = project["optional-dependencies"]
    assert isinstance(dependencies, list)
    assert isinstance(optional, dict)
    dev = optional["dev"]
    assert isinstance(dev, list)
    assert not any(str(item).startswith("hatchling") for item in dependencies)
    assert not any(str(item).startswith("hatchling") for item in dev)
