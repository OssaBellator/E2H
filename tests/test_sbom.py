from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from e2h.release_cli import release_app
from e2h.sbom import (
    SbomCanonicalizationError,
    canonicalize_cyclonedx_sbom,
    canonicalize_cyclonedx_sbom_file,
)

runner = CliRunner()


def _sbom(*, serial: str, timestamp: str) -> dict[str, object]:
    return {
        "$schema": "http://cyclonedx.org/schema/bom-1.5.schema.json",
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "name": "uv",
                        "version": "0.12.2",
                    }
                ]
            },
            "component": {
                "type": "application",
                "name": "e2h",
                "version": "0.27.0",
                "bom-ref": "e2h==0.27.0",
            },
        },
        "components": [
            {
                "type": "library",
                "name": "pydantic",
                "version": "2.13.4",
                "bom-ref": "pydantic==2.13.4",
            }
        ],
        "dependencies": [
            {"ref": "e2h==0.27.0", "dependsOn": ["pydantic==2.13.4"]},
            {"ref": "pydantic==2.13.4", "dependsOn": []},
        ],
    }


def test_canonicalization_removes_only_per_generation_identity() -> None:
    first = canonicalize_cyclonedx_sbom(
        _sbom(
            serial="urn:uuid:11111111-1111-4111-8111-111111111111",
            timestamp="2026-08-07T11:00:00Z",
        )
    )
    second = canonicalize_cyclonedx_sbom(
        _sbom(
            serial="urn:uuid:22222222-2222-4222-8222-222222222222",
            timestamp="2026-08-07T11:00:01Z",
        )
    )
    assert first == second
    parsed = json.loads(first)
    assert "serialNumber" not in parsed
    assert "timestamp" not in parsed["metadata"]
    assert parsed["metadata"]["tools"]["components"][0]["name"] == "uv"
    assert parsed["metadata"]["component"]["version"] == "0.27.0"
    assert parsed["components"][0]["name"] == "pydantic"
    assert parsed["dependencies"][0]["dependsOn"] == ["pydantic==2.13.4"]
    assert first.endswith("\n")


def test_canonicalization_is_stable_for_already_canonical_document() -> None:
    rendered = canonicalize_cyclonedx_sbom(
        _sbom(
            serial="urn:uuid:11111111-1111-4111-8111-111111111111",
            timestamp="2026-08-07T11:00:00Z",
        )
    )
    parsed = json.loads(rendered)
    assert canonicalize_cyclonedx_sbom(parsed) == rendered


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"bomFormat": "SPDX"}, "bomFormat"),
        ({"specVersion": "1.6"}, "specVersion"),
        ({"version": 2}, "version must be 1"),
        ({"metadata": []}, "metadata must be an object"),
        (
            {"metadata": {"component": {"name": "other"}}},
            "component must identify e2h",
        ),
        ({"components": {}}, "components must be an array"),
        ({"components": ["bad"]}, "components must contain objects"),
        ({"dependencies": {}}, "dependencies must be an array"),
        ({"dependencies": ["bad"]}, "dependencies must contain objects"),
    ],
)
def test_canonicalization_rejects_invalid_cyclonedx(
    mutation: dict[str, object],
    message: str,
) -> None:
    payload = _sbom(
        serial="urn:uuid:11111111-1111-4111-8111-111111111111",
        timestamp="2026-08-07T11:00:00Z",
    )
    payload.update(mutation)
    with pytest.raises(SbomCanonicalizationError, match=message):
        canonicalize_cyclonedx_sbom(payload)


def test_file_loader_rejects_invalid_input(tmp_path: Path) -> None:
    invalid_utf8 = tmp_path / "invalid.cdx.json"
    invalid_utf8.write_bytes(b"\xff\xfe")
    with pytest.raises(SbomCanonicalizationError, match="must be UTF-8"):
        canonicalize_cyclonedx_sbom_file(invalid_utf8)

    invalid_json = tmp_path / "invalid-json.cdx.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(SbomCanonicalizationError, match="invalid SBOM JSON"):
        canonicalize_cyclonedx_sbom_file(invalid_json)

    invalid_constant = tmp_path / "invalid-constant.cdx.json"
    invalid_constant.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(SbomCanonicalizationError, match="non-standard JSON constant"):
        canonicalize_cyclonedx_sbom_file(invalid_constant)

    invalid_root = tmp_path / "invalid-root.cdx.json"
    invalid_root.write_text("[]", encoding="utf-8")
    with pytest.raises(SbomCanonicalizationError, match="root must be an object"):
        canonicalize_cyclonedx_sbom_file(invalid_root)


def test_cli_canonicalizes_sbom_and_reports_invalid_input(tmp_path: Path) -> None:
    source = tmp_path / "raw.cdx.json"
    source.write_text(
        json.dumps(
            _sbom(
                serial="urn:uuid:11111111-1111-4111-8111-111111111111",
                timestamp="2026-08-07T11:00:00Z",
            )
        ),
        encoding="utf-8",
    )
    output = tmp_path / "canonical.cdx.json"
    result = runner.invoke(
        release_app,
        ["canonicalize-sbom", str(source), "--output", str(output)],
    )
    assert result.exit_code == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert "serialNumber" not in payload
    assert "timestamp" not in payload["metadata"]

    bad = tmp_path / "bad.cdx.json"
    bad.write_text('{"bomFormat":"SPDX"}', encoding="utf-8")
    failure = runner.invoke(
        release_app,
        ["canonicalize-sbom", str(bad), "--output", str(tmp_path / "bad-out.json")],
    )
    assert failure.exit_code == 2
    assert "Unable to canonicalize SBOM" in failure.stderr
