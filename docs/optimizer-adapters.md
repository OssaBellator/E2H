# DSPy and GEPA optimizer adapters

E2H exposes SDK-optional adapter documents for using external DSPy optimizers while keeping replay, identity, and candidate materialization inside the E2H trust boundary. Importing `e2h` does not import DSPy, start an optimizer, call a model, or execute a candidate.

## DSPy datasets

`DSPyDatasetDocument` stores canonical JSON-compatible inputs and optional outputs. Every example in a document must use the same input and output field sets, identifiers must be valid Python field names, and input/output names cannot overlap.

```bash
e2h optimizer export-dataset \
  examples/optimizer/dataset.yaml \
  --output .e2h/dspy-examples.json
```

Each exported record contains `values` and `input_fields`. A DSPy integration can construct the SDK object explicitly:

```python
example = dspy.Example(**record["values"]).with_inputs(*record["input_fields"])
```

E2H does not require DSPy as an installation dependency because these payloads are plain, versioned data.

## Adapter binding

An `OptimizerAdapterDocument` binds one optimizer family to an exact task capsule, exact typed variant, and finite set of mutable prompt message components. The adapter digest changes when any identity, component binding, or metadata value changes.

```bash
e2h optimizer validate \
  examples/optimizer/adapter.yaml \
  examples/genome/base.yaml \
  examples/variants/candidate.yaml
```

Only prompt `content` is mutable in schema version `0.1`. Tools, context references, routes, workflows, environment values, message roles, message identifiers, capsule identity, and variant metadata cannot be silently rewritten by an optimizer candidate.

## GEPA feedback

`feedback_from_run_result` converts deterministic replay results into a scalar score and bounded textual feedback. The score is the fraction of checks that passed. Text feedback includes check status and the structured E2H failure taxonomy, but never copies command stdout, stderr, or free-form runner error text.

```bash
e2h optimizer feedback .e2h/run.json --prediction
```

The `--prediction` shape is directly usable as keyword arguments for the GEPA metric result expected by DSPy:

```python
return dspy.Prediction(**payload)  # score=..., feedback=...
```

This follows GEPA's scalar-plus-text feedback interface without depending on optimizer result internals, which may change between DSPy and GEPA releases.

## Candidate import

An `OptimizerCandidateDocument` must repeat the optimizer kind, adapter digest, base capsule digest, and base variant digest. Every update must reference a declared component. E2H rejects unknown components, duplicate updates, identity mismatches, NUL-bearing text, no-op candidates, and candidates that violate the original prompt-variable contract.

```bash
e2h optimizer apply \
  examples/optimizer/adapter.yaml \
  examples/optimizer/candidate.yaml \
  examples/genome/base.yaml \
  examples/variants/candidate.yaml \
  --output .e2h/optimized-variant.json
```

The materialized variant receives canonical optimizer provenance containing the adapter identity, candidate identity, candidate digest, optimizer family, baseline variant digest, and optional score. The candidate is still declarative; replay and later promotion gates decide whether it is acceptable.

## Schemas and focused entry point

```bash
e2h optimizer schema --kind adapter
e2h optimizer schema --kind candidate
e2h optimizer schema --kind dataset
e2h optimizer schema --kind feedback
```

The focused `e2h-optimizer` entry point exposes the same commands.

## Security boundary

External optimizer output is untrusted input. Validate and materialize it before replaying it. E2H does not deserialize Python objects, import saved DSPy programs, inspect hidden model reasoning, or trust optimizer-reported scores as promotion evidence. Scores remain provenance only until the candidate is evaluated through controlled replay and statistical promotion gates.
