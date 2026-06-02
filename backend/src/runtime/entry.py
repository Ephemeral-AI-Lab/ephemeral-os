"""Request entry bootstrap for top-level user requests."""

from __future__ import annotations

import uuid
import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agents import get_definition, validate_agent_definitions_resolved
from db.stores import (
    AttemptStore,
    WorkflowStore,
    IterationStore,
    TaskStore,
)
from engine.api import run_ephemeral_agent
from task import AgentRole, TaskStatus
from workflow._core.primitives import WorkflowLifecycleConfig
from workflow.agent_launch.composer import AgentEntryComposer
from workflow.attempt.launch import (
    AgentStreamEmitter,
    AttemptDeps,
    EphemeralAttemptAgentLauncher,
)
from workflow.attempt.orchestrator_registry import AttemptOrchestratorRegistry
from workflow.context_engine.engine import ContextEngine, ContextEngineDeps
from runtime.sandbox_provisioning import RequestSandboxProvisioner
from workflow.iteration import OpenIterationCoordinatorRegistry
from tools import ExecutionMetadata

if TYPE_CHECKING:
    from engine.agent.lifecycle import EphemeralRunResult
    from runtime.app_factory import RuntimeConfig


@dataclass(frozen=True, slots=True)
class RequestEntryHandle:
    request_id: str
    root_task_id: str
    workflow_runtime: AttemptDeps
    launcher: EphemeralAttemptAgentLauncher
    root_agent_task: asyncio.Task[None]


def start_request(
    *,
    config: RuntimeConfig,
    prompt: str,
    sandbox_id: str | None,
    on_agent_event: AgentStreamEmitter | None,
    task_store: TaskStore,
    workflow_store: WorkflowStore,
    iteration_store: IterationStore,
    attempt_store: AttemptStore,
    runner: object | None = None,
    sandbox_provisioner: RequestSandboxProvisioner | None = None,
) -> RequestEntryHandle:
    """Start a request by minting the root task and scheduling the root agent."""
    return RequestEntry(
        config=config,
        prompt=prompt,
        sandbox_id=sandbox_id,
        on_agent_event=on_agent_event,
        task_store=task_store,
        workflow_store=workflow_store,
        iteration_store=iteration_store,
        attempt_store=attempt_store,
        runner=runner,
        sandbox_provisioner=sandbox_provisioner,
    ).start()


