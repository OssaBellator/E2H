# Contributing

1. Create a focused branch.
2. Install development dependencies with `uv sync --extra dev`.
3. Add or update tests for every behavioral change.
4. Run the lint, format, type, test, and smoke commands documented in `README.md`.
5. Keep capsule schema changes backwards-compatible or introduce a new schema version.

Security-sensitive changes to execution, path handling, redaction, or permissions require explicit adversarial tests.
