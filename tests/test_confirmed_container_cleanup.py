from __future__ import annotations

import subprocess

import pytest

import e2h.sandbox as sandbox
from e2h.sandbox import force_remove_confirmed_container

CONTAINER_ID = "a" * 64


def _completed(
    argv: list[str],
    returncode: int,
    *,
    stdout: bytes = b"",
    stderr: bytes = b"",
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)


def test_confirmed_cleanup_success_needs_no_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        calls.append(argv)
        return _completed(argv, 0)

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    assert force_remove_confirmed_container("docker-test", CONTAINER_ID) is None
    assert calls == [["docker-test", "rm", "-f", "-v", CONTAINER_ID]]


def test_confirmed_cleanup_accepts_exact_id_absence_after_failed_remove(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        calls.append(argv)
        if argv[1] == "rm":
            return _completed(argv, 1, stderr=b"remove response lost")
        assert argv[1] == "ps"
        return _completed(argv, 0, stdout=b"")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    assert force_remove_confirmed_container("docker-test", CONTAINER_ID) is None
    assert calls[1] == [
        "docker-test",
        "ps",
        "-a",
        "--no-trunc",
        "--format",
        "{{.ID}}",
        "--filter",
        f"id={CONTAINER_ID}",
    ]


def test_confirmed_cleanup_rejects_exact_id_still_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        if argv[1] == "rm":
            return _completed(argv, 1, stderr=b"busy")
        return _completed(argv, 0, stdout=(CONTAINER_ID + "\n").encode())

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    error = force_remove_confirmed_container("docker-test", CONTAINER_ID)
    assert error is not None
    assert "exact container still exists" in error


def test_confirmed_cleanup_does_not_confuse_prefix_match_for_exact_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_id = "a" * 63 + "b"

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        if argv[1] == "rm":
            return _completed(argv, 1, stderr=b"ambiguous failure")
        return _completed(argv, 0, stdout=(other_id + "\n").encode())

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    assert force_remove_confirmed_container("docker-test", CONTAINER_ID) is None


def test_confirmed_cleanup_probe_failure_remains_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        del kwargs
        if argv[1] == "rm":
            return _completed(argv, 1, stderr=b"remove failed")
        return _completed(argv, 17, stderr=b"probe failed")

    monkeypatch.setattr(sandbox.subprocess, "run", fake_run)

    error = force_remove_confirmed_container("docker-test", CONTAINER_ID)
    assert error is not None
    assert "probe failed" in error


@pytest.mark.parametrize("container_id", ["", "a" * 12, "g" * 64, "a" * 65])
def test_confirmed_cleanup_requires_full_hex_identity(
    monkeypatch: pytest.MonkeyPatch,
    container_id: str,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("invalid IDs must fail before Docker")

    monkeypatch.setattr(sandbox.subprocess, "run", forbidden)

    assert force_remove_confirmed_container("docker-test", container_id) == (
        "invalid confirmed Docker container ID"
    )
