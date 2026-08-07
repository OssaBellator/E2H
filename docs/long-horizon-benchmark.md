# Long-horizon constraint retention and correction benchmark

E2H's long-horizon benchmark measures whether an agent can retain independent constraints across many conversational turns while correctly applying later corrections and revocations.

The seed corpus is `benchmarks/long-horizon/v0.1.json`. It contains three synthetic dialogues with 18, 20, and 22 visible turns respectively and nine evaluation probes in total.

## Benchmark contract

Each labelled task contains:

- a stable set of `constraint_keys` that define the fields a candidate may report;
- visible system/user/assistant turns;
- private evaluator-side `updates` attached to the turns that introduce, correct, or revoke constraints;
- probes that ask for the active constraint mapping after specific visible turns.

An update is either `set` or `revoke`. A new constraint value can be introduced only while that key is inactive. Changing an active value requires an explicit `supersedes` reference to the currently active update. Revoking a constraint also requires an exact `supersedes` reference. This makes correction chains machine-checkable instead of inferring hidden intent from free-form text.

The private update labels are not included in the candidate-visible export.

## Validate the labelled corpus

```bash
uv run e2h benchmark long-horizon validate benchmarks/long-horizon/v0.1.json
```

Machine-readable validation reports both identities:

```bash
uv run e2h benchmark long-horizon validate \
  benchmarks/long-horizon/v0.1.json \
  --json
```

- `private_sha256` binds the complete labelled corpus, including evaluator updates.
- `public_sha256` binds the label-free candidate-visible export.

These digests are intentionally different.

## Export candidate-visible tasks

```bash
uv run e2h benchmark long-horizon export \
  benchmarks/long-horizon/v0.1.json \
  --output .e2h/long-horizon-public.json
```

The export keeps task IDs, titles, declared constraint keys, visible dialogue, and probe locations/prompts. It removes all private update labels and task/corpus metadata. Candidate prediction documents must include the exact `public_corpus_sha256` so results cannot be scored against a different public task set accidentally.

## Prediction format

A prediction supplies the active mapping for one task/probe pair:

```json
{
  "schema_version": "0.1",
  "public_corpus_sha256": "...",
  "predictions": [
    {
      "task_id": "release-brief-corrections",
      "probe_id": "r-p1",
      "active_constraints": {
        "audience": "internal engineering staff",
        "format": "concise bullets",
        "tone": "neutral"
      }
    }
  ]
}
```

Only declared task constraint keys may appear. Task/probe pairs must be unique.

## Evaluation

```bash
uv run e2h benchmark long-horizon evaluate \
  benchmarks/long-horizon/v0.1.json \
  predictions.json \
  --json
```

A probe is correct only when the predicted active mapping exactly matches the evaluator state after applying every update through the probe's referenced turn. A correction therefore requires the new value, and a revoked key must be absent.

Missing probes count as incorrect in the score. The CLI requires complete coverage by default and exits nonzero if predictions omit a probe. `--allow-partial` is available for development diagnostics.

The evaluation report deliberately contains only aggregate counts and task-level scores. It does not return expected active constraint mappings, which prevents the evaluator from becoming an answer oracle for a sealed deployment of the same format.

## Seed task shapes

The v0.1 seed exercises three different domains:

1. **Release brief** — audience and format are corrected after distractor questions; a tone restriction is later revoked while a date-format constraint remains active.
2. **Research brief** — source scope and word limit change independently; an explicit citation-style requirement is later removed while a no-speculation constraint survives the whole dialogue.
3. **Coding task** — runtime, network, output, dependency, and testing constraints evolve independently; two are corrected and one is revoked after unrelated technical questions.

The task content is synthetic so the benchmark can publish both dialogue and labels without privacy or redistribution ambiguity. Real-world failure provenance remains handled separately by the sanitized failure-pattern corpus.

## Why exact constraint maps

The first long-horizon benchmark focuses on state retention rather than prose quality. Exact maps make these errors distinguishable and reproducible:

- forgetting a constraint after many distractor turns;
- retaining an obsolete value after an explicit correction;
- failing to remove a revoked constraint;
- accidentally modifying an unrelated constraint when another key changes;
- inventing undeclared constraints.

Later benchmark layers can apply these same state-transition semantics to executable coding, research, and browser environments.

## Generate schemas

```bash
uv run e2h benchmark long-horizon schema --kind corpus
uv run e2h benchmark long-horizon schema --kind public
uv run e2h benchmark long-horizon schema --kind predictions
uv run e2h benchmark long-horizon schema --kind report
```

Each schema can be written with `--output`.
