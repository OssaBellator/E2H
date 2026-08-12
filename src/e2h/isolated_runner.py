"""Isolated workspace helpers for future remote container replay."""

from __future__ import annotations

import signal
import tarfile
from pathlib import Path, PurePosixPath

from e2h.docker_remote import (
    DockerRemoteError,
    _require_volume_free_image,
    _validated_remote_sandbox,
    prepared_workspace_volume,
    require_patched_docker_archive,
)
from e2h.models import TaskCapsule
from e2h.replay_budget import validate_replay_host_limits
from e2h.runner import CheckStatus, RunnerError, RunResult, _validated_capsule
from e2h.volume_runner import _member_path, _workspace_tree, run_capsule_prepared_volume
from e2h.workspace_archive import (
    _MAX_ARCHIVE_MEMBER_PATH_BYTES,
    WorkspaceArchive,
    WorkspaceArchiveError,
    sealed_workspace_archive_supported,
    stable_workspace_archive,
)

# Source bytes can appear once as file/link payload and again as PAX string metadata.
# Four times the capture path cap leaves room per entry for long PAX names, tar/PAX
# headers, and block padding; the fixed allowance covers the root member and trailer.
_REMOTE_ARCHIVE_ENTRY_OVERHEAD_BYTES = 4 * _MAX_ARCHIVE_MEMBER_PATH_BYTES
_REMOTE_ARCHIVE_FIXED_OVERHEAD_BYTES = 1024 * 1024
_REMOTE_SIGNAL_EXIT_CODES = frozenset(
    128 + int(value) for value in signal.valid_signals() if int(value) > 0
)
_REMOTE_ALLOWED_PAX_HEADERS = frozenset({"path", "linkpath", "mtime", "uid", "gid", "size"})
# Python's PAX writer adds hdrcharset=BINARY when source names/targets contain
# undecodable bytes. Permit that transport marker through the raw scan only so the
# later UTF-8 validation can reject the reconstructed path or link target precisely.
_REMOTE_PARSEABLE_PAX_HEADERS = frozenset({*_REMOTE_ALLOWED_PAX_HEADERS, "hdrcharset"})
_REMOTE_ALLOWED_TAR_HEADER_TYPES = frozenset(
    {
        tarfile.REGTYPE,
        tarfile.AREGTYPE,
        tarfile.DIRTYPE,
        tarfile.SYMTYPE,
        tarfile.XHDTYPE,
    }
)
_MAX_PAX_KEY_BYTES = 128
_MAX_PAX_LENGTH_DIGITS = 20


def isolated_workspace_snapshot_supported() -> bool:
    """Return whether the host can capture the sealed remote workspace archive."""
    return sealed_workspace_archive_supported()


def isolated_container_replay_supported() -> bool:
    """Return false until the runtime can consume workspace state by stable identity."""
    return False


def _validate_remote_expected_exit_codes(capsule: TaskCapsule) -> None:
    """Reject expected statuses that Docker cannot distinguish from signal death."""
    for check in capsule.success.commands:
        ambiguous = sorted(check.expected_exit_codes & _REMOTE_SIGNAL_EXIT_CODES)
        if ambiguous:
            raise RunnerError(
                "remote container replay cannot safely distinguish signal-encoded "
                f"expected exit codes for check {check.id!r}: {ambiguous}"
            )


def _validate_remote_result_exit_codes(result: RunResult) -> None:
    """Reject completed task verdicts with signal-ambiguous Docker exit statuses."""
    for check in result.checks:
        if (
            check.status in {CheckStatus.PASSED, CheckStatus.FAILED}
            and check.exit_code in _REMOTE_SIGNAL_EXIT_CODES
        ):
            raise RunnerError(
                "remote container replay observed a signal-ambiguous Docker exit status "
                f"for check {check.id!r}: {check.exit_code}"
            )


def _max_remote_archive_bytes(max_source_bytes: int, max_entries: int) -> int:
    """Return a conservative transfer cap for the uncompressed PAX workspace tar."""
    if max_source_bytes < 1 or max_entries < 1:
        raise RunnerError("remote workspace archive limits must be positive")
    return (
        2 * max_source_bytes
        + max_entries * _REMOTE_ARCHIVE_ENTRY_OVERHEAD_BYTES
        + _REMOTE_ARCHIVE_FIXED_OVERHEAD_BYTES
    )


