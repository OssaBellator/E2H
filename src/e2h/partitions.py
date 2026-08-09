"""Content-addressed train, validation, and sealed-test dataset partitions."""

from __future__ import annotations

import hashlib
import json
import math
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import _validate_json_compatible, load_mapping_document
from e2h.optimizer_adapters import DSPyDatasetDocument

_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_ID_RE = re.compile(_ID_PATTERN)
_FIELD_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$")
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_DOCUMENT_BYTES = 2_097_152
_MAX_METADATA_BYTES = 65_536


class DatasetPartitionError(ValueError):
    """Raised when a dataset partition artifact cannot be safely used."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


_ModelT = TypeVar("_ModelT", bound=StrictModel)
_InputModelT = TypeVar("_InputModelT", bound=BaseModel)


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        _validate_json_compatible(value)
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


def _validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if len(_canonical_json_bytes(value)) > _MAX_METADATA_BYTES:
        raise ValueError(f"partition metadata exceeds {_MAX_METADATA_BYTES} bytes")
    return value


def _validate_output_mapping(value: dict[str, Any]) -> dict[str, Any]:
    if not value:
        raise ValueError("sealed predictions must define at least one output")
    for key in value:
        if _FIELD_RE.fullmatch(key) is None:
            raise ValueError("sealed prediction output keys must be Python identifiers")
    _canonical_json_bytes(value)
    return value


class PartitionRole(StrEnum):
    """Stable optimizer dataset split names."""

    TRAIN = "train"
    VALIDATION = "validation"
    SEALED_TEST = "sealed_test"


def _validated_partition_role(role: PartitionRole) -> PartitionRole:
    if type(role) is not PartitionRole:
        raise DatasetPartitionError(
            f"invalid partition role: expected PartitionRole, got {type(role).__name__}"
        )
    return role


class DatasetPartitionDocument(StrictModel):
    """Bind complete, disjoint dataset splits to exact private and public identities."""

    schema_version: Literal["0.1"] = "0.1"
    id: str = Field(pattern=_ID_PATTERN)
    dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    train: list[str] = Field(min_length=1, max_length=10_000)
    validation: list[str] = Field(min_length=1, max_length=10_000)
    sealed_test: list[str] = Field(min_length=1, max_length=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("train", "validation", "sealed_test")
    @classmethod
    def example_ids_must_be_unique_and_canonical(cls, value: list[str]) -> list[str]:
        if any(_ID_RE.fullmatch(item) is None for item in value):
            raise ValueError("partition example ids must use stable identifiers")
        if len(value) != len(set(value)):
            raise ValueError("partition example ids must be unique within each split")
        return sorted(value)

    @field_validator("metadata")
    @classmethod
    def metadata_must_be_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def splits_must_not_overlap(self) -> DatasetPartitionDocument:
        train = set(self.train)
        validation = set(self.validation)
        sealed = set(self.sealed_test)
        overlaps = (train & validation) | (train & sealed) | (validation & sealed)
        if overlaps:
            raise ValueError(
                "dataset partitions must be disjoint; overlapping ids: "
                + ", ".join(sorted(overlaps))
            )
        return self

    def ids_for(self, role: PartitionRole) -> list[str]:
        """Return normalized example ids for one split."""
        role = _validated_partition_role(role)
        if role is PartitionRole.TRAIN:
            return self.train
        if role is PartitionRole.VALIDATION:
            return self.validation
        if role is PartitionRole.SEALED_TEST:
            return self.sealed_test
        raise DatasetPartitionError(f"unsupported partition role: {role!r}")


class DatasetPartitionVerification(StrictModel):
    """Private and public digest proofs plus cardinalities for one manifest."""

    schema_version: Literal["0.1"] = "0.1"
    partition_id: str = Field(pattern=_ID_PATTERN)
    partition_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_partition_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_id: str = Field(pattern=_ID_PATTERN)
    dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    train_examples: int = Field(ge=1)
    validation_examples: int = Field(ge=1)
    sealed_test_examples: int = Field(ge=1)
    output_fields: list[str] = Field(min_length=1)


class PartitionExamplePayload(StrictModel):
    """One optimizer-facing example with labels included or withheld."""

    id: str = Field(pattern=_ID_PATTERN)
    values: dict[str, Any]
    input_fields: list[str] = Field(min_length=1)


class DatasetPartitionExport(StrictModel):
    """One deterministic split export carrying label-free public provenance."""

    schema_version: Literal["0.1"] = "0.1"
    partition_id: str = Field(pattern=_ID_PATTERN)
    public_partition_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_id: str = Field(pattern=_ID_PATTERN)
    public_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    role: PartitionRole
    labels_revealed: bool
    examples: list[PartitionExamplePayload] = Field(min_length=1)


class SealedPrediction(StrictModel):
    """One candidate prediction for a sealed-test example."""

    example_id: str = Field(pattern=_ID_PATTERN)
    outputs: dict[str, Any] = Field(max_length=128)

    @field_validator("outputs")
    @classmethod
    def outputs_must_be_canonical(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_output_mapping(value)


class SealedPredictionDocument(StrictModel):
    """Predictions bound to label-free public dataset and partition identities."""

    schema_version: Literal["0.1"] = "0.1"
    public_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    public_partition_sha256: str = Field(pattern=_SHA256_PATTERN)
    predictions: list[SealedPrediction] = Field(min_length=1, max_length=10_000)

    @model_validator(mode="after")
    def prediction_ids_must_be_unique(self) -> SealedPredictionDocument:
        ids = [prediction.example_id for prediction in self.predictions]
        if len(ids) != len(set(ids)):
            raise ValueError("sealed prediction example ids must be unique")
        return self


class SealedEvaluationReport(StrictModel):
    """Aggregate-only sealed-test result that never returns expected labels."""

    schema_version: Literal["0.1"] = "0.1"
    partition_id: str = Field(pattern=_ID_PATTERN)
    public_partition_sha256: str = Field(pattern=_SHA256_PATTERN)
    dataset_id: str = Field(pattern=_ID_PATTERN)
    public_dataset_sha256: str = Field(pattern=_SHA256_PATTERN)
    total: int = Field(ge=1)
    correct: int = Field(ge=0)
    score: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def counts_must_be_consistent(self) -> SealedEvaluationReport:
        if self.correct > self.total:
            raise ValueError("sealed evaluation correct count must not exceed total")
        if not math.isclose(self.score, self.correct / self.total):
            raise ValueError("sealed evaluation score must match correct / total")
        return self


def _revalidate_partition_model(
    value: BaseModel,
    model_type: type[_InputModelT],
    *,
    noun: str,
) -> _InputModelT:
    if type(value) is not model_type:
        raise ValueError(f"{noun} must be {model_type.__name__}, got {type(value).__name__}")
    payload = value.model_dump(mode="python", warnings="none")
    return model_type.model_validate(payload)


def _validated_partition_digest_model(
    value: BaseModel,
    model_type: type[_InputModelT],
    *,
    noun: str,
) -> _InputModelT:
    try:
        return _revalidate_partition_model(value, model_type, noun=noun)
    except ValueError as exc:
        raise DatasetPartitionError(f"invalid {noun}: {exc}") from exc


def _dspy_dataset_sha256_validated(dataset: DSPyDatasetDocument) -> str:
    payload = dataset.model_dump(mode="json")
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def dspy_dataset_sha256(dataset: DSPyDatasetDocument) -> str:
    """Return the private canonical identity of the complete labelled dataset."""
    dataset = _validated_partition_digest_model(
        dataset,
        DSPyDatasetDocument,
        noun="DSPy dataset",
    )
    return _dspy_dataset_sha256_validated(dataset)


def _dspy_dataset_public_sha256_validated(dataset: DSPyDatasetDocument) -> str:
    first = dataset.examples[0]
    payload = {
        "schema_version": dataset.schema_version,
        "id": dataset.id,
        "input_fields": sorted(first.inputs),
        "output_fields": sorted(first.outputs),
        "examples": [
            {
                "id": example.id,
                "inputs": {key: example.inputs[key] for key in sorted(example.inputs)},
            }
            for example in sorted(dataset.examples, key=lambda item: item.id)
        ],
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def dspy_dataset_public_sha256(dataset: DSPyDatasetDocument) -> str:
    """Return a label- and metadata-free identity suitable for optimizer artifacts."""
    dataset = _validated_partition_digest_model(
        dataset,
        DSPyDatasetDocument,
        noun="DSPy dataset",
    )
    return _dspy_dataset_public_sha256_validated(dataset)


def _dataset_partition_sha256_validated(document: DatasetPartitionDocument) -> str:
    payload = document.model_dump(mode="json")
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def dataset_partition_sha256(document: DatasetPartitionDocument) -> str:
    """Return the private canonical identity of one complete partition manifest."""
    document = _validated_partition_digest_model(
        document,
        DatasetPartitionDocument,
        noun="dataset partition manifest",
    )
    return _dataset_partition_sha256_validated(document)


def _dataset_partition_public_sha256_validated(document: DatasetPartitionDocument) -> str:
    payload = {
        "schema_version": document.schema_version,
        "id": document.id,
        "public_dataset_sha256": document.public_dataset_sha256,
        "train": document.train,
        "validation": document.validation,
        "sealed_test": document.sealed_test,
    }
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def dataset_partition_public_sha256(document: DatasetPartitionDocument) -> str:
    """Return a public identity that excludes private dataset and metadata digests."""
    document = _validated_partition_digest_model(
        document,
        DatasetPartitionDocument,
        noun="dataset partition manifest",
    )
    return _dataset_partition_public_sha256_validated(document)


def _validated_inputs(
    document: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
) -> tuple[DatasetPartitionDocument, DSPyDatasetDocument]:
    try:
        validated_document = _revalidate_partition_model(
            document,
            DatasetPartitionDocument,
            noun="dataset partition manifest",
        )
        validated_dataset = _revalidate_partition_model(
            dataset,
            DSPyDatasetDocument,
            noun="DSPy dataset",
        )
    except ValueError as exc:
        raise DatasetPartitionError(f"invalid dataset partition inputs: {exc}") from exc
    return validated_document, validated_dataset


def _verify_validated_partitions(
    document: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
) -> DatasetPartitionVerification:
    digest = _dspy_dataset_sha256_validated(dataset)
    if document.dataset_sha256 != digest:
        raise DatasetPartitionError("partition dataset digest does not match the supplied dataset")
    public_digest = _dspy_dataset_public_sha256_validated(dataset)
    if document.public_dataset_sha256 != public_digest:
        raise DatasetPartitionError(
            "partition public dataset digest does not match the supplied dataset"
        )

    dataset_ids = {example.id for example in dataset.examples}
    assigned = set(document.train) | set(document.validation) | set(document.sealed_test)
    unknown = sorted(assigned - dataset_ids)
    missing = sorted(dataset_ids - assigned)
    if unknown:
        raise DatasetPartitionError(
            "partition references unknown dataset examples: " + ", ".join(unknown)
        )
    if missing:
        raise DatasetPartitionError(
            "partition does not assign every dataset example: " + ", ".join(missing)
        )

    output_fields = sorted(dataset.examples[0].outputs)
    if not output_fields:
        raise DatasetPartitionError(
            "partitioned optimizer datasets require at least one output field"
        )

    return DatasetPartitionVerification(
        partition_id=document.id,
        partition_sha256=_dataset_partition_sha256_validated(document),
        public_partition_sha256=_dataset_partition_public_sha256_validated(document),
        dataset_id=dataset.id,
        dataset_sha256=digest,
        public_dataset_sha256=public_digest,
        train_examples=len(document.train),
        validation_examples=len(document.validation),
        sealed_test_examples=len(document.sealed_test),
        output_fields=output_fields,
    )


def verify_dataset_partitions(
    document: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
) -> DatasetPartitionVerification:
    """Verify exact private identity, public identity, coverage, and labels."""
    validated_document, validated_dataset = _validated_inputs(document, dataset)
    return _verify_validated_partitions(validated_document, validated_dataset)


def export_dataset_partition(
    document: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
    role: PartitionRole,
) -> DatasetPartitionExport:
    """Export one split while withholding sealed-test outputs and metadata."""
    role = _validated_partition_role(role)
    document, dataset = _validated_inputs(document, dataset)
    verification = _verify_validated_partitions(document, dataset)
    examples_by_id = {example.id: example for example in dataset.examples}
    exported: list[PartitionExamplePayload] = []
    labels_revealed = role is not PartitionRole.SEALED_TEST

    for example_id in document.ids_for(role):
        example = examples_by_id[example_id]
        values: dict[str, Any] = {key: example.inputs[key] for key in sorted(example.inputs)}
        if labels_revealed:
            values.update({key: example.outputs[key] for key in sorted(example.outputs)})
            values = {key: values[key] for key in sorted(values)}
        exported.append(
            PartitionExamplePayload(
                id=example.id,
                values=values,
                input_fields=sorted(example.inputs),
            )
        )

    return DatasetPartitionExport(
        partition_id=document.id,
        public_partition_sha256=verification.public_partition_sha256,
        dataset_id=dataset.id,
        public_dataset_sha256=verification.public_dataset_sha256,
        role=role,
        labels_revealed=labels_revealed,
        examples=exported,
    )


def evaluate_sealed_predictions(
    document: DatasetPartitionDocument,
    dataset: DSPyDatasetDocument,
    predictions: SealedPredictionDocument,
) -> SealedEvaluationReport:
    """Score exact-match sealed predictions without returning labels or case results."""
    document, dataset = _validated_inputs(document, dataset)
    try:
        predictions = _revalidate_partition_model(
            predictions,
            SealedPredictionDocument,
            noun="sealed predictions",
        )
    except ValueError as exc:
        raise DatasetPartitionError(f"invalid sealed predictions: {exc}") from exc
    verification = _verify_validated_partitions(document, dataset)
    if predictions.public_dataset_sha256 != verification.public_dataset_sha256:
        raise DatasetPartitionError(
            "sealed predictions public dataset digest does not match the supplied dataset"
        )
    if predictions.public_partition_sha256 != verification.public_partition_sha256:
        raise DatasetPartitionError(
            "sealed predictions public partition digest does not match the supplied manifest"
        )

    expected_ids = set(document.sealed_test)
    predictions_by_id = {
        prediction.example_id: prediction for prediction in predictions.predictions
    }
    actual_ids = set(predictions_by_id)
    missing = sorted(expected_ids - actual_ids)
    unexpected = sorted(actual_ids - expected_ids)
    if missing:
        raise DatasetPartitionError(
            "sealed predictions are missing examples: " + ", ".join(missing)
        )
    if unexpected:
        raise DatasetPartitionError(
            "sealed predictions contain non-sealed examples: " + ", ".join(unexpected)
        )

    examples_by_id = {example.id: example for example in dataset.examples}
    output_fields = set(verification.output_fields)
    correct = 0
    for example_id in document.sealed_test:
        prediction = predictions_by_id[example_id]
        if set(prediction.outputs) != output_fields:
            raise DatasetPartitionError(
                f"sealed prediction {example_id} must define exactly these outputs: "
                + ", ".join(verification.output_fields)
            )
        expected = examples_by_id[example_id].outputs
        if _canonical_json_bytes(prediction.outputs) == _canonical_json_bytes(expected):
            correct += 1

    total = len(document.sealed_test)
    return SealedEvaluationReport(
        partition_id=document.id,
        public_partition_sha256=verification.public_partition_sha256,
        dataset_id=dataset.id,
        public_dataset_sha256=verification.public_dataset_sha256,
        total=total,
        correct=correct,
        score=correct / total,
    )


def _load_model(path: Path, model: type[_ModelT], *, noun: str) -> _ModelT:
    try:
        payload = load_mapping_document(path, noun=noun, max_bytes=_MAX_DOCUMENT_BYTES)
        return model.model_validate(payload)
    except ValueError as exc:
        raise DatasetPartitionError(str(exc)) from exc


def load_dataset_partitions(path: Path) -> DatasetPartitionDocument:
    """Load one strict JSON or YAML partition manifest."""
    return _load_model(path, DatasetPartitionDocument, noun="dataset partition manifest")


def load_sealed_predictions(path: Path) -> SealedPredictionDocument:
    """Load one strict JSON or YAML sealed prediction document."""
    return _load_model(path, SealedPredictionDocument, noun="sealed prediction document")
