from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from e2h.long_horizon import (
    LongHorizonCorpus,
    LongHorizonError,
    LongHorizonProbe,
    LongHorizonRole,
    LongHorizonTask,
    LongHorizonTurn,
    long_horizon_corpus_sha256,
    public_long_horizon_corpus_sha256,
)


def _task(metadata: dict[str, Any] | None = None) -> LongHorizonTask:
    return LongHorizonTask(
        id="task",
        title="Retention task",
        constraint_keys=["tone"],
        turns=[LongHorizonTurn(id="turn", role=LongHorizonRole.USER, content="Remember this.")],
        probes=[LongHorizonProbe(id="probe", after_turn_id="turn")],
        metadata={} if metadata is None else metadata,
    )


def _corpus(metadata: dict[str, Any] | None = None) -> LongHorizonCorpus:
    return LongHorizonCorpus(
        id="corpus",
        title="Long horizon corpus",
        tasks=[_task()],
        metadata={} if metadata is None else metadata,
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_long_horizon_task_rejects_json_coercible_metadata(
    metadata: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _task(metadata)


@pytest.mark.parametrize(
    "metadata",
    [
        {"nested": {1: "coerced key"}},
        {"nested": (1, 2)},
    ],
)
def test_long_horizon_corpus_rejects_json_coercible_metadata(
    metadata: dict[str, Any],
) -> None:
    with pytest.raises(ValidationError, match="canonical JSON data"):
        _corpus(metadata)


def test_long_horizon_private_digest_revalidates_mutated_metadata() -> None:
    corpus = _corpus()
    corpus.metadata["nested"] = (1, 2)

    with pytest.raises(LongHorizonError, match="invalid long-horizon corpus"):
        long_horizon_corpus_sha256(corpus)


def test_long_horizon_public_digest_revalidates_mutated_task_metadata() -> None:
    corpus = _corpus()
    corpus.tasks[0].metadata["nested"] = {1: "coerced key"}

    with pytest.raises(LongHorizonError, match="invalid long-horizon corpus"):
        public_long_horizon_corpus_sha256(corpus)


def test_long_horizon_preserves_valid_nested_json_metadata() -> None:
    metadata = {"enabled": True, "values": [1, 2.5, None], "mapping": {"1": "value"}}

    assert _task(metadata).metadata == metadata
    assert _corpus(metadata).metadata == metadata
