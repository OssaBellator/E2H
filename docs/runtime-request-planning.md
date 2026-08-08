# Runtime request planning

E2H can materialize the exact deterministic provider request for a verified harness variant without reading provider credentials or making a network call.

This is useful for reviewing routing, rendered prompts, context placement, tool schemas, token limits, and the request digest before live execution.

## Commands

```bash
e2h runtime plan openai-responses \
  capsule.yaml variant.yaml invocation.yaml --json

e2h runtime plan anthropic-messages \
  capsule.yaml variant.yaml invocation.yaml --output anthropic-plan.json

e2h runtime plan gemini-generate-content \
  capsule.yaml variant.yaml invocation.yaml --output gemini-plan.json
```

`--json` writes the complete request audit object to stdout. `--output PATH` atomically writes the same JSON document to disk. The options can be used together.

No `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` environment variable is read by these planning commands.

## Contract

Planning calls the same provider-specific deterministic request builders used immediately before live transport:

- `build_openai_responses_request`
- `build_anthropic_messages_request`
- `build_gemini_generate_content_request`

The resulting object therefore includes the provider request body plus E2H provenance such as the bound capsule digest, variant digest, selected route/model, and canonical request digest. Planning does not create a provider response archive because no provider request is sent.

The same fail-closed runtime constraints apply during planning. Invalid capsule/variant bindings, missing prompt variables, unsupported provider routes, workflow DAG execution, referenced context that has not been materialized, and provider-specific unrepresentable context placement are rejected before any request plan is emitted.

## Security and review

A request plan contains rendered prompts, literal context, tool definitions, routing choices, and invocation metadata. Treat plan files as sensitive evaluation artifacts even though they contain no provider API key.

Planning is not a provider connectivity check and does not validate whether the selected model currently exists or whether credentials have permission to use it. Those checks occur only during live provider execution.
