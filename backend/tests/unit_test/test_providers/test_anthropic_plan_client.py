"""Plan §A3 + §A13 — Claude Code OAuth strategy + AnthropicPlanClient."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from providers.auth_strategy import (
    CLAUDE_OAUTH_DEFAULT_HEADERS,
    CLAUDE_OAUTH_SYSTEM_PREFIX,
    ClaudeCodeOAuthCredentialError,
    make_api_key_strategy,
    make_claude_oauth_strategy,
)


FAKE_KEYCHAIN_JSON = json.dumps(
    {
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat01-FAKE",
            "refreshToken": "sk-ant-ort01-FAKE",
            "expiresAt": 9999999999999,
            "subscriptionType": "max",
        }
    }
)


def _fake_security_ok(*args, **kwargs):
    return subprocess.CompletedProcess(
        args=args, returncode=0, stdout=FAKE_KEYCHAIN_JSON, stderr=""
    )


def _fake_security_missing(*args, **kwargs):
    raise subprocess.CalledProcessError(
        returncode=44, cmd=args, stderr="The specified item could not be found"
    )


def test_api_key_strategy_returns_api_key_kwargs():
    strat = make_api_key_strategy("sk-x")
    assert strat.get_auth_kwargs() == {"api_key": "sk-x"}
    assert strat.refresh() is False


def test_api_key_strategy_use_auth_token():
    strat = make_api_key_strategy("sk-x", use_auth_token=True)
    assert strat.get_auth_kwargs() == {"auth_token": "sk-x"}


@patch("providers.auth_strategy.subprocess.run", side_effect=_fake_security_ok)
def test_claude_oauth_strategy_reads_keychain(_mock):
    strat = make_claude_oauth_strategy()
    kwargs = strat.get_auth_kwargs()
    assert kwargs["auth_token"] == "sk-ant-oat01-FAKE"
    assert kwargs["default_headers"] == CLAUDE_OAUTH_DEFAULT_HEADERS


@patch("providers.auth_strategy.subprocess.run", side_effect=_fake_security_missing)
def test_claude_oauth_strategy_missing_keychain_raises(_mock):
    with pytest.raises(ClaudeCodeOAuthCredentialError, match="not found"):
        make_claude_oauth_strategy()


@patch("providers.auth_strategy.subprocess.run", side_effect=_fake_security_ok)
def test_anthropic_plan_client_construction_via_dispatch(_mock):
    from providers.clients.coding_plan.anthropic import AnthropicPlanClient
    from providers.provider import make_api_client

    client = make_api_client(
        db_kwargs={
            "class_path": (
                "providers.clients.coding_plan.anthropic:AnthropicPlanClient"
            )
        }
    )
    assert isinstance(client, AnthropicPlanClient)
    assert client._system_prefix == CLAUDE_OAUTH_SYSTEM_PREFIX
