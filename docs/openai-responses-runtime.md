# OpenAI Responses runtime adapter

The OpenAI Responses runtime adapter executes one live model turn from an already validated E2H typed variant. It is deliberately narrower than a general agent runtime: E2H verifies provenance and request construction, sends one HTTPS request to the Responses API, records the returned observable response, and then reuses the existing archived Responses ingestion path for traces and redaction.

## Contract

The adapter requires three inputs:

1. a task capsule
2. a `HarnessVariantDocument` bound to that exact capsule digest
3. an `OpenAIResponsesInvocation` containing runtime prompt variables and route metadata

The variant must contain a prompt and routing dimension. The selected routing target must declare `provider: openai`. Prompt variables must be supplied exactly; missing and undeclared variables fail closed.

Prompt messages are sent in declared order. Literal context with `before_prompt` or `after_prompt` placement is materialized as developer messages. Referenced artifact, snapshot, and trace context is rejected because this adapter does not silently dereference locators. `tool_context` is also rejected because it has no provider-neutral implicit mapping.

Function-style E2H tools are converted to Responses function tools using the declared tool ID, description, and JSON Schema. `auto`, `none`, `required`, and named selection map to the provider's `tool_choice` field, and `parallel_calls` maps to `parallel_tool_calls`.

The adapter never executes returned function calls. It validates returned calls against the local tool catalogue, selection policy, and `max_calls`. Violations are recorded in the runtime result and raw archive instead of discarding the provider evidence; the CLI exits with status 1 after writing the artifacts.

Workflow variants are rejected. A later workflow executor may orchestrate multiple model and tool stages explicitly, but this runtime slice does not silently interpret a provider-neutral DAG.

## Live request boundary

The adapter posts JSON to `https://api.openai.com/v1/responses` using the API key supplied through an environment variable. The key is used only in the Authorization header and is never serialized into the request audit artifact or Responses archive.

Requests include four bounded provider metadata fields:

- E2H invocation ID
- E2H variant ID
- canonical variant digest
- capsule ID

`store` is set to `false`. E2H keeps its own raw response archive locally and does not depend on later provider retrieval for evidence reconstruction.

The runtime request body receives a canonical SHA-256 digest. The audit result also records the variant document digest, variant digest, exact capsule digest, selected route target, and model.

## Observable archive

The raw provider response is wrapped in the same `OpenAIResponsesDocument` used by archived evidence ingestion. Because the live request input does not contain provider-assigned item IDs, E2H adds deterministic local archive IDs such as `runtime-001.input.0` only to the local archive copy. Those IDs are not sent to OpenAI.

The archive contains raw prompt and response content and should be treated as sensitive source evidence. Optional normalized bundles, traces, and redaction reports can be produced immediately through the existing OpenAI Responses ingestion layer.

Reasoning handling remains unchanged from archived ingestion: provider-supplied reasoning summaries may be retained, while hidden reasoning text and encrypted reasoning content are not copied into normalized evidence.

## CLI

Set the API key without placing it on the command line:

```bash
export OPENAI_API_KEY=...
```

Run one verified variant:

```bash
e2h runtime openai-responses \
  examples/smoke/capsule.yaml \
  path/to/openai-variant.yaml \
  examples/runtime/openai-responses-invocation.yaml \
  --archive .e2h/openai-runtime-archive.json \
  --result .e2h/openai-runtime-result.json \
  --bundle .e2h/openai-runtime-bundle.json \
  --traces .e2h/openai-runtime.jsonl \
  --redaction-report .e2h/openai-runtime-redaction.json
```

The invocation document is intentionally small:

```yaml
schema_version: "0.1"
id: runtime-001
variables:
  task: verify the requested contract
route_metadata:
  tier: fast
max_output_tokens: 1024
timeout_seconds: 120
metadata:
  purpose: controlled live evaluation
```

Use `--api-key-env` when an operator-managed environment exposes the credential under another variable name. Do not put API keys into capsule, variant, invocation, archive, or metadata files.

## Security and reproducibility boundary

A live model call is not deterministic in the same sense as command replay. E2H makes the request construction and provenance deterministic and content-addressed; the provider response remains external evidence. Reproducible evaluation should therefore retain the raw archive, normalized traces, exact variant/capsule identities, provider model identity returned in the response, and any downstream sealed-test or promotion evidence.

The adapter makes a real network request and sends rendered prompt/context content to OpenAI. Operators remain responsible for authorization, data-handling policy, model access, cost controls, and deciding whether the source material is permitted to leave its current trust boundary.
