"""Shared revalidation for mutable object-backed runtime inputs."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from e2h.models import TaskCapsule
from e2h.variants import HarnessVariantDocument

InvocationT = TypeVar("InvocationT", bound=BaseModel)
ModelT = TypeVar("ModelT", bound=BaseModel)


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
    if not isinstance(invocation, invocation_type):
        raise error_type(
            f"invalid {invocation_noun}: expected {invocation_type.__name__}, "
            f"got {type(invocation).__name__}"
        )

    def revalidate_model(
        value: BaseModel,
        model_type: type[ModelT],
        *,
        noun: str,
    ) -> ModelT:
        try:
            payload = value.model_dump(mode="json", warnings="none")
            return model_type.model_validate(payload)
        except ValueError as exc:
            raise error_type(f"invalid {noun}: {exc}") from exc

    return (
        revalidate_model(document, HarnessVariantDocument, noun="variant document"),
        revalidate_model(capsule, TaskCapsule, noun="task capsule"),
        revalidate_model(invocation, invocation_type, noun=invocation_noun),
    )


__all__ = ["revalidate_runtime_inputs"]
