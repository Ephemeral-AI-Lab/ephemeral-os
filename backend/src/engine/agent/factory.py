"""Ephemeral agent — short-lived runtime for one user request.

Each agent has an identity, its own API client, tool registry, and query engine.
Every run is one provider request shaped as system prompt, user prompt, and
assistant response.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast
from collections.abc import AsyncIterator

if TYPE_CHECKING:
    from runtime.app_factory import RuntimeConfig
    from engine.query.context import QueryContext
    from notification import NotificationRule
    from tools import ToolRegistry

from agents import AgentDefinition
from config import Settings
from message.messages import ConversationMessage
from message.stream_events import StreamEvent
from providers.provider import make_api_client
from providers.types import UsageSnapshot
from prompt import build_runtime_system_prompt
from tools import (
    ExecutionMetadata,
    SANDBOX_CONTEXT,
    ToolFactoryContext,
    create_tool,
    create_default_tool_registry,
    has_tool,
    make_background_tools,
    make_sandbox_tools,
    resolve_harness_notification_triggers,
)

logger = logging.getLogger(__name__)

_BACKGROUND_CONTROL_TOOL_NAMES = frozenset(
    {
        "cancel_background_task",
        "check_background_task_result",
        "wait_background_tasks",
    }
)


@dataclass
class EphemeralAgent:
    """A short-lived agent that handles one user request then dies."""

    agent_name: str
    query_context: QueryContext
    model: str
    _messages: list[ConversationMessage]
    total_usage: UsageSnapshot | None = None

    @property
    def messages(self) -> list[ConversationMessage]:
        """Live view of the agent's run transcript.

        The list is owned by the agent. A run appends the current user prompt
        to any initial history, then appends provider and tool responses.
        """
        return self._messages

    async def run(
        self,
        prompt: str | None,
        *,
        auto_close: bool = True,
    ) -> AsyncIterator[StreamEvent]:
        """Execute one provider request and stream its events.

        Args:
            prompt: User prompt to append before invoking the query loop. Pass
                ``None`` to resume from the current transcript (used by the
                retry path in :func:`run_ephemeral_agent`, which injects its
                own nudge message directly into ``self._messages`` to keep
                role alternation idiomatic).
            auto_close: When ``True`` (default) the API client is released in
                ``finally``. Retry callers pass ``False`` and call
                :meth:`close` once after the final attempt.
        """
        from engine.query.loop import run_query

        if self.total_usage is None:
            self.total_usage = UsageSnapshot()
        try:
            if prompt is not None:
                self._messages = [*self._messages, ConversationMessage.from_user_text(prompt)]
            messages, event_iter = await run_query(
                self.query_context, self._messages
            )
            self._messages = messages
            async for event, usage in event_iter:
                if usage:
                    self.total_usage.input_tokens += usage.input_tokens
                    self.total_usage.output_tokens += usage.output_tokens
                yield event
        finally:
            if auto_close:
                await self.close()

    async def close(self) -> None:
        """Release resources held by the agent's API client."""
        client = self.query_context.api_client
        if hasattr(client, "aclose"):
            await client.aclose()


def _finalize_tool_registry_and_prompt(
    tool_registry: ToolRegistry,
    system_prompt: str,
    *,
    agent_type: str = "agent",
) -> tuple[str, bool]:
    """Finalize runtime tool registry and append terminal-tool guidance.

    This is the shared setup logic used by spawn_agent().
    Terminal tool names are derived from the registry — any tool whose class
    sets ``is_terminal_tool=True`` ends the query loop on success.

    Args:
        tool_registry: The tool registry (mutated in-place to add background tools).
        system_prompt: The base system prompt.
        agent_type: Type label for the agent. Subagents cannot launch
            background tasks or spawn further subagents, so background
            management tools are withheld for ``agent_type="subagent"``.

    Returns:
        Tuple of (updated_system_prompt, has_background_tools).
    """
    from prompt.runtime_prompt import build_termination_condition_prompt
    background_capable_tool_names = [
        t.name
        for t in tool_registry.list_tools()
        if getattr(t, "background", "forbidden") != "forbidden"
    ]
    has_background_tools = (
        bool(background_capable_tool_names) and agent_type != "subagent"
    )
    if has_background_tools:
        tool_registry.register_many(make_background_tools())

    terminal_tool_names = [
        t.name
        for t in tool_registry.list_tools()
        if getattr(t, "is_terminal_tool", False)
    ]
    termination_prompt = build_termination_condition_prompt(
        terminal_tools=terminal_tool_names,
    )
    if termination_prompt:
        system_prompt = system_prompt + "\n\n" + termination_prompt

    return system_prompt, has_background_tools


