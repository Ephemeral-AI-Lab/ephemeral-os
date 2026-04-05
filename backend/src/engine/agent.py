"""Ephemeral agent — short-lived runtime for one user request.

Each agent has an identity, its own API client, tool registry, hook
executor, and query engine.  In a relay model, different agents can
serve successive turns within the same session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, AsyncIterator

if TYPE_CHECKING:
    from server.app_factory import SessionConfig
    from utils.compact import SessionState

from agents.types import AgentDefinition
from config import Settings
from engine.query_engine import QueryEngine
from engine.messages import ConversationMessage
from engine.stream_events import StreamEvent
from hooks import make_hook_executor
from models.provider import make_api_client
from models.types import SupportsStreamingMessages
from prompts import build_runtime_system_prompt
from tools import create_default_tool_registry
from tools.factory import create_toolkit, has_factory, ToolkitContext

logger = logging.getLogger(__name__)


@dataclass
class EphemeralAgent:
    """A short-lived agent that handles one user request then dies."""

    agent_name: str
    api_client: SupportsStreamingMessages
    engine: QueryEngine
    settings: Settings
    model: str

    async def run(self, prompt: str) -> AsyncIterator[StreamEvent]:
        """Execute one complete tool-call loop for the given prompt."""
        async for event in self.engine.submit_message(prompt):
            yield event


def spawn_agent(
    config: "SessionConfig",
    messages: list[ConversationMessage],
    *,
    agent_def: AgentDefinition | None = None,
    latest_user_prompt: str | None = None,
    session_state: "SessionState | None" = None,
    sandbox_id: str | None = None,
) -> EphemeralAgent:
    """Spawn a fresh ephemeral agent with the given session history.

    If *agent_def* is provided, its fields override the session defaults:
    - ``model`` overrides the session model
    - ``system_prompt`` replaces the default system prompt
    - ``toolkits`` restricts available toolkits
    - ``max_turns`` caps the tool-call loop iterations
    """
    settings = config.resolve_settings()

    # --- Active model from DB (carries api_key, base_url, class_path) ------
    db_kwargs: dict | None = None
    db_class_path: str | None = None
    try:
        from server.app_factory import model_store
        active = model_store.get_active_resolved() if model_store.is_available else None
        if active:
            db_kwargs = active.get("kwargs")
            db_class_path = active.get("class_path")
    except Exception:
        active = None

    # --- Per-agent overrides ------------------------------------------------
    resolved_model = (
        agent_def.model if agent_def and agent_def.model
        else (db_kwargs or {}).get("model") or settings.model
    )
    agent_name = agent_def.name if agent_def else resolved_model

    # --- API client
    api_client = make_api_client(
        settings, config.external_api_client,
        db_kwargs=db_kwargs, db_class_path=db_class_path,
    )

    # --- Tool registry
    tool_registry = create_default_tool_registry()

    # --- Instantiate toolkits requested by the agent definition via factory ---
    toolkit_ctx = ToolkitContext(
        agent_name=agent_name,
        cwd=config.cwd,
        metadata={"sandbox_id": sandbox_id or ""},
    )
    if agent_def and agent_def.toolkits:
        for tk_name in agent_def.toolkits:
            if tool_registry.get_toolkit(tk_name) is not None:
                continue  # already registered
            if has_factory(tk_name):
                try:
                    tk = create_toolkit(tk_name, toolkit_ctx)
                    tool_registry.register_toolkit(tk)
                    logger.info("Registered toolkit %r for agent %r", tk_name, agent_name)
                except Exception:
                    logger.warning("Failed to create toolkit %r for agent %r", tk_name, agent_name, exc_info=True)
            else:
                logger.warning("No factory for toolkit %r requested by agent %r", tk_name, agent_name)

    # Register Daytona sandbox tools when a sandbox is selected (if not already registered above)
    if sandbox_id and tool_registry.get_toolkit("sandbox_operations") is None:
        try:
            from tools.daytona_toolkit import DaytonaToolkit
            daytona_toolkit = DaytonaToolkit(sandbox_id=sandbox_id)
            tool_registry.register_toolkit(daytona_toolkit)
            logger.info("Registered DaytonaToolkit for sandbox %s", sandbox_id)
        except Exception:
            logger.warning("Failed to register DaytonaToolkit for sandbox %s", sandbox_id, exc_info=True)

    if agent_def and agent_def.toolkits:
        tool_registry.restrict_to_toolkits(agent_def.toolkits)

    # --- Hook executor
    hook_executor = make_hook_executor(settings, config.cwd, api_client)

    # --- System prompt
    if agent_def and agent_def.system_prompt:
        system_prompt = agent_def.system_prompt
    else:
        system_prompt = build_runtime_system_prompt(
            settings, cwd=config.cwd, latest_user_prompt=latest_user_prompt,
        )

    # --- Inject skills & toolkit awareness into system prompt ----------------
    awareness_sections: list[str] = []

    # Skills awareness
    if agent_def and agent_def.skills:
        from skills.loader import load_skill_registry
        registry = load_skill_registry(config.cwd)
        skill_lines = []
        for slug in agent_def.skills:
            skill = registry.get(slug)
            if skill:
                skill_lines.append(f"- **{skill.name}**: {skill.description}")
        if skill_lines:
            awareness_sections.append(
                "# Available Skills\n\n"
                "The following skills are available via the `skill` tool. "
                "When a task matches a skill, invoke it to load detailed instructions.\n\n"
                + "\n".join(skill_lines)
            )

    # Toolkit awareness — tell the agent what tools it has
    registered_toolkits = tool_registry.list_toolkits()
    if registered_toolkits:
        tk_lines = []
        for tk in registered_toolkits:
            tool_names = ", ".join(tk.tool_names())
            tk_lines.append(f"- **{tk.name}**: {tool_names}")
        awareness_sections.append(
            "# Available Toolkits\n\n"
            "You have the following toolkits and tools available:\n\n"
            + "\n".join(tk_lines)
        )

    if awareness_sections:
        system_prompt = system_prompt + "\n\n" + "\n\n".join(awareness_sections)

    # --- Max turns
    max_turns = agent_def.max_turns if agent_def and agent_def.max_turns else 200

    # --- Query engine
    engine = QueryEngine(
        api_client=api_client,
        tool_registry=tool_registry,
        cwd=config.cwd,
        model=resolved_model,
        system_prompt=system_prompt,
        max_tokens=settings.max_tokens,
        hook_executor=hook_executor,
        session_state=session_state,
    )
    if messages:
        engine.load_messages(messages)

    return EphemeralAgent(
        agent_name=agent_name,
        api_client=api_client,
        engine=engine,
        settings=settings,
        model=resolved_model,
    )
