"""SDK-optional DSPy and GEPA optimizer adapter contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import load_mapping_document
from e2h.failures import FailureCode
from e2h.models import TaskCapsule
from e2h.runner import CheckStatus, RunResult
from e2h.variants import (
    HarnessVariant,
    HarnessVariantDocument,
    verify_variant_document,
)

_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_FIELD_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_DOCUMENT_BYTES = 2_097_152
_MAX_METADATA_BYTES = 65_536
_MAX_EXAMPLE_BYTES = 262_144
_MAX_FEEDBACK_CHARS = 50_000
_OPTIMIZER_PROVENANCE_KEY = "e2h_optimizer"
_FAILURE_SUMMARIES: dict[FailureCode, str] = {
    FailureCode.UNEXPECTED_EXIT: "command returned an unexpected exit code",
    FailureCode.SIGNAL_TERMINATION: "command was terminated by a signal",
    FailureCode.TIMEOUT: "command exceeded its declared time budget",
    FailureCode.COMMAND_NOT_FOUND: "command executable was not found",
    FailureCode.PERMISSION_DENIED: "command could not be started because permission was denied",
    FailureCode.PROCESS_LAUNCH_ERROR: "command process could not be started",
    FailureCode.WORKING_DIRECTORY_MISSING: "declared check working directory does not exist",
    FailureCode.SANDBOX_CONFIGURATION: "sandbox configuration is incomplete",
    FailureCode.SANDBOX_RUNTIME: "sandbox runtime could not execute the check",
    FailureCode.SANDBOX_CLEANUP: "timed-out container could not be cleaned up safely",
    FailureCode.OUTPUT_CAPTURE: "command output could not be captured reliably",
    FailureCode.SKIPPED_AFTER_FAILURE: "check was skipped after an earlier check failed",
}


class OptimizerAdapterError(ValueError):
    """Raised when an optimizer adapter artifact cannot be safely used."""


class OptimizerKind(StrEnum):
    """Supported external optimizer families."""

    DSPY = "dspy"
    GEPA = "gepa"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_ModelT = TypeVar("_ModelT", bound=StrictModel)
_InputModelT = TypeVar("_InputModelT", bound=BaseModel)


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must contain canonical JSON data") from exc
    return rendered.encode("utf-8")


def _revalidate_optimizer_input(
    value: BaseModel,
    model_type: type[_InputModelT],
    *,
    noun: str,
) -> _InputModelT:
    if type(value) is not model_type:
        raise OptimizerAdapterError(
            f"invalid {noun}: expected {model_type.__name__}, got {type(value).__name__}"
        )
    try:
        payload = value.model_dump(mode="python", warnings="none")
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise OptimizerAdapterError(f"invalid {noun}: {exc}") from exc


def _validate_metadata(value: dict[str, Any], *, noun: str) -> dict[str, Any]:
    if len(_canonical_json_bytes(value)) > _MAX_METADATA_BYTES:
        raise ValueError(f"{noun} metadata exceeds {_MAX_METADATA_BYTES} bytes")
    return value


def _validate_field_mapping(value: dict[str, Any], *, noun: str) -> dict[str, Any]:
    for key in value:
        if _FIELD_PATTERN.fullmatch(key) is None:
            raise ValueError(f"{noun} keys must be Python identifiers")
    _canonical_json_bytes(value)
    return value


class DSPyExample(StrictModel):
    """One SDK-neutral record convertible to ``dspy.Example``."""

    id: str = Field(pattern=_ID_PATTERN)
    inputs: dict[str, Any] = Field(min_length=1, max_length=128)
    outputs: dict[str, Any] = Field(default_factory=dict, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def inputs_must_be_canonical(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_field_mapping(value, noun="DSPy input")

    @field_validator("outputs")
    @classmethod
    def outputs_must_be_canonical(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_field_mapping(value, noun="DSPy output")

    @model_validator(mode="after")
    def example_must_be_unambiguous_and_bounded(self) -> DSPyExample:
        overlap = sorted(self.inputs.keys() & self.outputs.keys())
        if overlap:
            raise ValueError(f"DSPy input and output fields overlap: {', '.join(overlap)}")
        _validate_metadata(self.metadata, noun="DSPy example")
        if len(_canonical_json_bytes(self.model_dump(mode="json"))) > _MAX_EXAMPLE_BYTES:
            raise ValueError(f"DSPy example exceeds {_MAX_EXAMPLE_BYTES} bytes")
        return self


class DSPyExamplePayload(StrictModel):
    """Plain values plus the fields that should be marked as DSPy inputs."""

    values: dict[str, Any]
    input_fields: list[str]


class DSPyDatasetDocument(StrictModel):
    """Versioned SDK-neutral dataset for DSPy optimizer calls."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    examples: list[DSPyExample] = Field(min_length=1, max_length=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def dataset_must_have_one_signature(self) -> DSPyDatasetDocument:
        ids = [example.id for example in self.examples]
        if len(ids) != len(set(ids)):
            raise ValueError("DSPy example ids must be unique")
        input_fields = set(self.examples[0].inputs)
        output_fields = set(self.examples[0].outputs)
        for example in self.examples[1:]:
            if set(example.inputs) != input_fields:
                raise ValueError("DSPy examples must share identical input fields")
            if set(example.outputs) != output_fields:
                raise ValueError("DSPy examples must share identical output fields")
        _validate_metadata(self.metadata, noun="DSPy dataset")
        return self


