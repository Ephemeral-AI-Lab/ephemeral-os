"""ContextScope — identity surface every recipe sees.

The scope carries identity (mission / episode / attempt / task ids) and
helper parent references. It does **not** carry store handles; those live
on :class:`ContextEngineDeps` so recipes can be swapped without touching
call sites.

The flat dataclass shape is preserved for runtime/engine compatibility,
but the role-specific factory classmethods (:meth:`for_planner`,
:meth:`for_generator`, etc.) document the *required* fields per recipe
at the type level. Call sites that use the factories get a checked
construction — missing a required field for the role is a static error
instead of a runtime ``RecipeScopeError``.

Engine + recipe code still validates via :meth:`assert_fields` for
defensive coverage; the factories are an opt-in shortcut, not a
replacement.
"""

from __future__ import annotations

from dataclasses import dataclass

from task_center.context_engine.errors import RecipeScopeError


@dataclass(frozen=True, slots=True)
class ContextScope:
    """Identity surface threaded through resolver + engine + recipes."""

    mission_id: str | None = None

    # Optional identity fields — recipes declare which of these they need.
    episode_id: str | None = None
    attempt_id: str | None = None
    task_id: str | None = None

    # Helper-spawn fields — present only when a helper (advisor / resolver) is
    # being launched by a parent agent via ``ask_advisor`` / ``run_subagent``.
    parent_packet_id: str | None = None
    parent_task_id: str | None = None

    def assert_fields(self, required: frozenset[str]) -> None:
        """Raise :class:`RecipeScopeError` if any required field is None."""
        missing = sorted(f for f in required if getattr(self, f, None) is None)
        if missing:
            raise RecipeScopeError(
                f"ContextScope is missing required fields: {missing!r}"
            )

    # ---- Role-specific factory shortcuts -------------------------------
    #
    # Each factory takes ONLY the required fields for that recipe role as
    # positional/keyword args. Missing a required field is a static error
    # instead of a runtime assert. The defaults flow through the dataclass
    # for any optional fields the role might inspect.

    @classmethod
    def for_planner(
        cls,
        *,
        mission_id: str,
        episode_id: str,
        attempt_id: str,
    ) -> ContextScope:
        """Scope shape required by the planner recipe."""
        return cls(
            mission_id=mission_id,
            episode_id=episode_id,
            attempt_id=attempt_id,
        )

    @classmethod
    def for_generator(
        cls,
        *,
        mission_id: str,
        episode_id: str,
        attempt_id: str,
        task_id: str,
    ) -> ContextScope:
        """Scope shape required by the generator recipe."""
        return cls(
            mission_id=mission_id,
            episode_id=episode_id,
            attempt_id=attempt_id,
            task_id=task_id,
        )

    @classmethod
    def for_evaluator(
        cls,
        *,
        mission_id: str,
        episode_id: str,
        attempt_id: str,
    ) -> ContextScope:
        """Scope shape required by the evaluator recipe."""
        return cls(
            mission_id=mission_id,
            episode_id=episode_id,
            attempt_id=attempt_id,
        )

    @classmethod
    def for_entry_executor(cls, *, task_id: str) -> ContextScope:
        """Scope shape required by the entry-executor recipe."""
        return cls(task_id=task_id)
