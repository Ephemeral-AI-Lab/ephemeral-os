"""Single launch builder for every harness agent role.

Every harness launch flows through the composer with role-specific
:class:`ContextScope` fields populated, then bundles the
:class:`AgentLaunch` for the launcher to consume. Adding a per-launch
knob (priority, retry policy, latency budget) becomes one edit on
:class:`AgentLaunch` plus one edit on :meth:`_build`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from task_center.attempt.runtime import AgentLaunch
from task_center.context_engine.scope import ContextScope
from task_center.exceptions import TaskCenterInvariantViolation
from task_center.task_state import TaskCenterTaskRole

if TYPE_CHECKING:
    from task_center.attempt.state import Attempt
    from task_center.contexts import LaunchCtx


PLANNER_AGENT_NAME = "planner"
EVALUATOR_AGENT_NAME = "evaluator"


@dataclass(frozen=True, slots=True)
class LaunchBuilder:
    """Build :class:`AgentLaunch` records for each harness role."""

    runtime: LaunchCtx

    def for_planner(self, *, attempt: Attempt, task_id: str) -> AgentLaunch:
        episode = self._require_episode(attempt)
        return self._build(
            role=TaskCenterTaskRole.PLANNER,
            base_agent_name=PLANNER_AGENT_NAME,
            scope=ContextScope.for_planner(
                mission_id=episode.mission_id,
                episode_id=episode.id,
                attempt_id=attempt.id,
            ),
            task_id=task_id,
            task_center_run_id=self.runtime.run_id_for_attempt(attempt),
            attempt_id=attempt.id,
            needs=(),
            mission_id=episode.mission_id,
        )

    def for_generator(
        self,
        *,
        attempt: Attempt,
        task: dict[str, Any],
        base_agent_name: str,
    ) -> AgentLaunch:
        episode = self._require_episode(attempt)
        task_id = str(task["id"])
        return self._build(
            role=TaskCenterTaskRole.GENERATOR,
            base_agent_name=base_agent_name,
            scope=ContextScope.for_generator(
                mission_id=episode.mission_id,
                episode_id=episode.id,
                attempt_id=attempt.id,
                task_id=task_id,
            ),
            task_id=task_id,
            task_center_run_id=task["task_center_run_id"],
            attempt_id=attempt.id,
            needs=tuple(task["needs"]),
            mission_id=episode.mission_id,
        )

    def for_evaluator(self, *, attempt: Attempt, task_id: str) -> AgentLaunch:
        episode = self._require_episode(attempt)
        return self._build(
            role=TaskCenterTaskRole.EVALUATOR,
            base_agent_name=EVALUATOR_AGENT_NAME,
            scope=ContextScope.for_evaluator(
                mission_id=episode.mission_id,
                episode_id=episode.id,
                attempt_id=attempt.id,
            ),
            task_id=task_id,
            task_center_run_id=self.runtime.run_id_for_attempt(attempt),
            attempt_id=attempt.id,
            needs=tuple(attempt.generator_task_ids),
            mission_id=episode.mission_id,
        )

    def for_entry(
        self,
        *,
        task_id: str,
        task_center_run_id: str,
        base_agent_name: str,
    ) -> AgentLaunch:
        return self._build(
            role=TaskCenterTaskRole.ENTRY_EXECUTOR,
            base_agent_name=base_agent_name,
            scope=ContextScope.for_entry_executor(task_id=task_id),
            task_id=task_id,
            task_center_run_id=task_center_run_id,
            attempt_id=None,
            needs=(),
            mission_id=None,
        )

    def _build(
        self,
        *,
        role: TaskCenterTaskRole,
        base_agent_name: str,
        scope: ContextScope,
        task_id: str,
        task_center_run_id: str,
        attempt_id: str | None,
        needs: tuple[str, ...],
        mission_id: str | None,
    ) -> AgentLaunch:
        bundle = self.runtime.require_composer().compose(
            base_agent_name=base_agent_name, scope=scope
        )
        return AgentLaunch(
            task_id=task_id,
            task_center_run_id=task_center_run_id,
            attempt_id=attempt_id,
            role=role,
            agent_name=bundle.agent_def.name,
            rendered_prompt=bundle.rendered_prompt,
            needs=needs,
            context_packet_id=bundle.context_packet_id,
            mission_id=mission_id,
        )

    def _require_episode(self, attempt: Attempt) -> Any:
        episode = self.runtime.episode_store.get(attempt.episode_id)
        if episode is None:
            raise TaskCenterInvariantViolation(
                f"Episode {attempt.episode_id!r} not found"
            )
        return episode


__all__ = ["LaunchBuilder", "PLANNER_AGENT_NAME", "EVALUATOR_AGENT_NAME"]
