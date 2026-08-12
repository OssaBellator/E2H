from __future__ import annotations

import pytest
from pydantic import ValidationError

from e2h.models import ContainerSandbox

IMAGE = "python@sha256:" + "0" * 64
_MAX_ID = (1 << 31) - 1


@pytest.mark.parametrize(
    "user",
    [
        str(_MAX_ID + 1),
        f"65532:{_MAX_ID + 1}",
    ],
)
def test_container_sandbox_rejects_numeric_ids_above_docker_range(user: str) -> None:
    with pytest.raises(ValidationError, match=str(_MAX_ID)):
        ContainerSandbox(image=IMAGE, user=user)


@pytest.mark.parametrize("user", [str(_MAX_ID), f"65532:{_MAX_ID}"])
def test_container_sandbox_accepts_maximum_docker_numeric_id(user: str) -> None:
    assert ContainerSandbox(image=IMAGE, user=user).user == user
