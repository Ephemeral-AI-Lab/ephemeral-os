"""Coding plan-mode Anthropic client (macOS Keychain OAuth)."""

from __future__ import annotations

from typing import Any

from providers.auth_strategy import (
    CLAUDE_OAUTH_SYSTEM_PREFIX,
    make_claude_oauth_strategy,
)
from providers.clients.anthropic_native import AnthropicClient


class AnthropicPlanClient(AnthropicClient):
    """`AnthropicClient` configured with the Claude Code OAuth strategy.

    Reads the OAuth bearer token from macOS Keychain entry
    `Claude Code-credentials` (plan §A3) and prepends the Anthropic-required
    identity block (plan §A13).
    """

    def __init__(self, *, db_kwargs: dict[str, Any] | None = None) -> None:
        kwargs = db_kwargs or {}
        super().__init__(
            base_url=kwargs.get("base_url"),
            auth_strategy=make_claude_oauth_strategy(),
            system_prefix=CLAUDE_OAUTH_SYSTEM_PREFIX,
        )
