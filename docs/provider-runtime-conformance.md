# Provider runtime conformance

E2H's live OpenAI Responses, Anthropic Messages, and Gemini GenerateContent adapters share one provider-neutral typed harness contract even though their wire formats differ.

The cross-provider conformance suite in `tests/test_provider_runtime_conformance.py` locks the behavior that should remain equivalent across all three adapters.

## Shared contract

For an equivalent `HarnessVariantDocument` and task capsule, every provider runtime must:

- verify the variant's exact capsule binding before materializing a request;
- resolve the same routing fallback/rule semantics;
- require the exact declared prompt-variable set;
- apply literal context item limits, total limits, priority ordering, and low-priority truncation before provider mapping;
- preserve `auto`, `required`, `none`, and named custom-tool selection intent;
- reject workflow DAG execution in the single-turn runtime adapter;
- reject referenced artifact/snapshot/trace context instead of dereferencing it implicitly;
- reject `tool_context` instead of inventing provider-specific semantics;
- reject routes whose selected provider does not match the runtime adapter;
- produce a stable request digest for identical typed inputs;
- preserve the exact base capsule digest, selected route ID, model, and invocation ID in the request audit.

The conformance tests intentionally normalize provider-specific tool-choice structures before comparing intent. They do not require identical JSON request bodies because the provider APIs are structurally different.

## Provider-specific mappings

### OpenAI Responses

OpenAI represents custom functions directly in the top-level `tools` array. `tool_choice` uses `auto`, `required`, `none`, or a named function object. Literal context is represented as developer messages around the prompt where the typed placement can be represented faithfully.

### Anthropic Messages

Anthropic represents custom tools with `name`, `description`, and `input_schema`. Tool choice uses `auto`, `any`, `none`, or a named `tool` object. Leading system/developer material and supported literal context are represented in top-level system content.

### Gemini GenerateContent

Gemini represents custom functions inside `functionDeclarations`. Tool choice uses `AUTO`, `ANY`, or `NONE`, with `allowedFunctionNames` for a named function. Leading system/developer material and supported literal context become text parts in `systemInstruction`.

## Deliberate non-equivalence

Conformance does not mean every provider can express every typed placement or enforcement mechanism identically.

For example:

- OpenAI can represent supported `after_prompt` context with developer messages, while Anthropic and Gemini reject that placement because their system instructions are top-level only.
- Anthropic exposes a request-side parallel-tool-use control; Gemini does not, so Gemini enforces `parallel_calls: false` against the observable response instead.
- Provider response/archive shapes, token accounting, request IDs, stop reasons, and provider-specific metadata remain native to each provider's existing ingestion schema.

These differences should remain explicit. A runtime adapter must fail closed when it cannot faithfully map a provider-neutral typed contract rather than silently changing the contract.

## Why this suite exists

The provider adapters intentionally remain separate modules so their external API semantics are auditable. The conformance matrix prevents that separation from turning into accidental behavioral drift as one provider evolves faster than the others.

When adding or changing a provider runtime, update the conformance matrix for any behavior that is supposed to be provider-neutral. If a behavior must differ, document the difference and add provider-specific rejection or mapping coverage rather than weakening the shared assertions.
