# Anthropic Messages runtime adapter

E2H can execute one verified typed harness variant through Anthropic's direct Messages API while preserving the exact request body, provider response, route identity, and local tool-policy result as observable audit evidence.

The adapter is intentionally a **single stateless model turn**, not an autonomous agent loop. It never executes returned client tool calls.

## Requirements

The runtime consumes three existing E2H artifacts:

1. a task capsule;
2. a `HarnessVariantDocument` bound to that capsule's exact digest;
3. an Anthropic runtime invocation document containing prompt variables, route metadata, an output-token cap, timeout, and optional audit metadata.

The selected routing target must use provider `anthropic`.

Authentication is read only from an environment variable. By default:

```bash
export ANTHROPIC_API_KEY='...'
```

The API key is placed in the `x-api-key` request header and is never serialized into the request audit, runtime result, or archived evidence.

The adapter sends direct requests to `https://api.anthropic.com/v1/messages` with Anthropic API version `2023-06-01`.

## Invocation document

Example: [`examples/runtime/anthropic-messages-invocation.yaml`](../examples/runtime/anthropic-messages-invocation.yaml)

```yaml
schema_version: "0.1"
id: anthropic-runtime-001
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
uv run e2h runtime anthropic-messages \
  path/to/capsule.yaml \
  path/to/variant.yaml \
  examples/runtime/anthropic-messages-invocation.yaml \
  --archive .e2h/anthropic-messages-archive.json \
  --result .e2h/anthropic-runtime-result.json \
  --bundle .e2h/anthropic-bundle.json \
  --traces .e2h/anthropic-traces.jsonl \
  --redaction-report .e2h/anthropic-redaction-review.json
```

`--api-key-env` can select a different environment-variable name without placing the secret on the command line.

The raw provider archive can contain model output or other sensitive application data. Treat it as sensitive evidence and apply the normal E2H redaction/review workflow before moving it across trust boundaries.

## Provider-neutral mapping

Anthropic Messages differs from provider-neutral E2H prompt semantics in several important ways. The runtime fails closed where there is no faithful mapping.

### System and developer prompt messages

Anthropic Messages uses a top-level `system` parameter rather than `system` messages in the conversation. E2H therefore maps leading provider-neutral `system` and `developer` messages to ordered Anthropic system text blocks.

Once a `user` or `assistant` prompt message has started the conversation, a later `system` or `developer` message is rejected instead of being silently reordered.

### Context

Only literal `before_prompt` context is mapped automatically. It becomes ordered top-level Anthropic system text before the prompt's own system/developer blocks.

The adapter rejects:

- referenced artifact, snapshot, or trace context, because live runtimes do not implicitly dereference locators;
- `tool_context`, because it has no provider-independent Anthropic mapping;
- `after_prompt`, because Anthropic has no developer-message position after conversational messages that preserves that E2H placement contract.

Context ordering, per-item limits, total limits, rejection, and low-priority truncation follow the typed `ContextVariant` declaration before provider mapping.

### Tools

E2H custom tools map to Anthropic client tools:

```json
{
  "name": "lookup",
  "description": "Look up one value.",
  "input_schema": {
    "type": "object"
  }
}
```

Tool-selection mapping is deterministic:

| E2H selection | Anthropic `tool_choice` |
| --- | --- |
| `auto` | `{"type":"auto"}` |
| `none` | `{"type":"none"}` |
| `required` | `{"type":"any"}` |
| `named` | `{"type":"tool","name":"..."}` |

`parallel_calls: false` maps to `disable_parallel_tool_use: true` where Anthropic supports that field.

E2H does not execute returned `tool_use` blocks. After the provider response arrives, the adapter checks observable calls against the local typed tool contract, including declared names, maximum call count, required/named selection, input object shape, and parallel-call policy. Violations are retained in the runtime/archive evidence and make the CLI exit with status 1 rather than dropping the provider response.

## Observable archive

The runtime wraps the request context and response in the existing `AnthropicMessagesDocument` format, so the standard Anthropic ingestion adapter can normalize it into E2H traces.

The archive records:

- synthetic stable IDs for request conversation messages;
- top-level system content;
- raw observable Anthropic response payload;
- Anthropic `request-id` when available;
- request and variant digests;
- selected route target;
- local tool-policy violations.

The adapter does not enable Anthropic extended thinking. Existing Anthropic ingestion rules continue to exclude hidden/redacted thinking content from normalized observable traces if such blocks are present in imported external archives.

## Unsupported behavior

The adapter deliberately does not:

- execute workflow DAGs;
- execute returned client tools;
- perform an automatic tool-result round trip;
- dereference artifact/snapshot/trace context;
- enable beta Anthropic features or beta headers;
- infer provider semantics for placement that cannot be represented faithfully.

These boundaries keep live provider execution auditable and tied to the same typed harness contract used by E2H's controlled optimization layer.
