from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from e2h.privacy import (
    CustomRedactionRule,
    RedactionKind,
    RedactionPolicy,
    RedactionPolicyError,
    apply_redaction_policy,
    default_redaction_policy,
    load_redaction_policy,
    redaction_policy_sha256,
)
from e2h.trace import Trace, TraceContext, TraceEvent, TraceEventType


def trace_with_payload(payload: dict) -> Trace:
    context = TraceContext(run_id="privacy-run", capsule_id="privacy-capsule")
    timestamp = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)
    return Trace(
        trace_id="privacy-run",
        events=[
            TraceEvent(
                trace_id="privacy-run",
                sequence=0,
                event_type=TraceEventType.CONVERSATION_STARTED,
                timestamp=timestamp,
                context=context,
                payload={"conversation_id": "privacy-run"},
            ),
            TraceEvent(
                trace_id="privacy-run",
                sequence=1,
                event_type=TraceEventType.MESSAGE_OBSERVED,
                timestamp=timestamp,
                context=context,
                attributes={"email@example.com": "metadata"},
                payload=payload,
            ),
        ],
    )


def rendered_trace(outcome) -> str:
    return json.dumps([trace.model_dump(mode="json") for trace in outcome.traces])


def test_default_policy_redacts_built_in_classes_and_nested_keys() -> None:
    outcome = apply_redaction_policy(
        [
            trace_with_payload(
                {
                    "content": (
                        "Email alice@example.com, call +61 412 345 678, "
                        "use sk-abcdefghijklmnop, password=hunterhunter"
                    )
                }
            )
        ]
    )
    rendered = rendered_trace(outcome)
    assert "alice@example.com" not in rendered
    assert "+61 412 345 678" not in rendered
    assert "sk-abcdefghijklmnop" not in rendered
    assert "hunterhunter" not in rendered
    assert "email@example.com" not in rendered
    assert outcome.review.total_redactions == 5
    assert outcome.review.counts_by_kind == {"email": 2, "phone": 1, "secret": 2}
    assert outcome.review.residual_findings == []
    assert outcome.review.policy_id == "default"


def test_custom_rule_and_allowlist_are_applied_without_raw_review_values() -> None:
    policy = RedactionPolicy(
        id="customer-policy",
        custom_rules=[CustomRedactionRule(id="ticket", pattern=r"TKT-\d{6}")],
        allow_values=["allowed@example.com"],
    )
    outcome = apply_redaction_policy(
        [
            trace_with_payload(
                {
                    "content": (
                        "ticket TKT-123456 belongs to allowed@example.com and owner@example.com"
                    )
                }
            )
        ],
        policy=policy,
    )
    rendered = rendered_trace(outcome)
    report = outcome.review.model_dump_json()
    assert "TKT-123456" not in rendered
    assert "owner@example.com" not in rendered
    assert "allowed@example.com" in rendered
    assert outcome.review.counts_by_rule == {"ticket": 1}
    assert outcome.review.allow_value_count == 1
    assert outcome.review.residual_findings == []
    assert "TKT-123456" not in report
    assert "owner@example.com" not in report
    assert "allowed@example.com" not in report
    custom = next(record for record in outcome.records if record.kind is RedactionKind.CUSTOM)
    assert custom.rule_id == "ticket"
    assert custom.placeholder.startswith("<redacted:custom:ticket:")


def test_disabled_redaction_produces_hashed_residual_review() -> None:
    outcome = apply_redaction_policy(
        [trace_with_payload({"content": "Contact audit@example.com"})],
        redaction_enabled=False,
    )
    rendered = rendered_trace(outcome)
    report = outcome.review.model_dump_json()
    assert "audit@example.com" in rendered
    assert outcome.records == []
    assert outcome.review.redaction_enabled is False
    assert outcome.review.total_redactions == 0
    assert len(outcome.review.residual_findings) == 2
    assert {finding.kind for finding in outcome.review.residual_findings} == {RedactionKind.EMAIL}
    assert "audit@example.com" not in report
    assert "redaction_disabled_review_only" in outcome.review.warnings
    assert "residual_sensitive_patterns_detected" in outcome.review.warnings


