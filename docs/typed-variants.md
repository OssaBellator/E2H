# Typed harness variants

Typed harness variants are provider-neutral optimizer artifacts for describing candidate prompt, tool, context, routing, and workflow configurations. They are declarative data, not executable provider requests.

## Variant dimensions

A `HarnessVariant` can contain any combination of five typed dimensions:

- **Prompt variants** contain ordered role-tagged templates and explicit `${variable}` declarations. Duplicate message IDs, undeclared placeholders, and unused variables are rejected.
- **Tool variants** contain bounded function-style JSON Schema contracts and one total selection policy: `auto`, `none`, `required`, or `named`.
- **Context variants** contain literal or content-addressed artifact, snapshot, and trace references with placement, priority, per-item caps, a global character budget, and an explicit overflow policy.
- **Routing variants** contain a finite provider/model target catalogue, deterministic metadata-match rules, and a required fallback target.
- **Workflow variants** contain a finite acyclic stage graph with explicit dependencies, dimension usage, timeouts, retry bounds, failure behavior, and maximum parallelism.

All models reject unknown fields, NUL-bearing process data, ambiguous identifiers, non-canonical metadata, and unbounded documents.

## Binding and identity

A standalone `HarnessVariantDocument` binds one `HarnessVariant` to an exact canonical task-capsule SHA-256. E2H calculates separate canonical identities for the inner variant and the complete bound document. Verification fails when the supplied capsule differs from the bound digest.

Applying validation does not render templates, call tools, fetch context, select a provider, or execute workflow stages. Runtime adapters remain a separate integration boundary.

## CLI

```bash
e2h variant validate \
  examples/variants/candidate.yaml \
  examples/genome/base.yaml

e2h variant digest examples/variants/candidate.yaml

e2h variant schema --output .e2h/variant-schema.json
```

The focused `e2h-variant` entry point exposes the same commands.

## Replay provenance

Existing environment-only matrix variants remain valid. Every `ExperimentRun` now records the canonical variant SHA-256, and E2H injects `E2H_VARIANT_SHA256` beside `E2H_VARIANT_ID` and `E2H_REPETITION`. Those names are reserved and cannot be overridden by a variant.

Typed dimensions are recorded and validated at this layer but are not silently interpreted by the deterministic command runner. Future optimizer and provider adapters can consume the same content-addressed documents without changing their identity or provenance.
