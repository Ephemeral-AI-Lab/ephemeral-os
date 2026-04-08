"""Builtin team_planner / team_worker definitions.

Registered via ``register_all()`` which is called from a bootstrap module
(``backend/src/__main__.py``) at server startup. Never imported from
``agents/registry.py`` — that would create a reverse dependency.
"""

from __future__ import annotations

import logging

from agents.registry import register_definition
from agents.types import AgentDefinition
from hooks.agent_posthook import PosthookConfig

logger = logging.getLogger(__name__)

TEAM_PLANNER = "team_planner"
TEAM_WORKER = "team_worker"

_PLANNER_PROMPT = """You are team_planner. Decompose the user request into concrete WorkItems.
Think clearly, reference the user request, and produce a structured plan.
The next phase will call submit_plan with your output, so be explicit about
dependencies between items."""

_PLANNER_POSTHOOK_PROMPT = """You are the planner serialization phase. Call submit_plan exactly once with a Plan whose items match the work-phase output. Fix any Phase A errors and resubmit."""

_WORKER_PROMPT = """You are team_worker. Execute the specific WorkItem described in the payload. Return a concise summary and any artifacts. Use the team context tools (team_list_siblings, team_files_changed_since_dispatch) to stay aware of peer work."""


def register_all() -> None:
    register_definition(
        AgentDefinition(
            name=TEAM_PLANNER,
            description="Team-mode planner agent: decomposes requests and submits Plans.",
            system_prompt=_PLANNER_PROMPT,
            model="inherit",
            max_turns=10,
            toolkits=["code_intelligence"],
            source="builtin",
            posthook=PosthookConfig(
                submit_tool="submit_plan",
                metadata_key="submitted_plan",
                system_prompt=_PLANNER_POSTHOOK_PROMPT,
                max_turns=5,
            ),
        )
    )
    register_definition(
        AgentDefinition(
            name=TEAM_WORKER,
            description="Team-mode worker agent: executes one WorkItem with full toolkit.",
            system_prompt=_WORKER_PROMPT,
            model="inherit",
            max_turns=15,
            toolkits=["sandbox_operations", "code_intelligence"],
            source="builtin",
        )
    )
    logger.info("team builtins registered: %s, %s", TEAM_PLANNER, TEAM_WORKER)
