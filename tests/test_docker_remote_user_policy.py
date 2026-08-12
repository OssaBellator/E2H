from __future__ import annotations

import pytest

from e2h.docker_remote import DockerRemoteError, _validated_remote_sandbox
from e2h.models import ContainerSandbox

IMAGE = "python@sha256:" + "0" * 64
NON_ASCII_DECIMAL_ONE = chr(0x0661)


@pytest.mark.parametrize("user", ["65532", "65532:0"])
def test_remote_sandbox_requires_explicit_non_root_uid_gid(user: str) -> None:
    with pytest.raises(DockerRemoteError, match="explicit non-root numeric uid:gid"):
        _validated_remote_sandbox(ContainerSandbox(image=IMAGE, user=user))


@pytest.mark.parametrize(
    "user",
    [
        f"{NON_ASCII_DECIMAL_ONE}:{NON_ASCII_DECIMAL_ONE}",
        f"1:{NON_ASCII_DECIMAL_ONE}",
        f"{NON_ASCII_DECIMAL_ONE}:1",
    ],
)
def test_remote_sandbox_rejects_non_ascii_numeric_uid_gid(user: str) -> None:
    # The base model now rejects these spellings before Docker sees them. Mutate a
    # previously valid instance so the remote boundary still proves it revalidates
    # independently instead of relying only on construction-time validation.
    sandbox = ContainerSandbox(image=IMAGE, user="1:1")
    sandbox.user = user
    with pytest.raises(DockerRemoteError, match="invalid container sandbox"):
        _validated_remote_sandbox(sandbox)


def test_remote_sandbox_accepts_explicit_non_root_uid_gid() -> None:
    sandbox = ContainerSandbox(image=IMAGE, user="1234:5678")

    assert _validated_remote_sandbox(sandbox).user == "1234:5678"