def test_disabled_policy_class_is_reported_as_residual() -> None:
    policy = RedactionPolicy(redact_emails=False)
    outcome = apply_redaction_policy(
        [trace_with_payload({"content": "Contact residual@example.com"})],
        policy=policy,
    )
    assert "residual@example.com" in rendered_trace(outcome)
    assert any(finding.kind is RedactionKind.EMAIL for finding in outcome.review.residual_findings)


def test_policy_digest_is_canonical_and_sensitive_to_configuration() -> None:
    first = RedactionPolicy.model_validate(
        {
            "id": "stable",
            "custom_rules": [{"id": "case", "pattern": "ABC", "ignore_case": True}],
        }
    )
    second = RedactionPolicy.model_validate(first.model_dump(mode="json"))
    assert redaction_policy_sha256(first) == redaction_policy_sha256(second)
    assert redaction_policy_sha256(first) != redaction_policy_sha256(
        first.model_copy(update={"redact_phones": False})
    )
    assert redaction_policy_sha256(default_redaction_policy()) == (
        redaction_policy_sha256(RedactionPolicy())
    )


def test_policy_validation_rejects_invalid_or_ambiguous_rules() -> None:
    with pytest.raises(ValidationError, match="invalid regular expression"):
        CustomRedactionRule(id="bad", pattern="[")
    with pytest.raises(ValidationError, match="custom rule ids must be unique"):
        RedactionPolicy(
            custom_rules=[
                CustomRedactionRule(id="same", pattern="one"),
                CustomRedactionRule(id="same", pattern="two"),
            ]
        )
    with pytest.raises(ValidationError, match="allow_values entries must be unique"):
        RedactionPolicy(allow_values=["same", "same"])
    with pytest.raises(ValidationError, match="1 to 4096"):
        RedactionPolicy(allow_values=[""])


def test_policy_loader_accepts_json_and_yaml(tmp_path: Path) -> None:
    json_path = tmp_path / "policy.json"
    json_path.write_text(
        json.dumps({"id": "json-policy", "redact_phones": False}),
        encoding="utf-8",
    )
    yaml_path = tmp_path / "policy.yaml"
    yaml_path.write_text(
        "schema_version: '0.1'\nid: yaml-policy\nredact_emails: false\n",
        encoding="utf-8",
    )
    assert load_redaction_policy(json_path).id == "json-policy"
    assert load_redaction_policy(json_path).redact_phones is False
    assert load_redaction_policy(yaml_path).id == "yaml-policy"
    assert load_redaction_policy(yaml_path).redact_emails is False


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("policy.txt", "{}", "must use"),
        ("policy.json", "[]", "root must be an object"),
        ("policy.json", "not-json", "invalid redaction policy"),
        ("policy.yaml", "- list", "root must be an object"),
    ],
)
def test_policy_loader_rejects_invalid_files(
    tmp_path: Path,
    name: str,
    content: str,
    message: str,
) -> None:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RedactionPolicyError, match=message):
        load_redaction_policy(path)


def test_custom_rule_flags_are_honoured() -> None:
    policy = RedactionPolicy(
        custom_rules=[
            CustomRedactionRule(
                id="case-insensitive",
                pattern="private-id",
                ignore_case=True,
            )
        ]
    )
    outcome = apply_redaction_policy(
        [trace_with_payload({"content": "PRIVATE-ID"})],
        policy=policy,
    )
    assert "PRIVATE-ID" not in rendered_trace(outcome)
    assert outcome.review.counts_by_rule == {"case-insensitive": 1}


def test_residual_findings_are_deduplicated_by_location_and_digest() -> None:
    outcome = apply_redaction_policy(
        [trace_with_payload({"content": "same@example.com same@example.com"})],
        redaction_enabled=False,
    )
    content_findings = [
        finding
        for finding in outcome.review.residual_findings
        if finding.location.endswith("/payload/<value:0>")
    ]
    assert len(content_findings) == 1
