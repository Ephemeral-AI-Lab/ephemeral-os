"""Append completed agent conversation messages to a JSONL file."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from audit.jsonl import append_jsonl_event
from message.message import (
    Message,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from message.events import (
    AssistantMessageCompleteEvent,
    AssistantTextDeltaEvent,
    StreamEvent,
    ThinkingDeltaEvent,
    ToolExecutionCompletedEvent,
)

logger = logging.getLogger(__name__)

# Advisor-approval calls (the advisor-gated-terminal mechanism) are captured as
# per-call ``ExecutionMetadata`` and must NOT leak into the persisted transcript
# audit artifact; they stay in the LIVE conversation so the gate/prehook can
# still see them. Mirrors ``tools._names.ASK_ADVISOR_TOOL_NAME`` (kept local to
# avoid a ``message`` -> ``tools`` import inversion).
_ADVISOR_APPROVAL_TOOL_NAME = "ask_advisor"


class AgentMessageJsonlRecorder:
    """Record completed conversation messages as append-only JSONL.

    Text and thinking arrive as deltas, so they are buffered per agent lane and
    flushed into assistant messages when that lane starts another message or
    completes an assistant message. Tool calls stay inside assistant message
    content, and tool results are recorded as user messages.
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
        """Observe one stream event and append completed messages.

        Thinking and text deltas are buffered per (agent, run) lane and only
        materialized when ``AssistantMessageCompleteEvent`` arrives: if that
        message already contains the corresponding block, the buffer is
        dropped (the complete message is the canonical row); otherwise the
        buffer is flushed as its own row. Buffers that survive without a
        completion event — e.g. mid-stream cancellation — are drained by
        :meth:`flush`.
        """
        if isinstance(event, ThinkingDeltaEvent):
            self._thinking_for(event.agent_name, event.agent_run_id).append(event.text)
            return

        if isinstance(event, AssistantTextDeltaEvent):
            self._text_for(event.agent_name, event.agent_run_id).append(event.text)
            return

        agent_name = str(getattr(event, "agent_name", "") or "")
        run_id = str(getattr(event, "agent_run_id", "") or "")

        if isinstance(event, AssistantMessageCompleteEvent):
            block_types = {type(b).__name__ for b in event.message.content}
            if "ThinkingBlock" in block_types:
                self._thinking.pop((agent_name, run_id), None)
            else:
                self._flush_thinking(agent_name, run_id)
            if "TextBlock" in block_types:
                self._text.pop((agent_name, run_id), None)
            else:
                self._flush_text(agent_name, run_id)
            # Drop advisor-approval tool_use blocks (gated-terminal approval is
            # metadata, not a durable transcript row). If that empties the
            # assistant turn, skip recording it entirely.
            content = [
                block
                for block in event.message.content
                if not (
                    isinstance(block, ToolUseBlock)
                    and block.name == _ADVISOR_APPROVAL_TOOL_NAME
                )
            ]
            if content:
                self._record(
                    agent_name=event.agent_name,
                    run_id=event.agent_run_id,
                    message=event.message.model_copy(update={"content": content}),
                )
            return

        if isinstance(event, ToolExecutionCompletedEvent):
            # Exclude the paired advisor-approval result on the same tool
            # identity as its call, so the transcript never holds an orphan
            # tool_result (and the helper_role="advisor" result never leaks).
            if event.tool_name == _ADVISOR_APPROVAL_TOOL_NAME:
                return
            message = Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id=event.tool_use_id,
                        content=event.output,
                        is_error=event.is_error,
                        metadata=dict(event.metadata or {}),
                        is_terminal=event.is_terminal,
                    )
                ],
            )
            self._record(
                agent_name=event.agent_name,
                run_id=event.agent_run_id,
                message=message,
                tool_name=event.tool_name,
                tool_use_id=event.tool_use_id,
                is_error=event.is_error,
                is_terminal=event.is_terminal,
            )

    def record_initial_messages(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        agent_name: str,
        run_id: str,
        seeded_initial_messages: list[Message] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Append the system and initial user messages once.

        The live engine sends the system prompt outside the provider
        ``messages`` array, but the benchmark transcript stores it explicitly
        so ``message.jsonl`` can be replayed as a full agent conversation.

        For the two-user-message launch shape (see
        ``workflow/attempt/launch.py``), ``seeded_initial_messages``
        carries the messages the launcher passed via the ``initial_messages``
        kwarg (i.e. the composer's instruction). They are written
        BETWEEN the system row and the task-guidance ``user_prompt`` row so
        on-disk transcripts hold the full three-message launch shape.
        """
        if self._initial_messages_recorded:
            return
        self._initial_messages_recorded = True
        initial_metadata = dict(metadata or {})
        if system_prompt.strip():
            self._record_message(
                agent_name=agent_name,
                run_id=run_id,
                role="system",
                content=[{"type": "text", "text": system_prompt}],
                **initial_metadata,
            )
        for seeded in seeded_initial_messages or []:
            self._record_message(
                agent_name=agent_name,
                run_id=run_id,
                role=seeded.role,
                content=[
                    block.model_dump(mode="json") for block in seeded.content
                ],
                **initial_metadata,
            )
        message = Message.from_user_text(user_prompt)
        self._record_message(
            agent_name=agent_name,
            run_id=run_id,
            role=message.role,
            content=[
                block.model_dump(mode="json") for block in message.content
            ],
            **initial_metadata,
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
            agent_name=agent_name,
            run_id=run_id,
            message=Message(
                role="assistant", content=[ThinkingBlock(text=text)]
            ),
        )

    def _flush_text(self, agent_name: str, run_id: str) -> None:
        key = (agent_name, run_id)
        chunks = self._text.pop(key, [])
        text = "".join(chunks)
        if not text:
            return
        self._record(
            agent_name=agent_name,
            run_id=run_id,
            message=Message(
                role="assistant", content=[TextBlock(text=text)]
            ),
        )

    def _record(
        self,
        *,
        agent_name: str,
        run_id: str,
        message: Message,
        **extra: Any,
    ) -> None:
        self._record_message(
            agent_name=agent_name,
            run_id=run_id,
            role=message.role,
            content=[
                block.model_dump(mode="json") for block in message.content
            ],
            **extra,
        )

    def _record_message(
        self,
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
        metadata = {
            **self._base_event,
            "seq": self._seq,
            "agent_name": agent_name,
            "agent_run_id": run_id,
            **extra,
        }
        event = {
            "role": role,
            "content": content,
            "metadata": metadata,
        }
        try:
            append_jsonl_event(self._path, event)
        except Exception:
            logger.debug("agent message append failed", exc_info=True)


_BY_AGENT_RUN: dict[tuple[str, str], "AgentMessageJsonlRecorder"] = {}


def register_recorder(
    agent_name: str, run_id: str, recorder: "AgentMessageJsonlRecorder"
) -> None:
    """Make ``recorder`` discoverable via ``recorder_for_run``.

    Lets the LLM-request layer find the per-task message recorder without a
    direct handle to the audit recorder. The audit recorder is the only
    populator; consumers must not mutate the registry directly.
    """
    if run_id:
        _BY_AGENT_RUN[(agent_name, run_id)] = recorder


def clear_recorder(agent_name: str, run_id: str) -> None:
    _BY_AGENT_RUN.pop((agent_name, run_id), None)


def recorder_for_run(
    agent_name: str, run_id: str
) -> "AgentMessageJsonlRecorder | None":
    return _BY_AGENT_RUN.get((agent_name, run_id)) if run_id else None


__all__ = [
    "AgentMessageJsonlRecorder",
    "register_recorder",
    "clear_recorder",
    "recorder_for_run",
]
