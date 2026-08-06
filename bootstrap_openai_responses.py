"""One-use fail-closed integration patch for OpenAI Responses evidence ingestion."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Provider source format.
replace_once(
    "src/e2h/ingest.py",
    '    TRANSCRIPT_JSON = "transcript-json"\n    OTLP_JSON = "otlp-json"\n',
    '    TRANSCRIPT_JSON = "transcript-json"\n'
    '    OTLP_JSON = "otlp-json"\n'
    '    OPENAI_RESPONSES_JSON = "openai-responses-json"\n',
)

# Keep the provider module lint-clean and avoid a needless typed cast.
replace_once(
    "src/e2h/openai_responses.py",
    "    CorrectionRecord,\n",
    "",
)
replace_once(
    "src/e2h/openai_responses.py",
    "        corrections=cast(list[CorrectionRecord], []),\n",
    "        corrections=[],\n",
)
replace_once(
    "tests/test_openai_responses.py",
    "    assert trace.context if False else True\n",
    "",
)

# CLI registration.
replace_once(
    "src/e2h/cli.py",
    "from e2h.models import TaskCapsule\n",
    "from e2h.models import TaskCapsule\n"
    "from e2h.openai_responses import ingest_openai_responses_file\n",
)
openai_command = '''

@ingest_app.command("openai-responses")
def ingest_openai_responses_command(
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    capsule_id: Annotated[str | None, typer.Option("--capsule-id")] = None,
    output: Annotated[Path | None, typer.Option("--output", "-o", dir_okay=False)] = None,
    traces: Annotated[Path | None, typer.Option("--traces", dir_okay=False)] = None,
    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,
    json_stdout: Annotated[bool, typer.Option("--json", help="Write the bundle as JSON.")] = False,
) -> None:
    """Import an archived OpenAI Responses API export."""
    try:
        bundle = ingest_openai_responses_file(
            source,
            capsule_id=capsule_id,
            redact=redact,
        )
    except EvidenceIngestError as exc:
        error_console.print(f"[red]Unable to ingest evidence:[/red] {exc}")
        raise typer.Exit(code=2) from exc
    _emit_ingestion(bundle, output=output, traces=traces, json_stdout=json_stdout)
'''
replace_once(
    "src/e2h/cli.py",
    "\n\n@ingest_app.command(\"otlp\")\n",
    openai_command + "\n\n@ingest_app.command(\"otlp\")\n",
)

# Public API and package version.
replace_once(
    "src/e2h/__init__.py",
    "from e2h.models import ContainerSandbox, TaskCapsule\n",
    "from e2h.models import ContainerSandbox, TaskCapsule\n"
    "from e2h.openai_responses import (\n"
    "    OpenAIResponseRecord,\n"
    "    OpenAIResponsesDocument,\n"
    "    import_openai_responses_document,\n"
    "    ingest_openai_responses_file,\n"
    ")\n",
)
for name in (
    "OpenAIResponseRecord",
    "OpenAIResponsesDocument",
    "import_openai_responses_document",
    "ingest_openai_responses_file",
):
    replace_once(
        "src/e2h/__init__.py",
        '    "OracleEvaluation",\n',
        f'    "{name}",\n    "OracleEvaluation",\n',
    )
replace_once("src/e2h/__init__.py", '__version__ = "0.8.0"', '__version__ = "0.9.0"')
replace_once("pyproject.toml", 'version = "0.8.0"', 'version = "0.9.0"')

# Documentation and roadmap.
replace_once(
    "README.md",
    "The repository now contains eight connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, declarative **file, JSON, and artifact oracles**, content-addressed **workspace snapshots**, an optional **container sandbox backend**, and a transactional **DuckDB/Parquet experiment store**.",
    "The repository now contains nine connected vertical slices: deterministic **task capsule replay**, an observable **trace + replay-matrix layer**, a privacy-aware **evidence ingestion layer**, a review-gated **capsule compiler**, declarative **file, JSON, and artifact oracles**, content-addressed **workspace snapshots**, an optional **container sandbox backend**, a transactional **DuckDB/Parquet experiment store**, and native **OpenAI Responses evidence ingestion**.",
)
replace_once(
    "README.md",
    "- Canonical transcript JSON and OTLP/HTTP JSON ingestion.\n",
    "- Canonical transcript JSON, OTLP/HTTP JSON, and archived OpenAI Responses ingestion.\n"
    "- Provider-native message, function-call, function-output, usage, status, and request metadata normalization.\n",
)
replace_once(
    "README.md",
    "uv run e2h ingest otlp examples/ingest/otlp.json \\\n  --output .e2h/otlp-bundle.json \\\n  --traces .e2h/otlp.jsonl\n",
    "uv run e2h ingest otlp examples/ingest/otlp.json \\\n  --output .e2h/otlp-bundle.json \\\n  --traces .e2h/otlp.jsonl\n"
    "uv run e2h ingest openai-responses examples/ingest/openai-responses.json \\\n  --output .e2h/openai-responses-bundle.json \\\n  --traces .e2h/openai-responses.jsonl\n",
)
provider_section = '''## OpenAI Responses ingestion

`e2h ingest openai-responses` consumes an archived export envelope rather than making a live API request. Each record contains the raw `response` object returned by the Responses API plus the `input_items` retrieved for that response. This mirrors the provider API split: response output items are returned on the response object, while input items are retrieved separately when reconstructing observable request context.

The adapter normalizes input, developer, user, and assistant messages into `message.observed` events. Standard `function_call` and `function_call_output` items become linked `tool.called` and `tool.completed` events keyed by `call_id`. Response model, status, token usage, errors, incomplete details, request ID, conversation ID, previous-response linkage, and item IDs are retained as `openai.response` artifacts. Unknown output-item types remain observable provider artifacts rather than being silently discarded.

Reasoning items receive a deliberately narrower treatment. E2H records only provider-supplied summary text, status, and booleans indicating whether reasoning or encrypted content was present. It never copies `reasoning_text` or `encrypted_content` into normalized evidence. The built-in redactor then processes all retained messages, arguments, tool outputs, summaries, and response metadata by default.

Stable provider item IDs are deduplicated across chained response records, so an item returned as output in one response and replayed as input to the next produces one observable event. Function outputs from partial exports are still retained and explicitly marked as unlinked when their originating call is absent.

The export envelope is intentionally SDK-independent and additive-field tolerant inside provider payloads. E2H requires only the core response identity, object type, Unix creation time, model, ordered output array, and typed provider items; this preserves archived evidence across SDK upgrades without accepting malformed core structure.

'''
replace_once(
    "README.md",
    "## Privacy and provenance\n",
    provider_section + "## Privacy and provenance\n",
)
replace_once(
    "README.md",
    "- [ ] Provider-specific transcript adapters.\n",
    "- [x] OpenAI Responses API transcript adapter.\n"
    "- [ ] Anthropic Messages API transcript adapter.\n"
    "- [ ] Gemini GenerateContent transcript adapter.\n",
) if False else None

roadmap = Path("ROADMAP.md")
roadmap_text = roadmap.read_text(encoding="utf-8")
old = "- [ ] Provider-specific transcript adapters.\n"
new = (
    "- [x] OpenAI Responses API transcript adapter.\n"
    "- [ ] Anthropic Messages API transcript adapter.\n"
    "- [ ] Gemini GenerateContent transcript adapter.\n"
)
if roadmap_text.count(old) != 1:
    raise RuntimeError("ROADMAP.md: provider adapter anchor mismatch")
roadmap.write_text(roadmap_text.replace(old, new, 1), encoding="utf-8")

Path(__file__).unlink()
