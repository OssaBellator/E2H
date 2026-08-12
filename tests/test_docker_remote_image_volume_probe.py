from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from e2h.docker_remote import DockerRemoteError, prepared_workspace_volume
from e2h.models import ContainerSandbox
from e2h.workspace_archive import stable_workspace_archive

IMAGE_DIGEST = "sha256:" + "0" * 64
IMAGE = "python@" + IMAGE_DIGEST
_IMAGE_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_IMAGE_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"


def _descriptor(
    *,
    media_type: str = _IMAGE_MANIFEST_MEDIA_TYPE,
    digest: str = IMAGE_DIGEST,
) -> str:
    return json.dumps({"mediaType": media_type, "digest": digest, "size": 123})


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "docker-test"
    log = tmp_path / "docker-log.jsonl"
    runtime.write_text(
        f"""#!{sys.executable}
import json
import os
from pathlib import Path
import sys
args = sys.argv[1:]
with Path(os.environ["DOCKER_TEST_LOG"]).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(args) + "\\n")
if args and args[0] == "version":
    if args[-1] == "{{{{json .Server.Components}}}}":
        print(json.dumps([{{"Name": "runc", "Version": "1.3.6"}}]))
    else:
        print("29.7.2 29.7.2")
elif args and args[0] == "info":
    print("linux true true true true true")
elif args[:2] == ["image", "inspect"]:
    if os.environ.get("DOCKER_TEST_IMAGE_INSPECT_FAIL"):
        print("image inspect failed", file=sys.stderr)
        raise SystemExit(14)
    image_format = args[args.index("--format") + 1]
    if image_format == "{{{{json .Descriptor}}}}":
        default_descriptor = json.dumps(
            {{
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": "sha256:" + "0" * 64,
                "size": 123,
            }}
        )
        print(os.environ.get("DOCKER_TEST_IMAGE_DESCRIPTOR", default_descriptor))
    else:
        print(os.environ.get("DOCKER_TEST_IMAGE_VOLUMES", "none"))
else:
    raise SystemExit(13)
""",
        encoding="utf-8",
    )
    runtime.chmod(0o755)
    return runtime, log


def _archive(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "marker.txt").write_text("trusted", encoding="utf-8")
    return stable_workspace_archive(
        workspace.resolve(),
        max_bytes=1024,
        max_entries=10,
    )


def _commands(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def test_image_descriptor_probe_runtime_failure_stops_before_resource_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_IMAGE_INSPECT_FAIL", "1")

    with _archive(tmp_path) as archive:
        with pytest.raises(DockerRemoteError, match="exit 14: image inspect failed"):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise AssertionError("failed image inspection must not yield")

    commands = _commands(log)
    assert [args[0] for args in commands] == ["version", "version", "info", "image"]


@pytest.mark.parametrize(
    ("descriptor", "message"),
    [
        ("null", "descriptor proof"),
        (_descriptor(media_type=_IMAGE_INDEX_MEDIA_TYPE), "single-platform image manifest"),
        (_descriptor(digest="sha256:" + "1" * 64), "digest does not match"),
        (_descriptor(media_type="application/example"), "unsupported image descriptor"),
    ],
)
def test_image_descriptor_probe_rejects_ambiguous_image_identity_before_resource_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    descriptor: str,
    message: str,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_IMAGE_DESCRIPTOR", descriptor)

    with _archive(tmp_path) as archive:
        with pytest.raises(DockerRemoteError, match=message):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise AssertionError("ambiguous image identity must not yield")

    commands = _commands(log)
    assert [args[0] for args in commands] == ["version", "version", "info", "image"]


def test_image_volume_probe_unexpected_response_stops_before_resource_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_IMAGE_VOLUMES", "unexpected")

    with _archive(tmp_path) as archive:
        with pytest.raises(DockerRemoteError, match="unexpected response"):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise AssertionError("unexpected image probe must not yield")

    commands = _commands(log)
    assert [args[0] for args in commands] == [
        "version",
        "version",
        "info",
        "image",
        "image",
    ]
