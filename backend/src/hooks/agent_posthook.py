"""Generic posthook wrapper for enforcing structured output.

After the work phase (the agent runs with its full toolkit), a posthook
phase runs a constrained ephemeral ``AgentDefinition`` whose ``toolkits``
are empty and whose ``extra_tools`` contain only the submit tool. This is
the enforcement pattern borrowed from synthetic-os.

The helper is deliberately generic — ``team/`` is not imported from this
file. Any caller that wants structured output can use it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from agents.types import AgentDefinition


DEFAULT_POSTHOOK_PROMPT = """You are a posthook serializer. Your only job is to call the injected submit tool with a correctly-shaped payload based on the work-phase output above.

- Call the submit tool exactly once with valid arguments.
- If the submit tool returns a validation error, read the `issues` field, fix the payload, and call the submit tool again in the same turn.
- Stop immediately after the first accepted submission.
- Do not write prose. You have no other tools."""


@dataclass
class PosthookConfig:
    submit_tool: str
    metadata_key: str = "submitted_output"
    system_prompt: str | None = None
    max_turns: int = 5
    extra_tools: list[str] = field(default_factory=list)

    def resolved_system_prompt(self) -> str:
        return self.system_prompt or DEFAULT_POSTHOOK_PROMPT


class NoPosthookOutput(Exception):
    """Raised when the posthook agent never calls the submit tool successfully."""


def _build_posthook_definition(
    parent: "AgentDefinition", cfg: PosthookConfig
) -> "AgentDefinition":
    """Construct an ephemeral AgentDefinition whose only tool is cfg.submit_tool."""
    from agents.types import AgentDefinition

    data = parent.model_dump()
    data.update(
        {
            "name": f"{parent.name}__posthook",
            "description": f"Posthook serializer for {parent.name}",
            "system_prompt": cfg.resolved_system_prompt(),
            "max_turns": cfg.max_turns,
            "toolkits": [],
            "skills": [],
            "hooks": None,
            "background": False,
            "source": "builtin",
            "posthook": None,
            "posthook_extra_tools": [cfg.submit_tool, *cfg.extra_tools],
        }
    )
    return AgentDefinition.model_validate(data)


# ---------------------------------------------------------------------------
# Execution entry point
# ---------------------------------------------------------------------------

# The real engine's ``run_query`` has a streaming contract (returns an async
# iterator of events). Posthook callers supply a thin adapter that runs one
# agent end-to-end and returns a plain result; this keeps the helper usable
# both by the live Worker and by unit tests that mock the engine.
QueryRunner = Callable[["AgentDefinition", Any], Awaitable[Any]]


async def execute_with_posthook(
    work_defn: "AgentDefinition",
    work_ctx: Any,
    *,
    runner: QueryRunner,
    posthook_ctx_builder: Callable[["AgentDefinition", Any], Any] | None = None,
) -> tuple[Any, Any | None]:
    """Run the work phase; if posthook is configured, run a constrained second phase.

    ``runner(defn, ctx)`` drives a single ``run_query`` call for the given
    ``AgentDefinition`` and returns whatever result shape the caller wants
    (the helper only inspects ``ctx.tool_metadata`` after the call). The
    ``posthook_ctx_builder`` constructs the second-phase context from the
    posthook AgentDefinition + first-phase result; callers supply it so the
    helper stays ignorant of ``QueryContext`` construction details.

    Returns ``(work_result, submitted_output | None)``. Raises
    ``NoPosthookOutput`` if a posthook was configured but the agent never
    produced an accepted submission.
    """
    work_result = await runner(work_defn, work_ctx)

    cfg: PosthookConfig | None = getattr(work_defn, "posthook", None)
    if cfg is None:
        return work_result, None

    # If the work phase itself already submitted a plan (e.g. the planner has
    # submit_plan in its own toolkit), skip the posthook entirely.
    meta = getattr(work_ctx, "tool_metadata", None)
    if meta is not None:
        try:
            already = meta.get(cfg.metadata_key)
        except Exception:
            already = None
        if already is not None:
            return work_result, already

    posthook_defn = _build_posthook_definition(work_defn, cfg)
    if posthook_ctx_builder is None:
        raise RuntimeError(
            "execute_with_posthook: posthook configured but no posthook_ctx_builder supplied"
        )
    posthook_ctx = posthook_ctx_builder(posthook_defn, work_result)

    await runner(posthook_defn, posthook_ctx)

    posthook_meta = getattr(posthook_ctx, "tool_metadata", None)
    submitted: Any | None = None
    if posthook_meta is not None:
        try:
            submitted = posthook_meta.get(cfg.metadata_key)
        except Exception:
            submitted = None

    if submitted is None:
        raise NoPosthookOutput(
            f"Posthook for agent '{work_defn.name}' ended without a valid "
            f"'{cfg.submit_tool}' call."
        )
    return work_result, submitted
