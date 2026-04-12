"""Centralized default values for EphemeralOS.

This module contains all hardcoded constants, limits, and magic values
that should be configurable. Values here serve as defaults that can be
overridden via Settings or environment variables.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Team Coordination Defaults
# ---------------------------------------------------------------------------

DEFAULT_TEAM_TOOL_CALL_LIMIT: int = 100
OWNED_FAILURES_PREVIEW_LIMIT: int = 64

# Default budget limits for team runs
DEFAULT_MAX_WORK_ITEMS: int = 200
DEFAULT_MAX_DEPTH: int = 5
DEFAULT_MAX_PLAN_SIZE: int = 50
DEFAULT_MAX_VALIDATORS_PER_PLAN: int | None = None
DEFAULT_REQUIRE_VALIDATOR_FOR_PLAN_SIZE: int | None = None
DEFAULT_MAX_ARTIFACT_BYTES: int = 1_000_000
DEFAULT_MAX_TOTAL_ARTIFACT_BYTES: int = 50_000_000
DEFAULT_WORK_ITEM_TIMEOUT: float | None = None
DEFAULT_MAX_BRIEFING_BYTES: int = 32_000
DEFAULT_MAX_SHARED_BRIEFINGS: int = 1000
DEFAULT_MAX_RETRIES_PER_ITEM: int = 2
DEFAULT_MAX_REPLANS_PER_RUN: int = 5

# Agent names that use team-safe (CodeAct) execution instead of raw bash
DEFAULT_TEAM_SAFE_AGENT_NAMES: frozenset[str] = frozenset({"developer", "validator"})

# ---------------------------------------------------------------------------
# Provider/Retry Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_RETRIES: int = 3
DEFAULT_BASE_DELAY: float = 1.0
DEFAULT_MAX_DELAY: float = 30.0
DEFAULT_RETRY_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 529})

# ---------------------------------------------------------------------------
# Database Defaults
# ---------------------------------------------------------------------------

DEFAULT_DATABASE_POOL_SIZE: int = 5
DEFAULT_DATABASE_MAX_OVERFLOW: int = 10

# ---------------------------------------------------------------------------
# Sandbox Defaults
# ---------------------------------------------------------------------------

DEFAULT_SANDBOX_CI_ROOT: str = "/home/daytona"

# ---------------------------------------------------------------------------
# UI Defaults
# ---------------------------------------------------------------------------

DEFAULT_UI_PASSES: int = 1

# ---------------------------------------------------------------------------
# Skill/Token Limits
# ---------------------------------------------------------------------------

SKILL_REFERENCE_TRACE_LIMIT: int = 32
