from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from e2h.docker_remote import DockerRemoteError, prepared_workspace_volume
from e2h.models import ContainerSandbox
from e2h.workspace_archive import sealed_workspace_archive_supported, stable_workspace_archive

IMAGE = "python@sha256:" + "0" * 64

pytestmark = pytest.mark.skipif(
    not sealed_workspace_archive_supported(),
    reason="Docker cleanup priority tests require Linux memfd sealing",
)


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
    image_format = args[args.index("--format") + 1]
    if image_format == "{{{{json .Descriptor}}}}":
        print(
            json.dumps(
                {{
                    "mediaType": "application/vnd.oci.image.manifest.v1+json",
                    "digest": "sha256:" + "0" * 64,
                    "size": 123,
                }}
            )
        )
    else:
        print("none")
elif args[:2] == ["volume", "create"]:
    print(args[-1])
elif args and args[0] == "create":
    print("a" * 64)
elif args and args[0] == "cp":
    pass
elif args and args[0] == "rm":
    pass
elif args[:2] == ["volume", "rm"]:
    if os.environ.get("DOCKER_TEST_VOLUME_RM_FAIL"):
        print("volume cleanup failed", file=sys.stderr)
        raise SystemExit(19)
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


def test_cleanup_failure_is_visible_and_chains_body_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_VOLUME_RM_FAIL", "1")

    with _archive(tmp_path) as archive:
        with pytest.raises(DockerRemoteError, match="workspace cleanup failed") as caught:
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise ValueError("body failed")

    assert "volume cleanup failed" in str(caught.value)
    assert isinstance(caught.value.__cause__, ValueError)
    assert str(caught.value.__cause__) == "body failed"


def test_successful_cleanup_preserves_body_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))

    with _archive(tmp_path) as archive:
        with pytest.raises(ValueError, match="body failed"):
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise ValueError("body failed")


def test_cleanup_failure_does_not_mask_system_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, log = _fake_docker(tmp_path)
    monkeypatch.setenv("DOCKER_TEST_LOG", str(log))
    monkeypatch.setenv("DOCKER_TEST_VOLUME_RM_FAIL", "1")

    with _archive(tmp_path) as archive:
        with pytest.raises(SystemExit) as caught:
            with prepared_workspace_volume(
                ContainerSandbox(image=IMAGE),
                archive,
                runtime_binary=str(runtime),
            ):
                raise SystemExit(3)

    assert caught.value.code == 3
    assert caught.value.__notes__ is not None
    assert any("workspace cleanup failed" in note for note in caught.value.__notes__)
