# Reproducible coding, research, and browser environments

E2H's community benchmark includes three locally reproducible environment types: coding, research, and browser. The v0.1 suite is `benchmarks/environments/suite.json` and its generated content lock is `benchmarks/environments/suite.lock.json`.

The design has two layers:

- the **suite spec** is human-authored and declares each environment's kind, source directory, network boundary, entrypoint, candidate artifact, and description;
- the **suite lock** is generated from the committed source trees and records every regular file's relative path, SHA-256, byte size, executable bit, plus a canonical tree digest.

No environment requires a package download or an external service to materialize its initial state.

## Environment kinds

### Coding

`coding-python-normalizer` is a small Python workspace with an intentionally incomplete `src/task.py` and a deterministic checker. The environment declares `network: none`; the task can be solved and checked entirely from the local workspace.

### Research

`research-local-evidence` contains a fixed evidence packet under `sources/`, including two dated sources and a distractor. The candidate writes `answer.json`; the checker verifies the supported project/date comparison and rejects unsupported source IDs. The environment declares `network: none` so the evidence boundary is closed-world and reproducible.

### Browser

`browser-static-release` is a two-page static site. Its entrypoint is Python's standard-library HTTP server bound by the benchmark policy to localhost-only use. A browser starts from the local dashboard, follows visible navigation to the details page, and writes `result.json` containing the observed release code and navigation path. The checker verifies that result without contacting an external site.

The suite model requires browser environments to use `localhost_only`; coding and research environments must use `none`.

## Seal the suite

After intentionally changing any environment source file, regenerate the lock:

```bash
uv run e2h benchmark environments seal \
  benchmarks/environments/suite.json \
  --root . \
  --output benchmarks/environments/suite.lock.json
```

The lock includes:

- suite SHA-256 over the normalized human-authored spec;
- one source-tree SHA-256 per environment;
- sorted file manifests;
- per-file SHA-256, byte size, and owner-executable bit;
- file and byte totals.

Symlinks and non-regular filesystem entries are rejected rather than followed or copied.

## Verify a checkout

```bash
uv run e2h benchmark environments verify \
  benchmarks/environments/suite.json \
  benchmarks/environments/suite.lock.json \
  --root .
```

Verification fails if the suite spec changed without resealing, if an environment was added/removed or reordered, if a kind changed, or if any source file's path, contents, size, or executable bit differs from the lock. JSON and YAML inputs use E2H's shared strict document loader, so duplicate mapping keys are rejected instead of being silently overwritten.

## Materialize one environment

```bash
uv run e2h benchmark environments materialize \
  benchmarks/environments/suite.json \
  benchmarks/environments/suite.lock.json \
  coding-python-normalizer \
  .e2h/env-coding \
  --root .
```

Materialization first verifies the entire suite, then copies the selected source tree to a destination that must not already exist, and scans the copy again. If the copied tree does not match the lock exactly, the destination is removed and the command fails.

The source scanner compares filesystem identity before and after hashing so a file that changes during verification fails closed. Materialization also preserves any raced-in symlink as a symlink for the post-copy scan to reject, rather than following it to an out-of-tree target.

This operation does not install dependencies or execute the environment entrypoint. Execution policy remains an operator/runtime concern; the environment artifact defines reproducible bytes and the intended network boundary.

## Run local checkers

After a candidate writes its declared artifact, run the environment's deterministic checker from the materialized root.

Coding and research:

```bash
python checks/check.py
```

Browser result checking uses the same command. To serve the browser fixture itself:

```bash
python -m http.server 8000 --directory site
```

The benchmark contract permits only localhost connectivity for this browser environment.

## Security boundary

A valid environment lock establishes reproducibility of committed file bytes, not safety of arbitrary candidate changes. Before materialization, E2H rejects:

- absolute or parent-traversing source paths;
- symlinks;
- non-regular source files;
- trees over the configured hard file/byte limits;
- lock/spec identity mismatches.

Candidate code can still be untrusted. Running a checker or other candidate-modified code should use the existing E2H sandbox/runtime controls appropriate to the workload.

## Schemas

```bash
uv run e2h benchmark environments schema --kind suite
uv run e2h benchmark environments schema --kind lock
uv run e2h benchmark environments schema --kind verification
```

All schema commands accept `--output`.

## Updating the community benchmark

Environment changes are intentionally lock-visible. A contribution that edits a source fixture must regenerate `suite.lock.json`; reviewers can distinguish a spec change from a byte-level fixture change and CI verifies the resulting lock before merge.