def _resolve_agent_identity(
    config: RuntimeConfig,
    agent_def: AgentDefinition | None,
) -> tuple[str, str, Any, dict[str, Any] | None]:
    """Resolve the agent's name, model id, API client, and DB model kwargs.

    Returns ``(agent_name, resolved_model, api_client, db_kwargs)``.
    """
    from config.model_config import NoActiveModelError, get_active_model_kwargs

    try:
        db_kwargs = get_active_model_kwargs()
    except NoActiveModelError as exc:
        raise RuntimeError(
            "No active model registration found — configure a model in the "
            "model_registrations DB table before spawning agents."
        ) from exc

    # ``model`` on the agent_def can be an explicit id, an ``"inherit"``
    # sentinel meaning "use the active runtime model", or absent.
    agent_model = agent_def.model if agent_def else None
    if agent_model and agent_model.strip().lower() == "inherit":
        agent_model = None
    resolved_model = agent_model or db_kwargs.get("model")
    if not resolved_model:
        raise RuntimeError("Active model registration has no 'model' id")
    agent_name = agent_def.name if agent_def else resolved_model

    # Subagents get their own httpx pool so concurrent workers do not
    # contend over a shared connection pool.
    needs_fresh_client = bool(agent_def and agent_def.agent_type == "subagent")
    api_client = make_api_client(
        None if needs_fresh_client else config.external_api_client,
        db_kwargs=db_kwargs,
    )
    return agent_name, resolved_model, api_client, db_kwargs


def _build_agent_tool_registry(
    config: RuntimeConfig,
    agent_def: AgentDefinition | None,
    sandbox_id: str | None,
    agent_name: str,
) -> ToolRegistry:
    """Build the tool registry for a spawning agent.

    Registers tools requested by *agent_def* and sandbox tools when
    a sandbox is selected for a default agent.
    """
    tool_registry = create_default_tool_registry()

    tool_ctx = ToolFactoryContext(
        metadata={
            "agent_name": agent_name,
            "role": agent_def.agent_kind.value if agent_def else "",
            "cwd": config.cwd,
            "sandbox_id": sandbox_id or "",
        },
    )
    if agent_def:
        _register_requested_tools(
            tool_registry,
            sorted(set(agent_def.allowed_tools) | set(agent_def.terminals)),
            tool_ctx,
            agent_name,
        )
    elif sandbox_id:
        tool_registry.register_many(make_sandbox_tools())
        logger.info("Registered sandbox tools for sandbox %s", sandbox_id)

    return tool_registry


def _register_requested_tools(
    tool_registry: ToolRegistry,
    tool_names: list[str],
    tool_ctx: ToolFactoryContext,
    agent_name: str,
) -> None:
    """Add explicit tools into the final tool surface."""
    for name in tool_names:
        clean_name = str(name).strip()
        if not clean_name or tool_registry.get(clean_name) is not None:
            continue
        if clean_name in _BACKGROUND_CONTROL_TOOL_NAMES:
            # These are synthesized by _finalize_tool_registry_and_prompt when
            # the registered tools include at least one background-capable
            # tool. They are not ordinary tool factories.
            continue
        if not has_tool(clean_name):
            logger.warning("No tool factory for %r requested by agent %r", clean_name, agent_name)
            continue
        try:
            tool_registry.register(create_tool(clean_name, tool_ctx))
            logger.info("Registered tool %r for agent %r", clean_name, agent_name)
        except Exception:
            logger.warning(
                "Failed to create tool %r for agent %r",
                clean_name,
                agent_name,
                exc_info=True,
            )


def _attach_default_overshoot_rules(
    notification_rules: list[Any],
    *,
    agent_def: AgentDefinition | None,
    tool_registry: ToolRegistry,
) -> None:
    """Append the budget-overflow and missing-terminal reminder rules.

    No-op unless the agent declares a ``tool_call_limit`` AND the tool
    registry exposes at least one terminal-capable tool. Dedupes by
    ``rule.name`` so profiles that customize either rule via
    ``notification_rules`` win.
    """
    if agent_def is None or agent_def.tool_call_limit is None:
        return
    has_terminal_tools = any(
        getattr(t, "is_terminal_tool", False) for t in tool_registry.list_tools()
    )
    if not has_terminal_tools:
        return

    from config import get_central_config
    from notification import (
        make_budget_overflow_reminder,
        make_missing_terminal_reminder,
    )

    existing_names = {getattr(rule, "name", "") for rule in notification_rules}
    engine_cfg = get_central_config().engine
    if "budget_overflow_reminder" not in existing_names:
        notification_rules.append(
            make_budget_overflow_reminder(
                every=engine_cfg.budget_overflow_reminder_every,
            )
        )
    if "missing_terminal_reminder" not in existing_names:
        notification_rules.append(make_missing_terminal_reminder())


