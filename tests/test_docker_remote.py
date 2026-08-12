from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

from e2h.docker_remote import (
    DockerRemoteError,
    DockerVersion,
    _parse_version,
    inspect_docker_versions,
    patched_docker_archive_supported,
    prepared_workspace_volume,
    require_patched_docker_archive,
)
from e2h.models import ContainerSandbox
from e2h.workspace_archive import WorkspaceArchive, stable_workspace_archive

IMAGE = "python@sha256:" + "0" * 64


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "docker-test"
    log = tmp_path / "docker-log.jsonl"
    runtime.write_text(
        f"""#!{sys.executable}
import hashlib
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
record = {{"args": args}}
if args and args[0] == "cp":
    data = sys.stdin.buffer.read()
    record["stdin_bytes"] = len(data)
    record["stdin_sha256"] = hashlib.sha256(data).hexdigest()
with Path(os.environ["DOCKER_TEST_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\\n")

if args and args[0] == "version":
    if os.environ.get("DOCKER_TEST_FAIL"):
        print("version probe failed", file=sys.stderr)
        raise SystemExit(7)
    print(os.environ.get("DOCKER_TEST_VERSIONS", "29.7.2 29.7.2"))
elif args[:2] == ["image", "inspect"]:
    if os.environ.get("DOCKER_TEST_IMAGE_INSPECT_FAIL"):
        print("image inspect failed", file=sys.stderr)
        raise SystemExit(14)
    print(os.environ.get("DOCKER_TEST_IMAGE_VOLUMES", "none"))
elif args[:2] == ["volume", "create"]:
    if os.environ.get("DOCKER_TEST_VOLUME_CREATE_FAIL"):
        print("volume create failed", file=sys.stderr)
        raise SystemExit(8)
    print(args[-1])
elif args and args[0] == "create":
    if os.environ.get("DOCKER_TEST_CREATE_FAIL"):
        print("container create failed", file=sys.stderr)
        raise SystemExit(9)
    print("a" * 64)
elif args and args[0] == "cp":
    if os.environ.get("DOCKER_TEST_CP_FAIL"):
        print("copy failed", file=sys.stderr)
        raise SystemExit(10)
elif args and args[0] == "rm":
    if os.environ.get("DOCKER_TEST_RM_FAIL"):
        print("container removal failed", file=sys.stderr)
        raise SystemExit(11)
elif args[:2] == ["volume", "rm"]:
    if os.environ.get("DOCKER_TEST_VOLUME_RM_FAIL"):
        print("volume removal failed", file=sys.stderr)
        raise SystemExit(12)
else:
    raise SystemExit(13)
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def _records(log: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def _sealed_archive(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("trusted", encoding="utf-8")
    return stable_workspace_archive(
        workspace.resolve(),
        max_bytes=1024,
        max_entries=10,
    )


def test_parse_docker_version_accepts_release_suffix() -> None:
    assert _parse_version("29.5.2-ce", noun="client") == DockerVersion(29, 5, 2)
    assert _parse_version("30.0.0+vendor", noun="server") == DockerVersion(30, 0, 0)


def test_parse_docker_version_rejects_incomplete_value() -> None:
    with pytest.raises(DockerRemoteError, match="parse Docker client version"):
        _parse_version("29.6", noun="client")


@pytest.mark.parametrize("value", ["29.5.2-dev", "29.5.2-nightly", "29.5.2-snapshot"])
def test_parse_docker_version_rejects_unknown_hyphen_suffix(value: str) -> None:
    with pytest.raises(DockerRemoteError, match="prerelease version is not accepted"):
        _parse_version(value, noun="client")


def test_inspect_docker_versions_reads_client_and_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.6.2 30.0.1")

    assert inspect_docker_versions(str(runtime)) == (
        DockerVersion(29, 6, 2),
        DockerVersion(30, 0, 1),
    )


def test_patched_docker_archive_requires_both_sides_at_minimum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))

    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.7.2 29.7.2")
    assert patched_docker_archive_supported(str(runtime)) is True

    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.7.1 29.7.2")
    assert patched_docker_archive_supported(str(runtime)) is False

    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.7.2 29.7.1")
    assert patched_docker_archive_supported(str(runtime)) is False


def test_require_patched_docker_archive_reports_observed_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.7.1 29.7.2")

    with pytest.raises(
        DockerRemoteError,
        match=r"client and server >= 29\.7\.2; observed client 29\.7\.1, server 29\.7\.2",
    ):
        require_patched_docker_archive(str(runtime))


def test_version_probe_rejects_runtime_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_FAIL", "1")

    with pytest.raises(DockerRemoteError, match="exit 7: version probe failed"):
        inspect_docker_versions(str(runtime))


def test_version_probe_rejects_unexpected_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_VERSIONS", "29.6.2")

    with pytest.raises(DockerRemoteError, match="unexpected response"):
        inspect_docker_versions(str(runtime))


def test_version_probe_rejects_invalid_runtime_binary() -> None:
    assert patched_docker_archive_supported("") is False
    with pytest.raises(DockerRemoteError, match="non-empty"):
        inspect_docker_versions("\x00")


def test_prepared_workspace_volume_streams_sealed_archive_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))

    with _sealed_archive(tmp_path) as archive:
        archive.file.seek(0)
        expected = archive.file.read()
        expected_digest = hashlib.sha256(expected).hexdigest()

        with prepared_workspace_volume(
            ContainerSandbox(image=IMAGE),
            archive,
            runtime_binary=str(runtime),
        ) as volume_name:
            assert volume_name.startswith("e2h-replay-workspace-")

    records = _records(log)
    assert [record["args"][0] for record in records] == [
        "version",
        "image",
        "volume",
        "create",
        "cp",
        "rm",
        "volume",
    ]

    image_inspect = records[1]["args"]
    assert image_inspect[:2] == ["image", "inspect"]
    assert image_inspect[-1] == IMAGE

    volume_create = records[2]["args"]
    assert volume_create[:2] == ["volume", "create"]
    assert "e2h.remote-replay=workspace" in volume_create
    volume_name = str(volume_create[-1])

    create = records[3]["args"]
    assert create[0] == "create"
    assert create[create.index("--pull") + 1] == "never"
    assert create[create.index("--network") + 1] == "none"
    assert "--read-only" in create
    mount = create[create.index("--mount") + 1]
    assert mount == f"type=volume,src={volume_name},dst=/workspace,volume-nocopy"
    assert "type=bind" not in mount
    container_name = create[create.index("--name") + 1]

    copy = records[4]
    copy_args = copy["args"]
    assert copy_args == [
        "cp",
        "--archive",
        "--quiet",
        "-",
        f"{container_name}:/workspace",
    ]
    assert copy["stdin_bytes"] == len(expected)
    assert copy["stdin_sha256"] == expected_digest

    assert records[5]["args"] == ["rm", "-f", "-v", container_name]
    assert records[6]["args"] == ["volume", "rm", "-f", volume_name]


def test_prepared_workspace_volume_rejects_image_declared_volumes_before_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_IMAGE_VOLUMES", "declared")

    with _sealed_archive(tmp_path) as archive:
        with pytest.raises(DockerRemoteError, match="must not declare VOLUME"):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise AssertionError("image-declared volume should fail before create")

    commands = [record["args"] for record in _records(log)]
    assert [args[0] for args in commands] == ["version", "image"]


def test_prepared_workspace_volume_cleans_up_after_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_CP_FAIL", "1")

    with _sealed_archive(tmp_path) as archive:
        with pytest.raises(DockerRemoteError, match="exit 10: copy failed"):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise AssertionError("failed copy should not yield a volume")

    commands = [record["args"] for record in _records(log)]
    assert [args[0] for args in commands] == [
        "version",
        "image",
        "volume",
        "create",
        "cp",
        "rm",
        "volume",
    ]
    assert commands[-2][0:4] == ["rm", "-f", "-v", commands[-2][-1]]


def test_prepared_workspace_volume_preserves_body_failure_over_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_VOLUME_RM_FAIL", "1")

    with _sealed_archive(tmp_path) as archive:
        with pytest.raises(ValueError, match="body failed"):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise ValueError("body failed")

    assert _records(log)[-1]["args"][:2] == ["volume", "rm"]


def test_prepared_workspace_volume_reports_cleanup_failure_without_primary_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_VOLUME_RM_FAIL", "1")

    with _sealed_archive(tmp_path) as archive:
        with pytest.raises(DockerRemoteError, match="workspace cleanup failed"):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                pass


def test_prepared_workspace_volume_rejects_unsealed_archive_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "memfd_create") or not hasattr(os, "MFD_ALLOW_SEALING"):
        pytest.skip("unsealed memfd regression requires Linux memfd sealing")
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))

    descriptor = os.memfd_create("e2h-unsealed-test", flags=os.MFD_ALLOW_SEALING)
    with os.fdopen(descriptor, "w+b", buffering=0) as handle:
        handle.write(b"not sealed")
        forged = WorkspaceArchive(
            file=handle,
            directories=frozenset({"."}),
            source_bytes=10,
            entries=1,
            archive_bytes=10,
        )
        with pytest.raises(DockerRemoteError, match="not sealed against mutation"):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                forged,
                runtime_binary=str(runtime),
            ):
                raise AssertionError("unsealed archive should not reach Docker")

    assert not log.exists()


@pytest.mark.parametrize(
    ("sandbox", "message"),
    [
        (ContainerSandbox(image=IMAGE, workspace_access="read_write"), "workspace_access"),
        (ContainerSandbox(image=IMAGE, read_only_root=False), "read_only_root"),
        (ContainerSandbox(image=IMAGE, pull_policy="missing"), "pull_policy"),
    ],
)
def test_prepared_workspace_volume_rejects_unsafe_remote_policy_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sandbox: ContainerSandbox,
    message: str,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))

    with _sealed_archive(tmp_path) as archive:
        with pytest.raises(DockerRemoteError, match=message):
            with prepared_workspace_volume(
                sandbox,
                archive,
                runtime_binary=str(runtime),
            ):
                raise AssertionError("unsafe policy should not reach Docker")

    assert not log.exists()
