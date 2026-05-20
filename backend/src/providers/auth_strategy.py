"""Auth strategies for `AnthropicClient`.

Per plan §A2: an `AuthStrategy` returns the kwargs needed to authenticate an
Anthropic SDK call (`api_key` xor `auth_token`, plus optional default headers)
and exposes a `refresh()` hook that returns True if it mutated state with a
new credential. Today's behavior = `make_api_key_strategy`. Plan-mode =
`make_claude_oauth_strategy` (reads macOS Keychain entry `Claude Code-credentials`).
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Protocol


class AuthStrategy(Protocol):
    def get_auth_kwargs(self) -> dict[str, object]: ...
    def refresh(self) -> bool: ...


class _ApiKeyStrategy:
    def __init__(self, api_key: str, *, use_auth_token: bool = False) -> None:
        self._api_key = api_key
        self._use_auth_token = use_auth_token

    def get_auth_kwargs(self) -> dict[str, object]:
        if self._use_auth_token:
            return {"auth_token": self._api_key}
        return {"api_key": self._api_key}

    def refresh(self) -> bool:
        return False


def make_api_key_strategy(api_key: str, *, use_auth_token: bool = False) -> AuthStrategy:
    return _ApiKeyStrategy(api_key, use_auth_token=use_auth_token)


# ---------------------------------------------------------------------------
# Claude Code OAuth strategy (macOS Keychain). Linux deferred per plan §A3.
# ---------------------------------------------------------------------------

CLAUDE_OAUTH_DEFAULT_HEADERS: dict[str, str] = {
    "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
    "User-Agent": "claude-cli/2.1.75 (external, cli)",
    "x-app": "cli",
}


class ClaudeCodeOAuthCredentialError(RuntimeError):
    """Raised when macOS Keychain entry `Claude Code-credentials` is unreadable."""


class _ClaudeOAuthStrategy:
    """Reads `Claude Code-credentials` keychain entry, returns Bearer token."""

    KEYCHAIN_SERVICE = "Claude Code-credentials"

    def __init__(self) -> None:
        self._access_token = self._read_keychain()

    def _read_keychain(self) -> str:
        user = os.environ.get("USER") or ""
        if not user:
            raise ClaudeCodeOAuthCredentialError("$USER not set; cannot query Keychain")
        try:
            result = subprocess.run(
                [
                    "security",
                    "find-generic-password",
                    "-s",
                    self.KEYCHAIN_SERVICE,
                    "-a",
                    user,
                    "-w",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10.0,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ClaudeCodeOAuthCredentialError(
                f"Keychain entry '{self.KEYCHAIN_SERVICE}' not found for user {user!r}. "
                "Run `claude` once to populate it."
            ) from exc
        try:
            payload = json.loads(result.stdout.strip())
            token = payload["claudeAiOauth"]["accessToken"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ClaudeCodeOAuthCredentialError(
                "Keychain entry shape unexpected; cannot extract claudeAiOauth.accessToken"
            ) from exc
        if not isinstance(token, str) or not token:
            raise ClaudeCodeOAuthCredentialError("claudeAiOauth.accessToken is empty")
        return token

    def get_auth_kwargs(self) -> dict[str, object]:
        return {
            "auth_token": self._access_token,
            "default_headers": dict(CLAUDE_OAUTH_DEFAULT_HEADERS),
        }

    def refresh(self) -> bool:
        # Refresh-token exchange is plan §A7 follow-up; today we re-read the
        # Keychain so a recent `claude` invocation that rotated the token in
        # place is picked up.
        try:
            new_token = self._read_keychain()
        except ClaudeCodeOAuthCredentialError:
            return False
        if new_token == self._access_token:
            return False
        self._access_token = new_token
        return True


def make_claude_oauth_strategy() -> AuthStrategy:
    return _ClaudeOAuthStrategy()


CLAUDE_OAUTH_SYSTEM_PREFIX = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)
