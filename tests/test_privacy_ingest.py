from __future__ import annotations

import json
from pathlib import Path

from e2h.ingest import ingest_otlp_file, ingest_transcript_file
from e2h.openai_responses import ingest_openai_responses_file
from e2h.privacy import CustomRedactionRule, RedactionPolicy


def test_transcript_import_uses_custom_policy_and_review(tmp_path: Path) -> None:
    source = tmp_path / "transcript.json"
    source.write_text(
        json.dumps(
            {
                "id": "policy-transcript",
                "messages": [
                    {
                        "id": "m1",
                        "role": "user",
                        "content": "Customer CUS-123456 uses user@example.com",
                        "timestamp": "2026-08-06T08:00:00Z",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    policy = RedactionPolicy(
        id="transcript-policy",
        redact_emails=False,
        custom_rules=[CustomRedactionRule(id="customer-id", pattern=r"CUS-\d{6}")],
    )
    bundle = ingest_transcript_file(source, redaction_policy=policy)
    rendered = bundle.model_dump_json()
    assert "CUS-123456" not in rendered
    assert "user@example.com" in rendered
    assert bundle.provenance.redaction_policy_id == "transcript-policy"
    assert bundle.provenance.redaction_policy_sha256 == bundle.redaction_review.policy_sha256
    assert bundle.redaction_review.counts_by_rule == {"customer-id": 1}
    assert any(
        finding.kind.value == "email"
        for finding in bundle.redaction_review.residual_findings
    )


def test_otlp_import_supports_review_only_policy(tmp_path: Path) -> None:
    source = tmp_path / "otlp.json"
    source.write_text(
        json.dumps(
            {
                "resourceSpans": [
                    {
                        "scopeSpans": [
                            {
                                "spans": [
                                    {
                                        "traceId": "0123456789abcdef0123456789abcdef",
                                        "spanId": "0123456789abcdef",
                                        "name": "email audit@example.com",
                                        "startTimeUnixNano": "1700000000000000000",
                                        "endTimeUnixNano": "1700000001000000000",
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    bundle = ingest_otlp_file(source, redact=False)
    assert "audit@example.com" in bundle.model_dump_json()
    assert bundle.redactions == []
    assert bundle.redaction_review.redaction_enabled is False
    assert bundle.redaction_review.residual_findings


def test_openai_import_uses_allowlist_without_leaking_it_into_review(tmp_path: Path) -> None:
    source = tmp_path / "responses.json"
    source.write_text(
        json.dumps(
            {
                "id": "policy-openai",
                "responses": [
                    {
                        "input_items": [
                            {
                                "id": "msg_user",
                                "type": "message",
                                "role": "user",
                                "content": [
                                    {
                                        "type": "input_text",
                                        "text": (
                                            "Keep support@example.com but redact "
                                            "customer@example.com"
                                        ),
                                    }
                                ],
                            }
                        ],
                        "response": {
                            "id": "resp_policy",
                            "object": "response",
                            "created_at": 1786000000,
                            "model": "gpt-5.5",
                            "output": [],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    policy = RedactionPolicy(
        id="openai-policy",
        allow_values=["support@example.com"],
    )
    bundle = ingest_openai_responses_file(source, redaction_policy=policy)
    rendered = bundle.model_dump_json()
    report = bundle.redaction_review.model_dump_json()
    assert "support@example.com" in rendered
    assert "customer@example.com" not in rendered
    assert "support@example.com" not in report
    assert bundle.redaction_review.allow_value_count == 1
