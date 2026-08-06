"""One-use fail-closed integration for Anthropic Messages evidence ingestion."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Provider evidence format.
replace_once(
    "src/e2h/ingest.py",
    '    OPENAI_RESPONSES_JSON = "openai-responses-json"\n',
    '    OPENAI_RESPONSES_JSON = "openai-responses-json"\n'
    '    ANTHROPIC_MESSAGES_JSON = "anthropic-messages-json"\n',
)

# Never retain opaque encrypted server-tool payloads.
replace_once(
    "src/e2h/anthropic_messages.py",
    "            if str(key) not in hidden_keys\n",
    '            if str(key) not in hidden_keys and str(key) != "encrypted_content"\n',
)
replace_once(
    "tests/test_anthropic_messages.py",
    '    assert completed.attributes["tool_name"] == "web_search"\n',
    '    assert completed.attributes["tool_name"] == "web_search"\n'
    '    assert "opaque-search-token" not in bundle.model_dump_json()\n',
)
replace_once(
    "tests/test_anthropic_messages_cli.py",
    '    assert len(traces.read_text(encoding="utf-8").splitlines()) == 7\n',
    '    assert len(traces.read_text(encoding="utf-8").splitlines()) == 6\n',
)

# CLI registration.
replace_once(
    "src/e2h/cli.py",
    "from e2h.compiler_cli import compiler_app\n",
    "from e2h.anthropic_messages import ingest_anthropic_messages_file\n"
    "from e2h.compiler_cli import compiler_app\n",
)
anthropic_command = '''

@ingest_app.command("anthropic-messages")
def ingest_anthropic_messages_command(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule_id: Annotated[str | None, typer.Option("--capsule-id")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,
    redaction_policy_path: Annotated[
        Path | None,
        typer.Option("--redaction-policy", exists=True, dir_okay=False),
    ] = None,
    redaction_report: Annotated[
        Path | None, typer.Option("--redaction-report", dir_okay=False)
    ] = None,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the bundle as JSON.")] = False,
) -> None:
    """Import an archived Anthropic Messages API export."""
    try:
        redaction_policy: RedactionPolicy | None = (
            load_redaction_policy(redaction_policy_path)
            if redaction_policy_path is not None
            else None
        )
        bundle = ingest_anthropic_messages_file(
            source,
            capsule_id=capsule_id,
            redact=redact,
            redaction_policy=redaction_policy,
        )
    except (EvidenceIngestError, RedactionPolicyError) as exc:
        error_console.print(f"[red]Unable to ingest evidence:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_ingestion(
        bundle,
        output=output,
        traces=traces,
        redaction_report=redaction_report,
        json_stdout=json_stdout,
    )
'''
replace_once(
    "src/e2h/cli.py",
    '\n\n@ingest_app.command("otlp")\n',
    anthropic_command + '\n\n@ingest_app.command("otlp")\n',
)

# Public API and package version.
replace_once(
    "src/e2h/__init__.py",
    '"""Evidence-to-Harness core package."""\n\n',
    '"""Evidence-to-Harness core package."""\n\n'
    "from e2h.anthropic_messages import (\n"
    "    AnthropicInputMessage,\n"
    "    AnthropicMessageRecord,\n"
    "    AnthropicMessagesDocument,\n"
    "    import_anthropic_messages_document,\n"
    "    ingest_anthropic_messages_file,\n"
    ")\n",
)
replace_once(
    "src/e2h/__init__.py",
    '    "ArtifactKind",\n',
    '    "AnthropicInputMessage",\n'
    '    "AnthropicMessageRecord",\n'
    '    "AnthropicMessagesDocument",\n'
    '    "ArtifactKind",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "import_openai_responses_document",\n',
    '    "import_anthropic_messages_document",\n'
    '    "import_openai_responses_document",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "ingest_artifact",\n',
    '    "ingest_anthropic_messages_file",\n'
    '    "ingest_artifact",\n',
)
replace_once("src/e2h/__init__.py", '__version__ = "0.10.0"', '__version__ = "0.11.0"')
replace_once("pyproject.toml", 'version = "0.10.0"', 'version = "0.11.0"')

# Documentation and roadmap.
replace_once(
    "README.md",
    "The repository now contains ten connected vertical slices:",
    "The repository now contains eleven connected vertical slices:",
)
replace_once(
    "README.md",
    "native **OpenAI Responses evidence ingestion**, and configurable **redaction policies with privacy review reports**.",
    "native **OpenAI Responses and Anthropic Messages evidence ingestion**, and configurable **redaction policies with privacy review reports**.",
)
replace_once(
    "README.md",
    "- Canonical transcript JSON, OTLP/HTTP JSON, and archived OpenAI Responses ingestion.\n",
    "- Canonical transcript JSON, OTLP/HTTP JSON, archived OpenAI Responses, and archived Anthropic Messages ingestion.\n",
)
replace_once(
    "README.md",
    "uv run e2h ingest openai-responses examples/ingest/openai-responses.json \\\n  --output .e2h/openai-responses-bundle.json \\\n  --traces .e2h/openai-responses.jsonl\n",
    "uv run e2h ingest openai-responses examples/ingest/openai-responses.json \\\n"
    "  --output .e2h/openai-responses-bundle.json \\\n"
    "  --traces .e2h/openai-responses.jsonl\n"
    "uv run e2h ingest anthropic-messages examples/ingest/anthropic-messages.json \\\n"
    "  --output .e2h/anthropic-messages-bundle.json \\\n"
    "  --traces .e2h/anthropic-messages.jsonl\n",
)
anthropic_section = '''## Anthropic Messages ingestion

`e2h ingest anthropic-messages` consumes archived request/response envelopes and never makes a live API request. Each record contains a timezone-aware capture timestamp, exporter-assigned stable IDs for request messages, the request history, an optional system prompt, and the raw Anthropic response message. Stable IDs let chained histories be deduplicated while conflicting reuse fails validation.

Visible text becomes `message.observed` evidence. Client `tool_use` and Anthropic `server_tool_use` blocks become `tool.called` events; request `tool_result` blocks and provider result blocks become linked `tool.completed` events keyed by `tool_use_id`. Usage, model, stop reason, stop sequence, request ID, container metadata, and content-block types are retained in `anthropic.message` artifacts. Unknown future block types remain observable provider artifacts instead of being silently discarded.

Thinking blocks have a deliberately narrow evidence boundary. Normalized records retain only the block type and booleans indicating whether thinking, signature, or redacted data was present. E2H never copies thinking text, signatures, redacted-thinking data, image bytes, or opaque `encrypted_content` into evidence. Text citations and non-binary source metadata remain observable and pass through the shared redaction policy engine.

The envelope is SDK-independent and additive-field tolerant inside provider payloads, but requires core response identity, `message` type, assistant role, model, content array, token usage, timezone-aware record ordering, and consistent message/tool IDs. Partial archives retain unlinked tool results explicitly for review.

'''
replace_once(
    "README.md",
    "## Privacy and provenance\n",
    anthropic_section + "## Privacy and provenance\n",
)
replace_once(
    "ROADMAP.md",
    "- [ ] Anthropic Messages API transcript adapter.\n",
    "- [x] Anthropic Messages API transcript adapter.\n",
)

Path(__file__).unlink()
