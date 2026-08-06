"""One-use fail-closed integration for Gemini GenerateContent ingestion."""

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
    '    ANTHROPIC_MESSAGES_JSON = "anthropic-messages-json"\n',
    '    ANTHROPIC_MESSAGES_JSON = "anthropic-messages-json"\n'
    '    GEMINI_GENERATE_CONTENT_JSON = "gemini-generate-content-json"\n',
)

# Stable candidate IDs are archive metadata, because provider candidates lack IDs.
replace_once(
    "src/e2h/gemini_generate_content.py",
    "    contents: list[GeminiContentRecord] = Field(default_factory=list, max_length=_MAX_PROVIDER_ITEMS)\n"
    "    system_instruction: GeminiContentRecord | None = None\n",
    "    contents: list[GeminiContentRecord] = Field(\n"
    "        default_factory=list, max_length=_MAX_PROVIDER_ITEMS\n"
    "    )\n"
    "    candidate_ids: list[str] = Field(default_factory=list, max_length=_MAX_PROVIDER_ITEMS)\n"
    "    system_instruction: GeminiContentRecord | None = None\n",
)
replace_once(
    "src/e2h/gemini_generate_content.py",
    "        if len(candidates) > _MAX_PROVIDER_ITEMS:\n"
    "            raise ValueError(f\"response.candidates exceed {_MAX_PROVIDER_ITEMS}\")\n",
    "        if len(candidates) > _MAX_PROVIDER_ITEMS:\n"
    "            raise ValueError(f\"response.candidates exceed {_MAX_PROVIDER_ITEMS}\")\n"
    "        if len(self.candidate_ids) != len(candidates):\n"
    "            raise ValueError(\"candidate_ids must align with response candidates\")\n"
    "        if len(set(self.candidate_ids)) != len(self.candidate_ids):\n"
    "            raise ValueError(\"candidate_ids must be unique within a record\")\n",
)
replace_once(
    "src/e2h/gemini_generate_content.py",
    "                candidate_id = f\"{response_id}:candidate:{candidate.get('index', candidate_index)}\"\n"
    "                content_signatures[candidate_id] = _content_signature(\"model\", candidate_parts)\n",
    "                candidate_id = record.candidate_ids[candidate_index]\n"
    "                candidate_signature = _content_signature(\"model\", candidate_parts)\n"
    "                existing_candidate = content_signatures.get(candidate_id)\n"
    "                if (\n"
    "                    existing_candidate is not None\n"
    "                    and existing_candidate != candidate_signature\n"
    "                ):\n"
    "                    raise ValueError(\n"
    "                        \"candidate ids must retain identical observable content\"\n"
    "                    )\n"
    "                content_signatures[candidate_id] = candidate_signature\n",
)
replace_once(
    "src/e2h/gemini_generate_content.py",
    "                candidate_index = candidate.get(\"index\", candidate_position)\n"
    "                message_id = f\"{response_id}:candidate:{candidate_index}\"\n"
    "                _append_message(events, trace_id=document.id, context=context, timestamp=record.timestamp, message_id=message_id, role=\"model\", parts=parts, metadata={}, calls=calls, origin=\"response\", request_id=record.request_id)\n",
    "                candidate_index = candidate.get(\"index\", candidate_position)\n"
    "                message_id = record.candidate_ids[candidate_position]\n"
    "                signature = _content_signature(\"model\", parts)\n"
    "                if message_id not in seen:\n"
    "                    seen[message_id] = signature\n"
    "                    _append_message(\n"
    "                        events,\n"
    "                        trace_id=document.id,\n"
    "                        context=context,\n"
    "                        timestamp=record.timestamp,\n"
    "                        message_id=message_id,\n"
    "                        role=\"model\",\n"
    "                        parts=parts,\n"
    "                        metadata={},\n"
    "                        calls=calls,\n"
    "                        origin=\"response\",\n"
    "                        request_id=record.request_id,\n"
    "                    )\n",
)