def _build_sandbox_context_preparers(
    tool_registry: ToolRegistry,
    sandbox_id: str | None,
) -> list[Any]:
    """Build provider/toolkit-specific context hooks for registered tools."""
    if not sandbox_id:
        return []
    if not any(
        SANDBOX_CONTEXT in getattr(tool, "context_requirements", ())
        for tool in tool_registry.list_tools()
    ):
        return []

    import sandbox.api as sandbox_api

    return [sandbox_api.context_preparer_for(sandbox_id)]


def _build_agent_system_prompt(
    config: RuntimeConfig,
    agent_def: AgentDefinition | None,
    settings: Settings,
) -> str:
    """Return the instruction-only system prompt for *agent_def*.

    The main-role operating contract is prepended at agent-definition load
    time for in-harness main profiles via
    ``agents/profile/main/_main_role_contract.md``. This builder therefore
    just concatenates the runtime base + the agent profile body verbatim.
    """
    parts: list[str] = []
    base = build_runtime_system_prompt(
        settings,
        cwd=config.cwd,
    )
    if base:
        parts.append(base)
    if agent_def is not None and agent_def.system_prompt:
        parts.append(agent_def.system_prompt)
    return "\n\n".join(part for part in parts if part.strip())


def spawn_agent(
    config: RuntimeConfig,
    messages: list[ConversationMessage],
    *,
    agent_def: AgentDefinition | None = None,
    sandbox_id: str | None = None,
) -> EphemeralAgent:
    """Spawn a fresh ephemeral agent with the given message history.

    If *agent_def* is provided, its fields customize the runtime defaults:
    - ``model`` overrides the active model
    - ``system_prompt`` is appended after the runtime system prompt
    - ``allowed_tools`` + ``terminals`` declare the tool surface
    - ``tool_call_limit`` caps tool dispatches for the ephemeral run
    """
    from pathlib import Path

    from engine.query.context import QueryContext
    settings = config.resolve_settings()

    agent_name, resolved_model, api_client, db_kwargs = _resolve_agent_identity(
        config, agent_def
    )
    max_tokens = int((db_kwargs or {}).get("max_tokens") or 16384)

    tool_registry = _build_agent_tool_registry(
        config, agent_def, sandbox_id, agent_name
    )

    base_system_prompt = _build_agent_system_prompt(config, agent_def, settings)

    system_prompt, has_background_tools = _finalize_tool_registry_and_prompt(
        tool_registry,
        base_system_prompt,
        agent_type=agent_def.agent_type if agent_def else "agent",
    )

    tool_call_limit = agent_def.tool_call_limit if agent_def else None
    max_tolerance = (
        agent_def.max_tolerance_after_max_tool_call if agent_def else None
    )

    # Plumb runtime_config through tool_metadata so tools (e.g. run_subagent)
    # that need to spawn nested agents can reach it without a Protocol layer.
    initial_tool_metadata = ExecutionMetadata(
        runtime_config=config,
        sandbox_id=sandbox_id or "",
        agent_name=agent_name,
        context_preparers=_build_sandbox_context_preparers(tool_registry, sandbox_id),
    )
    if agent_def is not None:
        initial_tool_metadata["agent_type"] = agent_def.agent_type
        initial_tool_metadata["role"] = agent_def.agent_kind.value

    notification_rules = list(agent_def.notification_rules) if agent_def else []
    if agent_def and agent_def.notification_triggers:
        notification_rules.extend(
            resolve_harness_notification_triggers(agent_def.notification_triggers)
        )
    _attach_default_overshoot_rules(
        notification_rules,
        agent_def=agent_def,
        tool_registry=tool_registry,
    )

    query_context = QueryContext(
        api_client=api_client,
        tool_registry=tool_registry,
        cwd=Path(config.cwd),
        model=resolved_model,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        tool_call_limit=tool_call_limit,
        max_tolerance_after_max_tool_call=max_tolerance,
        tool_metadata=initial_tool_metadata,
        enable_background_tasks=has_background_tools,
        agent_name=agent_name,
        notification_rules=cast("list[NotificationRule]", notification_rules),
    )

    return EphemeralAgent(
        agent_name=agent_name,
        query_context=query_context,
        model=resolved_model,
        _messages=messages if messages else [],
    )