def _validate_remote_archive_resources(
    archive: WorkspaceArchive,
    *,
    max_source_bytes: int,
    max_entries: int,
) -> None:
    """Reject unexpected tar amplification before any bytes reach the Docker daemon."""
    if archive.source_bytes > max_source_bytes or archive.entries > max_entries:
        raise RunnerError("sealed workspace archive metadata exceeds configured capture limits")
    max_archive_bytes = _max_remote_archive_bytes(max_source_bytes, max_entries)
    if archive.archive_bytes > max_archive_bytes:
        raise RunnerError(
            "sealed workspace archive exceeds derived transfer bound "
            f"({max_archive_bytes} bytes)"
        )


def _require_utf8_archive_text(value: str, *, noun: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise RunnerError(f"sealed workspace archive {noun} is not valid UTF-8") from exc


def _validate_pax_payload(archive: WorkspaceArchive, payload_size: int) -> int | None:
    """Validate PAX record keys and return an optional following-member size override."""
    if payload_size < 1:
        raise RunnerError("sealed workspace archive contains an empty PAX extension")
    remaining = payload_size
    seen: set[str] = set()
    size_override: int | None = None
    while remaining:
        record_available = remaining
        length_digits = bytearray()
        while True:
            token = archive.file.read(1)
            if len(token) != 1:
                raise RunnerError("sealed workspace archive contains a truncated PAX length")
            remaining -= 1
            if token == b" ":
                break
            if (
                token < b"0"
                or token > b"9"
                or len(length_digits) >= _MAX_PAX_LENGTH_DIGITS
            ):
                raise RunnerError("sealed workspace archive contains an invalid PAX length")
            length_digits.extend(token)
        if not length_digits:
            raise RunnerError("sealed workspace archive contains an empty PAX length")
        record_size = int(length_digits)
        prefix_size = len(length_digits) + 1
        if record_size <= prefix_size or record_size > record_available:
            raise RunnerError("sealed workspace archive PAX record exceeds extension bounds")

        body_size = record_size - prefix_size
        body_consumed = 0
        key_bytes = bytearray()
        while body_consumed < body_size:
            token = archive.file.read(1)
            if len(token) != 1:
                raise RunnerError("sealed workspace archive contains a truncated PAX record")
            remaining -= 1
            body_consumed += 1
            if token == b"=":
                break
            if token == b"\n" or len(key_bytes) >= _MAX_PAX_KEY_BYTES:
                raise RunnerError("sealed workspace archive contains an invalid PAX key")
            key_bytes.extend(token)
        else:
            raise RunnerError("sealed workspace archive PAX record is missing '='")

        try:
            key = key_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise RunnerError("sealed workspace archive contains a non-ASCII PAX key") from exc
        if not key or key in seen:
            raise RunnerError("sealed workspace archive contains a duplicate or empty PAX key")
        seen.add(key)
        if key not in _REMOTE_PARSEABLE_PAX_HEADERS:
            raise RunnerError(
                f"sealed workspace archive contains unsupported PAX metadata key {key!r}"
            )

        body_remaining = body_size - body_consumed
        if body_remaining < 1:
            raise RunnerError("sealed workspace archive PAX record is missing its terminator")
        value_size = body_remaining - 1
        if key == "size":
            if value_size < 1 or value_size > _MAX_PAX_LENGTH_DIGITS:
                raise RunnerError("sealed workspace archive contains an invalid PAX size value")
            value = archive.file.read(value_size)
            if len(value) != value_size:
                raise RunnerError("sealed workspace archive contains a truncated PAX size value")
            remaining -= value_size
            if any(byte < ord("0") or byte > ord("9") for byte in value):
                raise RunnerError("sealed workspace archive contains a non-numeric PAX size value")
            size_override = int(value)
        elif value_size:
            archive.file.seek(value_size, 1)
            remaining -= value_size

        terminator = archive.file.read(1)
        if len(terminator) != 1:
            raise RunnerError("sealed workspace archive contains a truncated PAX terminator")
        remaining -= 1
        if terminator != b"\n":
            raise RunnerError("sealed workspace archive PAX record has an invalid terminator")
    return size_override


def _validate_archive_header_types(archive: WorkspaceArchive) -> None:
    """Reject physical tar extension records the PAX workspace producer cannot emit."""
    offset = 0
    pending_pax = False
    pending_size: int | None = None
    try:
        archive.file.seek(0)
        while offset + tarfile.BLOCKSIZE <= archive.archive_bytes:
            header = archive.file.read(tarfile.BLOCKSIZE)
            if len(header) != tarfile.BLOCKSIZE:
                raise RunnerError("sealed workspace archive contains a truncated tar header")
            if not any(header):
                if pending_pax:
                    raise RunnerError("sealed workspace archive ends with a dangling PAX extension")
                return
            try:
                member = tarfile.TarInfo.frombuf(header, "utf-8", "surrogateescape")
            except (tarfile.TarError, UnicodeError, ValueError) as exc:
                raise RunnerError(f"sealed workspace archive has an invalid tar header: {exc}") from exc
            if member.type == tarfile.XGLTYPE:
                raise RunnerError("sealed workspace archive contains unsupported global PAX metadata")
            if member.type not in _REMOTE_ALLOWED_TAR_HEADER_TYPES:
                raise RunnerError(
                    "sealed workspace archive contains unsupported tar header type "
                    f"{member.type!r}"
                )
            if member.size < 0:
                raise RunnerError("sealed workspace archive contains a negative tar member size")

            if member.type == tarfile.XHDTYPE:
                if pending_pax:
                    raise RunnerError(
                        "sealed workspace archive contains consecutive per-member PAX extensions"
                    )
                payload_bytes = (
                    (member.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
                ) * tarfile.BLOCKSIZE
                next_offset = offset + tarfile.BLOCKSIZE + payload_bytes
                if next_offset > archive.archive_bytes:
                    raise RunnerError("sealed workspace archive tar member exceeds archive bounds")
                pending_size = _validate_pax_payload(archive, member.size)
                pending_pax = True
                offset = next_offset
                archive.file.seek(offset)
                continue

            effective_size = member.size
            if pending_size is not None:
                if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}:
                    raise RunnerError(
                        "sealed workspace archive applies a PAX size override to a non-file member"
                    )
                effective_size = pending_size
            payload_bytes = (
                (effective_size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE
            ) * tarfile.BLOCKSIZE
            next_offset = offset + tarfile.BLOCKSIZE + payload_bytes
            if next_offset > archive.archive_bytes:
                raise RunnerError("sealed workspace archive tar member exceeds archive bounds")
            pending_pax = False
            pending_size = None
            offset = next_offset
            archive.file.seek(offset)
        raise RunnerError("sealed workspace archive is missing its tar trailer")
    except RunnerError:
        raise
    except (AttributeError, OSError, ValueError) as exc:
        raise RunnerError(f"unable to inspect sealed workspace archive headers: {exc}") from exc
    finally:
        try:
            archive.file.seek(0)
        except (AttributeError, OSError, ValueError):
            pass


def _validate_archive_member_ancestry(archive: WorkspaceArchive) -> None:
    """Reject archive shapes the descriptor-recursive producer cannot create."""
    _validate_archive_header_types(archive)
    member_names: list[str] = []
    directory_positions: dict[str, int] = {}
    symlink_names: set[str] = set()
    try:
        archive.file.seek(0)
        with tarfile.open(
            fileobj=archive.file,
            mode="r:",
            encoding="utf-8",
            errors="surrogateescape",
        ) as handle:
            if handle.pax_headers:
                raise RunnerError(
                    "sealed workspace archive contains unsupported global PAX metadata: "
                    f"{sorted(handle.pax_headers)}"
                )
            for position, member in enumerate(handle):
                name = _member_path(member.name)
                _require_utf8_archive_text(name, noun="member path")
                if member.issym():
                    _require_utf8_archive_text(member.linkname, noun="symlink target")
                unexpected_pax = sorted(set(member.pax_headers) - _REMOTE_ALLOWED_PAX_HEADERS)
                if unexpected_pax:
                    raise RunnerError(
                        "sealed workspace archive contains unsupported PAX metadata "
                        f"for member {member.name!r}: {unexpected_pax}"
                    )
                member_names.append(name)
                if member.isdir():
                    directory_positions[name] = position
                elif member.issym():
                    symlink_names.add(name)
    except RunnerError:
        raise
    except (AttributeError, OSError, tarfile.TarError, UnicodeError, ValueError) as exc:
        raise RunnerError(f"unable to inspect sealed workspace archive shape: {exc}") from exc
    finally:
        try:
            archive.file.seek(0)
        except (AttributeError, OSError, ValueError):
            pass

    if not member_names or member_names[0] != "." or directory_positions.get(".") != 0:
        raise RunnerError("sealed workspace archive root directory must be the first member")

    for position, name in enumerate(member_names):
        parts = PurePosixPath(name).parts
        for depth in range(1, len(parts)):
            ancestor = PurePosixPath(*parts[:depth]).as_posix()
            if ancestor in symlink_names:
                raise RunnerError(
                    f"sealed workspace archive member {name!r} is nested under "
                    f"symlink {ancestor!r}"
                )
            ancestor_position = directory_positions.get(ancestor)
            if ancestor_position is None:
                raise RunnerError(
                    f"sealed workspace archive member {name!r} has missing directory "
                    f"ancestor {ancestor!r}"
                )
            if ancestor_position >= position:
                raise RunnerError(
                    f"sealed workspace archive directory ancestor {ancestor!r} appears "
                    f"after child {name!r}"
                )


def _run_capsule_isolated_container_candidate(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    max_workspace_bytes: int,
    max_workspace_entries: int,
    container_runtime: str | None = None,
) -> RunResult:
    """Exercise the sealed-volume design without enabling the remote capability gate."""
    capsule = _validated_capsule(capsule)
    sandbox = capsule.sandbox
    if sandbox is None:
        raise RunnerError("isolated container replay requires capsule.sandbox")
    _validate_remote_expected_exit_codes(capsule)
    validate_replay_host_limits(capsule)
    # Validate caller-selected capture bounds before any Docker control-plane contact.
    _max_remote_archive_bytes(max_workspace_bytes, max_workspace_entries)
    runtime = container_runtime or sandbox.engine
    try:
        # Reject cheap runtime/policy failures before walking or archiving the workspace.
        # The shared Docker archive gate also enforces memory/swap capability. The
        # importer repeats archive/image boundaries again before Docker resource creation.
        sandbox = _validated_remote_sandbox(sandbox)
        require_patched_docker_archive(runtime)
        _require_volume_free_image(runtime, sandbox.image)
        with stable_workspace_archive(
            workspace,
            max_bytes=max_workspace_bytes,
            max_entries=max_workspace_entries,
        ) as archive:
            _validate_remote_archive_resources(
                archive,
                max_source_bytes=max_workspace_bytes,
                max_entries=max_workspace_entries,
            )
            # Reject physical extension records before generic tar normalization, then
            # revalidate the logical member tree from the same sealed bytes.
            _validate_archive_member_ancestry(archive)
            _workspace_tree(archive)
            with prepared_workspace_volume(
                sandbox,
                archive,
                runtime_binary=runtime,
            ) as volume_name:
                result = run_capsule_prepared_volume(
                    capsule,
                    archive,
                    volume_name,
                    container_runtime=runtime,
                )
                _validate_remote_result_exit_codes(result)
                return result
    except (WorkspaceArchiveError, DockerRemoteError) as exc:
        raise RunnerError(str(exc)) from exc


def run_capsule_isolated_container(
    capsule: TaskCapsule,
    workspace: Path,
    *,
    max_workspace_bytes: int,
    max_workspace_entries: int,
    container_runtime: str | None = None,
) -> RunResult:
    """Fail closed while real Docker runtime validation remains incomplete."""
    del capsule, workspace, max_workspace_bytes, max_workspace_entries, container_runtime
    raise RunnerError(
        "isolated container replay is unavailable until the sealed-volume path is validated "
        "against a real patched Docker runtime"
    )
