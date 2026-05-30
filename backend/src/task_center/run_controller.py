"""Root run path — the synthetic run-level bootstrap generator.

Every workflow is generator-spawned, including the root. The root has no real
agent: ``RunController`` seeds a synthetic bootstrap generator task
(``<run_id>:root``), delegates the root workflow to it via
:class:`WorkflowStarter`, and — when that workflow closes — writes the run's
result onto the bootstrap task and finishes the run. This is the old
entry-origin control path, re-expressed in the outcomes vocabulary, without
pulling in the agent-launch surface (profile / sandbox / agent_run / terminal).
"""

from __future__ import annotations

from task_center._core.outcomes import to_record, workflow_outcomes
from task_center._core.primitives import root_task_id
from task_center._core.state import Workflow, WorkflowStatus
from task_center._core.task_state import TaskCenterTaskRole, TaskCenterTaskStatus
from task_center.attempt.launch import AttemptDeps
from task_center.workflow.starter import StartedWorkflow, WorkflowStarter


class RunController:
    """Owns the root run: seed the bootstrap generator, start + resolve its workflow."""

    def __init__(self, *, runtime: AttemptDeps) -> None:
        self._runtime = runtime

    def start_root_run(self, *, prompt: str, task_center_run_id: str) -> StartedWorkflow:
        """Seed the bootstrap generator and delegate the root workflow to it.

        The bootstrap task is seeded ``RUNNING`` (the link does not exist yet);
        :meth:`WorkflowStarter.start` atomically flips it to
        ``WAITING_WORKFLOW`` and sets ``child_workflow_id``. Any throw during
        seed/start finishes the run ``failed`` so the run can never be left open
        nor the bootstrap task stranded ``WAITING_WORKFLOW``.
        """
        task_id = root_task_id(task_center_run_id)
        try:
            self._runtime.task_store.upsert_task(
                task_id=task_id,
                task_center_run_id=task_center_run_id,
                role=TaskCenterTaskRole.GENERATOR.value,
                agent_name=None,
                context_message="",
                status=TaskCenterTaskStatus.RUNNING.value,
                outcomes=[],
                needs=[],
            )
            return WorkflowStarter(
                runtime=self._runtime,
                run_close_handler=self.on_root_workflow_closed,
            ).start(prompt=prompt, parent_task_id=task_id)
        except Exception:
            self._finish_run_if_open(task_center_run_id, status="failed")
            raise

    def on_root_workflow_closed(self, *, child_workflow: Workflow) -> None:
        """Resolve the root workflow close: write the run result + finish the run."""
        run_id = child_workflow.task_center_run_id
        run = self._runtime.task_store.get_run(run_id)
        if run is not None and run.get("status") in ("done", "failed"):
            return
        succeeded = child_workflow.status == WorkflowStatus.SUCCEEDED
        outcomes = [
            to_record(outcome)
            for outcome in workflow_outcomes(
                child_workflow, iteration_store=self._runtime.iteration_store
            )
        ]
        self._runtime.task_store.set_task_status_if_current(
            root_task_id(run_id),
            expected_status=TaskCenterTaskStatus.WAITING_WORKFLOW.value,
            status=(
                TaskCenterTaskStatus.DONE.value
                if succeeded
                else TaskCenterTaskStatus.FAILED.value
            ),
            outcomes=outcomes,
            terminal_tool_result={"child_workflow_id": child_workflow.id},
        )
        self._finish_run_if_open(run_id, status="done" if succeeded else "failed")

    def _finish_run_if_open(self, run_id: str, *, status: str) -> None:
        run = self._runtime.task_store.get_run(run_id)
        if run is not None and run.get("status") not in ("done", "failed"):
            self._runtime.task_store.finish_run(run_id, status=status)