# CLI registration.
replace_once(
    "src/e2h/cli.py",
    "from e2h.experiment import resolve_under_root\n",
    "from e2h.experiment import resolve_under_root\n"
    "from e2h.gemini_generate_content import ingest_gemini_generate_content_file\n",
)
gemini_command = '''

@ingest_app.command("gemini-generate-content")
def ingest_gemini_generate_content_command(
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
    """Import an archived Gemini GenerateContent API export."""
    try:
        redaction_policy: RedactionPolicy | None = (
            load_redaction_policy(redaction_policy_path)
            if redaction_policy_path is not None
            else None
        )
        bundle = ingest_gemini_generate_content_file(
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
    gemini_command + '\n\n@ingest_app.command("otlp")\n',
)

# Public API and package version.
replace_once(
    "src/e2h/__init__.py",
    "from e2h.ingest import IngestionBundle, ingest_otlp_file, ingest_transcript_file\n",
    "from e2h.gemini_generate_content import (\n"
    "    GeminiContentRecord,\n"
    "    GeminiGenerateContentDocument,\n"
    "    GeminiGenerateContentRecord,\n"
    "    import_gemini_generate_content_document,\n"
    "    ingest_gemini_generate_content_file,\n"
    ")\n"
    "from e2h.ingest import IngestionBundle, ingest_otlp_file, ingest_transcript_file\n",
)
replace_once(
    "src/e2h/__init__.py",
    '    "FileOracle",\n',
    '    "FileOracle",\n'
    '    "GeminiContentRecord",\n'
    '    "GeminiGenerateContentDocument",\n'
    '    "GeminiGenerateContentRecord",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "import_anthropic_messages_document",\n',
    '    "import_anthropic_messages_document",\n'
    '    "import_gemini_generate_content_document",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "ingest_anthropic_messages_file",\n',
    '    "ingest_anthropic_messages_file",\n'
    '    "ingest_gemini_generate_content_file",\n',
)
replace_once("src/e2h/__init__.py", '__version__ = "0.11.0"', '__version__ = "0.12.0"')
replace_once("pyproject.toml", 'version = "0.11.0"', 'version = "0.12.0"')

# Documentation and roadmap.
replace_once(
    "README.md",
    "The repository now contains eleven connected vertical slices:",
    "The repository now contains twelve connected vertical slices:",
)
replace_once(
    "README.md",
    "native **OpenAI Responses and Anthropic Messages evidence ingestion**,",
    "native **OpenAI Responses, Anthropic Messages, and Gemini GenerateContent evidence ingestion**,",
)
replace_once(
    "README.md",
    "- Canonical transcript JSON, OTLP/HTTP JSON, archived OpenAI Responses, and archived Anthropic Messages ingestion.\n",
    "- Canonical transcript JSON, OTLP/HTTP JSON, archived OpenAI Responses, Anthropic Messages, and Gemini GenerateContent ingestion.\n",
)
replace_once(
    "README.md",
    "uv run e2h ingest anthropic-messages examples/ingest/anthropic-messages.json \\\n  --output .e2h/anthropic-messages-bundle.json \\\n  --traces .e2h/anthropic-messages.jsonl\n",
    "uv run e2h ingest anthropic-messages examples/ingest/anthropic-messages.json \\\n"
    "  --output .e2h/anthropic-messages-bundle.json \\\n"
    "  --traces .e2h/anthropic-messages.jsonl\n"
    "uv run e2h ingest gemini-generate-content examples/ingest/gemini-generate-content.json \\\n"
    "  --output .e2h/gemini-bundle.json \\\n"
    "  --traces .e2h/gemini.jsonl\n",
)
gemini_section = '''## Gemini GenerateContent ingestion

`e2h ingest gemini-generate-content` consumes archived request/response envelopes and never makes a live Google API request. Each record contains a timezone-aware capture timestamp, exporter-assigned stable IDs for request contents and response candidates, optional system instructions, the raw GenerateContent response, and request/model metadata. Candidate IDs are archive metadata because provider candidates do not carry durable content IDs; they enable chained-history deduplication and conflict detection.

Text becomes `message.observed` evidence. `functionCall`, server `toolCall`, and `executableCode` parts become `tool.called` events; matching `functionResponse`, `toolResponse`, and `codeExecutionResult` parts become linked `tool.completed` events. Candidate finish state, citations, grounding, safety ratings, URL context, response model/version, prompt feedback, and detailed usage metadata remain observable artifacts.

Thought parts have a strict presence-only boundary. E2H records whether thought text and a thought signature were present, but never copies either value. Inline media bytes and opaque thought signatures are excluded; file URIs, MIME types, and other non-binary source metadata remain observable and pass through the shared redaction policy engine.

The archive is SDK-independent and accepts snake_case or provider JSON camelCase fields. It validates response IDs, candidate alignment, timezone ordering, content identity, function-call identity, candidate roles, and canonical JSON values. Partial archives retain unlinked function or tool results explicitly for review.

'''
replace_once(
    "README.md",
    "## Privacy and provenance\n",
    gemini_section + "## Privacy and provenance\n",
)
replace_once(
    "ROADMAP.md",
    "- [ ] Gemini GenerateContent transcript adapter.\n",
    "- [x] Gemini GenerateContent transcript adapter.\n",
)

Path(__file__).unlink()
