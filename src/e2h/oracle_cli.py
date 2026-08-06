"""Command entry point for deterministic declarative oracle checks."""

from __future__ import annotations

import json
import os
import sys

from pydantic import ValidationError

from e2h.oracles import (
    ORACLE_ADAPTER,
    ORACLE_MUTATION_ENV,
    OracleError,
    evaluate_oracle,
)


def main() -> int:
    """Evaluate one serialized oracle and return a command-friendly exit code."""
    if len(sys.argv) != 2:
        print("usage: python -m e2h.oracle_cli <oracle-json>", file=sys.stderr)
        return 2
    try:
        payload = json.loads(sys.argv[1])
        template = ORACLE_ADAPTER.validate_python(payload)
        result = evaluate_oracle(
            template,
            mutation_operator=os.environ.get(ORACLE_MUTATION_ENV),
        )
    except (json.JSONDecodeError, ValidationError, OracleError, ValueError) as exc:
        print(f"invalid oracle: {exc}", file=sys.stderr)
        return 2
    print(result.model_dump_json())
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