class RequestEntry:
    """Bootstraps a top-level prompt into a first-class root task."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        prompt: str,
        sandbox_id: str | None,
        on_agent_event: AgentStreamEmitter | None,
        task_store: TaskStore,
        workflow_store: WorkflowStore,
        iteration_store: IterationStore,
        attempt_store: AttemptStore,
        runner: object | None = None,
        sandbox_provisioner: RequestSandboxProvisioner | None = None,
    ) -> None:
        self._config = config
        self._prompt = prompt
        self._sandbox_id = sandbox_id
        self._on_agent_event = on_agent_event
        self._task_store = task_store
        self._workflow_store = workflow_store
        self._iteration_store = iteration_store
        self._attempt_store = attempt_store
        self._runner = runner
        self._sandbox_provisioner = sandbox_provisioner or RequestSandboxProvisioner()

    def start(self) -> RequestEntryHandle:
        _assert_stores_ready(
            task_store=self._task_store,
            workflow_store=self._workflow_store,
            iteration_store=self._iteration_store,
            attempt_store=self._attempt_store,
        )
        request_id = self._create_top_level_request()
        iteration_coordinators = OpenIterationCoordinatorRegistry()
        runtime, launcher = self._create_runtime(iteration_coordinators=iteration_coordinators)
        root_task_id = self._create_root_task(request_id)
        root_agent_task = self._schedule_root_agent(
            request_id=request_id,
            root_task_id=root_task_id,
            runtime=runtime,
        )

        return RequestEntryHandle(
            request_id=request_id,
            root_task_id=root_task_id,
            workflow_runtime=runtime,
            launcher=launcher,
            root_agent_task=root_agent_task,
        )

    def _create_top_level_request(self) -> str:
        request_id = str(uuid.uuid4())
        binding = self._sandbox_provisioner.prepare_for_run(
            request_id=request_id,
            sandbox_id=self._sandbox_id,
        )
        self._sandbox_id = binding.sandbox_id
        self._task_store.create_request(
            request_id=request_id,
            cwd=self._config.cwd,
            sandbox_id=binding.sandbox_id,
            request_prompt=self._prompt,
        )
        return request_id

    def _create_root_task(self, request_id: str) -> str:
        root_task_id = f"root-{uuid.uuid4().hex[:16]}"
        self._task_store.upsert_task(
            task_id=root_task_id,
            request_id=request_id,
            role=AgentRole.ROOT.value,
            agent_name="root",
            instruction=self._prompt,
            status=TaskStatus.RUNNING.value,
            outcomes=[],
            needs=[],
            workflow_id=None,
            iteration_id=None,
            attempt_id=None,
        )
        self._task_store.set_root_task_id(request_id, root_task_id)
        return root_task_id

    def _schedule_root_agent(
        self,
        *,
        request_id: str,
        root_task_id: str,
        runtime: AttemptDeps,
    ) -> asyncio.Task[None]:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            raise RuntimeError("Request entry requires an active asyncio event loop.") from exc
        return loop.create_task(
            self._run_root_agent(
                request_id=request_id,
                root_task_id=root_task_id,
                runtime=runtime,
            )
        )

    async def _run_root_agent(
        self,
        *,
        request_id: str,
        root_task_id: str,
        runtime: AttemptDeps,
    ) -> None:
        root_def = get_definition("root")
        if root_def is None:
            self._fail_unfinished_root(
                request_id=request_id,
                root_task_id=root_task_id,
                summary="Root agent definition 'root' is not registered.",
            )
            return
        metadata = ExecutionMetadata(
            request_id=request_id,
            task_id=root_task_id,
            attempt_runtime=runtime,
        )
        metadata["task_store"] = self._task_store
        metadata["active_terminals"] = list(root_def.terminals)
        try:
            runner = self._runner or run_ephemeral_agent
            result: EphemeralRunResult = await runner(
                self._config,
                self._prompt,
                agent_def=root_def,
                sandbox_id=self._sandbox_id,
                persist_agent_run=True,
                task_id=root_task_id,
                on_event=self._on_agent_event,
                extra_tool_metadata=metadata,
            )
        except Exception as exc:  # pragma: no cover - defensive runner boundary
            self._fail_unfinished_root(
                request_id=request_id,
                root_task_id=root_task_id,
                summary=f"Root agent run crashed: {exc}",
            )
            return
        if result.status == "failed" or result.terminal_result is None:
            self._fail_unfinished_root(
                request_id=request_id,
                root_task_id=root_task_id,
                summary=result.error or "Root agent ended without submit_root_outcome.",
            )

    def _fail_unfinished_root(
        self,
        *,
        request_id: str,
        root_task_id: str,
        summary: str,
    ) -> None:
        task = self._task_store.get_task(root_task_id)
        if task is None or task.get("status") != TaskStatus.RUNNING.value:
            return
        self._task_store.set_task_status(
            root_task_id,
            status=TaskStatus.FAILED.value,
            outcomes=[
                {
                    "status": "failed",
                    "role": AgentRole.ROOT.value,
                    "task_id": root_task_id,
                    "outcome": summary,
                }
            ],
            terminal_tool_result={"fail_reason": "root_run_exhausted"},
        )
        self._task_store.finish_request(request_id, status="failed")

    def _create_runtime(
        self, *, iteration_coordinators: OpenIterationCoordinatorRegistry
    ) -> tuple[AttemptDeps, EphemeralAttemptAgentLauncher]:
        runtime_ref: AttemptDeps | None = None
        launcher = EphemeralAttemptAgentLauncher(
            config=self._config,
            deps_provider=lambda: runtime_ref,
            sandbox_id=self._sandbox_id,
            on_event=self._on_agent_event,
            runner=self._runner,
        )
        runtime = AttemptDeps(
            workflow_store=self._workflow_store,
            iteration_store=self._iteration_store,
            attempt_store=self._attempt_store,
            task_store=self._task_store,
            agent_launcher=launcher,
            orchestrator_registry=AttemptOrchestratorRegistry(),
            iteration_coordinators=iteration_coordinators,
            lifecycle_config=WorkflowLifecycleConfig(),
            composer=self._build_composer(),
        )
        runtime_ref = runtime
        return runtime, launcher

    def _build_composer(self) -> AgentEntryComposer:
        validate_agent_definitions_resolved()
        deps = ContextEngineDeps(
            workflow_store=self._workflow_store,
            iteration_store=self._iteration_store,
            attempt_store=self._attempt_store,
            task_store=self._task_store,
        )
        return AgentEntryComposer.default(ContextEngine(deps))


def _assert_stores_ready(
    *,
    task_store: TaskStore,
    workflow_store: WorkflowStore,
    iteration_store: IterationStore,
    attempt_store: AttemptStore,
) -> None:
    if not (
        task_store.is_ready
        and workflow_store.is_ready
        and iteration_store.is_ready
        and attempt_store.is_ready
    ):
        raise RuntimeError("Request stores are not ready.")
