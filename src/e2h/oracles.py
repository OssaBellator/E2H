"""Deterministic declarative oracle templates compiled into command checks."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from e2h.document import _validate_json_compatible
from e2h.models import CommandCheck

MAX_ORACLE_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_ORACLE_SPEC_BYTES = 64 * 1024
ORACLE_MUTATION_ENV = "E2H_ORACLE_MUTATION"
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MUTATION_DIGEST = "0" * 64
_OPEN_SUPPORTS_DIR_FD = os.open in os.supports_dir_fd
_STAT_SUPPORTS_DIR_FD = os.stat in os.supports_dir_fd
_ORACLE_DIR_FD_SUPPORTED = _OPEN_SUPPORTS_DIR_FD and _STAT_SUPPORTS_DIR_FD


class OracleError(ValueError):
    """Raised when an oracle cannot be evaluated safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _safe_relative_path(value: str) -> str:
    if "\x00" in value:
        raise ValueError("path must not contain NUL")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("path must be relative")
    if ".." in path.parts:
        raise ValueError("path must not contain parent traversal")
    return value


def _validate_json_object_keys(value: Any, *, active: set[int] | None = None) -> None:
    if active is None:
        active = set()
    if isinstance(value, dict):
        identity = id(value)
        if identity in active:
            raise ValueError("JSON value contains a recursive container")
        active.add(identity)
        try:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("JSON objects must use string keys")
                _validate_json_object_keys(item, active=active)
        finally:
            active.remove(identity)
        return
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise ValueError("JSON value contains a recursive container")
        active.add(identity)
        try:
            for item in value:
                _validate_json_object_keys(item, active=active)
        finally:
            active.remove(identity)


