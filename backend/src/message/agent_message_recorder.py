"""Append completed agent stream steps to a JSONL file."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from message.stream_events import (
    AssistantMessageComplete,
    AssistantTextDelta,
    StreamEvent,
    ThinkingDelta,
    ToolExecutionCompleted,
)
from prompt.message_recorder import append_prompt_report_event

logger = logging.getLogger(__name__)


class AgentMessageJsonlRecorder:
    """Record completed thinking/text/tool steps as append-only JSONL.

    Text and thinking arrive as deltas, so they are buffered per agent lane and
    flushed when that lane starts another step or completes an assistant message.
    Tool calls are read from completed assistant messages; tool results are
    already complete stream events and are appended immediately.
    """

    def __init__(
        self,
        path: str | Path | None,
        *,
        base_event: Mapping[str, Any] | None = None,
    ) -> None:
        self._path = Path(path).expanduser() if path else None
        self._base_event = dict(base_event or {})
        self._seq = 0
        self._initial_messages_recorded = False
        self._thinking: dict[tuple[str, str], list[str]] = {}
        self._text: dict[tuple[str, str], list[str]] = {}

    @property
    def path(self) -> Path | None:
        return self._path

    def emit(self, event: StreamEvent) -> None:
        """Observe one stream event and append completed message steps."""
        if isinstance(event, ThinkingDelta):
            self._flush_text(event.agent_name, event.run_id)
            self._thinking_for(event.agent_name, event.run_id).append(event.text)
            return

        if isinstance(event, AssistantTextDelta):
            self._flush_thinking(event.agent_name, event.run_id)
            self._text_for(event.agent_name, event.run_id).append(event.text)
            return

        agent_name = str(getattr(event, "agent_name", "") or "")
        run_id = str(getattr(event, "run_id", "") or "")
        self._flush_lane(agent_name, run_id)

        if isinstance(event, AssistantMessageComplete):
            self._record(
                "assistant_message",
                agent_name=event.agent_name,
                run_id=event.run_id,
                role="assistant",
                content=[
                    block.model_dump(mode="json")
                    for block in event.message.content
                ],
            )
            for tool_use in getattr(event.message, "tool_uses", []):
                self._record(
                    "tool_call",
                    agent_name=event.agent_name,
                    run_id=event.run_id,
                    role="assistant",
                    content=[
                        {
                            "type": "tool_use",
                            "id": tool_use.id,
                            "name": tool_use.name,
                            "input": tool_use.input,
                        }
                    ],
                    tool_name=tool_use.name,
                    tool_id=tool_use.id,
                )
        elif isinstance(event, ToolExecutionCompleted):
            self._record(
                "tool_result",
                agent_name=event.agent_name,
                run_id=event.run_id,
                role="user",
                content=[
                    {
                        "type": "tool_result",
                        "tool_use_id": event.tool_id,
                        "content": event.output,
                        "is_error": event.is_error,
                        "metadata": dict(event.metadata or {}),
                        "does_terminate": event.does_terminate,
                    }
                ],
                tool_name=event.tool_name,
                tool_id=event.tool_id,
                is_error=event.is_error,
                does_terminate=event.does_terminate,
            )

    def record_initial_messages(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        agent_name: str,
        run_id: str,
    ) -> None:
        """Append the system and initial user messages once.

        The live engine sends the system prompt outside the provider
        ``messages`` array, but the benchmark transcript stores it explicitly
        so ``message.jsonl`` can be replayed as a full agent conversation.
        """
        if self._initial_messages_recorded:
            return
        self._initial_messages_recorded = True
        if system_prompt.strip():
            self._record(
                "system_message",
                agent_name=agent_name,
                run_id=run_id,
                role="system",
                content=[{"type": "text", "text": system_prompt}],
            )
        self._record(
            "user_message",
            agent_name=agent_name,
            run_id=run_id,
            role="user",
            content=[{"type": "text", "text": user_prompt}],
        )

    def flush(self) -> None:
        """Append any buffered text/thinking still waiting on a boundary."""
        for agent_name, run_id in list(self._thinking):
            self._flush_thinking(agent_name, run_id)
        for agent_name, run_id in list(self._text):
            self._flush_text(agent_name, run_id)

    def _thinking_for(self, agent_name: str, run_id: str) -> list[str]:
        return self._thinking.setdefault((agent_name, run_id), [])

    def _text_for(self, agent_name: str, run_id: str) -> list[str]:
        return self._text.setdefault((agent_name, run_id), [])

    def _flush_lane(self, agent_name: str, run_id: str) -> None:
        self._flush_thinking(agent_name, run_id)
        self._flush_text(agent_name, run_id)

    def _flush_thinking(self, agent_name: str, run_id: str) -> None:
        key = (agent_name, run_id)
        chunks = self._thinking.pop(key, [])
        text = "".join(chunks)
        if not text:
            return
        self._record(
            "thinking",
            agent_name=agent_name,
            run_id=run_id,
            role="assistant",
            content=[{"type": "thinking", "text": text}],
            text=text,
        )

    def _flush_text(self, agent_name: str, run_id: str) -> None:
        key = (agent_name, run_id)
        chunks = self._text.pop(key, [])
        text = "".join(chunks)
        if not text:
            return
        self._record(
            "text",
            agent_name=agent_name,
            run_id=run_id,
            role="assistant",
            content=[{"type": "text", "text": text}],
            text=text,
        )

    def _record(
        self,
        step_type: str,
        *,
        agent_name: str,
        run_id: str,
        role: str,
        content: list[dict[str, Any]],
        **extra: Any,
    ) -> None:
        if self._path is None:
            return
        self._seq += 1
        event = {
            **self._base_event,
            "event": "agent_step",
            "seq": self._seq,
            "step_type": step_type,
            "agent_name": agent_name,
            "run_id": run_id,
            "role": role,
            "content": content,
            **extra,
        }
        try:
            append_prompt_report_event(self._path, event)
        except Exception:
            logger.debug("agent message append failed", exc_info=True)


__all__ = ["AgentMessageJsonlRecorder"]
