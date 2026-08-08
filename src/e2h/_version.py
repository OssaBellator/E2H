"""Internal package version and version-derived client identifiers."""

VERSION = "0.28.0"


def runtime_user_agent(runtime: str) -> str:
    """Return the canonical E2H User-Agent for one provider runtime."""
    return f"e2h-{runtime}-runtime/{VERSION}"


__all__ = ["VERSION", "runtime_user_agent"]
