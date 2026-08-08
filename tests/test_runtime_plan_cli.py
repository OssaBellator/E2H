from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import e2h.runtime_cli as runtime_cli
from e2h.runtime_cli import runtime_app
from e2h.runtime_plan import RuntimePlanError

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
