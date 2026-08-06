"""One-use behavioral corrections for redaction policy integration."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, found {count}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Keep per-ingestion limits thread-safe without threading a limit through every helper.
replace_once(
    "src/e2h/privacy.py",
    "from collections import Counter\nfrom collections.abc import Iterator\n",
    "from collections import Counter\nfrom collections.abc import Iterator\nfrom contextvars import ContextVar\n",
)
replace_once(
    "src/e2h/privacy.py",
    "_MAX_REDACTIONS = 10_000\n",
    "_MAX_REDACTIONS = 10_000\n"
    "_ACTIVE_REDACTION_LIMIT: ContextVar[int] = ContextVar(\n"
    "    \"e2h_redaction_limit\", default=_MAX_REDACTIONS\n"
    ")\n",
)
replace_once(
    "src/e2h/privacy.py",
    "    if len(records) >= _MAX_REDACTIONS:\n"
    "        raise RedactionPolicyError(f\"evidence exceeds {_MAX_REDACTIONS} redactions\")\n",
    "    limit = _ACTIVE_REDACTION_LIMIT.get()\n"
    "    if len(records) >= limit:\n"
    "        raise RedactionPolicyError(f\"evidence exceeds {limit} redactions\")\n",
)
replace_once(
    "src/e2h/privacy.py",
    "                        if len(findings) >= _MAX_REDACTIONS:\n"
    "                            raise RedactionPolicyError(\n"
    "                                f\"evidence exceeds {_MAX_REDACTIONS} residual findings\"\n"
    "                            )\n",
    "                        limit = _ACTIVE_REDACTION_LIMIT.get()\n"
    "                        if len(findings) >= limit:\n"
    "                            raise RedactionPolicyError(\n"
    "                                f\"evidence exceeds {limit} residual findings\"\n"
    "                            )\n",
)
replace_once(
    "src/e2h/privacy.py",
    "def apply_redaction_policy(\n",
    "def _apply_redaction_policy_with_active_limit(\n",
)
privacy_path = Path("src/e2h/privacy.py")
privacy_text = privacy_path.read_text(encoding="utf-8")
privacy_text += '''


def apply_redaction_policy(
    traces: list[Trace],
    *,
    policy: RedactionPolicy | None = None,
    redaction_enabled: bool = True,
    max_records: int = _MAX_REDACTIONS,
) -> RedactionOutcome:
    """Apply a policy under a bounded, invocation-local review limit."""
    if max_records < 1:
        raise RedactionPolicyError("max_records must be at least 1")
    token = _ACTIVE_REDACTION_LIMIT.set(max_records)
    try:
        return _apply_redaction_policy_with_active_limit(
            traces,
            policy=policy,
            redaction_enabled=redaction_enabled,
        )
    finally:
        _ACTIVE_REDACTION_LIMIT.reset(token)
'''
privacy_path.write_text(privacy_text, encoding="utf-8")

# Redacted placeholders must never be re-reported as residual secrets.
replace_once(
    "src/e2h/privacy.py",
    "    for match in _ASSIGNMENT.finditer(value):\n"
    "        yield RedactionKind.SECRET, \"assignment\", None, match.group(2)\n"
    "    for match in _BEARER.finditer(value):\n"
    "        yield RedactionKind.SECRET, \"bearer\", None, match.group(2)\n",
    "    for match in _ASSIGNMENT.finditer(value):\n"
    "        raw = match.group(2)\n"
    "        if not _is_placeholder(raw):\n"
    "            yield RedactionKind.SECRET, \"assignment\", None, raw\n"
    "    for match in _BEARER.finditer(value):\n"
    "        raw = match.group(2)\n"
    "        if not _is_placeholder(raw):\n"
    "            yield RedactionKind.SECRET, \"bearer\", None, raw\n",
)

# Preserve the legacy ingestion limit and exception type at the shared boundary.
replace_once(
    "src/e2h/ingest.py",
    "    RedactionPolicy,\n"
    "    RedactionRecord as RedactionRecord,\n"
    "    RedactionReview,\n",
    "    RedactionOutcome,\n"
    "    RedactionPolicy,\n"
    "    RedactionPolicyError,\n"
    "    RedactionRecord as RedactionRecord,\n"
    "    RedactionReview,\n",
)
privacy_helper = '''

def _apply_privacy_policy(
    traces: list[Trace],
    provenance: SourceProvenance,
    redaction_policy: RedactionPolicy | None,
) -> RedactionOutcome:
    try:
        return apply_redaction_policy(
            traces,
            policy=redaction_policy,
            redaction_enabled=provenance.redaction_enabled,
            max_records=_MAX_RECORDS,
        )
    except RedactionPolicyError as exc:
        raise EvidenceIngestError(str(exc)) from exc


'''
replace_once(
    "src/e2h/ingest.py",
    "def import_transcript_document(\n",
    privacy_helper + "def import_transcript_document(\n",
)
replace_once(
    "src/e2h/ingest.py",
    "    outcome = apply_redaction_policy(\n"
    "        [Trace(trace_id=document.id, events=events)],\n"
    "        policy=redaction_policy,\n"
    "        redaction_enabled=provenance.redaction_enabled,\n"
    "    )\n",
    "    outcome = _apply_privacy_policy(\n"
    "        [Trace(trace_id=document.id, events=events)],\n"
    "        provenance,\n"
    "        redaction_policy,\n"
    "    )\n",
)
replace_once(
    "src/e2h/ingest.py",
    "    outcome = apply_redaction_policy(\n"
    "        traces,\n"
    "        policy=redaction_policy,\n"
    "        redaction_enabled=provenance.redaction_enabled,\n"
    "    )\n",
    "    outcome = _apply_privacy_policy(traces, provenance, redaction_policy)\n",
)

# Provider imports use their existing provider-item bound and normalize policy errors.
replace_once(
    "src/e2h/openai_responses.py",
    "from e2h.privacy import RedactionPolicy, apply_redaction_policy\n",
    "from e2h.privacy import (\n"
    "    RedactionPolicy,\n"
    "    RedactionPolicyError,\n"
    "    apply_redaction_policy,\n"
    ")\n",
)
replace_once(
    "src/e2h/openai_responses.py",
    "    outcome = apply_redaction_policy(\n"
    "        [Trace(trace_id=document.id, events=events)],\n"
    "        policy=redaction_policy,\n"
    "        redaction_enabled=provenance.redaction_enabled,\n"
    "    )\n",
    "    try:\n"
    "        outcome = apply_redaction_policy(\n"
    "            [Trace(trace_id=document.id, events=events)],\n"
    "            policy=redaction_policy,\n"
    "            redaction_enabled=provenance.redaction_enabled,\n"
    "            max_records=_MAX_PROVIDER_ITEMS,\n"
    "        )\n"
    "    except RedactionPolicyError as exc:\n"
    "        raise EvidenceIngestError(str(exc)) from exc\n",
)

# Keep the established ingestion table readable and present privacy details separately.
replace_once(
    "src/e2h/cli.py",
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
    "    )\n"
    "    console.print(table)\n",
    "    table.add_column(\"Redactions\", justify=\"right\")\n"
    "    table.add_row(\n"
    "        bundle.provenance.source_name,\n"
    "        str(len(bundle.traces)),\n"
    "        str(event_count),\n"
    "        str(len(bundle.corrections)),\n"
    "        str(len(bundle.redactions)),\n"
    "    )\n"
    "    console.print(table)\n"
    "    review = bundle.redaction_review\n"
    "    privacy_table = Table(title=\"E2H privacy review\")\n"
    "    privacy_table.add_column(\"Policy\")\n"
    "    privacy_table.add_column(\"Residuals\", justify=\"right\")\n"
    "    privacy_table.add_row(\n"
    "        review.policy_id if review is not None else \"-\",\n"
    "        str(len(review.residual_findings) if review is not None else 0),\n"
    "    )\n"
    "    console.print(privacy_table)\n",
)

Path(__file__).unlink()
