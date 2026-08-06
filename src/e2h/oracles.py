"""Deterministic declarative oracle templates compiled into command checks."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from e2h.models import CommandCheck

MAX_ORACLE_DOCUMENT_BYTES = 20 * 1024 * 1024
MAX_ORACLE_SPEC_BYTES = 64 * 1024
ORACLE_MUTATION_ENV = "E2H_ORACLE_MUTATION"
_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MUTATION_DIGEST = "0" * 64


class OracleError(ValueError):
    """Raised when an oracle cannot be evaluated safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _safe_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError("path must be relative")
    if ".." in path.parts:
        raise ValueError("path must not contain parent traversal")
    return value


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


def compile_oracle(template: OracleTemplate) -> CommandCheck:
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


def _resolve(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise OracleError(f"path escapes oracle root: {relative}") from exc
    return candidate


def _read_bytes(path: Path) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise OracleError(f"unable to read {path}: {exc}") from exc
    if len(data) > MAX_ORACLE_DOCUMENT_BYTES:
        raise OracleError(f"document exceeds {MAX_ORACLE_DOCUMENT_BYTES} bytes")
    return data


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise OracleError(f"unable to hash {path}: {exc}") from exc
    return digest.hexdigest()


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


def _load_json(path: Path) -> Any:
    data = _read_bytes(path)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OracleError("JSON document must be UTF-8") from exc

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    try:
        return json.loads(text, parse_constant=reject_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise OracleError(f"invalid JSON document: {exc}") from exc


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
        template = _mutate(template, mutation_operator)
        path = _resolve(root, template.path)
        if isinstance(template, FileOracle):
            expected = template.expected if template.expected is not None else template.mode
            if template.mode == "exists":
                observed = path.is_file()
                passed = observed is True
            elif template.mode == "absent":
                observed = path.exists()
                passed = observed is False
            elif not path.is_file():
                return OracleEvaluation(
                    id=template.id,
                    kind=template.kind,
                    path=template.path,
                    passed=False,
                    expected=expected,
                    observed=None,
                    error="file does not exist",
                )
            elif template.mode in {"text_equals", "text_contains"}:
                try:
                    observed = _read_bytes(path).decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise OracleError("text oracle requires UTF-8 content") from exc
                assert template.expected is not None
                passed = (
                    observed == template.expected
                    if template.mode == "text_equals"
                    else template.expected in observed
                )
            else:
                observed = _sha256(path)
                passed = observed == template.expected
        elif isinstance(template, JsonOracle):
            if not path.is_file():
                return OracleEvaluation(
                    id=template.id,
                    kind=template.kind,
                    path=template.path,
                    passed=False,
                    expected=template.expected,
                    observed=None,
                    error="JSON file does not exist",
                )
            found, value = _lookup_pointer(_load_json(path), template.pointer)
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
            if not path.is_file():
                return OracleEvaluation(
                    id=template.id,
                    kind=template.kind,
                    path=template.path,
                    passed=False,
                    expected=expected,
                    observed=None,
                    error="artifact does not exist",
                )
            size = path.stat().st_size
            digest = _sha256(path) if template.sha256 is not None else None
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
