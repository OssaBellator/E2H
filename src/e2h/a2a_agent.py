"""A2A 1.0 verification agent backed by E2H's bounded verification service."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit, urlunsplit

import uvicorn
from a2a.helpers import new_data_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill, Message
from google.protobuf.json_format import MessageToJson
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError
from starlette.applications import Starlette

from e2h.mcp_server import E2HMCPService, MCPServerConfig, MCPServiceError
from e2h.runner import ExecutionBackend
from e2h.store_models import MAX_QUERY_ROWS, QueryView

_A2A_PROTOCOL_VERSION = "1.0"
_DEFAULT_REQUEST_BYTES = 65_536
_DEFAULT_RESPONSE_BYTES = 1_048_576
_DEFAULT_MEMORY_ROWS = 100
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
logger = logging.getLogger(__name__)


class A2AAgentError(ValueError):
    """Raised when an A2A verification request is malformed or unsafe."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StatusCommand(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    operation: Literal["status"]


class MemoryQueryCommand(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    operation: Literal["memory_query"]
    view: QueryView
    limit: int = Field(default=100, ge=1, le=MAX_QUERY_ROWS)


class VerifyArtifactCommand(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    operation: Literal["verify_artifact"]
    artifact: str = Field(min_length=1, max_length=4096)
    expected_sha256: str | None = Field(default=None, pattern=_SHA256_PATTERN)
    min_bytes: int | None = Field(default=None, ge=0)
    max_bytes: int | None = Field(default=None, ge=0)


class VerifySnapshotCommand(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    operation: Literal["verify_snapshot"]
    archive: str = Field(min_length=1, max_length=4096)


class ReplayCommand(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    operation: Literal["replay"]
    capsule: str = Field(min_length=1, max_length=4096)
    workspace: str = Field(default=".", min_length=1, max_length=4096)


VerificationCommand = Annotated[
    StatusCommand
    | MemoryQueryCommand
    | VerifyArtifactCommand
    | VerifySnapshotCommand
    | ReplayCommand,
    Field(discriminator="operation"),
]
_COMMAND_ADAPTER = TypeAdapter(VerificationCommand)


class A2AVerificationResponse(StrictModel):
    """Structured application-level result returned in one A2A data message."""

    schema_version: Literal["0.1"] = "0.1"
    operation: str = Field(min_length=1, max_length=64)
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=4096)
    response_sha256: str = Field(pattern=_SHA256_PATTERN)


@dataclass(frozen=True)
class A2AServerConfig:
    """A2A transport policy plus the shared E2H verification trust boundary."""

    verification: MCPServerConfig
    public_url: str
    max_request_bytes: int = _DEFAULT_REQUEST_BYTES
    max_response_bytes: int = _DEFAULT_RESPONSE_BYTES


def _package_version() -> str:
    try:
        return importlib.metadata.version("e2h")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0+unknown"


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
        raise A2AAgentError("value must contain canonical JSON data") from exc
    return rendered.encode("utf-8")


def _normalized_public_url(value: str) -> str:
    if not value or "\x00" in value:
        raise A2AAgentError("public URL must be a non-empty HTTP(S) origin")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise A2AAgentError("public URL must use http or https and include a host")
    if parsed.query or parsed.fragment:
        raise A2AAgentError("public URL must not include a query or fragment")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _public_error(message: str, *, root: str) -> str:
    sanitized = message.replace(root, "<root>")
    if len(sanitized) > 4096:
        return sanitized[:4093] + "..."
    return sanitized


def _response(
    operation: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> A2AVerificationResponse:
    material = {
        "schema_version": "0.1",
        "operation": operation,
        "ok": error is None,
        "result": result,
        "error": error,
    }
    digest = hashlib.sha256(_canonical_json_bytes(material)).hexdigest()
    return A2AVerificationResponse(**material, response_sha256=digest)


def parse_verification_message(message: Message, *, max_bytes: int) -> VerificationCommand:
    """Parse exactly one JSON text/data part into a strict verification command."""
    if max_bytes < 1:
        raise A2AAgentError("max request bytes must be positive")
    if len(message.parts) != 1:
        raise A2AAgentError("verification messages require exactly one content part")
    part = message.parts[0]
    kind = part.WhichOneof("content")
    try:
        if kind == "text":
            raw = part.text.encode("utf-8")
            if len(raw) > max_bytes:
                raise A2AAgentError(f"verification request exceeds {max_bytes} bytes")
            payload = json.loads(part.text)
        elif kind == "data":
            rendered = MessageToJson(
                part.data,
                preserving_proto_field_name=True,
                ensure_ascii=False,
            )
            raw = rendered.encode("utf-8")
            if len(raw) > max_bytes:
                raise A2AAgentError(f"verification request exceeds {max_bytes} bytes")
            payload = json.loads(rendered)
        else:
            raise A2AAgentError("verification messages accept only JSON text or data parts")
    except json.JSONDecodeError as exc:
        raise A2AAgentError("verification text part must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise A2AAgentError("verification request must be a JSON object")
    try:
        return _COMMAND_ADAPTER.validate_python(payload)
    except ValidationError as exc:
        raise A2AAgentError(f"verification request validation failed: {exc}") from exc


def execute_verification_command(
    service: E2HMCPService,
    command: VerificationCommand,
) -> A2AVerificationResponse:
    """Execute one strict verification command and return an application-level envelope."""
    try:
        if isinstance(command, StatusCommand):
            result = service.status().model_dump(mode="json")
        elif isinstance(command, MemoryQueryCommand):
            result = service.memory_query(command.view, limit=command.limit).model_dump(mode="json")
        elif isinstance(command, VerifyArtifactCommand):
            result = service.verify_artifact(
                command.artifact,
                expected_sha256=command.expected_sha256,
                min_bytes=command.min_bytes,
                max_bytes=command.max_bytes,
            ).model_dump(mode="json")
        elif isinstance(command, VerifySnapshotCommand):
            result = service.verify_snapshot(command.archive).model_dump(mode="json")
        elif isinstance(command, ReplayCommand):
            result = service.replay(command.capsule, workspace=command.workspace).model_dump(mode="json")
        else:
            raise AssertionError("unreachable verification command")
        return _response(command.operation, result=result)
    except MCPServiceError as exc:
        return _response(
            command.operation,
            error=_public_error(str(exc), root=str(service.config.root)),
        )


class E2HVerificationAgentExecutor(AgentExecutor):
    """Message-only A2A executor for deterministic E2H verification operations."""

    def __init__(
        self,
        service: E2HMCPService,
        *,
        max_request_bytes: int = _DEFAULT_REQUEST_BYTES,
        max_response_bytes: int = _DEFAULT_RESPONSE_BYTES,
    ) -> None:
        if max_request_bytes < 1:
            raise A2AAgentError("max request bytes must be positive")
        if max_response_bytes < 1:
            raise A2AAgentError("max response bytes must be positive")
        self.service = service
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Return exactly one structured Message, as required by A2A message-only mode."""
        operation = "invalid_request"
        try:
            if context.message is None:
                raise A2AAgentError("verification request does not contain a message")
            command = parse_verification_message(
                context.message,
                max_bytes=self.max_request_bytes,
            )
            operation = command.operation
            response = await asyncio.to_thread(execute_verification_command, self.service, command)
        except A2AAgentError as exc:
            response = _response(operation, error=str(exc))
        except Exception:
            logger.exception("unexpected A2A verification failure")
            response = _response(operation, error="internal verification error")

        payload = response.model_dump(mode="json")
        if len(_canonical_json_bytes(payload)) > self.max_response_bytes:
            response = _response(
                operation,
                error=(
                    "verification result exceeds the configured A2A response limit; "
                    "reduce the requested result size"
                ),
            )
            payload = response.model_dump(mode="json")
        context_id = context.context_id or context.message.context_id or None
        await event_queue.enqueue_event(
            new_data_message(
                payload,
                media_type="application/json",
                context_id=context_id,
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        """No-op because verification uses message-only responses and creates no tasks."""
        del context, event_queue


def build_agent_card(
    service: E2HMCPService,
    *,
    public_url: str,
) -> AgentCard:
    """Build a protocol-1.0 Agent Card whose skills reflect the operator capability policy."""
    base_url = _normalized_public_url(public_url)
    skills = [
        AgentSkill(
            id="e2h_status",
            name="E2H verification status",
            description="Report the configured E2H verification capability boundary.",
            tags=["verification", "e2h", "status"],
            examples=['{"schema_version":"0.1","operation":"status"}'],
            input_modes=["application/json"],
            output_modes=["application/json"],
        ),
        AgentSkill(
            id="e2h_verify_artifact",
            name="Verify E2H artifact",
            description="Hash a root-bounded artifact and evaluate SHA-256 or size expectations.",
            tags=["verification", "artifact", "sha256"],
            examples=[
                '{"schema_version":"0.1","operation":"verify_artifact","artifact":"result.json"}'
            ],
            input_modes=["application/json"],
            output_modes=["application/json"],
        ),
        AgentSkill(
            id="e2h_verify_snapshot",
            name="Verify E2H snapshot",
            description="Verify an E2H snapshot archive, manifest, members, and blob digests.",
            tags=["verification", "snapshot", "provenance"],
            examples=[
                '{"schema_version":"0.1","operation":"verify_snapshot","archive":"run.e2hsnap"}'
            ],
            input_modes=["application/json"],
            output_modes=["application/json"],
        ),
    ]
    if service.config.store is not None:
        skills.append(
            AgentSkill(
                id="e2h_memory_query",
                name="Query verified E2H memory",
                description="Query a bounded predefined experiment-store view with a result digest.",
                tags=["verification", "memory", "evidence"],
                examples=[
                    '{"schema_version":"0.1","operation":"memory_query","view":"sources","limit":10}'
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        )
    if service.config.allow_replay:
        skills.append(
            AgentSkill(
                id="e2h_replay",
                name="Replay E2H capsule",
                description=(
                    "Run a root-bounded E2H capsule under the operator-selected backend and "
                    "return replay evidence."
                ),
                tags=["verification", "replay", "capsule"],
                examples=[
                    '{"schema_version":"0.1","operation":"replay","capsule":"capsule.yaml"}'
                ],
                input_modes=["application/json"],
                output_modes=["application/json"],
            )
        )
    return AgentCard(
        name="E2H Verification Agent",
        description=(
            "Deterministic Agent2Agent interface for bounded E2H evidence memory, artifact, "
            "snapshot, and operator-enabled replay verification."
        ),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                protocol_version=_A2A_PROTOCOL_VERSION,
                url=f"{base_url}/a2a/jsonrpc",
            )
        ],
        version=_package_version(),
        capabilities=AgentCapabilities(streaming=False, push_notifications=False),
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        skills=skills,
    )


def create_a2a_app(config: A2AServerConfig) -> Starlette:
    """Create a Starlette application exposing A2A 1.0 JSON-RPC and Agent Card routes."""
    service = E2HMCPService(config.verification)
    card = build_agent_card(service, public_url=config.public_url)
    executor = E2HVerificationAgentExecutor(
        service,
        max_request_bytes=config.max_request_bytes,
        max_response_bytes=config.max_response_bytes,
    )
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    routes = [
        *create_agent_card_routes(card),
        *create_jsonrpc_routes(handler, rpc_url="/a2a/jsonrpc"),
    ]
    return Starlette(routes=routes)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e2h-a2a",
        description="Serve deterministic E2H verification over A2A 1.0 JSON-RPC.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=41242)
    parser.add_argument(
        "--public-url",
        default=None,
        help="Externally advertised HTTP(S) base URL. Defaults to http://HOST:PORT.",
    )
    parser.add_argument("--root", default=".", help="Allowed filesystem root.")
    parser.add_argument(
        "--store",
        default=None,
        help="DuckDB experiment store inside --root for verified memory queries.",
    )
    parser.add_argument(
        "--allow-replay",
        action="store_true",
        help="Advertise and enable command-executing replay. Disabled by default.",
    )
    parser.add_argument(
        "--backend",
        choices=[item.value for item in ExecutionBackend],
        default=ExecutionBackend.AUTO.value,
        help="Operator-selected replay backend when --allow-replay is enabled.",
    )
    parser.add_argument(
        "--container-runtime",
        default=None,
        help="Container runtime binary override for replay. Never controlled by A2A messages.",
    )
    parser.add_argument(
        "--expose-replay-output",
        action="store_true",
        help="Include bounded stdout/stderr in replay results instead of digest-only output.",
    )
    parser.add_argument(
        "--max-artifact-bytes",
        type=int,
        default=100 * 1024 * 1024,
        help="Maximum bytes hashed by one artifact verification request.",
    )
    parser.add_argument(
        "--max-memory-rows",
        type=int,
        default=_DEFAULT_MEMORY_ROWS,
        help=f"Maximum rows returned by one memory query (hard limit {MAX_QUERY_ROWS}).",
    )
    parser.add_argument(
        "--max-request-bytes",
        type=int,
        default=_DEFAULT_REQUEST_BYTES,
        help="Maximum serialized JSON bytes accepted in one A2A verification message.",
    )
    parser.add_argument(
        "--max-response-bytes",
        type=int,
        default=_DEFAULT_RESPONSE_BYTES,
        help="Maximum serialized JSON bytes returned in one A2A verification message.",
    )
    return parser


def main() -> None:
    """Run the E2H verification agent over A2A 1.0 JSON-RPC."""
    from pathlib import Path

    args = _parser().parse_args()
    public_url = args.public_url or f"http://{args.host}:{args.port}"
    try:
        config = A2AServerConfig(
            verification=MCPServerConfig(
                root=Path(args.root),
                store=Path(args.store) if args.store is not None else None,
                allow_replay=args.allow_replay,
                replay_backend=ExecutionBackend(args.backend),
                container_runtime=args.container_runtime,
                expose_replay_output=args.expose_replay_output,
                max_artifact_bytes=args.max_artifact_bytes,
                max_memory_rows=args.max_memory_rows,
            ),
            public_url=_normalized_public_url(public_url),
            max_request_bytes=args.max_request_bytes,
            max_response_bytes=args.max_response_bytes,
        )
        app = create_a2a_app(config)
    except (A2AAgentError, MCPServiceError) as exc:
        raise SystemExit(f"Unable to start E2H A2A agent: {exc}") from exc
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
