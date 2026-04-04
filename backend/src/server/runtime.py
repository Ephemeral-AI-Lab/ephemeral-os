"""Ephemeral agent runtime for EphemeralOS.

Each user request spawns a fresh agent (engine + API client + tools) that
inherits session history, executes one complete tool-call loop, then dies.
No in-memory state persists between requests — only durable config and
the session ID survive.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable

from ephemeralos.models.clients.anthropic import AnthropicApiClient
from ephemeralos.models.clients.openai_compat import OpenAICompatibleClient
from ephemeralos.models.types import SupportsStreamingMessages
from ephemeralos.config import Settings, load_settings
from ephemeralos.engine import QueryEngine
from ephemeralos.engine.messages import ConversationMessage
from ephemeralos.engine.stream_events import (
    AssistantTurnComplete,
    StreamEvent,
)
from ephemeralos.hooks import HookEvent, HookExecutionContext, HookExecutor, load_hook_registry
from ephemeralos.prompts import build_runtime_system_prompt
from ephemeralos.services.session_storage import save_session_snapshot
from ephemeralos.tools import ToolRegistry
from ephemeralos.tools import create_default_tool_registry

logger = logging.getLogger(__name__)

SystemPrinter = Callable[[str], Awaitable[None]]
StreamRenderer = Callable[[StreamEvent], Awaitable[None]]
ClearHandler = Callable[[], Awaitable[None]]


# ---------------------------------------------------------------------------
# SessionConfig — durable configuration that survives across requests
# ---------------------------------------------------------------------------


@dataclass
class SessionConfig:
    """Durable session configuration — persists across ephemeral agents."""

    cwd: str
    session_id: str
    # CLI overrides (take precedence over settings.json)
    model_override: str | None = None
    base_url_override: str | None = None
    system_prompt_override: str | None = None
    api_key_override: str | None = None
    api_format_override: str | None = None
    # If an external API client was injected, store it for reuse
    external_api_client: SupportsStreamingMessages | None = None
    # Messages to restore on first spawn (from session restore)
    _initial_messages: list[dict] | None = field(default=None, repr=False)

    def resolve_settings(self) -> Settings:
        """Load settings and apply any CLI overrides."""
        return load_settings().merge_cli_overrides(
            model=self.model_override,
            base_url=self.base_url_override,
            system_prompt=self.system_prompt_override,
            api_key=self.api_key_override,
            api_format=self.api_format_override,
        )


# ---------------------------------------------------------------------------
# EphemeralAgent — short-lived runtime for one request
# ---------------------------------------------------------------------------


@dataclass
class EphemeralAgent:
    """A short-lived agent that handles one user request then dies."""

    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    hook_executor: HookExecutor
    engine: QueryEngine
    settings: Settings

    async def run(self, prompt: str) -> AsyncIterator[StreamEvent]:
        """Execute one complete tool-call loop for the given prompt."""
        async for event in self.engine.submit_message(prompt):
            yield event


def spawn_agent(
    config: SessionConfig,
    messages: list[ConversationMessage],
    *,
    latest_user_prompt: str | None = None,
) -> EphemeralAgent:
    """Spawn a fresh ephemeral agent with the given session history.

    The agent gets its own API client, tool registry, hook executor,
    and query engine. It inherits *messages* as its conversation history.
    """
    settings = config.resolve_settings()

    if config.external_api_client is not None:
        api_client = config.external_api_client
    elif settings.api_format == "openai":
        api_client = OpenAICompatibleClient(
            api_key=settings.resolve_api_key(),
            base_url=settings.base_url,
        )
    else:
        api_client = AnthropicApiClient(
            api_key=settings.resolve_api_key(),
            base_url=settings.base_url,
        )

    tool_registry = create_default_tool_registry()
    hook_executor = HookExecutor(
        load_hook_registry(settings, []),
        HookExecutionContext(
            cwd=Path(config.cwd).resolve(),
            api_client=api_client,
            default_model=settings.model,
        ),
    )
    engine = QueryEngine(
        api_client=api_client,
        tool_registry=tool_registry,
        cwd=config.cwd,
        model=settings.model,
        system_prompt=build_runtime_system_prompt(
            settings, cwd=config.cwd, latest_user_prompt=latest_user_prompt,
        ),
        max_tokens=settings.max_tokens,
        hook_executor=hook_executor,
    )
    if messages:
        engine.load_messages(messages)

    return EphemeralAgent(
        api_client=api_client,
        tool_registry=tool_registry,
        hook_executor=hook_executor,
        engine=engine,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# Session config builder (replaces old build_runtime)
# ---------------------------------------------------------------------------


async def build_session_config(
    *,
    prompt: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    system_prompt: str | None = None,
    api_key: str | None = None,
    api_format: str | None = None,
    api_client: SupportsStreamingMessages | None = None,
    restore_messages: list[dict] | None = None,
) -> SessionConfig:
    """Build durable session config. Called once at server startup."""
    from uuid import uuid4

    cwd = str(Path.cwd())
    config = SessionConfig(
        cwd=cwd,
        session_id=uuid4().hex[:12],
        model_override=model,
        base_url_override=base_url,
        system_prompt_override=system_prompt,
        api_key_override=api_key,
        api_format_override=api_format,
        external_api_client=api_client,
        _initial_messages=restore_messages,
    )

    # Run session-start hooks with a temporary agent
    settings = config.resolve_settings()
    temp_client: SupportsStreamingMessages
    if api_client is not None:
        temp_client = api_client
    elif settings.api_format == "openai":
        temp_client = OpenAICompatibleClient(
            api_key=settings.resolve_api_key(),
            base_url=settings.base_url,
        )
    else:
        temp_client = AnthropicApiClient(
            api_key=settings.resolve_api_key(),
            base_url=settings.base_url,
        )
    hook_executor = HookExecutor(
        load_hook_registry(settings, []),
        HookExecutionContext(
            cwd=Path(cwd).resolve(),
            api_client=temp_client,
            default_model=settings.model,
        ),
    )
    await hook_executor.execute(
        HookEvent.SESSION_START,
        {"cwd": cwd, "event": HookEvent.SESSION_START.value},
    )

    return config


async def close_session(config: SessionConfig) -> None:
    """Run session-end hooks. Called once at server shutdown."""
    settings = config.resolve_settings()
    temp_client: SupportsStreamingMessages
    if config.external_api_client is not None:
        temp_client = config.external_api_client
    elif settings.api_format == "openai":
        temp_client = OpenAICompatibleClient(
            api_key=settings.resolve_api_key(),
            base_url=settings.base_url,
        )
    else:
        temp_client = AnthropicApiClient(
            api_key=settings.resolve_api_key(),
            base_url=settings.base_url,
        )
    hook_executor = HookExecutor(
        load_hook_registry(settings, []),
        HookExecutionContext(
            cwd=Path(config.cwd).resolve(),
            api_client=temp_client,
            default_model=settings.model,
        ),
    )
    await hook_executor.execute(
        HookEvent.SESSION_END,
        {"cwd": config.cwd, "event": HookEvent.SESSION_END.value},
    )


# ---------------------------------------------------------------------------
# handle_line — spawn, run, die
# ---------------------------------------------------------------------------


def _load_session_messages(config: SessionConfig) -> list[ConversationMessage]:
    """Load conversation history from DB or initial restore messages."""
    from ephemeralos.server.app_factory import session_store

    db_available = session_store._session_factory is not None

    if db_available:
        record = session_store.get(config.session_id)
        if record and record.message_history:
            try:
                return [
                    ConversationMessage.model_validate(m)
                    for m in record.message_history
                ]
            except Exception:
                logger.debug("Failed to load messages from DB", exc_info=True)

    # Fallback: initial restore messages from session start
    if config._initial_messages:
        try:
            msgs = [
                ConversationMessage.model_validate(m)
                for m in config._initial_messages
            ]
            config._initial_messages = None  # only use once
            return msgs
        except Exception:
            logger.debug("Failed to load initial restore messages", exc_info=True)

    return []


async def handle_line(
    config: SessionConfig,
    line: str,
    *,
    print_system: SystemPrinter,
    render_event: StreamRenderer,
    clear_output: ClearHandler,
) -> bool:
    """Spawn an ephemeral agent, run it, let it die.

    1. Load conversation history from persistence
    2. Spawn a fresh agent with that history
    3. Execute the user's request (full tool-call loop)
    4. Record the agent run + token usage
    5. Save updated history back to persistence
    6. Agent goes out of scope — dies
    """
    # 1. Load history
    messages = _load_session_messages(config)

    # 2. Spawn ephemeral agent
    agent = spawn_agent(config, messages, latest_user_prompt=line)

    # 3. Agent run logging
    from ephemeralos.server.app_factory import agent_run_store, usage_store

    run_id: str | None = None
    db_available = agent_run_store._session_factory is not None
    if db_available:
        from uuid import uuid4

        run_id = uuid4().hex[:12]
        try:
            agent_run_store.create_run(
                run_id=run_id,
                session_id=config.session_id,
                agent_name=agent.settings.model,
                input_query=line[:2000],
            )
        except Exception:
            logger.debug("Failed to create agent run record", exc_info=True)
            run_id = None

    event_count = 0
    run_error: str | None = None
    usage_snapshot = None

    try:
        async for event in agent.run(line):
            event_count += 1
            if isinstance(event, AssistantTurnComplete):
                usage_snapshot = event.usage
            await render_event(event)
    except Exception as exc:
        run_error = str(exc)
        raise
    finally:
        # 4. Record run + usage
        if run_id and db_available:
            try:
                agent_run_store.finish_run(
                    run_id,
                    status="failed" if run_error else "completed",
                    error=run_error,
                    event_count=event_count,
                )
            except Exception:
                logger.debug("Failed to finish agent run record", exc_info=True)

        if db_available and usage_snapshot and (
            usage_snapshot.input_tokens or usage_snapshot.output_tokens
        ):
            try:
                usage_store.record(
                    session_id=config.session_id,
                    agent_name=agent.settings.model,
                    model_id=agent.settings.model,
                    prompt_tokens=usage_snapshot.input_tokens,
                    completion_tokens=usage_snapshot.output_tokens,
                )
            except Exception:
                logger.debug("Failed to record token usage", exc_info=True)

    # 5. Save updated history
    save_session_snapshot(
        cwd=config.cwd,
        model=agent.settings.model,
        system_prompt=build_runtime_system_prompt(
            agent.settings, cwd=config.cwd, latest_user_prompt=line,
        ),
        messages=agent.engine.messages,
        usage=agent.engine.total_usage,
        session_id=config.session_id,
    )

    # 6. Agent goes out of scope here — ephemeral lifecycle complete
    return True
