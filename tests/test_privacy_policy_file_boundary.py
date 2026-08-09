from __future__ import annotations

from pathlib import Path

import pytest

from e2h.privacy import (
    RedactionPolicy,
    RedactionPolicyError,
    load_redaction_policy,
    redaction_policy_sha256,
)


def test_privacy_policy_loader_rejects_final_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text('{"id":"target"}\n', encoding="utf-8")
    policy = tmp_path / "policy.json"
    policy.symlink_to(target)

    with pytest.raises(RedactionPolicyError, match="must be a regular file"):
        load_redaction_policy(policy)

    assert target.read_text(encoding="utf-8") == '{"id":"target"}\n'


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("policy.json", '{"id":"first","id":"second"}\n'),
        (
            "policy.yaml",
            "custom_rules:\n"
            "  - id: duplicate\n"
            "    pattern: one\n"
            "    pattern: two\n",
        ),
    ],
)
def test_privacy_policy_loader_rejects_duplicate_mapping_keys(
    tmp_path: Path,
    name: str,
    content: str,
) -> None:
    policy = tmp_path / name
    policy.write_text(content, encoding="utf-8")

    with pytest.raises(RedactionPolicyError, match="duplicate"):
        load_redaction_policy(policy)


def test_privacy_policy_digest_revalidates_mutated_policy() -> None:
    policy = RedactionPolicy(id="digest-boundary")
    policy.allow_values.append("")

    with pytest.raises(RedactionPolicyError, match="invalid redaction policy"):
        redaction_policy_sha256(policy)
