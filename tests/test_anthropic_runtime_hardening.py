from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest
from typer.testing import CliRunner

import e2h.anthropic_runtime as runtime
import e2h.runtime_cli as runtime_cli
from e2h.anthropic_runtime import (
    AnthropicMessagesInvocation,
    AnthropicMessagesRequest,
    AnthropicRuntimeError,
)
from e2h.ingest import EvidenceIngestError
from e2h.runtime_cli import runtime_app
from e2h.variants import ContextVariant, ToolVariant

runner = CliRunner()


class _Dumpable:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def model_dump_json(self, *, indent: int | None = None) -> str:
        return json.dumps(self.payload, indent=indent)


class _RuntimeResult:
    def __init__(self, *, accepted: bool) -> None:
        self.accepted = accepted
        self.policy_violations = [] if accepted else ["tool contract rejected"]
        self.request = SimpleNamespace(
            invocation_id="runtime-cli",
            model="claude-test",
            route_target_id="primary",
            request_sha256="1" * 64,
        )
        self.archive = _Dumpable({"archive": True})

    def model_dump_json(self, *, indent: int | None = None) -> str:
        return json.dumps(
            {
                "accepted": self.accepted,
                "policy_violations": self.policy_violations,
            },
            indent=indent,
        )


class _Bundle(_Dumpable):
    def __init__(self) -> None:
        super().__init__({"bundle": True})
        self.traces: list[object] = []
        self.redaction_review = _Dumpable({"review": True})


def _input_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    paths = tuple(tmp_path / name for name in ("capsule.json", "variant.json", "invocation.json"))
    for path in paths:
        path.write_text("{}\n", encoding="utf-8")
    return paths


def _stub_loaders(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_cli, "load_capsule", lambda path: object())
    monkeypatch.setattr(runtime_cli, "load_variant_document", lambda path: object())
    monkeypatch.setattr(runtime_cli, "load_anthropic_messages_invocation", lambda path: object())


