"""Shared exact-type revalidation for mutable promotion inputs."""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def revalidate_promotion_model(
    value: BaseModel,
    model_type: type[ModelT],
    *,
    noun: str,
) -> ModelT:
    """Return a warning-free revalidated copy after enforcing one concrete model type."""
    if type(value) is not model_type:
        raise ValueError(f"{noun} must be {model_type.__name__}, got {type(value).__name__}")
    payload = value.model_dump(mode="python", warnings="none")
    return model_type.model_validate(payload)


__all__ = ["revalidate_promotion_model"]
