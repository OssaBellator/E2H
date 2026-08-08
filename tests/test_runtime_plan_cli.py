from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import e2h.runtime_cli as runtime_cli
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule
from e2h.openai_runtime import OpenAIResponsesInvocation
from e2h.runtime_cli import runtime_app
from e2h.runtime_plan import RuntimePlanError
from e2h.variants import HarnessVariantDocument

runner = CliRunner()


class _Plan:
    def __init__(self, provider: str = "openai-responses") -> None:
        self.provider = SimpleNamespace(value=provider)
        self.request = SimpleNamespace(
            invocation_id="plan-001",
            model="provider-test",
            route_target_id="primary",
            request_sha256="1" * 64,
        )

    def model_dump_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            {
                "schema_version": "0.1",
                "provider": self.provider.value,
                "request": {
                    "invocation_id": self.request.invocation_id,
                    "model": self.request.model,
                    "route_target_id": self.request.route_target_id,
                    "request_sha256": self.request.request_sha256,
                },
            },
            indent=indent,
        )


def _input_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = tuple(tmp_path / name for name in ("capsule.json", "variant.json", "invocation.json"))
    for path in paths:
        path.write_text("{}\n", encoding="utf-8")
    return paths


def _real_openai_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    capsule = TaskCapsule.model_validate(
        {
            "id": "runtime-plan-cli",
            "goal": "Plan one provider request from the CLI.",
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
    variant = HarnessVariantDocument.model_validate(
        {
            "base_capsule_sha256": capsule_sha256(capsule),
            "variant": {
                "id": "runtime-plan-cli-variant",
                "prompt": {
                    "id": "prompt",
                    "variables": ["task"],
                    "messages": [
                        {
                            "id": "system",
                            "role": "system",
                            "content": "Preserve observable evidence.",
                        },
                        {
                            "id": "user",
                            "role": "user",
                            "content": "Execute ${task}.",
                        },
                    ],
                },
                "routing": {
                    "id": "routing",
                    "targets": [
                        {
                            "id": "primary",
                            "provider": "openai",
                            "model": "openai-test",
                            "capabilities": ["text"],
                        }
                    ],
                    "rules": [],
                    "fallback_target": "primary",
                },
            },
        }
    )
    invocation = OpenAIResponsesInvocation.model_validate(
        {
            "id": "plan-cli-001",
            "variables": {"task": "the deterministic check"},
            "max_output_tokens": 128,
        }
    )
    paths = _input_paths(tmp_path)
    paths[0].write_text(capsule.model_dump_json(indent=2) + "\n", encoding="utf-8")
    paths[1].write_text(variant.model_dump_json(indent=2) + "\n", encoding="utf-8")
    paths[2].write_text(invocation.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return paths


def test_plan_cli_writes_exact_json_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    output = tmp_path / "plan.json"
    observed: dict[str, object] = {}

    def fake_load(
        provider: str,
        capsule_path: Path,
        variant_path: Path,
        invocation_path: Path,
    ) -> _Plan:
        observed.update(
            provider=provider,
            capsule=capsule_path,
            variant=variant_path,
            invocation=invocation_path,
        )
        return _Plan(provider)

    monkeypatch.setattr(runtime_cli, "load_runtime_request_plan", fake_load)
    result = runner.invoke(
        runtime_app,
        [
            "plan",
            "openai-responses",
            str(capsule),
            str(variant),
            str(invocation),
            "--output",
            str(output),
            "--json",
        ],
        env={
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "GEMINI_API_KEY": "",
        },
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["provider"] == "openai-responses"
    assert json.loads(output.read_text(encoding="utf-8"))["provider"] == "openai-responses"
    assert observed == {
        "provider": "openai-responses",
        "capsule": capsule,
        "variant": variant,
        "invocation": invocation,
    }


def test_plan_cli_runs_actual_file_backed_planner_without_credentials(tmp_path: Path) -> None:
    capsule, variant, invocation = _real_openai_inputs(tmp_path)
    result = runner.invoke(
        runtime_app,
        [
            "plan",
            "openai-responses",
            str(capsule),
            str(variant),
            str(invocation),
            "--json",
        ],
        env={
            "OPENAI_API_KEY": "",
            "ANTHROPIC_API_KEY": "",
            "GEMINI_API_KEY": "",
        },
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["provider"] == "openai-responses"
    assert payload["request"]["invocation_id"] == "plan-cli-001"
    assert payload["request"]["model"] == "openai-test"
    assert payload["request"]["route_target_id"] == "primary"
    assert len(payload["request"]["request_sha256"]) == 64
    assert payload["request"]["body"]["input"][-1] == {
        "role": "user",
        "content": "Execute the deterministic check.",
    }


def test_plan_cli_renders_summary_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    monkeypatch.setattr(
        runtime_cli,
        "load_runtime_request_plan",
        lambda *args: _Plan("anthropic-messages"),
    )

    result = runner.invoke(
        runtime_app,
        [
            "plan",
            "anthropic-messages",
            str(capsule),
            str(variant),
            str(invocation),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "E2H runtime plan: plan-001" in result.stdout
    assert "anthropic-messages" in result.stdout
    assert "provider-test" in result.stdout
    assert "primary" in result.stdout
    assert "1" * 64 in result.stdout


def test_plan_cli_normalizes_planner_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)

    def fail(*args: object) -> object:
        raise RuntimePlanError("unsupported runtime provider 'other'")

    monkeypatch.setattr(runtime_cli, "load_runtime_request_plan", fail)
    result = runner.invoke(
        runtime_app,
        [
            "plan",
            "other",
            str(capsule),
            str(variant),
            str(invocation),
        ],
    )

    assert result.exit_code == 2
    normalized = " ".join(result.stderr.split())
    assert "Unable to plan runtime request" in normalized
    assert "unsupported runtime provider" in normalized
