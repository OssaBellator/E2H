"""Evidence-to-Harness core package."""

from e2h.anthropic_messages import (
    AnthropicInputMessage,
    AnthropicMessageRecord,
    AnthropicMessagesDocument,
    import_anthropic_messages_document,
    ingest_anthropic_messages_file,
)
from e2h.compiler import (
    CapsuleProposal,
    CompilerSpec,
    VerificationReport,
    compile_proposal,
    materialize_capsule,
    review_proposal,
    verify_proposal,
)
from e2h.experiment import ExperimentResult, ExperimentSpec, run_experiment
from e2h.gemini_generate_content import (
    GeminiContentRecord,
    GeminiGenerateContentDocument,
    GeminiGenerateContentRecord,
    import_gemini_generate_content_document,
    ingest_gemini_generate_content_file,
)
from e2h.ingest import IngestionBundle, ingest_otlp_file, ingest_transcript_file
from e2h.models import ContainerSandbox, TaskCapsule
from e2h.openai_responses import (
    OpenAIResponseRecord,
    OpenAIResponsesDocument,
    import_openai_responses_document,
    ingest_openai_responses_file,
)
from e2h.oracles import ArtifactOracle, FileOracle, JsonOracle, OracleEvaluation
from e2h.privacy import (
    CustomRedactionRule,
    RedactionKind,
    RedactionPolicy,
    RedactionPolicyError,
    RedactionRecord,
    RedactionReview,
    ResidualFinding,
    apply_redaction_policy,
    default_redaction_policy,
    load_redaction_policy,
    redaction_policy_sha256,
)
from e2h.runner import ExecutionBackend, RunResult, run_capsule
from e2h.snapshot import (
    SnapshotLimits,
    SnapshotManifest,
    SnapshotReference,
    create_snapshot,
    restore_snapshot,
    snapshot_reference,
    verify_snapshot,
)
from e2h.store import (
    export_parquet,
    ingest_artifact,
    initialize_store,
    query_store,
    store_info,
)
from e2h.store_models import ArtifactKind, IngestResult, QueryView, StoreInfo
from e2h.trace import Trace, TraceEvent, trace_from_run_result

__all__ = [
    "AnthropicInputMessage",
    "AnthropicMessageRecord",
    "AnthropicMessagesDocument",
    "ArtifactKind",
    "ArtifactOracle",
    "CapsuleProposal",
    "CompilerSpec",
    "ContainerSandbox",
    "CustomRedactionRule",
    "ExecutionBackend",
    "ExperimentResult",
    "ExperimentSpec",
    "FileOracle",
    "GeminiContentRecord",
    "GeminiGenerateContentDocument",
    "GeminiGenerateContentRecord",
    "IngestResult",
    "IngestionBundle",
    "JsonOracle",
    "OpenAIResponseRecord",
    "OpenAIResponsesDocument",
    "OracleEvaluation",
    "QueryView",
    "RedactionKind",
    "RedactionPolicy",
    "RedactionPolicyError",
    "RedactionRecord",
    "RedactionReview",
    "ResidualFinding",
    "RunResult",
    "SnapshotLimits",
    "SnapshotManifest",
    "SnapshotReference",
    "StoreInfo",
    "TaskCapsule",
    "Trace",
    "TraceEvent",
    "VerificationReport",
    "apply_redaction_policy",
    "compile_proposal",
    "create_snapshot",
    "default_redaction_policy",
    "export_parquet",
    "import_anthropic_messages_document",
    "import_gemini_generate_content_document",
    "import_openai_responses_document",
    "ingest_anthropic_messages_file",
    "ingest_artifact",
    "ingest_gemini_generate_content_file",
    "ingest_openai_responses_file",
    "ingest_otlp_file",
    "ingest_transcript_file",
    "initialize_store",
    "load_redaction_policy",
    "materialize_capsule",
    "query_store",
    "redaction_policy_sha256",
    "restore_snapshot",
    "review_proposal",
    "run_capsule",
    "run_experiment",
    "snapshot_reference",
    "store_info",
    "trace_from_run_result",
    "verify_proposal",
    "verify_snapshot",
]
__version__ = "0.12.0"
