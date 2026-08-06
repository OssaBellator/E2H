"""Typed optimizer-facing harness variant models and identities."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from e2h.document import load_mapping_document
from e2h.genome import capsule_sha256
from e2h.models import TaskCapsule

_ID_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$"
_VARIABLE_PATTERN = r"^[a-zA-Z_][a-zA-Z0-9_]{0,127}$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PLACEHOLDER_PATTERN = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]{0,127})\}")
_MAX_DOCUMENT_BYTES = 2_097_152
_MAX_METADATA_BYTES = 65_536
_MAX_SCHEMA_BYTES = 65_536
_RESERVED_VARIANT_ENV = frozenset({"E2H_VARIANT_ID", "E2H_REPETITION", "E2H_VARIANT_SHA256"})


class VariantError(ValueError):
    """Raised when a typed harness variant cannot be loaded or verified."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        rendered = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value must contain canonical JSON data") from exc
    return rendered.encode("utf-8")


def _validate_metadata(value: dict[str, Any], *, noun: str) -> dict[str, Any]:
    if len(_canonical_json_bytes(value)) > _MAX_METADATA_BYTES:
        raise ValueError(f"{noun} metadata exceeds {_MAX_METADATA_BYTES} bytes")
    return value


def _validate_text(value: str, *, noun: str) -> str:
    if "\x00" in value:
        raise ValueError(f"{noun} must not contain NUL")
    return value


class PromptMessage(StrictModel):
    """One ordered provider-neutral prompt message template."""

    id: str = Field(pattern=_ID_PATTERN)
    role: Literal["system", "developer", "user", "assistant"]
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator("content")
    @classmethod
    def content_must_be_process_safe(cls, value: str) -> str:
        return _validate_text(value, noun="prompt content")


PromptVariable = Annotated[str, Field(pattern=_VARIABLE_PATTERN)]


