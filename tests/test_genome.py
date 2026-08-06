from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from e2h.genome import (
    GenomeApplication,
    GenomeError,
    HarnessGenome,
    apply_genome,
    capsule_sha256,
    genome_sha256,
    load_genome,
    load_genome_application,
    materialize_application,
)
from e2h.models import TaskCapsule


def capsule() -> TaskCapsule:
    return TaskCapsule.model_validate(
        {
            "id": "genome-base",
            "goal": "Run the baseline checks.",
            "allowed_actions": {"tools": ["command"], "network": "deny"},
            "limits": {
                "max_commands": 10,
                "default_timeout_seconds": 30,
                "max_output_chars": 20_000,
            },
            "success": {
                "commands": [
                    {
                        "id": "contract",
                        "argv": ["python", "-c", "print('baseline')"],
                        "env": {"REMOVE_ME": "yes"},
                    },
                    {
                        "id": "secondary",
                        "argv": ["python", "-c", "print('secondary')"],
                    },
                ]
            },
            "metadata": {"evidence_sha256": "a" * 64},
        }
    )


def genome_payload(base: TaskCapsule) -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "id": "candidate-a",
        "base_capsule_sha256": capsule_sha256(base),
        "metadata": {"generation": 1, "optimizer": "manual"},
        "patches": [
            {"id": "goal", "op": "goal.set", "value": "Run the candidate checks."},
            {
                "id": "network",
                "op": "allowed_actions.network.set",
                "value": "allow",
            },
            {
                "id": "argv",
                "op": "check.argv.set",
                "check_id": "contract",
                "argv": ["python", "-c", "print('candidate')"],
            },
            {
                "id": "cwd",
                "op": "check.cwd.set",
                "check_id": "contract",
                "value": "src",
            },
            {
                "id": "env-set",
                "op": "check.env.set",
                "check_id": "contract",
                "name": "MODE",
                "value": "candidate",
            },
            {
                "id": "env-remove",
                "op": "check.env.remove",
                "check_id": "contract",
                "name": "REMOVE_ME",
            },
            {
                "id": "timeout",
                "op": "check.timeout.set",
                "check_id": "contract",
                "seconds": 12.5,
            },
            {
                "id": "exits",
                "op": "check.expected_exit_codes.set",
                "check_id": "contract",
                "values": [2, 0],
            },
            {
                "id": "continue",
                "op": "check.continue_on_failure.set",
                "check_id": "secondary",
                "value": True,
            },
            {
                "id": "default-timeout",
                "op": "limits.default_timeout.set",
                "seconds": 45,
            },
            {
                "id": "output",
                "op": "limits.max_output_chars.set",
                "value": 4096,
            },
        ],
    }


def test_apply_genome_covers_all_patch_types_without_mutating_input() -> None:
    base = capsule()
    original = base.model_dump(mode="json")
    genome = HarnessGenome.model_validate(genome_payload(base))

    application = apply_genome(genome, base)

    assert base.model_dump(mode="json") == original
    assert application.genome_id == "candidate-a"
    assert application.genome_sha256 == genome_sha256(genome)
    assert application.base_capsule_sha256 == capsule_sha256(base)
    assert application.result_capsule_sha256 == capsule_sha256(application.capsule)
    assert application.applied_patch_ids == [patch.id for patch in genome.patches]
    assert application.capsule.goal == "Run the candidate checks."
    assert application.capsule.allowed_actions.network == "allow"
    contract = application.capsule.success.commands[0]
    assert contract.argv[-1] == "print('candidate')"
    assert contract.cwd == "src"
    assert contract.env == {"MODE": "candidate"}
    assert contract.timeout_seconds == 12.5
    assert contract.expected_exit_codes == {0, 2}
    assert application.capsule.success.commands[1].continue_on_failure is True
    assert application.capsule.limits.default_timeout_seconds == 45
    assert application.capsule.limits.max_output_chars == 4096
    assert application.capsule.metadata == base.metadata


