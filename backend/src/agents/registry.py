"""Runtime registry for agent definitions.

Holds builtin, user-supplied (loaded from disk), and plugin agent definitions
in a single in-memory map. Builtins are seeded at import time; user/plugin
agents are loaded lazily on first lookup (and can be reloaded explicitly).
"""

from __future__ import annotations

import logging

from agents.types import AgentDefinition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Builtin definitions
# ---------------------------------------------------------------------------

SUBAGENT_NAME = "subagent"

_SUBAGENT_SYSTEM_PROMPT = """You are a focused worker subagent spawned by a parent agent to complete one specific delegated task.

**Output contract**
Return your final result as plain text in your last assistant message. The parent agent reads ONLY that final message — anything you say in earlier turns is invisible to it. Be concise, clear, and structured.

**Scope discipline**
- Do exactly what the parent asked. Nothing more, nothing less.
- Do NOT ask clarifying questions. If a detail is ambiguous, make a best-effort decision and proceed; explain the decision in your final answer.
- Do NOT start work unrelated to the task.
- Do NOT spawn further subagents — you do not have the run_subagent tool.

**Tool access**
You have access to the same tools as the parent (read/write, shell, code intelligence) EXCEPT:
- You cannot launch background tasks. Every tool call you make blocks until it returns. Plan accordingly: prefer focused commands over open-ended long-running ones.
- You cannot spawn other subagents.

**Termination**
When the task is complete, stop calling tools and emit your final answer. The parent values a single, well-organized summary over a long narrative."""


def _builtin_definitions() -> list[AgentDefinition]:
    return [
        AgentDefinition(
            name=SUBAGENT_NAME,
            description=(
                "Focused worker subagent spawned by parent agents via run_subagent "
                "to complete one delegated task in isolation."
            ),
            system_prompt=_SUBAGENT_SYSTEM_PROMPT,
            model="inherit",
            max_turns=15,
            toolkits=["sandbox_operations", "code_intelligence"],
            agent_type="subagent",
            source="builtin",
        ),
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_DEFINITIONS: dict[str, AgentDefinition] = {}
_external_loaded = False


def register_definition(defn: AgentDefinition) -> None:
    """Register or replace an agent definition at runtime."""
    _DEFINITIONS[defn.name] = defn


def unregister_definition(name: str) -> bool:
    """Remove an agent definition. Returns True if it existed."""
    return _DEFINITIONS.pop(name, None) is not None


def get_definition(name: str) -> AgentDefinition | None:
    """Look up an agent definition by name (loads user/plugin agents lazily)."""
    _ensure_external_loaded()
    return _DEFINITIONS.get(name)


def list_definitions(source: str | None = None) -> list[AgentDefinition]:
    """List all registered definitions, optionally filtered by source."""
    _ensure_external_loaded()
    defs = list(_DEFINITIONS.values())
    if source:
        defs = [d for d in defs if d.source == source]
    return defs


def _ensure_external_loaded() -> None:
    global _external_loaded
    if _external_loaded:
        return
    _external_loaded = True  # set first to avoid recursion on failure
    try:
        from agents.loader import load_external_agents

        for defn in load_external_agents():
            # Don't overwrite a builtin with itself; user/plugin defs take
            # precedence over builtins of the same name.
            existing = _DEFINITIONS.get(defn.name)
            if existing is not None and existing.source != "builtin":
                continue
            _DEFINITIONS[defn.name] = defn
    except Exception:
        logger.debug("Failed to load external agent definitions", exc_info=True)


# Seed builtins at import time.
for _defn in _builtin_definitions():
    _DEFINITIONS.setdefault(_defn.name, _defn)
