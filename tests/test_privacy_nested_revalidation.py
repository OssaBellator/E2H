from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.privacy import CustomRedactionRule, RedactionPolicy


def _rule() -> CustomRedactionRule:
    return CustomRedactionRule(id="rule-1", pattern="secret")


def test_redaction_policy_revalidates_mutated_rule_id() -> None:
    rule = _rule()
    rule.id = "invalid rule id"

    with pytest.raises(ValidationError) as exc_info:
        RedactionPolicy(custom_rules=[rule])

    assert exc_info.value.errors()[0]["loc"][-1] == "id"


def test_redaction_policy_revalidates_mutated_rule_pattern_bound() -> None:
    rule = _rule()
    rule.pattern = "x" * 513

    with pytest.raises(ValidationError) as exc_info:
        RedactionPolicy(custom_rules=[rule])

    assert exc_info.value.errors()[0]["loc"][-1] == "pattern"
