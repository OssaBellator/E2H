"""Shared User-Agent construction for live provider runtime adapters."""

from typing import Literal

RuntimeProvider = Literal["openai", "anthropic", "gemini"]


def runtime_user_agent(provider: RuntimeProvider) -> str:
    """Return a provider-specific User-Agent tied to the public E2H version."""
    from e2h import __version__

    return f"e2h-{provider}-runtime/{__version__}"
