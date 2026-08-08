from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel
from typer import Typer
from typer.testing import CliRunner

import e2h.runtime_plan_cli as plan_cli
from e2h.main_cli import app
from e2h.runtime_plan import RuntimePlanError, RuntimeProvider

runner = CliRunner()


class _Request(BaseModel):
    invocation_id: str = "plan-001"
    model: str = "provider-test-model"
    route_target_id: str = "primary"
    request_sha256: str = "1" * 64


class _Plan(BaseModel):
    provider: RuntimeProvider = RuntimeProvider.OPENAI_RESPONSES
    request: _Request = _Request()

    @property
    def request_sha256(self) -> str:
        return self.request.request_sha256


def _input_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = tuple(tmp_path / name for name in ("capsule.json", "variant.json", "invocation.json"))
    for path in paths:
        path.write_text("{}\n", encoding="utf-8")
    return paths


def _command_app() -> Typer:
    command_app = Typer()
    command_app.command("plan")(plan_cli.plan_runtime_request_command)
    return command_app


def test_plan_command_writes_json_without_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    output = tmp_path / "plan.json"
    observed: dict[str, object] = {}

    def fake_load(
        provider: RuntimeProvider,
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
        return _Plan()

    monkeypatch.setattr(plan_cli, "load_runtime_request_plan", fake_load)
    result = runner.invoke(
        _command_app(),
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
    payload = json.loads(result.stdout)
    assert payload["provider"] == "openai-responses"
    assert payload["request"]["request_sha256"] == "1" * 64
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert observed == {
        "provider": RuntimeProvider.OPENAI_RESPONSES,
        "capsule": capsule,
        "variant": variant,
        "invocation": invocation,
    }


def test_plan_command_human_summary_and_output_notice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    output = tmp_path / "plan.json"
    monkeypatch.setattr(plan_cli, "load_runtime_request_plan", lambda *args: _Plan())

    result = runner.invoke(
        _command_app(),
        [
            "plan",
            "openai-responses",
            str(capsule),
            str(variant),
            str(invocation),
            "--output",
            str(output),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "plan-001" in result.stdout
    assert "openai-responses" in result.stdout
    assert "provider-test-model" in result.stdout
    assert "primary" in result.stdout
    assert "Wrote request plan to" in result.stdout
    assert output.is_file()


def test_plan_command_normalizes_planner_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)

    def fail(*args: object) -> _Plan:
        raise RuntimePlanError("invalid plan input")

    monkeypatch.setattr(plan_cli, "load_runtime_request_plan", fail)
    result = runner.invoke(
        _command_app(),
        [
            "plan",
            "anthropic-messages",
            str(capsule),
            str(variant),
            str(invocation),
        ],
    )

    assert result.exit_code == 2
    assert "Unable to plan anthropic-messages request" in result.stderr
    assert "invalid plan input" in result.stderr


def test_plan_command_rejects_unknown_provider(tmp_path: Path) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    result = runner.invoke(
        _command_app(),
        ["plan", "unknown-provider", str(capsule), str(variant), str(invocation)],
    )

    assert result.exit_code == 2
    assert "Invalid value" in result.output


def test_main_cli_registers_runtime_plan_command() -> None:
    result = runner.invoke(app, ["runtime", "--help"])

    assert result.exit_code == 0, result.output
    assert "plan" in result.stdout
    assert "openai-responses" in result.stdout
    assert "anthropic-messages" in result.stdout
    assert "gemini-generate-content" in result.stdout
