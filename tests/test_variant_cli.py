from __future__ import annotations

import json
from pathlib import Path

import typer
import yaml
from typer.testing import CliRunner

from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.variant_cli import variant_app
from e2h.variants import HarnessVariant, HarnessVariantDocument, variant_document_sha256

app = typer.Typer()
app.add_typer(variant_app, name="variant")
runner = CliRunner()


def write_capsule(path: Path) -> TaskCapsule:
    capsule = TaskCapsule.model_validate(
        {
            "id": "variant-cli-base",
            "goal": "Validate a typed variant.",
            "success": {
                "commands": [
                    {
                        "id": "contract",
                        "argv": ["python", "-c", "print('ok')"],
                    }
                ]
            },
        }
    )
    path.write_text(capsule.model_dump_json(indent=2), encoding="utf-8")
    return capsule


def write_variant(path: Path, capsule: TaskCapsule) -> HarnessVariantDocument:
    document = HarnessVariantDocument(
        base_capsule_sha256=capsule_sha256(capsule),
        variant=HarnessVariant.model_validate(
            {
                "id": "typed-cli",
                "prompt": {
                    "id": "prompt",
                    "messages": [
                        {
                            "id": "system",
                            "role": "system",
                            "content": "Be deterministic.",
                        }
                    ],
                },
            }
        ),
    )
    path.write_text(
        yaml.safe_dump(document.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return document


def test_validate_digest_and_schema_flow(tmp_path: Path) -> None:
    capsule_path = tmp_path / "capsule.json"
    variant_path = tmp_path / "variant.yaml"
    verification_path = tmp_path / "verification.json"
    schema_path = tmp_path / "variant-schema.json"
    capsule = write_capsule(capsule_path)
    document = write_variant(variant_path, capsule)

    result = runner.invoke(
        app,
        [
            "variant",
            "validate",
            str(variant_path),
            str(capsule_path),
            "--output",
            str(verification_path),
        ],
    )
    assert result.exit_code == 0
    assert "E2H typed variant" in result.stdout
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    assert verification["variant_id"] == "typed-cli"
    assert verification["dimensions"] == ["prompt"]

    result = runner.invoke(app, ["variant", "digest", str(variant_path)])
    assert result.exit_code == 0
    assert result.stdout.strip() == variant_document_sha256(document)

    result = runner.invoke(
        app,
        ["variant", "schema", "--output", str(schema_path)],
    )
    assert result.exit_code == 0
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["title"] == "HarnessVariantDocument"

    result = runner.invoke(app, ["variant", "schema"])
    assert result.exit_code == 0
    assert "base_capsule_sha256" in result.stdout


def test_validate_json_and_invalid_binding(tmp_path: Path) -> None:
    capsule_path = tmp_path / "capsule.json"
    variant_path = tmp_path / "variant.yaml"
    capsule = write_capsule(capsule_path)
    write_variant(variant_path, capsule)

    result = runner.invoke(
        app,
        ["variant", "validate", str(variant_path), str(capsule_path), "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["document_sha256"]
    assert payload["variant_sha256"]

    wrong = write_capsule(tmp_path / "wrong.json")
    wrong.goal = "Wrong base."
    wrong_path = tmp_path / "wrong.json"
    wrong_path.write_text(wrong.model_dump_json(indent=2), encoding="utf-8")
    result = runner.invoke(
        app,
        ["variant", "validate", str(variant_path), str(wrong_path)],
    )
    assert result.exit_code == 2
    assert "base capsule digest" in result.stderr


def test_digest_returns_two_for_invalid_document(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    path.write_text("{}", encoding="utf-8")
    result = runner.invoke(app, ["variant", "digest", str(path)])
    assert result.exit_code == 2
    assert "Invalid variant" in result.stderr