class FileOracle(StrictModel):
    """Check file presence, text content, or a content digest."""

    kind: Literal["file"] = "file"
    id: str = Field(pattern=_ID_PATTERN)
    path: str
    mode: Literal["exists", "absent", "text_equals", "text_contains", "sha256"]
    expected: str | None = Field(default=None, max_length=50_000)
    cwd: str = "."
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("path", "cwd")
    @classmethod
    def paths_must_be_safe(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def expected_must_match_mode(self) -> FileOracle:
        needs_expected = self.mode in {"text_equals", "text_contains", "sha256"}
        if needs_expected and self.expected is None:
            raise ValueError(f"{self.mode} requires expected")
        if not needs_expected and self.expected is not None:
            raise ValueError(f"{self.mode} does not accept expected")
        if self.mode == "sha256" and self.expected is not None:
            valid_digest = len(self.expected) == 64 and all(
                char in "0123456789abcdef" for char in self.expected
            )
            if not valid_digest:
                raise ValueError("sha256 expected must be a lowercase SHA-256 digest")
        return self


class JsonOracle(StrictModel):
    """Check an RFC 6901 JSON pointer against a document."""

    kind: Literal["json"] = "json"
    id: str = Field(pattern=_ID_PATTERN)
    path: str
    pointer: str = ""
    mode: Literal["equals", "exists", "absent"] = "equals"
    expected: Any = None
    cwd: str = "."
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("path", "cwd")
    @classmethod
    def paths_must_be_safe(cls, value: str) -> str:
        return _safe_relative_path(value)

    @field_validator("pointer")
    @classmethod
    def pointer_must_be_valid(cls, value: str) -> str:
        if value and not value.startswith("/"):
            raise ValueError("JSON pointer must be empty or start with '/'")
        _decode_pointer(value)
        return value

    @model_validator(mode="after")
    def expected_must_be_json(self) -> JsonOracle:
        try:
            _validate_json_compatible(self.expected)
            _validate_json_object_keys(self.expected)
            rendered = json.dumps(self.expected, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected must be JSON-serializable") from exc
        if len(rendered.encode("utf-8")) > MAX_ORACLE_SPEC_BYTES:
            raise ValueError("expected JSON value is too large")
        if self.mode != "equals" and self.expected is not None:
            raise ValueError(f"{self.mode} does not accept expected")
        return self


class ArtifactOracle(StrictModel):
    """Check artifact existence, byte size bounds, and an optional digest."""

    kind: Literal["artifact"] = "artifact"
    id: str = Field(pattern=_ID_PATTERN)
    path: str
    sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    min_bytes: int | None = Field(default=None, ge=0)
    max_bytes: int | None = Field(default=None, ge=0)
    cwd: str = "."
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("path", "cwd")
    @classmethod
    def paths_must_be_safe(cls, value: str) -> str:
        return _safe_relative_path(value)

    @model_validator(mode="after")
    def constraints_must_be_consistent(self) -> ArtifactOracle:
        if self.sha256 is None and self.min_bytes is None and self.max_bytes is None:
            raise ValueError("artifact oracle requires sha256 or a byte-size bound")
        if (
            self.min_bytes is not None
            and self.max_bytes is not None
            and self.min_bytes > self.max_bytes
        ):
            raise ValueError("min_bytes must not exceed max_bytes")
        return self


OracleTemplate = Annotated[FileOracle | JsonOracle | ArtifactOracle, Field(discriminator="kind")]
ORACLE_ADAPTER: TypeAdapter[OracleTemplate] = TypeAdapter(OracleTemplate)


class OracleEvaluation(StrictModel):
    """Machine-readable result emitted by the oracle command."""

    id: str
    kind: Literal["file", "json", "artifact"]
    path: str
    passed: bool
    expected: Any = None
    observed: Any = None
    error: str | None = None


def oracle_mutation_id(template: OracleTemplate) -> str:
    return f"oracle-{template.id}"


def oracle_mutation_operator(template: OracleTemplate) -> str:
    if isinstance(template, FileOracle):
        return "invert_presence" if template.mode in {"exists", "absent"} else "digest_mismatch"
    if isinstance(template, JsonOracle):
        return "invert_presence" if template.mode in {"exists", "absent"} else "value_mismatch"
    return "digest_mismatch"


def _revalidate_oracle_template(template: OracleTemplate) -> OracleTemplate:
    if type(template) not in {FileOracle, JsonOracle, ArtifactOracle}:
        raise ValueError(f"invalid oracle template type: {type(template).__name__}")
    try:
        payload = template.model_dump(mode="python", warnings="none")
        _validate_json_compatible(payload)
        _validate_json_object_keys(payload)
        return ORACLE_ADAPTER.validate_python(payload)
    except ValueError as exc:
        raise ValueError(f"invalid oracle template: {exc}") from exc


def compile_oracle(template: OracleTemplate) -> CommandCheck:
    template = _revalidate_oracle_template(template)
    payload = json.dumps(
        template.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if len(payload.encode("utf-8")) > MAX_ORACLE_SPEC_BYTES:
        raise ValueError("oracle template is too large to compile into a command check")
    return CommandCheck(
        id=template.id,
        argv=["python", "-m", "e2h.oracle_cli", payload],
        description=template.description or f"Evaluate {template.kind} oracle for {template.path}",
        cwd=template.cwd,
        timeout_seconds=30,
    )


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
    )


def _resolve_oracle_root(root: Path) -> tuple[Path, os.stat_result]:
    if "\x00" in os.fspath(root):
        raise OracleError("oracle root must not contain NUL")
    try:
        resolved = root.resolve(strict=True)
        expected = resolved.stat(follow_symlinks=False)
    except (OSError, RuntimeError, ValueError) as exc:
        raise OracleError(f"unable to inspect oracle root: {exc}") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        raise OracleError("oracle root must resolve to a real directory")
    return resolved, expected


def _oracle_root_must_be_stable(root: Path, expected: os.stat_result) -> None:
    try:
        current = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise OracleError(f"unable to restat oracle root: {exc}") from exc
    if not stat.S_ISDIR(current.st_mode) or _stat_identity(current) != _stat_identity(expected):
        raise OracleError("oracle root changed while evaluating")


def _resolve_from_root(root: Path, root_info: os.stat_result, relative: str) -> Path:
    try:
        candidate = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise OracleError(f"unable to resolve oracle path {relative!r}: {exc}") from exc
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise OracleError(f"path escapes oracle root: {relative}") from exc
    _oracle_root_must_be_stable(root, root_info)
    return candidate


def _resolve(root: Path, relative: str) -> Path:
    resolved_root, root_info = _resolve_oracle_root(root)
    return _resolve_from_root(resolved_root, root_info, relative)


@contextmanager
def _open_regular_file(
    root: Path,
    root_info: os.stat_result,
    path: Path,
) -> Iterator[tuple[int, os.stat_result] | None]:
    parent_descriptor: int | None = None
    descriptor: int | None = None
    parent = path.parent
    try:
        try:
            parent.relative_to(root)
        except ValueError as exc:
            raise OracleError(f"path escapes oracle root: {path}") from exc
        try:
            parent_expected = parent.stat(follow_symlinks=False)
        except FileNotFoundError:
            yield None
            return
        except OSError as exc:
            raise OracleError(f"unable to inspect oracle file parent {parent}: {exc}") from exc
        if stat.S_ISLNK(parent_expected.st_mode) or not stat.S_ISDIR(parent_expected.st_mode):
            yield None
            return
        _oracle_root_must_be_stable(root, root_info)
        if _ORACLE_DIR_FD_SUPPORTED:
            parent_flags = (
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            )
            parent_descriptor = os.open(parent, parent_flags)
            parent_opened = os.fstat(parent_descriptor)
            if _stat_identity(parent_opened) != _stat_identity(parent_expected):
                raise OracleError(f"oracle file parent changed while opening: {parent}")
            try:
                expected = os.stat(
                    path.name,
                    dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                yield None
                return
            if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
                yield None
                return
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        else:
            try:
                expected = path.stat(follow_symlinks=False)
            except FileNotFoundError:
                yield None
                return
            if stat.S_ISLNK(expected.st_mode) or not stat.S_ISREG(expected.st_mode):
                yield None
                return
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            parent_opened = parent_expected
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _stat_identity(opened) != _stat_identity(expected):
            raise OracleError(f"oracle file changed while opening: {path}")
        _oracle_root_must_be_stable(root, root_info)
        yield descriptor, opened
        after = os.fstat(descriptor)
        current = (
            os.stat(
                path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
            if parent_descriptor is not None
            else path.stat(follow_symlinks=False)
        )
        parent_after = (
            os.fstat(parent_descriptor)
            if parent_descriptor is not None
            else parent.stat(follow_symlinks=False)
        )
        parent_current = parent.stat(follow_symlinks=False)
        if _stat_identity(after) != _stat_identity(opened) or _stat_identity(
            current
        ) != _stat_identity(opened):
            raise OracleError(f"oracle file changed while reading: {path}")
        if _stat_identity(parent_after) != _stat_identity(parent_opened) or _stat_identity(
            parent_current
        ) != _stat_identity(parent_opened):
            raise OracleError(f"oracle file parent changed while reading: {parent}")
        _oracle_root_must_be_stable(root, root_info)
    except OSError as exc:
        raise OracleError(f"unable to access oracle file {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            with suppress(OSError):
                os.close(descriptor)
        if parent_descriptor is not None:
            with suppress(OSError):
                os.close(parent_descriptor)


def _regular_file_info(
    root: Path,
    root_info: os.stat_result,
    path: Path,
) -> os.stat_result | None:
    with _open_regular_file(root, root_info, path) as opened:
        if opened is None:
            return None
        _, info = opened
        return info


def _read_bound_bytes(
    root: Path,
    root_info: os.stat_result,
    path: Path,
) -> bytes | None:
    with _open_regular_file(root, root_info, path) as opened:
        if opened is None:
            return None
        descriptor, info = opened
        if info.st_size > MAX_ORACLE_DOCUMENT_BYTES:
            raise OracleError(f"document exceeds {MAX_ORACLE_DOCUMENT_BYTES} bytes")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            data = handle.read(MAX_ORACLE_DOCUMENT_BYTES + 1)
        if len(data) > MAX_ORACLE_DOCUMENT_BYTES:
            raise OracleError(f"document exceeds {MAX_ORACLE_DOCUMENT_BYTES} bytes")
        return data


def _hash_bound_file(
    root: Path,
    root_info: os.stat_result,
    path: Path,
) -> tuple[str, int] | None:
    with _open_regular_file(root, root_info, path) as opened:
        if opened is None:
            return None
        descriptor, info = opened
        digest = hashlib.sha256()
        observed = 0
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while chunk := handle.read(1024 * 1024):
                observed += len(chunk)
                digest.update(chunk)
        if observed != info.st_size:
            raise OracleError(f"oracle file changed while hashing: {path}")
        return digest.hexdigest(), observed


def _read_bytes(path: Path) -> bytes:
    root, root_info = _resolve_oracle_root(path.parent)
    resolved = _resolve_from_root(root, root_info, path.name)
    data = _read_bound_bytes(root, root_info, resolved)
    if data is None:
        raise OracleError(f"unable to read {path}: file does not exist")
    return data


def _sha256(path: Path) -> str:
    root, root_info = _resolve_oracle_root(path.parent)
    resolved = _resolve_from_root(root, root_info, path.name)
    hashed = _hash_bound_file(root, root_info, resolved)
    if hashed is None:
        raise OracleError(f"unable to hash {path}: file does not exist")
    return hashed[0]


def _decode_pointer(pointer: str) -> list[str]:
    if not pointer:
        return []
    segments: list[str] = []
    for raw in pointer[1:].split("/"):
        index = 0
        decoded = ""
        while index < len(raw):
            char = raw[index]
            if char != "~":
                decoded += char
                index += 1
                continue
            if index + 1 >= len(raw) or raw[index + 1] not in {"0", "1"}:
                raise ValueError("JSON pointer contains an invalid escape")
            decoded += "~" if raw[index + 1] == "0" else "/"
            index += 2
        segments.append(decoded)
    return segments


def _lookup_pointer(document: Any, pointer: str) -> tuple[bool, Any]:
    value = document
    for segment in _decode_pointer(pointer):
        if isinstance(value, dict):
            if segment not in value:
                return False, None
            value = value[segment]
            continue
        if isinstance(value, list):
            if not segment.isdigit() or (len(segment) > 1 and segment.startswith("0")):
                return False, None
            index = int(segment)
            if index >= len(value):
                return False, None
            value = value[index]
            continue
        return False, None
    return True, value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate object key: {key!r}")
        result[key] = value
    return result


def _load_bound_json(
    root: Path,
    root_info: os.stat_result,
    path: Path,
) -> Any | None:
    data = _read_bound_bytes(root, root_info, path)
    if data is None:
        return None
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleError("JSON document must be UTF-8") from exc
    try:
        return json.loads(
            text,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise OracleError(f"invalid JSON document: {exc}") from exc


def _load_json(path: Path) -> Any:
    root, root_info = _resolve_oracle_root(path.parent)
    resolved = _resolve_from_root(root, root_info, path.name)
    document = _load_bound_json(root, root_info, resolved)
    if document is None:
        raise OracleError(f"unable to read {path}: file does not exist")
    return document


def _mutate(template: OracleTemplate, operator: str | None) -> OracleTemplate:
    if operator is None:
        return template
    expected_operator = oracle_mutation_operator(template)
    if operator != expected_operator:
        raise OracleError(
            f"unsupported mutation operator {operator!r}; expected {expected_operator!r}"
        )
    if isinstance(template, FileOracle):
        if operator == "invert_presence":
            mode = "absent" if template.mode == "exists" else "exists"
            return template.model_copy(update={"mode": mode, "expected": None})
        digest = _MUTATION_DIGEST
        if template.mode == "sha256" and template.expected == digest:
            digest = "1" * 64
        return template.model_copy(update={"mode": "sha256", "expected": digest})
    if isinstance(template, JsonOracle):
        if operator == "invert_presence":
            mode = "absent" if template.mode == "exists" else "exists"
            return template.model_copy(update={"mode": mode, "expected": None})
        return template.model_copy(update={"expected": {"__e2h_mutation__": template.expected}})
    digest = _MUTATION_DIGEST if template.sha256 != _MUTATION_DIGEST else "1" * 64
    return template.model_copy(update={"sha256": digest})


def evaluate_oracle(
    template: OracleTemplate,
    *,
    root: Path = Path("."),
    mutation_operator: str | None = None,
) -> OracleEvaluation:
    expected: Any = None
    observed: Any = None
    try:
        template = _revalidate_oracle_template(template)
        template = _mutate(template, mutation_operator)
        resolved_root, root_info = _resolve_oracle_root(root)
        path = _resolve_from_root(resolved_root, root_info, template.path)
        if isinstance(template, FileOracle):
            expected = template.expected if template.expected is not None else template.mode
            if template.mode in {"exists", "absent"}:
                info = _regular_file_info(resolved_root, root_info, path)
                observed = info is not None
                passed = observed if template.mode == "exists" else not observed
            elif template.mode in {"text_equals", "text_contains"}:
                raw = _read_bound_bytes(resolved_root, root_info, path)
                if raw is None:
                    return OracleEvaluation(
                        id=template.id,
                        kind=template.kind,
                        path=template.path,
                        passed=False,
                        expected=expected,
                        observed=None,
                        error="file does not exist",
                    )
                try:
                    observed = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise OracleError("text oracle requires UTF-8 content") from exc
                assert template.expected is not None
                passed = (
                    observed == template.expected
                    if template.mode == "text_equals"
                    else template.expected in observed
                )
            else:
                hashed = _hash_bound_file(resolved_root, root_info, path)
                if hashed is None:
                    return OracleEvaluation(
                        id=template.id,
                        kind=template.kind,
                        path=template.path,
                        passed=False,
                        expected=expected,
                        observed=None,
                        error="file does not exist",
                    )
                observed, _ = hashed
                passed = observed == template.expected
        elif isinstance(template, JsonOracle):
            document = _load_bound_json(resolved_root, root_info, path)
            if document is None:
                return OracleEvaluation(
                    id=template.id,
                    kind=template.kind,
                    path=template.path,
                    passed=False,
                    expected=template.expected,
                    observed=None,
                    error="JSON file does not exist",
                )
            found, value = _lookup_pointer(document, template.pointer)
            observed = {"found": found, "value": value if found else None}
            expected = template.expected if template.mode == "equals" else template.mode
            if template.mode == "exists":
                passed = found
            elif template.mode == "absent":
                passed = not found
            else:
                passed = found and value == template.expected
        else:
            expected = {
                "sha256": template.sha256,
                "min_bytes": template.min_bytes,
                "max_bytes": template.max_bytes,
            }
            digest: str | None
            size: int
            if template.sha256 is not None:
                hashed = _hash_bound_file(resolved_root, root_info, path)
                if hashed is None:
                    return OracleEvaluation(
                        id=template.id,
                        kind=template.kind,
                        path=template.path,
                        passed=False,
                        expected=expected,
                        observed=None,
                        error="artifact does not exist",
                    )
                digest, size = hashed
            else:
                info = _regular_file_info(resolved_root, root_info, path)
                if info is None:
                    return OracleEvaluation(
                        id=template.id,
                        kind=template.kind,
                        path=template.path,
                        passed=False,
                        expected=expected,
                        observed=None,
                        error="artifact does not exist",
                    )
                digest = None
                size = info.st_size
            observed = {"sha256": digest, "bytes": size}
            passed = True
            if template.sha256 is not None:
                passed = passed and digest == template.sha256
            if template.min_bytes is not None:
                passed = passed and size >= template.min_bytes
            if template.max_bytes is not None:
                passed = passed and size <= template.max_bytes
        return OracleEvaluation(
            id=template.id,
            kind=template.kind,
            path=template.path,
            passed=passed,
            expected=expected,
            observed=observed,
        )
    except (OSError, OracleError) as exc:
        return OracleEvaluation(
            id=template.id,
            kind=template.kind,
            path=template.path,
            passed=False,
            expected=expected,
            observed=observed,
            error=str(exc),
        )
