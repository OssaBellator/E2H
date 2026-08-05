from __future__ import annotations

from pathlib import Path

import pytest

from e2h.loader import (
    CapsuleLoadError,
    ExperimentLoadError,
    load_capsule,
    load_experiment,
)


def test_load_yaml_capsule(tmp_path: Path) -> None:
    path = tmp_path / "capsule.yaml"
    path.write_text(
        "id: demo\n"
        "goal: Demonstrate loading\n"
        "success:\n"
        "  commands:\n"
        "    - id: ok\n"
        "      argv: [python, -V]\n",
        encoding="utf-8",
    )
    assert load_capsule(path).id == "demo"


def test_load_json_capsule(tmp_path: Path) -> None:
    path = tmp_path / "capsule.json"
    path.write_text(
        '{"id":"demo","goal":"Load JSON","success":'
        '{"commands":[{"id":"ok","argv":["python","-V"]}]}}',
        encoding="utf-8",
    )
    assert load_capsule(path).goal == "Load JSON"


def test_reject_unsupported_extension(tmp_path: Path) -> None:
    path = tmp_path / "capsule.txt"
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(CapsuleLoadError, match="must use"):
        load_capsule(path)


def test_reject_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "capsule.yaml"
    path.write_text("- item\n", encoding="utf-8")
    with pytest.raises(CapsuleLoadError, match="root"):
        load_capsule(path)


def test_reject_invalid_yaml(tmp_path: Path) -> None:
    path = tmp_path / "capsule.yaml"
    path.write_text("id: [unterminated\n", encoding="utf-8")
    with pytest.raises(CapsuleLoadError, match="syntax"):
        load_capsule(path)


def test_reject_schema_validation_error(tmp_path: Path) -> None:
    path = tmp_path / "capsule.yaml"
    path.write_text("id: demo\ngoal: missing success\n", encoding="utf-8")
    with pytest.raises(CapsuleLoadError, match="success"):
        load_capsule(path)


def test_missing_file_is_wrapped(tmp_path: Path) -> None:
    with pytest.raises(CapsuleLoadError, match="unable to read"):
        load_capsule(tmp_path / "missing.yaml")


def test_load_yaml_experiment(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text(
        "id: demo\ncapsule: capsule.yaml\nvariants:\n  - id: baseline\n",
        encoding="utf-8",
    )
    assert load_experiment(path).variants[0].id == "baseline"


def test_load_json_experiment(tmp_path: Path) -> None:
    path = tmp_path / "experiment.json"
    path.write_text(
        '{"id":"demo","capsule":"capsule.yaml","variants":[{"id":"baseline"}]}',
        encoding="utf-8",
    )
    assert load_experiment(path).capsule == "capsule.yaml"


def test_reject_invalid_experiment(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text("id: demo\n", encoding="utf-8")
    with pytest.raises(ExperimentLoadError, match="capsule"):
        load_experiment(path)


def test_reject_experiment_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "experiment.yaml"
    path.write_text("- item\n", encoding="utf-8")
    with pytest.raises(ExperimentLoadError, match="root"):
        load_experiment(path)
