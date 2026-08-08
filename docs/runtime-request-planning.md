# Runtime request planning

E2H can materialize the exact request audit for any supported live provider runtime without reading credentials or making a network call.

This is useful when reviewing a harness variant before execution, comparing provider mappings, recording a request digest in an external approval system, or testing routing/prompt/tool changes in an environment that has no provider credentials.

## Supported providers

`RuntimeProvider` currently contains:

- `openai-responses`;
- `anthropic-messages`;
- `gemini-generate-content`.

The planner delegates directly to the existing provider request builders. It does not maintain a second request format or recompute a different provider digest.

The planner's public types and functions are exported from both `e2h` and `e2h.runtime_plan`. Package-root imports are convenient for callers that already use E2H's public runtime APIs, while the submodule remains available for explicit namespacing.

## Planning loaded objects

```python
from e2h import RuntimeProvider, plan_runtime_request

plan = plan_runtime_request(
    RuntimeProvider.OPENAI_RESPONSES,
    variant_document,
    capsule,
    invocation,
)

print(plan.provider)
print(plan.request.request_sha256)
print(plan.request.model_dump_json(indent=2))
```

`plan.request` is the native typed request model for the selected provider:

- `OpenAIResponsesRequest`;
- `AnthropicMessagesRequest`;
- `GeminiGenerateContentRequest`.

The `RuntimeRequestPlan.request_sha256` property is only a convenience view of the underlying provider request digest.

## Planning directly from files

```python
from pathlib import Path

from e2h import RuntimeProvider, load_runtime_request_plan

plan = load_runtime_request_plan(
    RuntimeProvider.ANTHROPIC_MESSAGES,
    Path("capsule.yaml"),
    Path("variant.yaml"),
    Path("invocation.yaml"),
)
```

The same capsule, variant, and provider invocation loaders used by live execution are used here. Invalid capsule bindings, prompt variables, routes, context mappings, tool mappings, workflows, and invocation documents therefore fail with the same provider-runtime rules.

Planner-facing failures are normalized to `RuntimePlanError` so callers can handle one error boundary when the provider is selected dynamically.

## Credential and network boundary

The planner calls only deterministic request builders. It does not:

- read `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY`;
- construct authorization headers;
- invoke the provider HTTP transports;
- execute returned tools;
- write archives, traces, or evidence bundles.

A successful plan means E2H could materialize the provider request from the supplied typed inputs. It does not prove that credentials are valid, the provider model exists, the account has access, or the later network request will succeed.

## Provider/invocation type safety

The selected provider and invocation type must agree. For example, an `AnthropicMessagesInvocation` cannot be planned as `openai-responses`.

The resulting `RuntimeRequestPlan` also validates that its native request model matches the selected provider, preventing a dynamically assembled plan from pairing the wrong provider label with a request audit.

## Relationship to live execution

Live provider execution continues to use the existing provider runtime functions. Planning does not add another execution path.

For identical capsule, variant, and invocation inputs, the planner delegates to the same request builder that live execution calls immediately before authentication and transport. The provider-native request body and `request_sha256` are therefore the reviewable pre-network contract.
