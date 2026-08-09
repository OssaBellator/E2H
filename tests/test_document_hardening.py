from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.document import load_mapping_document
from e2h.genome import (
    GenomeApplication,
    GenomeError,
    HarnessGenome,
    apply_genome,
    capsule_sha256,
    load_genome,
)
from e2h.loader import CapsuleLoadError, load_capsule
from e2h.models import TaskCapsule


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "strict-document-base",
            "goal": "Verify strict document parsing.",
            "success": {"commands": [{"id": "contract", "argv": ["true"]}]},
        }
    )


def test_genome_loader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "genome.json"
    path.write_text(
        '{"id":"first","id":"second","base_capsule_sha256":"'
        + "0" * 64
        + '","patches":[{"id":"goal","op":"goal.set","value":"new"}]}',
        encoding="utf-8",
    )

    with pytest.raises(GenomeError, match="duplicate object key"):
        load_genome(path)


def test_genome_loader_rejects_duplicate_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "genome.yaml"
    path.write_text(
        "id: first\nid: second\nbase_capsule_sha256: "
        + "0" * 64
        + "\npatches:\n  - id: goal\n    op: goal.set\n    value: new\n",
        encoding="utf-8",
    )

    with pytest.raises(GenomeError, match="duplicate key"):
        load_genome(path)


def test_capsule_loader_rejects_duplicate_nested_keys(tmp_path: Path) -> None:
    path = tmp_path / "capsule.yaml"
    path.write_text(
        "id: duplicate-capsule\n"
        "goal: Verify parsing.\n"
        "success:\n"
        "  commands:\n"
        "    - id: contract\n"
        "      argv: [true]\n"
        "      argv: [false]\n",
        encoding="utf-8",
    )

    with pytest.raises(CapsuleLoadError, match="duplicate key"):
        load_capsule(path)


@pytest.mark.parametrize("scalar", [".nan", ".inf", "-.inf"])
def test_mapping_document_rejects_non_finite_yaml_numbers(
    tmp_path: Path,
    scalar: str,
) -> None:
    path = tmp_path / "document.yaml"
    path.write_text(f"value: {scalar}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="non-finite number"):
        load_mapping_document(path, noun="test document")


def test_mapping_document_rejects_non_string_yaml_keys(tmp_path: Path) -> None:
    path = tmp_path / "document.yaml"
    path.write_text("1: value\n", encoding="utf-8")

    with pytest.raises(ValueError, match="mapping keys must be strings"):
        load_mapping_document(path, noun="test document")


def test_mapping_document_rejects_yaml_timestamp_values(tmp_path: Path) -> None:
    path = tmp_path / "document.yaml"
    path.write_text("created: 2026-08-09\n", encoding="utf-8")

    with pytest.raises(ValueError, match="unsupported value type date"):
        load_mapping_document(path, noun="test document")


def test_mapping_document_rejects_recursive_yaml_aliases(tmp_path: Path) -> None:
    path = tmp_path / "document.yaml"
    path.write_text("loop: &loop\n  - *loop\n", encoding="utf-8")

    with pytest.raises(ValueError, match="recursive value"):
        load_mapping_document(path, noun="test document")


def test_mapping_document_accepts_json_compatible_yaml_values(tmp_path: Path) -> None:
    path = tmp_path / "document.yaml"
    path.write_text(
        "name: example\n"
        "count: 2\n"
        "ratio: 1.5\n"
        "enabled: true\n"
        "optional: null\n"
        "nested:\n"
        "  - value\n"
        "  - 3\n",
        encoding="utf-8",
    )

    assert load_mapping_document(path, noun="test document") == {
        "name": "example",
        "count": 2,
        "ratio": 1.5,
        "enabled": True,
        "optional": None,
        "nested": ["value", 3],
    }


def test_capsule_identity_rejects_non_json_metadata() -> None:
    base = capsule()
    base.metadata = {"tags": {"one", "two"}}
    genome = HarnessGenome.model_validate(
        {
            "id": "candidate",
            "base_capsule_sha256": "0" * 64,
            "patches": [{"id": "goal", "op": "goal.set", "value": "new"}],
        }
    )

    with pytest.raises(GenomeError, match="cannot be canonically identified"):
        apply_genome(genome, base)


def test_application_patch_ids_follow_patch_identity_rules() -> None:
    base = capsule()
    genome = HarnessGenome.model_validate(
        {
            "id": "candidate",
            "base_capsule_sha256": capsule_sha256(base),
            "patches": [{"id": "goal", "op": "goal.set", "value": "new"}],
        }
    )
    payload = apply_genome(genome, base).model_dump()
    payload["applied_patch_ids"] = ["not a valid patch id"]

    with pytest.raises(ValidationError):
        GenomeApplication.model_validate(payload)
