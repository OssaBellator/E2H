from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.models import ContainerSandbox


def test_container_sandbox_rejects_option_like_immutable_image_reference() -> None:
    value = "--volume=/host:/mnt@sha256:" + "0" * 64

    with pytest.raises(ValidationError, match="must not begin with '-'"):
        ContainerSandbox(image=value)


def test_container_sandbox_accepts_normal_immutable_image_reference() -> None:
    value = "registry.example/replay@sha256:" + "0" * 64

    assert ContainerSandbox(image=value).image == value