class PromptVariant(StrictModel):
    """Ordered prompt templates with explicit interpolation variables."""

    id: str = Field(pattern=_ID_PATTERN)
    messages: list[PromptMessage] = Field(min_length=1, max_length=64)
    variables: list[PromptVariable] = Field(default_factory=list, max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def prompt_must_be_unambiguous(self) -> PromptVariant:
        message_ids = [message.id for message in self.messages]
        if len(message_ids) != len(set(message_ids)):
            raise ValueError("prompt message ids must be unique")
        if len(self.variables) != len(set(self.variables)):
            raise ValueError("prompt variables must be unique")
        referenced = {
            variable
            for message in self.messages
            for variable in _PLACEHOLDER_PATTERN.findall(message.content)
        }
        declared = set(self.variables)
        undeclared = sorted(referenced - declared)
        unused = sorted(declared - referenced)
        if undeclared:
            raise ValueError(f"prompt references undeclared variables: {', '.join(undeclared)}")
        if unused:
            raise ValueError(f"prompt declares unused variables: {', '.join(unused)}")
        _validate_metadata(self.metadata, noun="prompt variant")
        return self


class ToolDefinition(StrictModel):
    """One declarative function-style tool contract."""

    id: str = Field(pattern=_ID_PATTERN)
    description: str = Field(min_length=1, max_length=10_000)
    input_schema: dict[str, Any]

    @field_validator("description")
    @classmethod
    def description_must_be_process_safe(cls, value: str) -> str:
        return _validate_text(value, noun="tool description")

    @model_validator(mode="after")
    def schema_must_be_bounded_object_json_schema(self) -> ToolDefinition:
        if self.input_schema.get("type") != "object":
            raise ValueError("tool input_schema must declare type 'object'")
        if len(_canonical_json_bytes(self.input_schema)) > _MAX_SCHEMA_BYTES:
            raise ValueError(f"tool input_schema exceeds {_MAX_SCHEMA_BYTES} bytes")
        return self


class ToolVariant(StrictModel):
    """Tool catalogue and deterministic selection policy."""

    id: str = Field(pattern=_ID_PATTERN)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=64)
    selection: Literal["auto", "none", "required", "named"] = "auto"
    selected_tool: str | None = Field(default=None, pattern=_ID_PATTERN)
    parallel_calls: bool = False
    max_calls: int = Field(default=16, ge=1, le=10_000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def selection_must_reference_catalogue(self) -> ToolVariant:
        tool_ids = [tool.id for tool in self.tools]
        if len(tool_ids) != len(set(tool_ids)):
            raise ValueError("tool ids must be unique")
        if self.selection != "none" and not self.tools:
            raise ValueError("tool selection requires at least one tool")
        if self.selection == "named":
            if self.selected_tool is None:
                raise ValueError("named tool selection requires selected_tool")
            if self.selected_tool not in set(tool_ids):
                raise ValueError("selected_tool must reference a declared tool")
        elif self.selected_tool is not None:
            raise ValueError("selected_tool is only valid for named selection")
        _validate_metadata(self.metadata, noun="tool variant")
        return self


class ContextItemBase(StrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    placement: Literal["before_prompt", "after_prompt", "tool_context"] = "before_prompt"
    priority: int = Field(default=50, ge=0, le=100)
    max_chars: int = Field(ge=1, le=5_000_000)


class LiteralContextItem(ContextItemBase):
    kind: Literal["literal"] = "literal"
    content: str = Field(min_length=1, max_length=1_000_000)

    @field_validator("content")
    @classmethod
    def content_must_be_process_safe(cls, value: str) -> str:
        return _validate_text(value, noun="literal context")

    @model_validator(mode="after")
    def cap_must_not_exceed_content(self) -> LiteralContextItem:
        if self.max_chars > len(self.content):
            raise ValueError("literal context max_chars must not exceed content length")
        return self


class ReferencedContextItem(ContextItemBase):
    kind: Literal["artifact", "snapshot", "trace"]
    sha256: str = Field(pattern=_SHA256_PATTERN)
    locator: str | None = Field(default=None, max_length=2_048)

    @field_validator("locator")
    @classmethod
    def locator_must_be_process_safe(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value:
            raise ValueError("context locator must not be empty")
        return _validate_text(value, noun="context locator")


ContextItem = Annotated[
    LiteralContextItem | ReferencedContextItem,
    Field(discriminator="kind"),
]


class ContextVariant(StrictModel):
    """Bounded context sources with explicit ordering and overflow policy."""

    id: str = Field(pattern=_ID_PATTERN)
    items: list[ContextItem] = Field(min_length=1, max_length=128)
    max_chars: int = Field(default=100_000, ge=1, le=5_000_000)
    overflow: Literal["reject", "truncate_low_priority"] = "reject"
    ordering: Literal["declared", "priority"] = "declared"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def context_must_be_bounded_and_unique(self) -> ContextVariant:
        item_ids = [item.id for item in self.items]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("context item ids must be unique")
        total_item_chars = sum(item.max_chars for item in self.items)
        if self.overflow == "reject" and total_item_chars > self.max_chars:
            raise ValueError("context item caps exceed max_chars under reject overflow policy")
        _validate_metadata(self.metadata, noun="context variant")
        return self


Capability = Literal["text", "tools", "vision", "json"]


def _default_capabilities() -> list[Capability]:
    return ["text"]


class RouteTarget(StrictModel):
    """One provider/model target available to a routing policy."""

    id: str = Field(pattern=_ID_PATTERN)
    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=256)
    capabilities: list[Capability] = Field(default_factory=_default_capabilities, min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider", "model")
    @classmethod
    def target_strings_must_be_process_safe(cls, value: str) -> str:
        return _validate_text(value, noun="routing target")

    @field_validator("capabilities")
    @classmethod
    def capabilities_must_be_unique(cls, value: list[Capability]) -> list[Capability]:
        if len(value) != len(set(value)):
            raise ValueError("routing target capabilities must be unique")
        return value

    @model_validator(mode="after")
    def metadata_must_be_bounded(self) -> RouteTarget:
        _validate_metadata(self.metadata, noun="route target")
        return self


class RoutingRule(StrictModel):
    """Metadata match rule selecting one declared target."""

    id: str = Field(pattern=_ID_PATTERN)
    match: dict[str, str] = Field(min_length=1, max_length=32)
    target_id: str = Field(pattern=_ID_PATTERN)
    priority: int = Field(default=0, ge=-10_000, le=10_000)

    @field_validator("match")
    @classmethod
    def match_must_be_safe(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if re.fullmatch(_VARIABLE_PATTERN, key) is None:
                raise ValueError("routing match keys must be identifiers")
            if not item:
                raise ValueError("routing match values must be non-empty")
            _validate_text(item, noun="routing match value")
        return value


class RoutingVariant(StrictModel):
    """Deterministic rule ordering over a finite target catalogue."""

    id: str = Field(pattern=_ID_PATTERN)
    targets: list[RouteTarget] = Field(min_length=1, max_length=64)
    rules: list[RoutingRule] = Field(default_factory=list, max_length=256)
    fallback_target: str = Field(pattern=_ID_PATTERN)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def routing_must_be_total_and_unambiguous(self) -> RoutingVariant:
        target_ids = [target.id for target in self.targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError("routing target ids must be unique")
        target_set = set(target_ids)
        if self.fallback_target not in target_set:
            raise ValueError("fallback_target must reference a declared target")
        rule_ids = [rule.id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("routing rule ids must be unique")
        signatures: set[tuple[int, tuple[tuple[str, str], ...]]] = set()
        for rule in self.rules:
            if rule.target_id not in target_set:
                raise ValueError(f"routing rule {rule.id} references an unknown target")
            signature = (rule.priority, tuple(sorted(rule.match.items())))
            if signature in signatures:
                raise ValueError(
                    "routing rules must not have duplicate priority and match criteria"
                )
            signatures.add(signature)
        _validate_metadata(self.metadata, noun="routing variant")
        return self


VariantDimension = Literal["prompt", "tools", "context", "routing"]


class WorkflowStage(StrictModel):
    """One node in a provider-neutral workflow DAG."""

    id: str = Field(pattern=_ID_PATTERN)
    kind: Literal["model", "tool", "router", "validator", "transform"]
    handler: str = Field(pattern=_ID_PATTERN)
    depends_on: list[str] = Field(default_factory=list, max_length=64)
    uses: list[VariantDimension] = Field(default_factory=list, max_length=4)
    timeout_seconds: float = Field(default=60.0, gt=0, le=3600)
    max_attempts: int = Field(default=1, ge=1, le=100)
    on_failure: Literal["stop", "continue"] = "stop"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def stage_must_be_unambiguous(self) -> WorkflowStage:
        if self.id in self.depends_on:
            raise ValueError("workflow stage must not depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("workflow stage dependencies must be unique")
        if len(self.uses) != len(set(self.uses)):
            raise ValueError("workflow stage uses entries must be unique")
        _validate_metadata(self.metadata, noun="workflow stage")
        return self


class WorkflowVariant(StrictModel):
    """Finite acyclic workflow with bounded retries and parallelism."""

    id: str = Field(pattern=_ID_PATTERN)
    stages: list[WorkflowStage] = Field(min_length=1, max_length=256)
    max_parallelism: int = Field(default=1, ge=1, le=256)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def workflow_must_be_a_dag(self) -> WorkflowVariant:
        stage_ids = [stage.id for stage in self.stages]
        if len(stage_ids) != len(set(stage_ids)):
            raise ValueError("workflow stage ids must be unique")
        stage_set = set(stage_ids)
        dependencies = {stage.id: stage.depends_on for stage in self.stages}
        for stage in self.stages:
            unknown = sorted(set(stage.depends_on) - stage_set)
            if unknown:
                joined = ", ".join(unknown)
                raise ValueError(
                    f"workflow stage {stage.id} references unknown dependencies: {joined}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(stage_id: str) -> None:
            if stage_id in visited:
                return
            if stage_id in visiting:
                raise ValueError("workflow dependencies must be acyclic")
            visiting.add(stage_id)
            for dependency in dependencies[stage_id]:
                visit(dependency)
            visiting.remove(stage_id)
            visited.add(stage_id)

        for stage_id in stage_ids:
            visit(stage_id)
        _validate_metadata(self.metadata, noun="workflow variant")
        return self


class HarnessVariant(StrictModel):
    """One typed harness configuration usable in replay matrices."""

    id: str = Field(pattern=_ID_PATTERN)
    env: dict[str, str] = Field(default_factory=dict)
    prompt: PromptVariant | None = None
    tools: ToolVariant | None = None
    context: ContextVariant | None = None
    routing: RoutingVariant | None = None
    workflow: WorkflowVariant | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("env")
    @classmethod
    def environment_must_be_process_safe(cls, value: dict[str, str]) -> dict[str, str]:
        for key, item in value.items():
            if not key or "=" in key or "\x00" in key:
                raise ValueError(
                    "environment keys must be non-empty and contain neither '=' nor NUL"
                )
            if key.upper() in _RESERVED_VARIANT_ENV:
                raise ValueError("environment keys must not override reserved E2H slot identifiers")
            if "\x00" in item:
                raise ValueError("environment values must not contain NUL")
        return value

    @model_validator(mode="after")
    def metadata_must_be_bounded(self) -> HarnessVariant:
        _validate_metadata(self.metadata, noun="harness variant")
        return self

    @property
    def dimensions(self) -> list[str]:
        """Return enabled typed dimensions in stable schema order."""
        return [
            name
            for name in ("prompt", "tools", "context", "routing", "workflow")
            if getattr(self, name) is not None
        ]


class HarnessVariantDocument(StrictModel):
    """Content-addressed variant bound to one exact task capsule."""

    schema_version: Literal["0.1"] = "0.1"
    base_capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    variant: HarnessVariant
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def metadata_must_be_bounded(self) -> HarnessVariantDocument:
        _validate_metadata(self.metadata, noun="variant document")
        return self


class VariantVerification(StrictModel):
    """Digest proof that one typed variant is bound to the supplied capsule."""

    schema_version: Literal["0.1"] = "0.1"
    document_sha256: str = Field(pattern=_SHA256_PATTERN)
    variant_sha256: str = Field(pattern=_SHA256_PATTERN)
    base_capsule_sha256: str = Field(pattern=_SHA256_PATTERN)
    variant_id: str = Field(pattern=_ID_PATTERN)
    dimensions: list[str]


def variant_sha256(variant: HarnessVariant) -> str:
    """Return the canonical identity of one typed harness configuration."""
    return hashlib.sha256(_canonical_json_bytes(variant.model_dump(mode="json"))).hexdigest()


def variant_document_sha256(document: HarnessVariantDocument) -> str:
    """Return the canonical identity of one bound variant document."""
    return hashlib.sha256(_canonical_json_bytes(document.model_dump(mode="json"))).hexdigest()


def verify_variant_document(
    document: HarnessVariantDocument,
    capsule: TaskCapsule,
) -> VariantVerification:
    """Verify exact capsule binding without executing the variant."""
    try:
        base_digest = capsule_sha256(capsule)
    except ValueError as exc:
        raise VariantError(f"base capsule cannot be canonically identified: {exc}") from exc
    if document.base_capsule_sha256 != base_digest:
        raise VariantError("variant base capsule digest does not match the supplied capsule")
    return VariantVerification(
        document_sha256=variant_document_sha256(document),
        variant_sha256=variant_sha256(document.variant),
        base_capsule_sha256=base_digest,
        variant_id=document.variant.id,
        dimensions=document.variant.dimensions,
    )


def load_variant_document(path: Path) -> HarnessVariantDocument:
    """Load one strict JSON/YAML typed variant document."""
    try:
        payload = load_mapping_document(
            path,
            noun="variant document",
            max_bytes=_MAX_DOCUMENT_BYTES,
        )
        return HarnessVariantDocument.model_validate(payload)
    except ValueError as exc:
        if isinstance(exc, VariantError):
            raise
        raise VariantError(f"invalid variant document: {exc}") from exc
