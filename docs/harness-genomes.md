# Typed harness genomes

A harness genome is a versioned, content-addressed set of schema-aware changes to one exact task capsule. It is a transformation document, not executable code: applying a genome never runs a command, evaluates output, or reads a workspace.

## Safety and identity

Each genome binds to `base_capsule_sha256`. Application fails when the supplied capsule does not have that exact canonical digest. Patch IDs and patch targets must be unique, so a genome cannot silently overwrite an earlier decision. Patches cannot change the capsule ID, schema version, sandbox image, compiler evidence metadata, or arbitrary JSON-pointer locations.

The application result records:

- the canonical genome SHA-256;
- the exact base capsule SHA-256;
- the resulting capsule SHA-256;
- the ordered patch IDs;
- the fully validated result capsule.

Materialization revalidates the embedded result digest before writing a standalone JSON or YAML capsule.

## Patch operations

The first patch language supports these typed operations:

- `goal.set`
- `allowed_actions.network.set`
- `check.argv.set`
- `check.cwd.set`
- `check.env.set`
- `check.env.remove`
- `check.timeout.set`
- `check.expected_exit_codes.set`
- `check.continue_on_failure.set`
- `limits.default_timeout.set`
- `limits.max_output_chars.set`

Command arguments remain argument vectors; there is no shell-string patch. Working directories remain relative and cannot traverse parents. Environment names and values reject NUL, and malformed or duplicate patch targets are rejected before application.

## CLI flow

```bash
e2h-genome validate examples/genome/candidate.yaml examples/genome/base.yaml

e2h-genome apply \
  examples/genome/candidate.yaml \
  examples/genome/base.yaml \
  --output .e2h/genome-application.json

e2h-genome materialize \
  .e2h/genome-application.json \
  --output .e2h/candidate-capsule.yaml

e2h run .e2h/candidate-capsule.yaml --workspace .
```

`e2h-genome schema` emits the complete JSON Schema for optimizer and editor integrations.

## Optimization boundary

A genome describes a candidate harness change; it does not establish that the candidate is better. Later controlled-optimization layers can generate genomes, execute them through replay matrices, and promote them only after partitioned evaluation and statistical gates. The immutable application record provides the provenance link between an optimizer proposal and the exact capsule that was evaluated.
