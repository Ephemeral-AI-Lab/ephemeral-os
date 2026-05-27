"""Small prompt-report recorder with per-context sequencing."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from collections.abc import Mapping

from audit.jsonl import append_jsonl_event

if TYPE_CHECKING:
    from engine.query.context import QueryContext
    from message.message import Message, ToolResultBlock
    from providers.types import UsageSnapshot

logger = logging.getLogger(__name__)


class PromptReportRecorder:
    """Append prompt-report events with a monotonically increasing sequence."""

    def __init__(
        self,
        path: str | Path | None,
        *,
        base_event: Mapping[str, Any] | None = None,
    ) -> None:
        self._path = path
        self._base_event = dict(base_event or {})
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def record(self, event: Mapping[str, Any]) -> None:
        if not self._path:
            return
        try:
            append_jsonl_event(
                self._path,
                {
                    **self._base_event,
                    **dict(event),
                },
            )
        except Exception:
            logger.debug("prompt report append failed", exc_info=True)

    def record_llm_request(
        self,
        *,
        seq: int,
        system_prompt: str,
        messages: list[Any],
        tools: list[dict[str, Any]],
    ) -> None:
        self.record(
            {
                "event": "llm_request",
                "seq": seq,
                "system_prompt": system_prompt,
                "messages": [m.model_dump(mode="json") for m in messages],
                "tools": tools,
            }
        )

    def record_assistant(
        self,
        *,
        seq: int,
        message: "Message",
        usage: "UsageSnapshot",
    ) -> None:
        self.record(
            {
                "event": "assistant",
                "seq": seq,
                "message": message.model_dump(mode="json"),
                "usage": usage.model_dump(mode="json"),
            }
        )

    def record_tool_results(
        self,
        *,
        seq: int,
        tool_results: list["ToolResultBlock"],
    ) -> None:
        self.record(
            {
                "event": "tool_results",
                "seq": seq,
                "tool_results": [
                    result.model_dump(mode="json") for result in tool_results
                ],
            }
        )


def recorder_for_context(context: "QueryContext") -> PromptReportRecorder:
    if context.prompt_report_recorder is not None:
        return context.prompt_report_recorder

    metadata = context.tool_metadata
    context.prompt_report_recorder = PromptReportRecorder(
        metadata.get("prompt_report_messages_path") if metadata is not None else None,
        base_event=(
            {
                "agent_run_id": metadata.get("agent_run_id"),
                "agent": context.agent_name or metadata.get("agent_name"),
                "model": context.model,
            }
            if metadata is not None
            else {"agent": context.agent_name, "model": context.model}
        ),
    )
    return context.prompt_report_recorder

__all__ = ["PromptReportRecorder", "recorder_for_context"]
