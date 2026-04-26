"""Core API routes — health, state, chat, config, sessions."""

from __future__ import annotations

import logging
import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from collections.abc import Awaitable

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from agents.types import AgentDefinition
from providers.provider import detect_provider, auth_status
from message.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    StreamEvent,
    SystemNotification,
    ThinkingDelta,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from server.protocol import BackendEvent, TranscriptItem
from tools.core.base import ExecutionMetadata

if TYPE_CHECKING:
    from server.app_factory import SessionConfig, SessionState

logger = logging.getLogger(__name__)

AgentStreamEmitter = Callable[[StreamEvent], Awaitable[None]]

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    line: str
    agent_name: str | None = None
    sandbox_id: str | None = None


class ConfigRequest(BaseModel):
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


# ---------------------------------------------------------------------------
# Ephemeral agent lifecycle — spawn, run, persist, die
# ---------------------------------------------------------------------------


async def execute_ephemeral_agent_run(
    config: SessionConfig,
    input_message: str,
    *,
    on_agent_event: AgentStreamEmitter,
    agent_def: AgentDefinition | None = None,
    sandbox_id: str | None = None,
    extra_tool_metadata: ExecutionMetadata | dict[str, Any] | None = None,
) -> bool:
    """Spawn an ephemeral agent, run it, persist its run + session, let it die.

    Thin wrapper around :func:`engine.runtime.lifecycle.run_ephemeral_agent`
    that re-raises run errors to preserve the existing chat-route contract.
    """
    from engine.runtime.lifecycle import run_ephemeral_agent

    result = await run_ephemeral_agent(
        config,
        input_message,
        agent_def=agent_def,
        sandbox_id=sandbox_id,
        persist_session=True,
        on_event=on_agent_event,
        extra_tool_metadata=extra_tool_metadata,
    )
    logger.info(
        "Agent %r finished (events=%d, status=%s)",
        result.agent_name,
        result.event_count,
        result.status,
    )
    if result.status == "failed" and result.error:
        raise RuntimeError(result.error)
    return True


# ---------------------------------------------------------------------------
# Router factory — receives get_session callable from web_server
# ---------------------------------------------------------------------------


