"""One-use fail-closed integration for configurable redaction policies."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def remove_between(path: str, start: str, end: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    start_index = text.find(start)
    end_index = text.find(end, start_index + len(start))
    if start_index < 0 or end_index < 0:
        raise RuntimeError(f"{path}: unable to find removal anchors")
    target.write_text(text[:start_index] + text[end_index:], encoding="utf-8")


# Privacy engine typing annotations.
replace_once(
    "src/e2h/privacy.py",
    "from typing import Any, Literal, cast\n",
    "from collections.abc import Iterator\nfrom typing import Any, Literal, cast\n",
)
replace_once(
    "src/e2h/privacy.py",
    "def _iter_strings(value: Any, location: str):\n",
    "def _iter_strings(value: Any, location: str) -> Iterator[tuple[str, str]]:\n",
)
replace_once(
    "src/e2h/privacy.py",
    "def _candidate_matches(\n    value: str,\n    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],\n):\n",
    "def _candidate_matches(\n"
    "    value: str,\n"
    "    custom_patterns: list[tuple[CustomRedactionRule, re.Pattern[str]]],\n"
    ") -> Iterator[tuple[RedactionKind, str, str | None, str]]:\n",
)
replace_once(
    "src/e2h/privacy.py",
    "    kind_counts = Counter(record.kind.value for record in records)\n"
    "    rule_counts = Counter(record.rule_id for record in records if record.rule_id is not None)\n",
    "    kind_counts: Counter[str] = Counter(record.kind.value for record in records)\n"
    "    rule_counts: Counter[str] = Counter(\n"
    "        record.rule_id for record in records if record.rule_id is not None\n"
    "    )\n",
)

# Move policy-aware redaction behind the shared ingestion contract.
replace_once(
    "src/e2h/ingest.py",
    "from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator\n\n"
    "from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType\n",
    "from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator\n\n"
    "from e2h.privacy import (\n"
    "    RedactionKind as RedactionKind,\n"
    "    RedactionPolicy,\n"
    "    RedactionRecord as RedactionRecord,\n"
    "    RedactionReview,\n"
    "    apply_redaction_policy,\n"
    "    default_redaction_policy,\n"
    "    redaction_policy_sha256,\n"
    ")\n"
    "from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType\n",
)
remove_between("src/e2h/ingest.py", "_EMAIL = re.compile", "\n\nclass EvidenceIngestError")
remove_between("src/e2h/ingest.py", "class RedactionKind", "\n\nclass TranscriptRole")
remove_between("src/e2h/ingest.py", "class RedactionRecord", "\n\nclass CorrectionRecord")
replace_once(
    "src/e2h/ingest.py",
    "    redaction_enabled: bool\n",
    "    redaction_enabled: bool\n"
    "    redaction_policy_id: str | None = Field(default=None, pattern=_ID_PATTERN)\n"
    "    redaction_policy_sha256: str | None = Field(\n"
    "        default=None, pattern=r\"^[0-9a-f]{64}$\"\n"
    "    )\n",
)
replace_once(
    "src/e2h/ingest.py",
    "    redactions: list[RedactionRecord] = Field(default_factory=list)\n",
    "    redactions: list[RedactionRecord] = Field(default_factory=list)\n"
    "    redaction_review: RedactionReview | None = None\n",
)
replace_once(
    "src/e2h/ingest.py",
    "def _load_json(path: Path, source_format: EvidenceFormat, redact: bool) -> _LoadedSource:\n",
    "def _load_json(\n"
    "    path: Path,\n"
    "    source_format: EvidenceFormat,\n"
    "    redact: bool,\n"
    "    redaction_policy: RedactionPolicy | None = None,\n"
    ") -> _LoadedSource:\n",
)
replace_once(
    "src/e2h/ingest.py",
    "    provenance = SourceProvenance(\n"
    "        format=source_format,\n"
    "        source_name=path.name,\n"
    "        sha256=hashlib.sha256(raw).hexdigest(),\n"
    "        size_bytes=len(raw),\n"
    "        redaction_enabled=redact,\n"
    "    )\n",
    "    active_policy = redaction_policy or default_redaction_policy()\n"
    "    provenance = SourceProvenance(\n"
    "        format=source_format,\n"
    "        source_name=path.name,\n"
    "        sha256=hashlib.sha256(raw).hexdigest(),\n"
    "        size_bytes=len(raw),\n"
    "        redaction_enabled=redact,\n"
    "        redaction_policy_id=active_policy.id,\n"
    "        redaction_policy_sha256=redaction_policy_sha256(active_policy),\n"
    "    )\n",
)
remove_between("src/e2h/ingest.py", "def _pointer_child", "\ndef import_transcript_document")
replace_once(
    "src/e2h/ingest.py",
    "def import_transcript_document(\n"
    "    document: TranscriptDocument,\n"
    "    provenance: SourceProvenance,\n"
    "    *,\n"
    "    capsule_id: str | None = None,\n"
    ") -> IngestionBundle:\n",
    "def import_transcript_document(\n"
    "    document: TranscriptDocument,\n"
    "    provenance: SourceProvenance,\n"
    "    *,\n"
    "    capsule_id: str | None = None,\n"
    "    redaction_policy: RedactionPolicy | None = None,\n"
    ") -> IngestionBundle:\n",
)
replace_once(
    "src/e2h/ingest.py",
    "    trace = Trace(trace_id=document.id, events=events)\n"
    "    redactions: list[RedactionRecord] = []\n"
    "    if provenance.redaction_enabled:\n"
    "        trace = _apply_redaction(trace, redactions, trace_index=0)\n"
    "    return IngestionBundle(\n"
    "        provenance=provenance,\n"
    "        traces=[trace],\n"
    "        corrections=corrections,\n"
    "        redactions=redactions,\n"
    "    )\n",
    "    outcome = apply_redaction_policy(\n"
    "        [Trace(trace_id=document.id, events=events)],\n"
    "        policy=redaction_policy,\n"
    "        redaction_enabled=provenance.redaction_enabled,\n"
    "    )\n"
    "    return IngestionBundle(\n"
    "        provenance=provenance,\n"
    "        traces=outcome.traces,\n"
    "        corrections=corrections,\n"
    "        redactions=outcome.records,\n"
    "        redaction_review=outcome.review,\n"
    "    )\n",
)
replace_once(
    "src/e2h/ingest.py",
    "def ingest_transcript_file(\n"
    "    path: Path,\n"
    "    *,\n"
    "    capsule_id: str | None = None,\n"
    "    redact: bool = True,\n"
    ") -> IngestionBundle:\n",
    "def ingest_transcript_file(\n"
    "    path: Path,\n"
    "    *,\n"
    "    capsule_id: str | None = None,\n"
    "    redact: bool = True,\n"
    "    redaction_policy: RedactionPolicy | None = None,\n"
    ") -> IngestionBundle:\n",
)
replace_once(
    "src/e2h/ingest.py",
    "    source = _load_json(path, EvidenceFormat.TRANSCRIPT_JSON, redact)\n"
    "    try:\n"
    "        document = TranscriptDocument.model_validate(source.data)\n"
    "        return import_transcript_document(document, source.provenance, capsule_id=capsule_id)\n",
    "    source = _load_json(\n"
    "        path, EvidenceFormat.TRANSCRIPT_JSON, redact, redaction_policy\n"
    "    )\n"
    "    try:\n"
    "        document = TranscriptDocument.model_validate(source.data)\n"
    "        return import_transcript_document(\n"
    "            document,\n"
    "            source.provenance,\n"
    "            capsule_id=capsule_id,\n"
    "            redaction_policy=redaction_policy,\n"
    "        )\n",
)
replace_once(
    "src/e2h/ingest.py",
    "def import_otlp_data(\n"
    "    data: dict[str, Any],\n"
    "    provenance: SourceProvenance,\n"
    "    *,\n"
    "    capsule_id: str = \"unassigned\",\n"
    ") -> IngestionBundle:\n",
    "def import_otlp_data(\n"
    "    data: dict[str, Any],\n"
    "    provenance: SourceProvenance,\n"
    "    *,\n"
    "    capsule_id: str = \"unassigned\",\n"
    "    redaction_policy: RedactionPolicy | None = None,\n"
    ") -> IngestionBundle:\n",
)
replace_once(
    "src/e2h/ingest.py",
    "    redactions: list[RedactionRecord] = []\n"
    "    if provenance.redaction_enabled:\n"
    "        traces = [\n"
    "            _apply_redaction(trace, redactions, trace_index=index)\n"
    "            for index, trace in enumerate(traces)\n"
    "        ]\n"
    "    return IngestionBundle(provenance=provenance, traces=traces, redactions=redactions)\n",
    "    outcome = apply_redaction_policy(\n"
    "        traces,\n"
    "        policy=redaction_policy,\n"
    "        redaction_enabled=provenance.redaction_enabled,\n"
    "    )\n"
    "    return IngestionBundle(\n"
    "        provenance=provenance,\n"
    "        traces=outcome.traces,\n"
    "        redactions=outcome.records,\n"
    "        redaction_review=outcome.review,\n"
    "    )\n",
)
replace_once(
    "src/e2h/ingest.py",
    "def ingest_otlp_file(\n"
    "    path: Path,\n"
    "    *,\n"
    "    capsule_id: str = \"unassigned\",\n"
    "    redact: bool = True,\n"
    ") -> IngestionBundle:\n",
    "def ingest_otlp_file(\n"
    "    path: Path,\n"
    "    *,\n"
    "    capsule_id: str = \"unassigned\",\n"
    "    redact: bool = True,\n"
    "    redaction_policy: RedactionPolicy | None = None,\n"
    ") -> IngestionBundle:\n",
)
replace_once(
    "src/e2h/ingest.py",
    "    source = _load_json(path, EvidenceFormat.OTLP_JSON, redact)\n"
    "    try:\n"
    "        return import_otlp_data(source.data, source.provenance, capsule_id=capsule_id)\n",
    "    source = _load_json(path, EvidenceFormat.OTLP_JSON, redact, redaction_policy)\n"
    "    try:\n"
    "        return import_otlp_data(\n"
    "            source.data,\n"
    "            source.provenance,\n"
    "            capsule_id=capsule_id,\n"
    "            redaction_policy=redaction_policy,\n"
    "        )\n",
)

# OpenAI provider integration.
replace_once(
    "src/e2h/openai_responses.py",
    "from e2h.ingest import (\n"
    "    EvidenceFormat,\n"
    "    EvidenceIngestError,\n"
    "    IngestionBundle,\n"
    "    RedactionRecord,\n"
    "    SourceProvenance,\n"
    "    _apply_redaction,\n"
    "    _load_json,\n"
    ")\n",
    "from e2h.ingest import (\n"
    "    EvidenceFormat,\n"
    "    EvidenceIngestError,\n"
    "    IngestionBundle,\n"
    "    SourceProvenance,\n"
    "    _load_json,\n"
    ")\n"
    "from e2h.privacy import RedactionPolicy, apply_redaction_policy\n",
)
replace_once(
    "src/e2h/openai_responses.py",
    "def import_openai_responses_document(\n"
    "    document: OpenAIResponsesDocument,\n"
    "    provenance: SourceProvenance,\n"
    "    *,\n"
    "    capsule_id: str | None = None,\n"
    ") -> IngestionBundle:\n",
    "def import_openai_responses_document(\n"
    "    document: OpenAIResponsesDocument,\n"
    "    provenance: SourceProvenance,\n"
    "    *,\n"
    "    capsule_id: str | None = None,\n"
    "    redaction_policy: RedactionPolicy | None = None,\n"
    ") -> IngestionBundle:\n",
)
replace_once(
    "src/e2h/openai_responses.py",
    "    trace = Trace(trace_id=document.id, events=events)\n"
    "    redactions: list[RedactionRecord] = []\n"
    "    if provenance.redaction_enabled:\n"
    "        trace = _apply_redaction(trace, redactions, trace_index=0)\n"
    "    return IngestionBundle(\n"
    "        provenance=provenance,\n"
    "        traces=[trace],\n"
    "        corrections=[],\n"
    "        redactions=redactions,\n"
    "    )\n",
    "    outcome = apply_redaction_policy(\n"
    "        [Trace(trace_id=document.id, events=events)],\n"
    "        policy=redaction_policy,\n"
    "        redaction_enabled=provenance.redaction_enabled,\n"
    "    )\n"
    "    return IngestionBundle(\n"
    "        provenance=provenance,\n"
    "        traces=outcome.traces,\n"
    "        corrections=[],\n"
    "        redactions=outcome.records,\n"
    "        redaction_review=outcome.review,\n"
    "    )\n",
)
replace_once(
    "src/e2h/openai_responses.py",
    "def ingest_openai_responses_file(\n"
    "    path: Path,\n"
    "    *,\n"
    "    capsule_id: str | None = None,\n"
    "    redact: bool = True,\n"
    ") -> IngestionBundle:\n",
    "def ingest_openai_responses_file(\n"
    "    path: Path,\n"
    "    *,\n"
    "    capsule_id: str | None = None,\n"
    "    redact: bool = True,\n"
    "    redaction_policy: RedactionPolicy | None = None,\n"
    ") -> IngestionBundle:\n",
)
replace_once(
    "src/e2h/openai_responses.py",
    "    source = _load_json(path, EvidenceFormat.OPENAI_RESPONSES_JSON, redact)\n",
    "    source = _load_json(\n"
    "        path, EvidenceFormat.OPENAI_RESPONSES_JSON, redact, redaction_policy\n"
    "    )\n",
)
replace_once(
    "src/e2h/openai_responses.py",
    "            capsule_id=capsule_id,\n"
    "        )\n",
    "            capsule_id=capsule_id,\n"
    "            redaction_policy=redaction_policy,\n"
    "        )\n",
)

# CLI policy and report options.
replace_once(
    "src/e2h/cli.py",
    "from e2h.openai_responses import ingest_openai_responses_file\n",
    "from e2h.openai_responses import ingest_openai_responses_file\n"
    "from e2h.privacy import (\n"
    "    RedactionPolicy,\n"
    "    RedactionPolicyError,\n"
    "    load_redaction_policy,\n"
    ")\n",
)
replace_once(
    "src/e2h/cli.py",
    "def _emit_ingestion(\n"
    "    bundle: IngestionBundle,\n"
    "    *,\n"
    "    output: Path | None,\n"
    "    traces: Path | None,\n"
    "    json_stdout: bool,\n"
    ") -> None:\n",
    "def _emit_ingestion(\n"
    "    bundle: IngestionBundle,\n"
    "    *,\n"
    "    output: Path | None,\n"
    "    traces: Path | None,\n"
    "    redaction_report: Path | None,\n"
    "    json_stdout: bool,\n"
    ") -> None:\n",
)
replace_once(
    "src/e2h/cli.py",
    "    if traces is not None:\n"
    "        write_traces_jsonl(traces, bundle.traces)\n",
    "    if traces is not None:\n"
    "        write_traces_jsonl(traces, bundle.traces)\n"
    "    if redaction_report is not None and bundle.redaction_review is not None:\n"
    "        write_json_atomic(\n"
    "            redaction_report,\n"
    "            bundle.redaction_review.model_dump_json(indent=2) + \"\\n\",\n"
    "        )\n",
)
replace_once(
    "src/e2h/cli.py",
    "    table.add_column(\"Redactions\", justify=\"right\")\n"
    "    table.add_row(\n"
    "        bundle.provenance.source_name,\n"
    "        str(len(bundle.traces)),\n"
    "        str(event_count),\n"
    "        str(len(bundle.corrections)),\n"
    "        str(len(bundle.redactions)),\n"
    "    )\n",
    "    table.add_column(\"Redactions\", justify=\"right\")\n"
    "    table.add_column(\"Residuals\", justify=\"right\")\n"
    "    table.add_column(\"Policy\")\n"
    "    review = bundle.redaction_review\n"
    "    table.add_row(\n"
    "        bundle.provenance.source_name,\n"
    "        str(len(bundle.traces)),\n"
    "        str(event_count),\n"
    "        str(len(bundle.corrections)),\n"
    "        str(len(bundle.redactions)),\n"
    "        str(len(review.residual_findings) if review is not None else 0),\n"
    "        review.policy_id if review is not None else \"-\",\n"
    "    )\n",
)

# Add shared options and behavior to all three ingestion commands.
for function_name in (
    "ingest_transcript_command",
    "ingest_openai_responses_command",
    "ingest_otlp_command",
):
    marker = f"def {function_name}(\n"
    text = Path("src/e2h/cli.py").read_text(encoding="utf-8")
    start = text.index(marker)
    end = text.index("\n\n@ingest_app.command", start + len(marker)) if function_name != "ingest_otlp_command" else len(text)
    block = text[start:end]
    block = block.replace(
        '    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,\n',
        '    redact: Annotated[bool, typer.Option("--redact/--no-redact")] = True,\n'
        '    redaction_policy_path: Annotated[\n'
        '        Path | None,\n'
        '        typer.Option("--redaction-policy", exists=True, dir_okay=False),\n'
        '    ] = None,\n'
        '    redaction_report: Annotated[\n'
        '        Path | None, typer.Option("--redaction-report", dir_okay=False)\n'
        '    ] = None,\n',
        1,
    )
    block = block.replace(
        "    try:\n",
        "    try:\n"
        "        redaction_policy: RedactionPolicy | None = (\n"
        "            load_redaction_policy(redaction_policy_path)\n"
        "            if redaction_policy_path is not None\n"
        "            else None\n"
        "        )\n",
        1,
    )
    block = block.replace(
        "    except EvidenceIngestError as exc:\n",
        "    except (EvidenceIngestError, RedactionPolicyError) as exc:\n",
        1,
    )
    block = block.replace(
        "redact=redact)",
        "redact=redact, redaction_policy=redaction_policy)",
        1,
    )
    block = block.replace(
        "            redact=redact,\n",
        "            redact=redact,\n"
        "            redaction_policy=redaction_policy,\n",
        1,
    )
    block = block.replace(
        "_emit_ingestion(bundle, output=output, traces=traces, json_stdout=json_stdout)",
        "_emit_ingestion(\n"
        "        bundle,\n"
        "        output=output,\n"
        "        traces=traces,\n"
        "        redaction_report=redaction_report,\n"
        "        json_stdout=json_stdout,\n"
        "    )",
        1,
    )
    updated = text[:start] + block + text[end:]
    Path("src/e2h/cli.py").write_text(updated, encoding="utf-8")

# Public API and version.
replace_once(
    "src/e2h/__init__.py",
    "from e2h.oracles import ArtifactOracle, FileOracle, JsonOracle, OracleEvaluation\n",
    "from e2h.oracles import ArtifactOracle, FileOracle, JsonOracle, OracleEvaluation\n"
    "from e2h.privacy import (\n"
    "    CustomRedactionRule,\n"
    "    RedactionKind,\n"
    "    RedactionPolicy,\n"
    "    RedactionPolicyError,\n"
    "    RedactionRecord,\n"
    "    RedactionReview,\n"
    "    ResidualFinding,\n"
    "    apply_redaction_policy,\n"
    "    default_redaction_policy,\n"
    "    load_redaction_policy,\n"
    "    redaction_policy_sha256,\n"
    ")\n",
)
replace_once(
    "src/e2h/__init__.py",
    '    "ContainerSandbox",\n',
    '    "ContainerSandbox",\n    "CustomRedactionRule",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "QueryView",\n',
    '    "QueryView",\n'
    '    "RedactionKind",\n'
    '    "RedactionPolicy",\n'
    '    "RedactionPolicyError",\n'
    '    "RedactionRecord",\n'
    '    "RedactionReview",\n'
    '    "ResidualFinding",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "compile_proposal",\n',
    '    "apply_redaction_policy",\n    "compile_proposal",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "export_parquet",\n',
    '    "default_redaction_policy",\n    "export_parquet",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "initialize_store",\n',
    '    "initialize_store",\n    "load_redaction_policy",\n',
)
replace_once(
    "src/e2h/__init__.py",
    '    "query_store",\n',
    '    "query_store",\n    "redaction_policy_sha256",\n',
)
replace_once("src/e2h/__init__.py", '__version__ = "0.9.0"', '__version__ = "0.10.0"')
replace_once("pyproject.toml", 'version = "0.9.0"', 'version = "0.10.0"')

# Documentation and roadmap.
replace_once(
    "README.md",
    "The repository now contains nine connected vertical slices:",
    "The repository now contains ten connected vertical slices:",
)
replace_once(
    "README.md",
    "and native **OpenAI Responses evidence ingestion**.",
    "native **OpenAI Responses evidence ingestion**, and configurable **redaction policies with privacy review reports**.",
)
replace_once(
    "README.md",
    "- Default-on secret, email, and phone redaction with stable non-reversible placeholders.\n",
    "- Configurable secret, email, phone, custom-regex, and exact-allowlist redaction policies.\n"
    "- Non-reversible privacy review reports with counts, policy digests, and residual findings.\n",
)
replace_once(
    "README.md",
    "uv run e2h ingest transcript examples/ingest/transcript.json \\\n  --output .e2h/transcript-bundle.json \\\n  --traces .e2h/transcript.jsonl\n",
    "uv run e2h ingest transcript examples/ingest/transcript.json \\\n"
    "  --redaction-policy examples/ingest/redaction-policy.yaml \\\n"
    "  --redaction-report .e2h/redaction-review.json \\\n"
    "  --output .e2h/transcript-bundle.json \\\n"
    "  --traces .e2h/transcript.jsonl\n",
)
replace_once(
    "README.md",
    "Ingestion redacts recognized secrets, email addresses, and phone numbers by default. Placeholders include a truncated SHA-256 digest, so repeated values remain comparable without storing the original value. Redaction records contain only the class, safe JSON-pointer location, digest, and placeholder.\n",
    "Ingestion uses the built-in `default` policy unless `--redaction-policy` selects a strict JSON or YAML policy. Policies can independently enable secret, email, and phone detectors, add trusted custom Python regular expressions, and retain exact allowlisted values. Placeholders include a truncated SHA-256 digest, so repeated values remain comparable without storing the original value. Redaction records contain only the class, rule identifier, safe JSON-pointer location, digest, and placeholder.\n\n"
    "Every ingestion bundle now includes a `redaction_review` with the canonical policy digest, counts by class and custom rule, unique-value counts, warnings, and hashed residual findings. `--redaction-report` writes that review separately. Review reports never include raw matches or allowlist values. `--no-redact` remains available and now acts as review-only mode: evidence is retained verbatim while sensitive-looking residuals are reported by digest and location.\n\n"
    "Custom rules are trusted Python regular expressions and can be computationally expensive or over-broad. Policy authors must review them like code. Exact allowlists deliberately retain matching values, and pattern matching cannot prove complete de-identification; manual review remains recommended before evidence leaves its original trust boundary.\n",
)
replace_once(
    "ROADMAP.md",
    "- [ ] Configurable redaction policies and review reports.\n",
    "- [x] Configurable redaction policies and review reports.\n",
)

Path(__file__).unlink()
