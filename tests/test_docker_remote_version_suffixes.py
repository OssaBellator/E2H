from __future__ import annotations

import pytest

from e2h.docker_remote import DockerRemoteError, DockerVersion, _parse_version


def test_docker_stable_legacy_suffix_is_accepted() -> None:
    assert _parse_version("29.5.2-ce", noun="client") == DockerVersion(29, 5, 2)


@pytest.mark.parametrize("value", ["29.5.2-rc.1", "29.5.2-dev", "29.5.2-ce-rc.1"])
def test_docker_nonrelease_hyphen_suffix_fails_closed(value: str) -> None:
    with pytest.raises(DockerRemoteError, match="prerelease version is not accepted"):
        _parse_version(value, noun="client")
