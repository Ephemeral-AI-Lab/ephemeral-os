"""Shared stream-event printer for single- and multi-agent runs.

Mirrors the dense column-aligned log style used by the e2e conftest's
eval harness (``tests/test_e2e/conftest.py``) but keyed on
``(agent_name, work_id)`` so concurrent agents can coexist without
interleaving mid-sentence.

Key ideas:

- **Per-agent delta buffers.** ``ThinkingDelta`` / ``AssistantTextDelta``
  events from different agents are buffered independently and flushed
  when that same agent produces a structural event, so two workers
  streaming at once don't clobber each other's prose.
- **Lineage via bg task_id.** A ``BackgroundTaskStarted`` whose
  ``tool_name == "run_subagent"`` is treated as a spawn; its
  ``task_id`` becomes the child's work_id so the child's own events
  indent one level deeper than the dispatching parent.
- **Color per agent.** Each distinct ``agent_name`` is assigned a
  stable ANSI color from an 8-color palette (deterministic via hash)
  so the eye can follow one worker down a wall of output.
- **Summary.** ``summary()`` returns per-agent counts (tool calls,
  subagents spawned) plus totals for a closing one-liner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from message.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    BackgroundTaskCompleted,
    BackgroundTaskStarted,
    StreamEvent,
    SystemNotification,
    ThinkingDelta,
    ToolExecutionCancelled,
    ToolExecutionCompleted,
    ToolExecutionProgress,
    ToolExecutionStarted,
)


_PALETTE = (
    "\033[36m",  # cyan
    "\033[33m",  # yellow
    "\033[35m",  # magenta
    "\033[32m",  # green
    "\033[34m",  # blue
    "\033[91m",  # bright red
    "\033[94m",  # bright blue
    "\033[95m",  # bright magenta
)
_RESET = "\033[0m"
_DIM = "\033[2m"
_RED = "\033[31m"
_GREEN = "\033[32m"


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class _AgentState:
    color: str = ""
    thinking_buf: list[str] = field(default_factory=list)
    text_buf: list[str] = field(default_factory=list)
    tool_calls: int = 0
    subagents_spawned: int = 0


class MultiAgentEventPrinter:
    """Format and print ``StreamEvent``s to stdout (or any sink).

    Pass the result of a ``run_query`` stream into :meth:`emit` per event.
    Buffers thinking/text deltas per-agent and flushes on the next
    structural event from that same agent.
    """

    def __init__(
        self,
        *,
        color: bool = True,
        tag_width: int = 14,
        truncate: int = 500,
        sink: "Any" = None,
    ) -> None:
        self._color = color
        self._tag_width = tag_width
        self._truncate_n = truncate
        self._sink = sink  # callable taking a line; default = print
        self._agents: dict[str, _AgentState] = {}
        self._depth: dict[str, int] = {}  # work_id -> depth
        self._work_to_agent: dict[str, str] = {}  # work_id -> agent_name
        self._palette_idx = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def emit(self, event: StreamEvent) -> None:
        agent = getattr(event, "agent_name", "") or "?"
        work_id = getattr(event, "work_id", "")
        state = self._state_for(agent)

        # Stream deltas into per-agent buffers; do not print yet.
        if isinstance(event, ThinkingDelta):
            state.thinking_buf.append(event.text)
            return
        if isinstance(event, AssistantTextDelta):
            state.text_buf.append(event.text)
            return

        # Flush any buffered deltas for *this agent* before structural events.
        self._flush_buffers(agent)

        if isinstance(event, ToolExecutionStarted):
            state.tool_calls += 1
            self._line(
                agent,
                work_id,
                f"-> tool_start: {event.tool_name}"
                f"({_truncate(str(event.tool_input), 120)})",
            )
        elif isinstance(event, ToolExecutionCompleted):
            status = self._c("red", "ERROR") if event.is_error else self._c("green", "ok")
            self._line(
                agent,
                work_id,
                f"<- tool_done:  {event.tool_name} [{status}] "
                f"{_truncate(event.output, 120)}",
            )
        elif isinstance(event, ToolExecutionProgress):
            self._line(
                agent,
                work_id,
                f".. progress:   {event.tool_name} {_truncate(event.output, 120)}",
            )
        elif isinstance(event, ToolExecutionCancelled):
            self._line(
                agent,
                work_id,
                f"x  cancelled:  {event.tool_name} {_truncate(event.reason, 120)}",
            )
        elif isinstance(event, BackgroundTaskStarted):
            # run_subagent is a regular background tool — the only thing that
            # makes it a "spawn" is its name. Treat it specially so the printed
            # log reads as team coordination rather than generic bg plumbing.
            if event.tool_name == "run_subagent":
                state.subagents_spawned += 1
                child = str(event.tool_input.get("agent_name") or "subagent")
                task_text = str(event.tool_input.get("prompt") or event.tool_input.get("task_note") or "")
                # Record lineage so the child's own events indent one level
                # deeper when they arrive (keyed on bg task_id = child work_id).
                parent_depth = self._depth.get(work_id, 0) if work_id else 0
                self._depth[event.task_id] = parent_depth + 1
                self._work_to_agent[event.task_id] = child
                self._line(
                    agent,
                    work_id,
                    f"~> spawn:      {self._c('bold', child)} "
                    f"task_id={event.task_id} task={_truncate(task_text, 120)}",
                )
            else:
                self._line(
                    agent,
                    work_id,
                    f">> bg_start:   {event.tool_name} task_id={event.task_id}",
                )
        elif isinstance(event, BackgroundTaskCompleted):
            status = self._c("red", "ERROR") if event.is_error else self._c("green", "ok")
            if event.tool_name == "run_subagent":
                child = self._work_to_agent.get(event.task_id, "subagent")
                self._line(
                    agent,
                    work_id,
                    f"<~ return:     {self._c('bold', child)} "
                    f"task_id={event.task_id} [{status}] "
                    f"{_truncate(event.output, 120)}",
                )
            else:
                self._line(
                    agent,
                    work_id,
                    f"<< bg_done:    {event.tool_name} [{status}] "
                    f"{_truncate(event.output, 120)}",
                )
        elif isinstance(event, AssistantTurnComplete):
            # Turn-complete is quiet — the deltas already told the story.
            pass
        elif isinstance(event, SystemNotification):
            tag = f"[system{':' + event.category if event.category else ''}]"
            self._line(agent, work_id, f"{tag} {_truncate(event.text, 200)}")

    def flush(self) -> None:
        for agent in list(self._agents):
            self._flush_buffers(agent)

    def summary(self) -> dict[str, Any]:
        per_agent = {
            name: {
                "tool_calls": st.tool_calls,
                "subagents_spawned": st.subagents_spawned,
            }
            for name, st in self._agents.items()
        }
        totals = {
            "agents": len(self._agents),
            "tool_calls": sum(st.tool_calls for st in self._agents.values()),
            "subagents_spawned": sum(
                st.subagents_spawned for st in self._agents.values()
            ),
        }
        return {"per_agent": per_agent, "totals": totals}

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _state_for(self, agent: str) -> _AgentState:
        st = self._agents.get(agent)
        if st is None:
            color = _PALETTE[self._palette_idx % len(_PALETTE)] if self._color else ""
            self._palette_idx += 1
            st = _AgentState(color=color)
            self._agents[agent] = st
        return st

    def _flush_buffers(self, agent: str) -> None:
        st = self._agents.get(agent)
        if st is None:
            return
        if st.thinking_buf:
            self._line(
                agent,
                "",
                f"[thinking] {_truncate(''.join(st.thinking_buf), self._truncate_n)}",
            )
            st.thinking_buf.clear()
        if st.text_buf:
            self._line(
                agent,
                "",
                f"[text] {_truncate(''.join(st.text_buf), self._truncate_n)}",
            )
            st.text_buf.clear()

    def _line(self, agent: str, work_id: str, body: str) -> None:
        depth = self._depth.get(work_id, 0) if work_id else 0
        indent = "  " * depth
        tag = self._agent_tag(agent)
        line = f"{tag} {indent}{body}"
        if self._sink is not None:
            self._sink(line)
        else:
            print(line, flush=True)

    def _agent_tag(self, agent: str) -> str:
        st = self._state_for(agent)
        name = agent[: self._tag_width].ljust(self._tag_width)
        raw = f"[{name}]"
        if self._color and st.color:
            return f"{st.color}{raw}{_RESET}"
        return raw

    def _c(self, key: str, text: str) -> str:
        if not self._color:
            return text
        code = {
            "red": _RED,
            "green": _GREEN,
            "bold": "\033[1m",
        }.get(key, "")
        return f"{code}{text}{_RESET}" if code else text
