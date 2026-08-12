from __future__ import annotations

import pytest

from e2h.docker_remote import DockerRemoteError, _validated_remote_sandbox
from e2h.models import ContainerSandbox

IMAGE = "python@sha256:" + "0" * 64


@pytest.mark.parametrize("user", ["65532", "65532:0"])
def test_remote_sandbox_requires_explicit_non_root_uid_gid(user: str) -> None:
    with pytest.raises(DockerRemoteError, match="explicit non-root numeric uid:gid"):
        _validated_remote_sandbox(ContainerSandbox(image=IMAGE, user=user))


def test_remote_sandbox_accepts_explicit_non_root_uid_gid() -> None:
    sandbox = ContainerSandbox(image=IMAGE, user="1234:5678")

    assert _validated_remote_sandbox(sandbox).user == "1234:5678"
