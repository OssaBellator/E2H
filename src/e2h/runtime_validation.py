"""Shared revalidation for mutable object-backed runtime inputs."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from e2h.models import TaskCapsule
from e2h.variants import HarnessVariantDocument

InvocationT = TypeVar("InvocationT", bound=BaseModel)
ModelT = TypeVar("ModelT", bound=BaseModel)


def revalidate_runtime_model(
    value: BaseModel,
    model_type: type[ModelT],
    *,
    error_type: type[ValueError],
    noun: str,
) -> ModelT:
    """Return one fully revalidated model after enforcing its concrete type boundary."""
    if not isinstance(value, model_type):
        raise error_type(
            f"invalid {noun}: expected {model_type.__name__}, got {type(value).__name__}"
        )
    try:
        payload = value.model_dump(mode="json", warnings="none")
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise error_type(f"invalid {noun}: {exc}") from exc


def revalidate_runtime_inputs(
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
    invocation: InvocationT,
    invocation_type: type[InvocationT],
    *,
    error_type: type[ValueError],
    invocation_noun: str,
) -> tuple[HarnessVariantDocument, TaskCapsule, InvocationT]:
    """Return fully revalidated copies of one object-backed runtime input set."""
    return (
        revalidate_runtime_model(
            document,
            HarnessVariantDocument,
            error_type=error_type,
            noun="variant document",
        ),
        revalidate_runtime_model(
            capsule,
            TaskCapsule,
            error_type=error_type,
            noun="task capsule",
        ),
        revalidate_runtime_model(
            invocation,
            invocation_type,
            error_type=error_type,
            noun=invocation_noun,
        ),
    )


__all__ = ["revalidate_runtime_inputs", "revalidate_runtime_model"]