def test_digests_are_canonical() -> None:
    first = capsule()
    second = TaskCapsule.model_validate(first.model_dump())
    second.success.commands[0].expected_exit_codes = {2, 0}
    first.success.commands[0].expected_exit_codes = {0, 2}
    assert capsule_sha256(first) == capsule_sha256(second)

    payload = genome_payload(first)
    payload["metadata"] = {"b": 2, "a": 1}
    left = HarnessGenome.model_validate(payload)
    payload["metadata"] = {"a": 1, "b": 2}
    right = HarnessGenome.model_validate(payload)
    assert genome_sha256(left) == genome_sha256(right)


def test_base_digest_mismatch_is_rejected() -> None:
    base = capsule()
    payload = genome_payload(base)
    payload["base_capsule_sha256"] = "0" * 64
    with pytest.raises(GenomeError, match="base capsule digest"):
        apply_genome(HarnessGenome.model_validate(payload), base)


def test_unknown_check_noop_and_missing_removal_are_rejected() -> None:
    base = capsule()
    common = {
        "schema_version": "0.1",
        "id": "bad",
        "base_capsule_sha256": capsule_sha256(base),
    }
    unknown = HarnessGenome.model_validate(
        {
            **common,
            "patches": [
                {
                    "id": "unknown",
                    "op": "check.argv.set",
                    "check_id": "missing",
                    "argv": ["true"],
                }
            ],
        }
    )
    with pytest.raises(GenomeError, match="unknown check id"):
        apply_genome(unknown, base)

    noop = HarnessGenome.model_validate(
        {
            **common,
            "patches": [
                {"id": "noop", "op": "goal.set", "value": base.goal}
            ],
        }
    )
    with pytest.raises(GenomeError, match="no-op"):
        apply_genome(noop, base)

    missing = HarnessGenome.model_validate(
        {
            **common,
            "patches": [
                {
                    "id": "remove",
                    "op": "check.env.remove",
                    "check_id": "contract",
                    "name": "MISSING",
                }
            ],
        }
    )
    with pytest.raises(GenomeError, match="remove missing"):
        apply_genome(missing, base)


def test_timeout_can_be_reset_to_inherited_default() -> None:
    base = capsule()
    base.success.commands[0].timeout_seconds = 5
    genome = HarnessGenome.model_validate(
        {
            "id": "reset-timeout",
            "base_capsule_sha256": capsule_sha256(base),
            "patches": [
                {
                    "id": "reset",
                    "op": "check.timeout.set",
                    "check_id": "contract",
                    "seconds": None,
                }
            ],
        }
    )
    assert apply_genome(genome, base).capsule.success.commands[0].timeout_seconds is None


def test_patch_identity_and_target_conflicts_are_rejected() -> None:
    base = capsule()
    common = {
        "id": "ambiguous",
        "base_capsule_sha256": capsule_sha256(base),
    }
    with pytest.raises(ValidationError, match="patch ids must be unique"):
        HarnessGenome.model_validate(
            {
                **common,
                "patches": [
                    {"id": "same", "op": "goal.set", "value": "one"},
                    {
                        "id": "same",
                        "op": "limits.max_output_chars.set",
                        "value": 1000,
                    },
                ],
            }
        )
    with pytest.raises(ValidationError, match="same target"):
        HarnessGenome.model_validate(
            {
                **common,
                "patches": [
                    {"id": "one", "op": "goal.set", "value": "one"},
                    {"id": "two", "op": "goal.set", "value": "two"},
                ],
            }
        )
    with pytest.raises(ValidationError, match="same target"):
        HarnessGenome.model_validate(
            {
                **common,
                "patches": [
                    {
                        "id": "set",
                        "op": "check.env.set",
                        "check_id": "contract",
                        "name": "MODE",
                        "value": "one",
                    },
                    {
                        "id": "remove",
                        "op": "check.env.remove",
                        "check_id": "contract",
                        "name": "MODE",
                    },
                ],
            }
        )


