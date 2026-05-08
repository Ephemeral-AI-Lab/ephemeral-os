"""MockSquadRunner — deterministic mock agent execution for SWE-EVO benchmarks.

Relocated from ``benchmarks.sweevo.mock_agent_execution`` in S-03.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import sandbox.api as sandbox_api
from agents import AgentDefinition
from engine.api import EphemeralRunResult
from sandbox.api import (
    EditFileRequest,
    SandboxCaller,
    SearchReplaceEdit,
)
from task_center.attempt import Attempt
from task_center.episode.episode import Episode
from tools.core.base import BaseTool
from tools.core.context import ToolExecutionContextService
from tools.core.results import ToolResult
from tools.core.runtime import ExecutionMetadata
from tools.execution.tool_call import execute_tool_once
from tools.sandbox_toolkit.edit_file import edit_file as edit_file_tool
from tools.sandbox_toolkit.read_file import read_file as read_file_tool
from tools.sandbox_toolkit.shell import shell as shell_tool
from tools.sandbox_toolkit.write_file import write_file as write_file_tool
from tools.submission.main_agent.evaluator import (
    submit_evaluation_failure,
    submit_evaluation_success,
)
from tools.submission.main_agent.generator.executor import (
    submit_execution_success,
)
from tools.submission.main_agent.generator.request_mission_solution import (
    request_mission_solution,
)
from tools.submission.main_agent.planner import (
    submit_full_plan,
    submit_partial_plan,
)

from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR
from benchmarks.sweevo.live_test.audit.bus import AuditEventBus
from benchmarks.sweevo.live_test.audit.events import Event, EventType
from benchmarks.sweevo.live_test.audit.node_id import NodeId
from benchmarks.sweevo.live_test.scenarios.base import (
    Scenario,
    ScenarioContext,
)
from benchmarks.sweevo.live_test.squad.prompt_inspector import (
    LaunchRecord,
    PromptInspection,
    ToolCallRecord,
)
from benchmarks.sweevo.live_test.squad.sandbox_probe import SandboxCheck

_PLANNER_EVENT_BY_TOOL: dict[str, EventType] = {
    submit_full_plan.name: EventType.PLANNER_FULL_PLAN,
    submit_partial_plan.name: EventType.PLANNER_PARTIAL_PLAN,
}

_EVALUATOR_EVENT_BY_TOOL: dict[str, EventType] = {
    submit_evaluation_success.name: EventType.EVALUATOR_SUCCESS,
    submit_evaluation_failure.name: EventType.EVALUATOR_FAILURE,
}


async def _noop_emit(_event: Any) -> None:
    return None


class MockSquadRunner:
    """Deterministic agent execution handlers that call real tools."""

    def __init__(
        self,
        *,
        instance: SWEEvoInstance,
        repo_dir: str = _REPO_DIR,
        bus: AuditEventBus | None = None,
        task_center_run_id: str = "",
        scenario: Scenario | None = None,
    ) -> None:
        # Late import to avoid circular import (correctness_testing imports
        # ScenarioBase from scenarios.base which is fine, but
        # benchmarks.sweevo.live_test.scenarios re-exports CorrectnessTesting
        # which lives in the same package — keeping the import local sidesteps
        # any future package-level loops).
        from benchmarks.sweevo.live_test.scenarios.correctness_testing import (
            CorrectnessTesting,
        )

        self._instance = instance
        self._repo_dir = repo_dir
        self._bus = bus
        self._task_center_run_id = task_center_run_id
        self._scenario: Scenario = scenario or CorrectnessTesting()
        self.launches: list[LaunchRecord] = []
        self.tool_calls: list[ToolCallRecord] = []
        self.prompt_inspections: list[PromptInspection] = []
        self.sandbox_checks: list[SandboxCheck] = []

    async def __call__(
        self,
        config: Any,
        prompt: str,
        *,
        agent_def: AgentDefinition | None = None,
        sandbox_id: str | None = None,
        extra_tool_metadata: ExecutionMetadata | dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> EphemeralRunResult:
        if agent_def is None:
            raise RuntimeError("MockSquadRunner requires agent_def.")

        metadata = self._metadata_for(
            config=config,
            agent_def=agent_def,
            sandbox_id=sandbox_id,
            extra_tool_metadata=extra_tool_metadata,
        )
        task_id = str(metadata.get("task_center_task_id") or "")
        attempt_id = str(metadata.get("task_center_attempt_id") or "") or None
        self.launches.append(
            LaunchRecord(
                task_id=task_id,
                attempt_id=attempt_id,
                agent_name=agent_def.name,
                role=str(agent_def.role or ""),
                prompt_preview=prompt[:500],
            )
        )
        self.prompt_inspections.append(
            self._inspect_prompt(
                prompt=prompt,
                agent_def=agent_def,
                metadata=metadata,
            )
        )

        # Publish invocation event.
        if agent_def.name == "entry_executor":
            invocation_type = EventType.ENTRY_EXECUTOR_INVOKED
        elif agent_def.role == "planner":
            invocation_type = EventType.PLANNER_INVOKED
        elif agent_def.role == "executor":
            invocation_type = EventType.EXECUTOR_INVOKED
        elif agent_def.role == "evaluator":
            invocation_type = EventType.EVALUATOR_INVOKED
        else:
            invocation_type = None

        if invocation_type is not None:
            self._publish(invocation_type, agent_def=agent_def, metadata=metadata)

        if agent_def.name == "entry_executor":
            terminal = await self._run_entry_executor(prompt, metadata)
        elif agent_def.role == "planner":
            terminal = await self._run_planner(metadata)
        elif agent_def.role == "executor":
            terminal = await self._run_executor(prompt, metadata)
        elif agent_def.role == "evaluator":
            terminal = await self._run_evaluator(metadata)
        else:
            raise RuntimeError(f"Unsupported SWE-EVO mock agent role: {agent_def.role!r}")

        return EphemeralRunResult(
            status="completed",
            error=None,
            terminal_result=terminal,
            agent_name=agent_def.name,
            event_count=1,
        )

    def _metadata_for(
        self,
        *,
        config: Any,
        agent_def: AgentDefinition,
        sandbox_id: str | None,
        extra_tool_metadata: ExecutionMetadata | dict[str, Any] | None,
    ) -> ExecutionMetadata:
        if isinstance(extra_tool_metadata, ExecutionMetadata):
            metadata = extra_tool_metadata.copy()
        else:
            metadata = ExecutionMetadata()
            metadata.update(extra_tool_metadata or {})

        metadata.sandbox_id = str(sandbox_id or metadata.sandbox_id or "")
        metadata.agent_name = agent_def.name
        metadata.repo_root = self._repo_dir
        metadata.cwd = str(getattr(config, "cwd", self._repo_dir) or self._repo_dir)
        metadata.exec_cwd = self._repo_dir
        metadata["role"] = str(agent_def.role or "")
        metadata["agent_type"] = agent_def.agent_type
        metadata["run_id"] = str(metadata.task_center_run_id or "")
        metadata["task_id"] = str(metadata.task_center_task_id or "")
        return metadata

    async def _run_entry_executor(
        self,
        prompt: str,
        metadata: ExecutionMetadata,
    ) -> ToolResult:
        return await self._call_tool(
            request_mission_solution,
            {"goal": prompt},
            metadata,
        )

    async def _run_planner(self, metadata: ExecutionMetadata) -> ToolResult:
        ctx = self._scenario_context(prompt="", metadata=metadata)
        spec = self._scenario.planner_response(ctx)
        result = await self._call_tool(spec.tool, dict(spec.args), metadata)
        event_type = _PLANNER_EVENT_BY_TOOL.get(spec.tool.name)
        if event_type is not None:
            criteria = list(spec.args.get("evaluation_criteria", ()) or ())
            tasks = list(spec.args.get("tasks", ()) or ())
            self._publish(
                event_type,
                agent_def=None,
                metadata=metadata,
                payload={
                    "task_specification": spec.args.get("task_specification", ""),
                    "evaluation_criteria": criteria,
                    "task_count": len(tasks),
                },
            )
        return result

    async def _run_executor(
        self,
        prompt: str,
        metadata: ExecutionMetadata,
    ) -> ToolResult:
        ctx = self._scenario_context(prompt=prompt, metadata=metadata)
        actions = self._scenario.executor_actions(ctx)
        summary = "Workspace preflight completed."
        artifacts: list[str] = []
        for action in actions:
            if action == "sandbox_integrity":
                await self._run_sandbox_integrity_probe(metadata)
                summary = "Sandbox integrity probe passed."
                artifacts = [self._probe_path()]
            elif action == "final_probe":
                await self._run_final_probe(metadata)
                summary = "Continuation final probe passed."
                artifacts = [self._probe_path()]
            elif action == "preflight":
                await self._run_preflight_probe(metadata)
                summary = "Workspace preflight completed."
                artifacts = []
            else:
                raise RuntimeError(f"Unknown executor action: {action!r}")
        result = await self._call_tool(
            submit_execution_success,
            {"summary": summary, "artifacts": artifacts},
            metadata,
        )
        self._publish(
            EventType.EXECUTOR_SUCCESS,
            agent_def=None,
            metadata=metadata,
            payload={"summary": summary},
        )
        return result

    async def _run_evaluator(self, metadata: ExecutionMetadata) -> ToolResult:
        ctx = self._scenario_context(prompt="", metadata=metadata)
        spec = self._scenario.evaluator_response(ctx)
        result = await self._call_tool(spec.tool, dict(spec.args), metadata)
        event_type = _EVALUATOR_EVENT_BY_TOOL.get(spec.tool.name)
        if event_type is not None:
            self._publish(event_type, agent_def=None, metadata=metadata)
        return result

    def _scenario_context(
        self,
        *,
        prompt: str,
        metadata: ExecutionMetadata,
    ) -> ScenarioContext:
        attempt, episode = self._current_attempt_and_episode(metadata)
        return ScenarioContext(
            attempt=attempt,
            episode=episode,
            mission=None,
            prompt=prompt,
            metadata=metadata,
            audit_recorder=None,
            mutable_state=None,
        )

    async def _run_preflight_probe(self, metadata: ExecutionMetadata) -> None:
        result = await self._call_tool(
            shell_tool,
            {"command": "pwd && git rev-parse --is-inside-work-tree", "timeout": 60},
            metadata,
        )
        self._record_tool_check("tool.shell.preflight", result)

    async def _run_sandbox_integrity_probe(self, metadata: ExecutionMetadata) -> None:
        probe_dir = ".ephemeralos/sweevo-mock"
        probe_path = self._probe_path()

        mkdir = await self._call_tool(
            shell_tool,
            {
                "command": (
                    f"mkdir -p {probe_dir} && "
                    f"printf 'shell-created\\n' > {probe_dir}/shell.txt"
                ),
                "timeout": 60,
            },
            metadata,
        )
        self._record_tool_check("tool.shell.gated_merge", mkdir)

        written = await self._call_tool(
            write_file_tool,
            {
                "file_path": probe_path,
                "content": "alpha\nbeta\n",
            },
            metadata,
        )
        self._record_tool_check("tool.write_file.direct_merge", written)

        first_read = await self._call_tool(
            read_file_tool,
            {"file_path": probe_path, "start_line": 1, "end_line": 20},
            metadata,
        )
        self._assert_read_contains(first_read, "alpha", "tool.read_file.after_write")

        edited = await self._call_tool(
            edit_file_tool,
            {
                "file_path": probe_path,
                "old_text": "beta\n",
                "new_text": "beta-edited\n",
                "description": "single edit for mock SWE-EVO probe",
            },
            metadata,
        )
        self._record_tool_check("tool.edit_file.direct_merge", edited)

        await self._run_batch_edit(metadata, probe_path)
        await self._run_expected_conflict(metadata, probe_path)

        squash = await self._call_tool(
            shell_tool,
            {
                "command": f"printf 'squash-check\\n' >> {probe_path}",
                "timeout": 60,
            },
            metadata,
        )
        self._record_tool_check("tool.shell.squash_append", squash)

        final_read = await self._call_tool(
            read_file_tool,
            {"file_path": probe_path, "start_line": 1, "end_line": 20},
            metadata,
        )
        self._assert_read_contains(final_read, "squash-check", "tool.read_file.after_squash")

    async def _run_batch_edit(
        self,
        metadata: ExecutionMetadata,
        probe_path: str,
    ) -> None:
        sandbox_id = self._require_sandbox_id(metadata)
        result = await sandbox_api.edit_file(
            sandbox_id,
            EditFileRequest(
                path=self._absolute_probe_path(probe_path),
                edits=(
                    SearchReplaceEdit(old_text="alpha\n", new_text="alpha-batch\n"),
                    SearchReplaceEdit(
                        old_text="beta-edited\n",
                        new_text="beta-batch\n",
                    ),
                ),
                caller=self._caller(metadata),
                description="batch edit for mock SWE-EVO probe",
            ),
        )
        passed = result.success and result.applied_edits == 2
        self.sandbox_checks.append(
            SandboxCheck(
                name="api.edit_file.batch",
                passed=passed,
                detail=f"applied_edits={result.applied_edits} status={result.status}",
                changed_paths=tuple(result.changed_paths),
            )
        )
        if passed:
            self._publish(
                EventType.SANDBOX_BATCH_EDIT_APPLIED,
                metadata=metadata,
                payload={"applied_edits": result.applied_edits},
            )
        if not passed:
            raise RuntimeError("Batch edit did not apply both replacements.")

    async def _run_expected_conflict(
        self,
        metadata: ExecutionMetadata,
        probe_path: str,
    ) -> None:
        sandbox_id = self._require_sandbox_id(metadata)
        result = await sandbox_api.edit_file(
            sandbox_id,
            EditFileRequest(
                path=self._absolute_probe_path(probe_path),
                edits=(
                    SearchReplaceEdit(
                        old_text="missing-old-text\n",
                        new_text="should-not-apply\n",
                    ),
                ),
                caller=self._caller(metadata),
                description="expected conflict for mock SWE-EVO probe",
            ),
        )
        passed = not result.success
        detail = result.conflict_reason or result.status or "conflict reported"
        self.sandbox_checks.append(
            SandboxCheck(
                name="api.edit_file.conflict_detection",
                passed=passed,
                detail=detail,
                changed_paths=tuple(result.changed_paths),
            )
        )
        if passed:
            self._publish(
                EventType.SANDBOX_CONFLICT_DETECTED,
                metadata=metadata,
                payload={"conflict_reason": detail},
            )
        if not passed:
            raise RuntimeError("Expected conflict edit unexpectedly succeeded.")

    async def _run_final_probe(self, metadata: ExecutionMetadata) -> None:
        final_read = await self._call_tool(
            read_file_tool,
            {"file_path": self._probe_path(), "start_line": 1, "end_line": 20},
            metadata,
        )
        self._assert_read_contains(final_read, "squash-check", "tool.read_file.final_probe")
        verify = await self._call_tool(
            shell_tool,
            {
                "command": f"grep -q 'squash-check' {self._probe_path()}",
                "timeout": 60,
            },
            metadata,
        )
        self._record_tool_check("tool.shell.final_probe", verify)

    async def _call_tool(
        self,
        tool_obj: BaseTool,
        raw_input: dict[str, Any],
        metadata: ExecutionMetadata,
    ) -> ToolResult:
        self._publish(
            EventType.TOOL_CALL_STARTED,
            metadata=metadata,
            tool_name=tool_obj.name,
            payload={"tool_name": tool_obj.name},
        )
        result = await execute_tool_once(
            tool_obj,
            raw_input,
            ToolExecutionContextService(cwd=Path(self._repo_dir), services=metadata),
            emit=_noop_emit,
            emit_started=False,
        )
        self.tool_calls.append(
            ToolCallRecord(
                task_id=str(metadata.get("task_center_task_id") or ""),
                tool_name=tool_obj.name,
                is_error=result.is_error,
                metadata=dict(result.metadata or {}),
            )
        )
        completed_type = (
            EventType.TOOL_CALL_ERROR if result.is_error else EventType.TOOL_CALL_COMPLETED
        )
        self._publish(
            completed_type,
            metadata=metadata,
            tool_name=tool_obj.name,
            payload={
                "tool_name": tool_obj.name,
                "is_error": result.is_error,
                "metadata": dict(result.metadata or {}),
            },
        )
        if result.is_error:
            raise RuntimeError(f"{tool_obj.name} failed: {result.output}")
        return result

    def _record_tool_check(self, name: str, result: ToolResult) -> None:
        changed_paths = tuple(str(path) for path in result.metadata.get("changed_paths", ()))
        status = str(result.metadata.get("status") or "ok")
        self.sandbox_checks.append(
            SandboxCheck(
                name=name,
                passed=not result.is_error,
                detail=status,
                changed_paths=changed_paths,
            )
        )

    def _assert_read_contains(
        self,
        result: ToolResult,
        needle: str,
        check_name: str,
    ) -> None:
        try:
            payload = json.loads(result.output)
        except json.JSONDecodeError:
            payload = {"content": result.output}
        content = str(payload.get("content") or "")
        passed = needle in content
        self.sandbox_checks.append(
            SandboxCheck(
                name=check_name,
                passed=passed,
                detail=f"needle={needle!r}",
            )
        )
        if not passed:
            raise RuntimeError(f"{check_name} did not find {needle!r}.")

    def _inspect_prompt(
        self,
        *,
        prompt: str,
        agent_def: AgentDefinition,
        metadata: ExecutionMetadata,
    ) -> PromptInspection:
        role = str(agent_def.role or "")
        checks: dict[str, bool]
        reason: str
        if agent_def.name == "entry_executor":
            checks = {
                "entry_request_heading": "# Entry request" in prompt,
                "workspace_root": self._repo_dir in prompt,
                "pr_description": "<pr_description>" in prompt,
            }
            reason = (
                "Entry executor receives the exact SWE-EVO user request as a "
                "required entry_request block before it delegates the mission."
            )
        elif role == "planner":
            attempt, episode = self._current_attempt_and_episode(metadata)
            checks = {
                "mission": "# Mission" in prompt,
                "current_episode": (
                    "# Current Episode" in prompt
                    or "# Mission / Current Episode" in prompt
                ),
            }
            if attempt.attempt_sequence_no > 1:
                checks["failed_attempts"] = "# Failed Attempts" in prompt
            if episode.sequence_no > 1:
                checks["previous_episode_results"] = "# Previous Episode Results" in prompt
            reason = (
                "Planner context is mission and episode scoped; retry planners "
                "also receive failed-attempt evidence, and continuation planners "
                "receive previous episode results."
            )
        elif role == "executor":
            checks = {
                "attempt_plan": "# Attempt Plan" in prompt,
                "assigned_task": "# Assigned Task" in prompt,
            }
            reason = (
                "Executor context is local to the current planned task with the "
                "attempt contract as framing."
            )
        elif role == "evaluator":
            checks = {
                "attempt_plan": "# Attempt Plan" in prompt,
                "dependency_results": "# Dependency Results" in prompt,
                "evaluation_criteria": "# Evaluation Criteria" in prompt,
            }
            reason = (
                "Evaluator context is graph-local: attempt contract, completed "
                "generator evidence, and the criteria it must judge."
            )
        else:
            checks = {"known_role": False}
            reason = f"Unknown role {role!r}."

        return PromptInspection(
            task_id=str(metadata.get("task_center_task_id") or ""),
            agent_name=agent_def.name,
            role=role,
            checks=checks,
            justification=reason,
        )

    def _current_attempt_and_episode(
        self,
        metadata: ExecutionMetadata,
    ) -> tuple[Attempt, Episode]:
        runtime = metadata.get("attempt_runtime")
        if runtime is None:
            raise RuntimeError("Missing AttemptRuntime in mocked agent metadata.")
        attempt_id = str(metadata.get("task_center_attempt_id") or "")
        attempt = runtime.attempt_store.get(attempt_id)
        if attempt is None:
            raise RuntimeError(f"Attempt {attempt_id!r} not found.")
        episode = runtime.episode_store.get(attempt.episode_id)
        if episode is None:
            raise RuntimeError(f"Episode {attempt.episode_id!r} not found.")
        return attempt, episode

    def _probe_path(self) -> str:
        return ".ephemeralos/sweevo-mock/probe.txt"

    def _absolute_probe_path(self, path: str) -> str:
        if path.startswith("/"):
            return path
        return f"{self._repo_dir.rstrip('/')}/{path}"

    @staticmethod
    def _require_sandbox_id(metadata: ExecutionMetadata) -> str:
        sandbox_id = str(metadata.get("sandbox_id") or "").strip()
        if not sandbox_id:
            raise RuntimeError("Sandbox id is required for SWE-EVO sandbox checks.")
        return sandbox_id

    def _caller(self, metadata: ExecutionMetadata) -> SandboxCaller:
        return SandboxCaller(
            agent_id=str(metadata.agent_name or "sweevo-mock"),
            run_id=str(metadata.get("run_id") or ""),
            agent_run_id=str(metadata.agent_run_id or ""),
            task_id=str(metadata.get("task_center_task_id") or ""),
        )

    def _publish(
        self,
        event_type: EventType,
        *,
        agent_def: AgentDefinition | None = None,
        metadata: ExecutionMetadata | None = None,
        tool_name: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self._bus is None:
            return
        agent_name: str | None = None
        agent_role: str | None = None
        agent_run_id: str | None = None
        attempt_id: str | None = None
        if agent_def is not None:
            agent_name = agent_def.name or None
            agent_role = str(agent_def.role or "") or None
        if metadata is not None:
            if agent_name is None:
                agent_name = str(metadata.agent_name or "") or None
            agent_run_id = str(metadata.agent_run_id or "") or None
            attempt_id = str(metadata.get("task_center_attempt_id") or "") or None
        node = NodeId(
            task_center_run_id=self._task_center_run_id,
            agent_name=agent_name,
            agent_role=agent_role,  # type: ignore[arg-type]
            agent_run_id=agent_run_id,
            attempt_id=attempt_id,
            tool_name=tool_name,
        )
        self._bus.publish(Event(type=event_type, node=node, payload=payload or {}))


__all__ = ["MockSquadRunner"]