def test_runtime_cli_writes_all_optional_outputs_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    archive = tmp_path / "archive.json"
    result_path = tmp_path / "result.json"
    bundle_path = tmp_path / "bundle.json"
    traces_path = tmp_path / "traces.jsonl"
    review_path = tmp_path / "review.json"
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{}\n", encoding="utf-8")
    _stub_loaders(monkeypatch)
    monkeypatch.setattr(
        runtime_cli,
        "run_anthropic_messages",
        lambda *args, **kwargs: _RuntimeResult(accepted=True),
    )
    observed: dict[str, object] = {}
    policy = object()
    monkeypatch.setattr(runtime_cli, "load_redaction_policy", lambda path: policy)

    def fake_ingest(path: Path, *, redact: bool, redaction_policy: object) -> _Bundle:
        observed.update(path=path, redact=redact, policy=redaction_policy)
        return _Bundle()

    monkeypatch.setattr(runtime_cli, "ingest_anthropic_messages_file", fake_ingest)
    monkeypatch.setattr(
        runtime_cli,
        "write_traces_jsonl",
        lambda path, traces: path.write_text("trace\n", encoding="utf-8"),
    )

    result = runner.invoke(
        runtime_app,
        [
            "anthropic-messages",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
            "--result",
            str(result_path),
            "--bundle",
            str(bundle_path),
            "--traces",
            str(traces_path),
            "--redaction-report",
            str(review_path),
            "--redaction-policy",
            str(policy_path),
            "--no-redact",
            "--json",
        ],
        env={"ANTHROPIC_API_KEY": "test-secret"},
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["accepted"] is True
    assert json.loads(archive.read_text(encoding="utf-8"))["archive"] is True
    assert json.loads(result_path.read_text(encoding="utf-8"))["accepted"] is True
    assert json.loads(bundle_path.read_text(encoding="utf-8"))["bundle"] is True
    assert traces_path.read_text(encoding="utf-8") == "trace\n"
    assert json.loads(review_path.read_text(encoding="utf-8"))["review"] is True
    assert observed == {"path": archive, "redact": False, "policy": policy}


def test_runtime_cli_reports_policy_failure_and_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    archive = tmp_path / "archive.json"
    _stub_loaders(monkeypatch)
    monkeypatch.setattr(
        runtime_cli,
        "run_anthropic_messages",
        lambda *args, **kwargs: _RuntimeResult(accepted=False),
    )
    failed = runner.invoke(
        runtime_app,
        [
            "anthropic-messages",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
        ],
        env={"ANTHROPIC_API_KEY": "test-secret"},
    )
    assert failed.exit_code == 1
    assert "Policy violation" in failed.stderr
    assert "violated" in failed.stdout

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    missing = runner.invoke(
        runtime_app,
        [
            "anthropic-messages",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
        ],
        env={"ANTHROPIC_API_KEY": ""},
    )
    assert missing.exit_code == 2
    assert "environment variable" in missing.stderr


def test_runtime_cli_fails_closed_on_ingestion_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capsule, variant, invocation = _input_paths(tmp_path)
    archive = tmp_path / "archive.json"
    bundle = tmp_path / "bundle.json"
    _stub_loaders(monkeypatch)
    monkeypatch.setattr(
        runtime_cli,
        "run_anthropic_messages",
        lambda *args, **kwargs: _RuntimeResult(accepted=True),
    )

    def fail_ingest(*args: object, **kwargs: object) -> object:
        raise EvidenceIngestError("bad archive")

    monkeypatch.setattr(runtime_cli, "ingest_anthropic_messages_file", fail_ingest)
    result = runner.invoke(
        runtime_app,
        [
            "anthropic-messages",
            str(capsule),
            str(variant),
            str(invocation),
            "--archive",
            str(archive),
            "--bundle",
            str(bundle),
        ],
        env={"ANTHROPIC_API_KEY": "test-secret"},
    )
    assert result.exit_code == 2
    assert "Runtime archive ingestion failed" in result.stderr


def test_invocation_and_request_models_reject_unsafe_or_inconsistent_values() -> None:
    with pytest.raises(ValueError, match="variable keys"):
        AnthropicMessagesInvocation(id="runtime", variables={"bad-key": "value"})
    with pytest.raises(ValueError, match="NUL"):
        AnthropicMessagesInvocation(id="runtime", variables={"key": "bad\x00value"})
    with pytest.raises(ValueError, match="non-empty"):
        AnthropicMessagesInvocation(id="runtime", route_metadata={"tier": ""})
    with pytest.raises(ValueError, match="65536"):
        AnthropicMessagesInvocation(id="runtime", metadata={"large": "x" * 70_000})

    with pytest.raises(ValueError, match="request_sha256"):
        AnthropicMessagesRequest(
            invocation_id="runtime",
            variant_id="variant",
            variant_sha256="1" * 64,
            variant_document_sha256="2" * 64,
            base_capsule_sha256="3" * 64,
            route_target_id="route",
            model="claude-test",
            body={"model": "claude-test"},
            request_sha256="0" * 64,
        )


def test_context_truncation_priority_and_tool_context_rejection() -> None:
    context = ContextVariant.model_validate(
        {
            "id": "context",
            "max_chars": 5,
            "overflow": "truncate_low_priority",
            "ordering": "priority",
            "items": [
                {
                    "id": "low",
                    "kind": "literal",
                    "content": "xyz",
                    "max_chars": 3,
                    "priority": 1,
                },
                {
                    "id": "high",
                    "kind": "literal",
                    "content": "abcd",
                    "max_chars": 4,
                    "priority": 100,
                },
            ],
        }
    )
    items = runtime._context_items(context)
    assert [(item.id, item.content) for item in items] == [("high", "abcd"), ("low", "x")]

    tool_context = ContextVariant.model_validate(
        {
            "id": "tool-context",
            "items": [
                {
                    "id": "literal",
                    "kind": "literal",
                    "content": "tool",
                    "max_chars": 4,
                    "placement": "tool_context",
                }
            ],
        }
    )
    with pytest.raises(AnthropicRuntimeError, match="tool_context"):
        runtime._context_items(tool_context)


def _tool_variant(*, selection: str = "none") -> ToolVariant:
    payload: dict[str, object] = {
        "id": "tools",
        "tools": [
            {
                "id": "lookup",
                "description": "Look up one value.",
                "input_schema": {"type": "object"},
            }
        ],
        "selection": selection,
        "parallel_calls": False,
        "max_calls": 1,
    }
    if selection == "named":
        payload["selected_tool"] = "lookup"
    return ToolVariant.model_validate(payload)


def test_tool_policy_reports_malformed_unknown_parallel_and_named_calls() -> None:
    malformed = {
        "content": [
            {"type": "tool_use", "id": "", "name": "other", "input": "bad"},
            {"type": "tool_use", "id": "toolu_2", "name": "lookup", "input": {}},
        ]
    }
    violations = runtime._tool_policy_violations(_tool_variant(), malformed)
    assert "provider tool call 0 has invalid id" in violations
    assert "provider tool call 0 input is not an object" in violations
    assert "provider called undeclared tools: other" in violations
    assert "provider returned 2 tool calls; max_calls is 1" in violations
    assert "provider returned parallel tool calls despite parallel_calls=false" in violations
    assert "provider returned tool calls despite selection='none'" in violations

    named = runtime._tool_policy_violations(
        _tool_variant(selection="named"),
        {"content": [{"type": "tool_use", "id": "toolu_1", "name": "other", "input": {}}]},
    )
    assert "provider called tools outside selected_tool 'lookup': other" in named
    assert runtime._tool_policy_violations(None, malformed) == [
        "provider returned tool calls with no declared tools"
    ]
    assert runtime._tool_policy_violations(_tool_variant(), {"content": "bad"}) == [
        "provider response content is not an array"
    ]


def test_archive_helpers_and_empty_tool_mapping_fail_closed() -> None:
    assert runtime._build_tools(None) == ([], None)
    empty_tools = ToolVariant.model_validate(
        {
            "id": "empty",
            "tools": [],
            "selection": "none",
        }
    )
    assert runtime._build_tools(empty_tools) == ([], None)

    with pytest.raises(AnthropicRuntimeError, match="messages must be an array"):
        runtime._archive_messages(SimpleNamespace(body={"messages": "bad"}, invocation_id="run"))
    with pytest.raises(AnthropicRuntimeError, match="messages must be objects"):
        runtime._archive_messages(SimpleNamespace(body={"messages": [1]}, invocation_id="run"))
    with pytest.raises(AnthropicRuntimeError, match="user/assistant role"):
        runtime._archive_messages(
            SimpleNamespace(
                body={"messages": [{"role": "system", "content": "bad"}]},
                invocation_id="run",
            )
        )
    with pytest.raises(AnthropicRuntimeError, match="system content must be an array"):
        runtime._archive_system(SimpleNamespace(body={"system": "bad"}))


def test_http_transport_handles_success_and_provider_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        def __init__(self, raw: bytes) -> None:
            self.raw = raw
            self.headers = {"request-id": "req_123"}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return self.raw

    payload = {"id": "msg", "content": []}
    monkeypatch.setattr(
        runtime,
        "urlopen",
        lambda request, timeout: Response(json.dumps(payload).encode("utf-8")),
    )
    result = runtime._http_transport("https://example.test", b"{}", {}, 1.0)
    assert result.payload == payload
    assert result.request_id == "req_123"

    monkeypatch.setattr(runtime, "urlopen", lambda request, timeout: Response(b"not-json"))
    with pytest.raises(AnthropicRuntimeError, match="not valid JSON"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)

    monkeypatch.setattr(runtime, "urlopen", lambda request, timeout: Response(b"[]"))
    with pytest.raises(AnthropicRuntimeError, match="JSON object"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)

    monkeypatch.setattr(runtime, "_MAX_RESPONSE_BYTES", 4)
    monkeypatch.setattr(runtime, "urlopen", lambda request, timeout: Response(b"12345"))
    with pytest.raises(AnthropicRuntimeError, match="response exceeds"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)

    error = HTTPError(
        "https://example.test",
        429,
        "rate limited",
        hdrs=None,
        fp=io.BytesIO(b'{"error":"rate"}'),
    )

    def raise_http(request: object, timeout: float) -> object:
        raise error

    monkeypatch.setattr(runtime, "urlopen", raise_http)
    with pytest.raises(AnthropicRuntimeError, match="HTTP 429"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)

    def raise_url(request: object, timeout: float) -> object:
        raise URLError("offline")

    monkeypatch.setattr(runtime, "urlopen", raise_url)
    with pytest.raises(AnthropicRuntimeError, match="request failed"):
        runtime._http_transport("https://example.test", b"{}", {}, 1.0)


def test_format_http_error_bounds_provider_detail() -> None:
    message = runtime._format_http_error(500, b"x" * 3_000)
    assert message.startswith("Anthropic Messages request failed with HTTP 500: ")
    assert message.endswith("...")
    assert len(message) < 2_100
    assert runtime._format_http_error(500, b"") == "Anthropic Messages request failed with HTTP 500"


def test_request_digest_helper_matches_canonical_body() -> None:
    body = {"model": "claude-test", "messages": [{"role": "user", "content": "hello"}]}
    digest = hashlib.sha256(runtime._canonical_json_bytes(body)).hexdigest()
    request = AnthropicMessagesRequest(
        invocation_id="runtime",
        variant_id="variant",
        variant_sha256="1" * 64,
        variant_document_sha256="2" * 64,
        base_capsule_sha256="3" * 64,
        route_target_id="route",
        model="claude-test",
        body=body,
        request_sha256=digest,
    )
    assert request.request_sha256 == digest