class PromptComponentBinding(StrictModel):
    """Bind one external optimizer component to one prompt message."""

    id: str = Field(pattern=_ID_PATTERN)
    message_id: str = Field(pattern=_ID_PATTERN)
    mutable_field: Literal["content"] = "content"
    description: str | None = Field(default=None, max_length=1_000)

    @field_validator("description")
    @classmethod
    def description_must_be_safe(cls, value: str | None) -> str | None:
        if value is not None and "\x00" in value:
            raise ValueError("component description must not contain NUL")
        return value


class OptimizerAdapterDocument(StrictModel):
    """Bind an external optimizer to exact E2H capsule and variant identities."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    optimizer: OptimizerKind
    base_capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    base_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    components: list[PromptComponentBinding] = Field(min_length=1, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def components_must_be_unique(self) -> OptimizerAdapterDocument:
        component_ids = [component.id for component in self.components]
        message_ids = [component.message_id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("optimizer component ids must be unique")
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("optimizer prompt message bindings must be unique")
        _validate_metadata(self.metadata, noun="optimizer adapter")
        return self


class OptimizerAdapterVerification(StrictModel):
    """Digest proof that an optimizer adapter matches its capsule and variant."""

    schema_version: Literal["0.1"] = "0.1"
    adapter_id: str = Field(pattern=_ID_PATTERN)
    optimizer: OptimizerKind
    adapter_sha256: str = Field(pattern=_SHA256_PATTERN)
    base_capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    base_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    component_ids: list[str]


class PromptCandidateUpdate(StrictModel):
    """One optimizer-proposed prompt component replacement."""

    component_id: str = Field(pattern=_ID_PATTERN)
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("content")
    @classmethod
    def content_must_be_safe(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("candidate prompt content must not contain NUL")
        return value


class OptimizerCandidateDocument(StrictModel):
    """One externally proposed candidate tied to an exact adapter and baseline."""

    schema_version: Literal["0.1"] = "0.1"
    candidate_id: str = Field(pattern=_ID_PATTERN)
    variant_id: str = Field(pattern=_ID_PATTERN)
    optimizer: OptimizerKind
    adapter_sha256: str = Field(pattern=_SHA256_PATTERN)
    base_capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    base_variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    updates: list[PromptCandidateUpdate] = Field(min_length=1, max_length=64)
    score: float | None = Field(default=None, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def updates_must_be_unique(self) -> OptimizerCandidateDocument:
        component_ids = [update.component_id for update in self.updates]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("optimizer candidate component ids must be unique")
        if self.score is not None and not math.isfinite(self.score):
            raise ValueError("optimizer candidate score must be finite")
        _validate_metadata(self.metadata, noun="optimizer candidate")
        return self


class OptimizerCheckFeedback(StrictModel):
    """Sanitized check-level signal suitable for optimizer metrics."""

    check_id: str
    status: str
    score: float = Field(ge=0, le=1)
    failure_category: str | None = None
    failure_code: str | None = None
    failure_impact: str | None = None
    retryability: str | None = None
    summary: str | None = Field(default=None, max_length=255)
    caused_by_check_id: str | None = None


class OptimizerFeedback(StrictModel):
    """SDK-neutral scalar and textual feedback for DSPy or GEPA metrics."""

    schema_version: Literal["0.1"] = "0.1"
    capsule_id: str
    run_status: str
    score: float = Field(ge=0, le=1)
    feedback: str = Field(min_length=1, max_length=_MAX_FEEDBACK_CHARS)
    checks: list[OptimizerCheckFeedback]


def dspy_example_payload(example: DSPyExample) -> DSPyExamplePayload:
    """Return data consumable by ``dspy.Example(**values).with_inputs(...)``."""
    values = {**example.inputs, **example.outputs}
    return DSPyExamplePayload(
        values={key: values[key] for key in sorted(values)},
        input_fields=sorted(example.inputs),
    )


def dspy_dataset_payload(dataset: DSPyDatasetDocument) -> list[DSPyExamplePayload]:
    """Return deterministic SDK-neutral payloads for every dataset record."""
    return [dspy_example_payload(example) for example in dataset.examples]


def optimizer_adapter_sha256(document: OptimizerAdapterDocument) -> str:
    """Return the canonical identity of one optimizer adapter."""
    return hashlib.sha256(_canonical_json_bytes(document.model_dump(mode="json"))).hexdigest()


def optimizer_candidate_sha256(document: OptimizerCandidateDocument) -> str:
    """Return the canonical identity of one optimizer candidate."""
    return hashlib.sha256(_canonical_json_bytes(document.model_dump(mode="json"))).hexdigest()


def verify_optimizer_adapter(
    adapter: OptimizerAdapterDocument,
    capsule: TaskCapsule,
    variant_document: HarnessVariantDocument,
) -> OptimizerAdapterVerification:
    """Verify exact identities and prompt component bindings without execution."""
    adapter = _revalidate_optimizer_input(
        adapter,
        OptimizerAdapterDocument,
        noun="optimizer adapter",
    )
    capsule = _revalidate_optimizer_input(
        capsule,
        TaskCapsule,
        noun="task capsule",
    )
    variant_document = _revalidate_optimizer_input(
        variant_document,
        HarnessVariantDocument,
        noun="variant document",
    )
    verification = verify_variant_document(variant_document, capsule)
    if adapter.base_capsule_sha256 != verification.base_capsule_sha256:
        raise OptimizerAdapterError(
            "adapter base capsule digest does not match the supplied capsule"
        )
    if adapter.base_variant_sha256 != verification.variant_sha256:
        raise OptimizerAdapterError(
            "adapter base variant digest does not match the supplied variant"
        )
    prompt = variant_document.variant.prompt
    if prompt is None:
        raise OptimizerAdapterError("optimizer adapters require a prompt variant")
    message_ids = {message.id for message in prompt.messages}
    for component in adapter.components:
        if component.message_id not in message_ids:
            raise OptimizerAdapterError(
                f"optimizer component {component.id} references unknown prompt message "
                f"{component.message_id}"
            )
    return OptimizerAdapterVerification(
        adapter_id=adapter.id,
        optimizer=adapter.optimizer,
        adapter_sha256=optimizer_adapter_sha256(adapter),
        base_capsule_sha256=verification.base_capsule_sha256,
        base_variant_sha256=verification.variant_sha256,
        component_ids=[component.id for component in adapter.components],
    )


def apply_optimizer_candidate(
    adapter: OptimizerAdapterDocument,
    candidate: OptimizerCandidateDocument,
    capsule: TaskCapsule,
    variant_document: HarnessVariantDocument,
) -> HarnessVariantDocument:
    """Apply declared prompt replacements after complete digest verification."""
    adapter = _revalidate_optimizer_input(
        adapter,
        OptimizerAdapterDocument,
        noun="optimizer adapter",
    )
    candidate = _revalidate_optimizer_input(
        candidate,
        OptimizerCandidateDocument,
        noun="optimizer candidate",
    )
    capsule = _revalidate_optimizer_input(
        capsule,
        TaskCapsule,
        noun="task capsule",
    )
    variant_document = _revalidate_optimizer_input(
        variant_document,
        HarnessVariantDocument,
        noun="variant document",
    )
    verification = verify_optimizer_adapter(adapter, capsule, variant_document)
    if candidate.optimizer is not adapter.optimizer:
        raise OptimizerAdapterError("candidate optimizer does not match adapter optimizer")
    if candidate.adapter_sha256 != verification.adapter_sha256:
        raise OptimizerAdapterError("candidate adapter digest does not match the supplied adapter")
    if candidate.base_capsule_sha256 != verification.base_capsule_sha256:
        raise OptimizerAdapterError(
            "candidate base capsule digest does not match the supplied capsule"
        )
    if candidate.base_variant_sha256 != verification.base_variant_sha256:
        raise OptimizerAdapterError(
            "candidate base variant digest does not match the supplied variant"
        )

    bindings = {component.id: component.message_id for component in adapter.components}
    unknown = sorted({update.component_id for update in candidate.updates} - bindings.keys())
    if unknown:
        raise OptimizerAdapterError(
            f"candidate references undeclared optimizer components: {', '.join(unknown)}"
        )

    payload = variant_document.variant.model_dump(mode="json")
    if _OPTIMIZER_PROVENANCE_KEY in payload["metadata"]:
        raise OptimizerAdapterError(
            f"base variant metadata already contains reserved key {_OPTIMIZER_PROVENANCE_KEY}"
        )
    prompt = payload["prompt"]
    if not isinstance(prompt, dict):
        raise OptimizerAdapterError("optimizer adapters require a prompt variant")
    messages = prompt.get("messages")
    if not isinstance(messages, list):
        raise OptimizerAdapterError("optimizer adapter prompt messages are invalid")

    updates_by_message = {
        bindings[update.component_id]: update.content for update in candidate.updates
    }
    changed = False
    for message in messages:
        if not isinstance(message, dict):
            raise OptimizerAdapterError("optimizer adapter prompt messages are invalid")
        message_id = message.get("id")
        if isinstance(message_id, str) and message_id in updates_by_message:
            replacement = updates_by_message[message_id]
            if message.get("content") != replacement:
                message["content"] = replacement
                changed = True
    if not changed:
        raise OptimizerAdapterError("optimizer candidate must change at least one prompt component")

    payload["id"] = candidate.variant_id
    provenance: dict[str, Any] = {
        "adapter_id": adapter.id,
        "adapter_sha256": verification.adapter_sha256,
        "candidate_id": candidate.candidate_id,
        "candidate_sha256": optimizer_candidate_sha256(candidate),
        "optimizer": candidate.optimizer.value,
        "base_variant_sha256": verification.base_variant_sha256,
    }
    if candidate.score is not None:
        provenance["score"] = candidate.score
    payload["metadata"] = {
        **payload["metadata"],
        _OPTIMIZER_PROVENANCE_KEY: provenance,
    }
    variant = HarnessVariant.model_validate(payload)
    return HarnessVariantDocument(
        base_capsule_sha256=verification.base_capsule_sha256,
        variant=variant,
        metadata=variant_document.metadata,
    )


def feedback_from_run_result(result: RunResult) -> OptimizerFeedback:
    """Create bounded GEPA-friendly feedback without copying free-form runner text."""
    total = len(result.checks)
    passed = sum(check.status is CheckStatus.PASSED for check in result.checks)
    score = passed / total if total else 0.0
    lines = [
        f"Run {result.capsule_id} finished with status {result.status.value}.",
        f"Passed {passed} of {total} checks; scalar score {score:.6f}.",
    ]
    checks: list[OptimizerCheckFeedback] = []
    for check in result.checks:
        failure = check.failure
        safe_summary = _FAILURE_SUMMARIES[failure.code] if failure is not None else None
        check_feedback = OptimizerCheckFeedback(
            check_id=check.id,
            status=check.status.value,
            score=1.0 if check.status is CheckStatus.PASSED else 0.0,
            failure_category=failure.category.value if failure is not None else None,
            failure_code=failure.code.value if failure is not None else None,
            failure_impact=failure.impact.value if failure is not None else None,
            retryability=failure.retryability.value if failure is not None else None,
            summary=safe_summary,
            caused_by_check_id=failure.caused_by_check_id if failure is not None else None,
        )
        checks.append(check_feedback)
        line = f"{check.id}: {check.status.value}"
        if failure is not None:
            line += (
                f"; {failure.impact.value}/{failure.category.value}/{failure.code.value}; "
                f"{safe_summary}"
            )
            if failure.caused_by_check_id is not None:
                line += f"; caused by {failure.caused_by_check_id}"
        lines.append(line + ".")
    feedback = "\n".join(lines)
    if len(feedback) > _MAX_FEEDBACK_CHARS:
        marker = "\n... <feedback truncated> ..."
        feedback = feedback[: _MAX_FEEDBACK_CHARS - len(marker)] + marker
    return OptimizerFeedback(
        capsule_id=result.capsule_id,
        run_status=result.status.value,
        score=score,
        feedback=feedback,
        checks=checks,
    )


def gepa_prediction_payload(feedback: OptimizerFeedback) -> dict[str, float | str]:
    """Return kwargs for ``dspy.Prediction(score=..., feedback=...)``."""
    return {"score": feedback.score, "feedback": feedback.feedback}


def _load_document(path: Path, model: type[_ModelT], noun: str) -> _ModelT:
    try:
        payload = load_mapping_document(path, noun=noun, max_bytes=_MAX_DOCUMENT_BYTES)
        return model.model_validate(payload)
    except ValueError as exc:
        if isinstance(exc, OptimizerAdapterError):
            raise
        raise OptimizerAdapterError(f"invalid {noun}: {exc}") from exc


def load_dspy_dataset(path: Path) -> DSPyDatasetDocument:
    """Load one strict SDK-neutral DSPy dataset."""
    return _load_document(path, DSPyDatasetDocument, "DSPy dataset")


def load_optimizer_adapter(path: Path) -> OptimizerAdapterDocument:
    """Load one strict DSPy/GEPA adapter document."""
    return _load_document(path, OptimizerAdapterDocument, "optimizer adapter")


def load_optimizer_candidate(path: Path) -> OptimizerCandidateDocument:
    """Load one strict external optimizer candidate."""
    return _load_document(path, OptimizerCandidateDocument, "optimizer candidate")
