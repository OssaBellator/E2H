# Gemini GenerateContent runtime adapter

E2H can execute one verified typed harness variant through Google's Gemini `generateContent` REST API while preserving the exact request body, observable response, route identity, and local tool-policy result as audit evidence.

The adapter is intentionally a **single stateless model turn**, not an autonomous function-calling loop. It never executes returned function calls.

Google currently recommends the newer Interactions API for greenfield Gemini applications. E2H deliberately targets `generateContent` here because E2H already has a strict `GeminiGenerateContentDocument` archive and ingestion contract. This keeps live execution and imported Gemini evidence on one observable format rather than introducing a parallel provider schema.

## Requirements

The runtime consumes:

1. a task capsule;
2. a `HarnessVariantDocument` bound to that capsule's exact digest;
3. a Gemini runtime invocation document containing exact prompt variables, route metadata, an output-token cap, timeout, and optional audit metadata.

The selected routing target must use provider `google` or `gemini`. Route models may be written as either `gemini-...` or `models/gemini-...`; E2H canonicalizes the REST endpoint to `v1beta/models/<model>:generateContent`.

Authentication is read only from an environment variable. By default:

```bash
export GEMINI_API_KEY='...'
```

The key is placed in the `x-goog-api-key` request header and is never serialized into request audits or archives.

## Invocation document

Example: [`examples/runtime/gemini-generate-content-invocation.yaml`](../examples/runtime/gemini-generate-content-invocation.yaml)

```yaml
schema_version: "0.1"
id: gemini-runtime-001
variables:
  task: verify the requested contract
route_metadata:
  tier: fast
max_output_tokens: 1024
timeout_seconds: 120
metadata:
  purpose: controlled live evaluation
```

Prompt variables are exact: missing declared variables and undeclared supplied variables both fail before a provider request is made.

## CLI

```bash
uv run e2h runtime gemini-generate-content \
  path/to/capsule.yaml \
  path/to/variant.yaml \
  examples/runtime/gemini-generate-content-invocation.yaml \
  --archive .e2h/gemini-generate-content-archive.json \
  --result .e2h/gemini-runtime-result.json \
  --bundle .e2h/gemini-bundle.json \
  --traces .e2h/gemini-traces.jsonl \
  --redaction-report .e2h/gemini-redaction-review.json
```

`--api-key-env` can select a different environment-variable name without putting the secret on the command line.

The raw archive can contain sensitive model/application data. Treat it as sensitive evidence and apply the normal E2H redaction/review workflow before moving it across trust boundaries.

## Provider-neutral mapping

### System and developer messages

Gemini `generateContent` accepts a top-level, text-only `systemInstruction`. E2H maps leading provider-neutral `system` and `developer` prompt messages to ordered text parts in that object.

Once a `user` or `assistant` prompt message has started the conversation, a later `system` or `developer` message is rejected instead of being silently reordered.

### Conversation contents

E2H maps:

- `user` → Gemini `user`;
- `assistant` → Gemini `model`.

Each provider-neutral prompt message remains a distinct Gemini `Content` object with one text part, preserving observable turn boundaries.

### Context

Only literal `before_prompt` context is mapped automatically. It becomes ordered text parts at the beginning of `systemInstruction`.

The adapter rejects:

- referenced artifact, snapshot, or trace context;
- `tool_context`;
- `after_prompt`.

Those placements require external resolution or ordering semantics that the runtime cannot faithfully infer. Context ordering, per-item limits, total limits, rejection, and low-priority truncation follow the typed `ContextVariant` before provider mapping.

## Tools

E2H custom tools are emitted as Gemini function declarations:

```json
{
  "tools": [
    {
      "functionDeclarations": [
        {
          "name": "lookup",
          "description": "Look up one value.",
          "parameters": {"type": "object"}
        }
      ]
    }
  ]
}
```

Tool selection maps deterministically:

| E2H selection | Gemini `functionCallingConfig` |
| --- | --- |
| `auto` | `{"mode":"AUTO"}` |
| `none` | `{"mode":"NONE"}` |
| `required` | `{"mode":"ANY"}` |
| `named` | `{"mode":"ANY","allowedFunctionNames":["..."]}` |

Gemini `generateContent` supports multiple function calls in one turn but does not expose an equivalent of Anthropic's request-side `disable_parallel_tool_use`. Therefore `parallel_calls: false` is enforced after the response: multiple returned function calls become a local policy violation.

E2H never executes returned `functionCall` parts or sends automatic `functionResponse` turns. It validates observable calls against declared names, `max_calls`, named/required/none selection, object arguments, and the local parallel-call contract. Unexpected server-side `toolCall` or code-execution parts are also flagged because this adapter does not request built-in/server tools.

## Observable archive

The runtime wraps the request context and provider response in the existing `GeminiGenerateContentDocument` format, so the standard Gemini ingestion adapter can normalize the result into E2H traces.

The archive records:

- stable synthetic IDs for request contents and response candidates;
- system instruction content;
- raw observable `GenerateContentResponse` data, including `responseId`;
- optional HTTP request ID when one is exposed by the service;
- selected model and route;
- request/variant/capsule provenance digests;
- local tool-policy violations.

E2H does not request visible thinking. Existing Gemini ingestion behavior continues to treat thought presence/signatures as provider evidence without exposing hidden reasoning content.

## Request privacy

The adapter sends `store: false` on `generateContent`. This is an explicit per-request logging preference supported by the current API. It does not replace the provider's broader service terms, abuse monitoring, or account-level data controls.

## Unsupported behavior

The adapter deliberately does not:

- use the newer Gemini Interactions API;
- execute workflow DAGs;
- execute returned functions or built-in tools;
- perform an automatic function-response round trip;
- dereference artifact/snapshot/trace context;
- request code execution, Search, URL context, MCP, or other server-side tools;
- request visible model thinking;
- infer provider semantics for context/prompt placement that cannot be represented faithfully.

These boundaries keep live provider execution replayable and tied to the same typed harness contract used by E2H's controlled optimization layer.