def create_core_router(get_session: Callable[[], SessionState]) -> APIRouter:
    """Build the core API router."""
    router = APIRouter(prefix="/api")

    @router.get("/health")
    async def health():
        return {"status": "ok", "service": "ephemeralos"}

    @router.get("/state")
    async def get_state():
        session = get_session()
        if session.config is None:
            raise HTTPException(status_code=503, detail="Session not ready")
        settings = session.current_settings()
        from config.model_config import try_get_active_model_kwargs

        active_kwargs = try_get_active_model_kwargs() or {}
        provider_info = detect_provider()
        app_state = {
            "model": active_kwargs.get("model", ""),
            "cwd": session.cwd,
            "provider": provider_info.name,
            "auth_status": "authorized",
            "base_url": active_kwargs.get("base_url") or "",
            "theme": settings.theme,
            "vim_enabled": False,
            "voice_enabled": False,
            "voice_available": False,
            "voice_reason": "",
            "fast_mode": settings.fast_mode,
            "effort": settings.effort,
            "passes": settings.passes,
            "bridge_sessions": 0,
            "output_style": "verbose" if settings.verbose else "normal",
        }
        ready = BackendEvent.ready(
            tools=session._tool_snapshots(),
            state=app_state,
        )
        return JSONResponse(content=json.loads(ready.model_dump_json()))

    @router.post("/chat")
    async def chat(req: ChatRequest):
        session = get_session()
        if session.config is None:
            raise HTTPException(status_code=503, detail="Session not ready")

        async with session._busy_lock:
            if session.busy:
                return JSONResponse(status_code=409, content={"error": "Session is busy"})
            session.busy = True

        queue: asyncio.Queue[BackendEvent | None] = asyncio.Queue()
        session.set_event_queue(queue)

        async def process() -> None:
            try:
                config = session.config
                if config is None:
                    raise RuntimeError("Session not ready")

                await session.emit(
                    BackendEvent(
                        type="transcript_item",
                        item=TranscriptItem(role="user", text=req.line),
                    )
                )

                async def _on_system_notification(message: str) -> None:
                    await session.emit(
                        BackendEvent(
                            type="transcript_item",
                            item=TranscriptItem(role="system", text=message),
                        )
                    )

                def _stream_event_to_backend(event: StreamEvent) -> BackendEvent | None:
                    if isinstance(event, ThinkingDelta):
                        return BackendEvent(type="thinking_delta", message=event.text)
                    if isinstance(event, AssistantTextDelta):
                        return BackendEvent(type="assistant_delta", message=event.text)
                    if isinstance(event, AssistantTurnComplete):
                        text = event.message.text.strip()
                        return BackendEvent(
                            type="assistant_complete",
                            message=text,
                            item=TranscriptItem(role="assistant", text=text),
                        )
                    if isinstance(event, ToolExecutionStarted):
                        return BackendEvent(
                            type="tool_started",
                            tool_name=event.tool_name,
                            tool_input=event.tool_input,
                            item=TranscriptItem(
                                role="tool",
                                text=f"{event.tool_name} {json.dumps(event.tool_input, ensure_ascii=True)}",
                                tool_name=event.tool_name,
                                tool_input=event.tool_input,
                            ),
                        )
                    if isinstance(event, ToolExecutionCompleted):
                        return BackendEvent(
                            type="tool_completed",
                            tool_name=event.tool_name,
                            output=event.output,
                            is_error=event.is_error,
                            item=TranscriptItem(
                                role="tool_result",
                                text=event.output,
                                tool_name=event.tool_name,
                                is_error=event.is_error,
                            ),
                        )
                    if isinstance(event, ToolExecutionCancelled):
                        return BackendEvent(
                            type="tool_cancelled",
                            tool_name=event.tool_name,
                            cancel_reason=event.reason,
                            item=TranscriptItem(
                                role="tool_result",
                                text=f"[CANCELLED] {event.tool_name}: {event.reason}",
                                tool_name=event.tool_name,
                                is_error=True,
                            ),
                        )
                    return None

                async def _on_agent_event(event: StreamEvent) -> None:
                    if isinstance(event, SystemNotification):
                        await _on_system_notification(event.text)
                        return
                    backend_event = _stream_event_to_backend(event)
                    if backend_event is not None:
                        await session.emit(backend_event)

                # Route every user query through the per-session TaskCenter
                # (US-009 — the new phased executor-evaluator tree). The
                # TaskCenter spawns a root executor for the user's prompt,
                # drives any phased subtasks it submits, and runs an evaluator
                # before closing the root.
                if session.task_center is not None:
                    session.task_center.set_event_callback(_on_agent_event)
                    try:
                        root = await session.task_center.run_query(
                            req.line,
                            sandbox_id=req.sandbox_id,
                        )
                    finally:
                        session.task_center.set_event_callback(None)
                    # Surface the final root summary as a transcript item so
                    # the user sees the closure text even if no child emitted
                    # it as an AssistantTurnComplete with the same content.
                    if root.summary:
                        await session.emit(
                            BackendEvent(
                                type="transcript_item",
                                item=TranscriptItem(
                                    role="assistant",
                                    text=root.summary,
                                ),
                            )
                        )
                else:
                    # Legacy fallback when TaskCenter is unavailable (tests,
                    # uninitialized sessions). Kept so existing chat tests
                    # don't break before they're rewritten.
                    agent_def = None
                    if req.agent_name:
                        from agents.registry import get_definition

                        agent_def = get_definition(req.agent_name)
                        if agent_def is None:
                            await _on_system_notification(
                                f"Agent '{req.agent_name}' not found — using default"
                            )
                    await execute_ephemeral_agent_run(
                        config,
                        req.line,
                        on_agent_event=_on_agent_event,
                        agent_def=agent_def,
                        sandbox_id=req.sandbox_id,
                    )
                await session.emit(BackendEvent(type="line_complete"))
            except Exception as exc:
                await session.emit(BackendEvent(type="error", message=f"Processing error: {exc}"))
            finally:
                await queue.put(None)
                session.busy = False
                session.set_event_queue(None)

        task = asyncio.create_task(process())

        async def event_generator():
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {event.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"
            except asyncio.CancelledError:
                task.cancel()

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/config")
    async def update_config(req: ConfigRequest):
        session = get_session()
        if session.config is None:
            raise HTTPException(status_code=503, detail="Session not ready")

        from server.app_factory import model_store

        if not model_store.is_available:
            raise HTTPException(status_code=503, detail="Model store not ready")

        active = model_store.get_active(redact=False)
        if active is None:
            raise HTTPException(
                status_code=400,
                detail="No active model registration to update",
            )

        kwargs = dict(active.get("kwargs") or {})
        changed = False
        if req.model is not None:
            kwargs["model"] = req.model
            changed = True
        if req.base_url is not None:
            kwargs["base_url"] = req.base_url
            changed = True
        if req.api_key is not None:
            kwargs["api_key"] = req.api_key
            changed = True

        if not changed:
            return JSONResponse(content={"changed": False})

        model_store.register(
            key=active["key"],
            label=active.get("label") or active["key"],
            class_path=active.get("class_path") or "",
            kwargs=kwargs,
            activate=True,
        )

        provider = detect_provider()
        return JSONResponse(
            content={
                "changed": True,
                "model": kwargs.get("model", ""),
                "provider": provider.name,
                "auth_status": auth_status(),
                "base_url": kwargs.get("base_url") or "",
            }
        )

    @router.get("/sessions")
    async def list_sessions():
        session = get_session()
        if session.config is None:
            raise HTTPException(status_code=503, detail="Session not ready")
        from server.app_factory import session_store
        import time as _time

        if session_store._session_factory is None:
            return JSONResponse(content={"sessions": []})

        snapshots = session_store.list_sessions(cwd=session.cwd, limit=10)
        options = []
        for s in snapshots:
            ts = _time.strftime("%m/%d %H:%M", _time.localtime(s["created_at"]))
            summary = s.get("summary", "")[:50] or "(no summary)"
            options.append(
                {
                    "value": s["session_id"],
                    "label": f"{ts}  {s['message_count']}msg  {summary}",
                }
            )
        return JSONResponse(content={"sessions": options})

    return router