def test_patch_value_validation_is_strict() -> None:
    base = capsule()
    common = {"id": "invalid", "base_capsule_sha256": capsule_sha256(base)}
    invalid_patches = [
        {"id": "argv", "op": "check.argv.set", "check_id": "contract", "argv": [""]},
        {
            "id": "cwd",
            "op": "check.cwd.set",
            "check_id": "contract",
            "value": "../escape",
        },
        {
            "id": "name",
            "op": "check.env.set",
            "check_id": "contract",
            "name": "BAD=KEY",
            "value": "x",
        },
        {
            "id": "value",
            "op": "check.env.set",
            "check_id": "contract",
            "name": "GOOD",
            "value": "bad\x00value",
        },
        {
            "id": "timeout",
            "op": "check.timeout.set",
            "check_id": "contract",
            "seconds": 0,
        },
        {
            "id": "exits",
            "op": "check.expected_exit_codes.set",
            "check_id": "contract",
            "values": [0, 0],
        },
    ]
    for patch in invalid_patches:
        with pytest.raises(ValidationError):
            HarnessGenome.model_validate({**common, "patches": [patch]})


def test_metadata_must_be_canonical_and_bounded() -> None:
    base = capsule()
    common = {
        "id": "metadata",
        "base_capsule_sha256": capsule_sha256(base),
        "patches": [{"id": "goal", "op": "goal.set", "value": "new"}],
    }
    with pytest.raises(ValidationError, match="canonical JSON"):
        HarnessGenome.model_validate({**common, "metadata": {"bad": float("nan")}})
    with pytest.raises(ValidationError, match="metadata exceeds"):
        HarnessGenome.model_validate({**common, "metadata": {"large": "x" * 70_000}})


def test_application_detects_tampering_and_materializes_a_detached_copy() -> None:
    base = capsule()
    application = apply_genome(HarnessGenome.model_validate(genome_payload(base)), base)
    payload = application.model_dump()
    payload["capsule"]["goal"] = "tampered"
    with pytest.raises(ValidationError, match="digest does not match"):
        GenomeApplication.model_validate(payload)

    payload = application.model_dump()
    payload["applied_patch_ids"].append(payload["applied_patch_ids"][0])
    with pytest.raises(ValidationError, match="must be unique"):
        GenomeApplication.model_validate(payload)

    materialized = materialize_application(application)
    materialized.goal = "changed"
    assert application.capsule.goal == "Run the candidate checks."


def test_loaders_accept_json_and_yaml_and_reject_invalid_documents(tmp_path: Path) -> None:
    base = capsule()
    genome = HarnessGenome.model_validate(genome_payload(base))
    json_path = tmp_path / "genome.json"
    yaml_path = tmp_path / "genome.yaml"
    json_path.write_text(genome.model_dump_json(indent=2), encoding="utf-8")
    yaml_path.write_text(
        yaml.safe_dump(genome.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    assert load_genome(json_path) == genome
    assert load_genome(yaml_path) == genome

    application = apply_genome(genome, base)
    application_path = tmp_path / "application.json"
    application_path.write_text(application.model_dump_json(indent=2), encoding="utf-8")
    assert load_genome_application(application_path) == application

    invalid_extension = tmp_path / "genome.txt"
    invalid_extension.write_text("{}", encoding="utf-8")
    with pytest.raises(GenomeError, match="must use"):
        load_genome(invalid_extension)

    root_list = tmp_path / "list.json"
    root_list.write_text("[]", encoding="utf-8")
    with pytest.raises(GenomeError, match="root must be an object"):
        load_genome(root_list)

    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(GenomeError, match="invalid genome syntax"):
        load_genome(nonfinite)

    oversized = tmp_path / "oversized.json"
    oversized.write_text(" " * 1_048_577, encoding="utf-8")
    with pytest.raises(GenomeError, match="exceeds"):
        load_genome(oversized)

    missing = tmp_path / "missing.json"
    with pytest.raises(GenomeError, match="unable to read"):
        load_genome(missing)


def test_invalid_genome_and_application_models_are_wrapped(tmp_path: Path) -> None:
    bad_genome = tmp_path / "bad.json"
    bad_genome.write_text(json.dumps({"id": "bad"}), encoding="utf-8")
    with pytest.raises(GenomeError, match="invalid genome"):
        load_genome(bad_genome)

    bad_application = tmp_path / "application.json"
    bad_application.write_text(json.dumps({"genome_id": "bad"}), encoding="utf-8")
    with pytest.raises(GenomeError, match="invalid genome application"):
        load_genome_application(bad_application)
