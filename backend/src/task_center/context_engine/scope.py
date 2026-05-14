"""ContextScope — identity surface every recipe sees.

The scope carries identity (mission / episode / attempt / task ids) and
helper parent references. It does **not** carry store handles; those live
on :class:`ContextEngineDeps` so recipes can be swapped without touching
call sites.

The role-specific factory classmethods (:meth:`for_planner`,
:meth:`for_generator`, etc.) document the required fields per role at the
call site: omitting one raises ``TypeError`` at call time, and strict
mypy will narrow the kwargs to their declared ``str`` types. The engine
still validates via :meth:`assert_fields` so direct ``ContextScope(...)``
construction is also covered at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from task_center.context_engine.core import RecipeScopeError


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
