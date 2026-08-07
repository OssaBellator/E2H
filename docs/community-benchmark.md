# E2H community benchmark: sanitized failure patterns

Milestone 5 starts with a versioned corpus format for observable failure patterns derived from public real-world reports without copying raw issue text, logs, usernames, local paths, or secrets into the benchmark.

The seed corpus is `benchmarks/failure-patterns/v0.1.json`.

## Why patterns instead of copied incidents

A community benchmark should preserve the engineering failure shape without turning someone else's bug report into a permanent raw-data archive. E2H therefore stores a small paraphrased pattern with:

- one existing `FailureCode` and its required `FailureCategory`;
- a short sanitized scenario;
- observable signals that a replay or integration test could measure;
- the expected harness behavior;
- an HTTPS public source reference used only for provenance/audit;
- a sanitization attestation declaring that raw source text, raw logs, direct identifiers, and secrets are not included;
- a list of non-reversible sanitization actions.

Synthetic entries use a different origin value and are forbidden from claiming a public real-world source. This keeps synthetic benchmark expansion useful without inflating the number of real-world-derived examples.

## Seed public patterns

The v0.1 seed contains four independently reported failure shapes:

1. A valid workspace becomes unreachable after a cross-environment path translation, mapped to `working_directory_missing`.
   Source: `https://github.com/openai/codex/issues/28174`
2. A background agent service has a narrower executable search path than the interactive shell, mapped to `command_not_found`.
   Source: `https://github.com/openclaw/openclaw/issues/9302`
3. A fixed wall-clock limit interrupts legitimate long-running agent work, mapped to `timeout`.
   Source: `https://github.com/NousResearch/hermes-agent/issues/4815`
4. A self-hosted setup step reaches a filesystem location the runner identity cannot modify, mapped to `permission_denied`.
   Source: `https://github.com/actions/setup-python/issues/792`

Only the public issue URL is retained as provenance. The pattern descriptions and signals are E2H-authored paraphrases rather than excerpts.

## Validate a corpus

```bash
uv run e2h benchmark validate benchmarks/failure-patterns/v0.1.json
```

Machine-readable verification:

```bash
uv run e2h benchmark validate benchmarks/failure-patterns/v0.1.json --json
```

Validation checks the following boundaries:

- strict schema and unique pattern identifiers;
- failure-code/category consistency with E2H's replay taxonomy;
- real-world provenance claims require a public HTTPS source reference;
- synthetic patterns cannot claim a public real-world source;
- source URLs cannot embed credentials, query strings, or fragments;
- sanitization attestations are explicit and bounded;
- E2H's existing secret, email, and phone detectors scan the published pattern text in review-only mode;
- a canonical SHA-256 binds the complete normalized corpus.

The privacy scan is a baseline, not proof of complete de-identification. Manual review remains mandatory for a real-world-derived contribution.

By default `benchmark validate` also requires at least one `sanitized_real_world` pattern. A development-only synthetic corpus can be checked with `--allow-synthetic-only`.

## Inspect without reproducing pattern text

```bash
uv run e2h benchmark inspect benchmarks/failure-patterns/v0.1.json
```

Inspection reports counts by taxonomy and the corpus digest rather than printing scenarios or source issue content.

Generate the schema with:

```bash
uv run e2h benchmark schema --output .e2h/failure-pattern-corpus.schema.json
```

## Contribution policy

A new `sanitized_real_world` entry should meet all of these conditions before review:

1. The source is publicly accessible over HTTPS and describes an observed failure rather than a hypothetical feature request.
2. The benchmark text is independently paraphrased and reduced to observable signals.
3. Usernames, email addresses, phone numbers, local absolute paths, tokens, credentials, raw logs, stack traces, exact private project names, and unnecessary environment identifiers are removed.
4. The selected `FailureCode` matches the observable failure, not a guessed hidden cause.
5. The expected behavior describes what the harness or evaluator should do, not how the original project eventually fixed its issue.
6. `e2h benchmark validate` reports zero residual sensitive-pattern findings.
7. A human reviewer confirms the source/reference and sanitization attestation before merge.

Do not add a private support ticket, private conversation, customer transcript, or locally captured evidence to the public benchmark merely by paraphrasing it. The initial corpus deliberately uses public bug reports so provenance can be audited without exposing non-public material.

## Scope of v0.1

The seed corpus is intentionally small. Its purpose is to establish the publication contract and cover several different failure domains before adding larger long-horizon tasks and reproducible environments. Future corpus revisions can add more public patterns while retaining the same distinction between sanitized real-world provenance and synthetic benchmark cases.
