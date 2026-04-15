# ruff: noqa
"""Live e2e tests: declare_blocker full lifecycle with real LLM responses.

Tests the complete blocker state machine per docs/architecture/dynamic-replanning-blocker-protocol.md:

  1. Conductor spawns pause assessment agents (real LLM) for RUNNING siblings+descendants
  2. Assessment agent inherits original conversation snapshot + blocker question
  3. YES → original terminated, assessment conversation saved as checkpoint
  4. Resolver runs → RESOLVED or FAILED
  5. RESOLVED: resumed agent gets checkpoint (snapshot + Q + YES) + resume notification
  6. FAILED: paused tasks cancelled

Key invariant from the fork diagram:
  The resumed agent sees: [original tools 1..N] + [blocker Q] + [YES answer] + [resume msg]
  NOT just the original snapshot — the assessment conversation IS the checkpoint.

Run with:
    .venv/bin/python -m pytest backend/tests/test_e2e/test_blocker_lifecycle_live.py -v -m live -o "addopts="
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

from engine.testing.eval_agent import EvalAgent
from external_trigger.pause_assessment import PauseVerdict, assess_pause
from external_trigger.runner import run as run_trigger
from team.models import Blocker, BlockerStatus, TaskDefinition, TaskStatus
from team.runtime.conductor import Conductor
from tests.test_e2e.conftest import create_eval_agent
from tools.task_center.toolkit import PostNoteTool

pytestmark = [pytest.mark.e2e, pytest.mark.live]

HAS_CREDENTIALS = EvalAgent.has_credentials()


# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------


@dataclass
class FakeTaskRecord:
    id: str
    status: str
    agent_name: str = "developer"
    agent_run_id: str = ""
    task: str = ""
    scope_paths: list[str] = field(default_factory=list)
    parent_id: str | None = None
    root_id: str = ""
    depth: int = 0
    blocker_id: str | None = None
    pause_checkpoint: str | None = None
    pause_verdict: str | None = None


class FakeTaskCenter:
    def __init__(self) -> None:
        self.tasks: dict[str, FakeTaskRecord] = {}
        self.inserted_plans: list[list[TaskDefinition]] = []
        self._notes: list[Any] = []

    def add_task(self, rec: FakeTaskRecord) -> None:
        self.tasks[rec.id] = rec

    async def get_siblings_and_descendants(self, initiating_task_id: str) -> list[FakeTaskRecord]:
        return [t for t in self.tasks.values() if t.id != initiating_task_id]

    async def pause_running_task(
        self, task_id: str, blocker_id: str, checkpoint: str, verdict: str,
    ) -> bool:
        rec = self.tasks.get(task_id)
        if rec is None or rec.status != "running":
            return False
        rec.status = "paused"
        rec.blocker_id = blocker_id
        rec.pause_checkpoint = checkpoint
        rec.pause_verdict = verdict
        return True

    async def resume_paused_tasks(self, blocker_id: str) -> int:
        count = 0
        for rec in self.tasks.values():
            if rec.blocker_id == blocker_id and rec.status == "paused":
                rec.status = "ready"
                rec.blocker_id = None
                count += 1
        return count

    async def cancel_paused_tasks(self, blocker_id: str) -> int:
        count = 0
        for rec in self.tasks.values():
            if rec.blocker_id == blocker_id and rec.status == "paused":
                rec.status = "cancelled"
                rec.blocker_id = None
                count += 1
        return count

    async def insert_plan(
        self, specs: list[TaskDefinition], parent_id: str | None = None,
        parent_depth: int = 0, parent_root_id: str | None = None,
    ) -> list[str]:
        self.inserted_plans.append(specs)
        ids = []
        for spec in specs:
            task_id = spec.id or str(uuid.uuid4())
            self.tasks[task_id] = FakeTaskRecord(
                id=task_id, status="ready", agent_name=spec.agent,
                task=spec.task, scope_paths=list(spec.scope_paths),
            )
            ids.append(task_id)
        return ids

    async def context_for(self, task: FakeTaskRecord, **kwargs) -> str:
        return f"## Your task\n{task.task}\n\nScope: {', '.join(task.scope_paths)}"


class FakeTeamRun:
    def __init__(self, task_center: FakeTaskCenter, api_client: Any) -> None:
        self.id = f"test-run-{uuid.uuid4().hex[:8]}"
        self.task_center = task_center
        self.api_client = api_client
        self._cancelled_runs: list[str] = []

    def cancel_agent_run(self, task_id: str) -> bool:
        self._cancelled_runs.append(task_id)
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent():
    if not HAS_CREDENTIALS:
        pytest.skip("No LLM credentials configured")
    return create_eval_agent()


@pytest.fixture(scope="module")
def api_client(agent):
    return agent.api_client


# ---------------------------------------------------------------------------
# Test 1: Pause assessment — affected task says YES (real LLM)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_pause_assessment_affected_task_says_yes(api_client):
    """Agent that imports from broken file should answer YES to pause."""
    messages = [
        {"role": "user", "content": (
            "Fix the IO module exports in pkg/io.py. "
            "The module imports load_defaults from pkg/_compat.py on line 3."
        )},
        {"role": "assistant", "content": (
            "I've read pkg/io.py. The file imports `load_defaults` from `pkg._compat` "
            "on line 3, which is central to the IO module's initialization flow."
        )},
    ]

    verdict = await assess_pause(
        task_id="fix-io",
        agent_run_id="run-fix-io",
        messages=messages,
        system_prompt="You are a developer agent working on pkg/io.py.",
        broken_files=["pkg/_compat.py"],
        problem="pkg/_compat.py was refactored — load_defaults() renamed to get_defaults(). All importers will fail.",
        api_client=api_client,
    )

    assert verdict.answer == "YES", f"Expected YES, got {verdict.answer}: {verdict.reason}"
    # The conversation should contain: original snapshot + blocker Q + YES answer
    assert len(verdict.conversation) > len(messages), "Verdict conversation should extend the original snapshot"


# ---------------------------------------------------------------------------
# Test 2: Pause assessment — unaffected task says NO (real LLM)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_pause_assessment_unaffected_task_says_no(api_client):
    """Agent with no dependency on broken files should answer NO."""
    messages = [
        {"role": "user", "content": (
            "Fix the CLI entry points in pkg/cli.py. "
            "The CLI uses argparse to parse commands."
        )},
        {"role": "assistant", "content": (
            "I've read pkg/cli.py. The CLI module uses argparse and "
            "only imports from the standard library and pkg.commands. "
            "It has no dependency on the database layer."
        )},
    ]

    verdict = await assess_pause(
        task_id="fix-cli",
        agent_run_id="run-fix-cli",
        messages=messages,
        system_prompt="You are a developer agent working on pkg/cli.py.",
        broken_files=["src/db/connection.py", "src/db/pool.py"],
        problem="Database connection pool has a deadlock bug. All DB queries will hang.",
        api_client=api_client,
    )

    assert verdict.answer == "NO", f"Expected NO, got {verdict.answer}: {verdict.reason}"


# ---------------------------------------------------------------------------
# Test 3: Full lifecycle — assess → pause → resolve → resume agent
#
# Per the fork diagram:
#   Resumed agent gets verdict.conversation (snapshot + Q + YES) as messages
#   PLUS a resume notification as the new user message.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_full_lifecycle_resolve_and_resume_agent(api_client):
    """
    Complete lifecycle with real LLM at every stage:
      1. Conductor spawns pause assessments → affected says YES, unaffected says NO
      2. YES task paused — verdict.conversation saved as checkpoint
      3. Resolver succeeds → paused tasks resume
      4. Resumed agent gets checkpoint conversation + resume notification
      5. Resumed agent acknowledges and continues (real LLM)
    """
    tc = FakeTaskCenter()
    team_run = FakeTeamRun(tc, api_client)

    # Task tree: initiating + 1 affected running + 1 unaffected running
    tc.add_task(FakeTaskRecord(
        id="fix-compat", status="failed", agent_name="developer",
        task="Refactor compat module", scope_paths=["pkg/_compat.py"],
    ))
    tc.add_task(FakeTaskRecord(
        id="fix-io", status="running", agent_name="developer",
        task="Fix IO module — imports load_defaults from pkg/_compat.py on line 3",
        scope_paths=["pkg/io.py"],
    ))
    tc.add_task(FakeTaskRecord(
        id="fix-cli", status="running", agent_name="developer",
        task="Fix CLI — uses only argparse and pkg.commands, no _compat dependency",
        scope_paths=["pkg/cli.py"],
    ))

    conductor = Conductor(team_run)

    # Register conversation snapshots (what the original agents have seen so far)
    conductor.register_snapshot("fix-io", [
        {"role": "user", "content": (
            "Fix IO module exports in pkg/io.py. It imports load_defaults from pkg._compat on line 3. "
            "Run pytest pkg/tests/test_io.py to verify."
        )},
        {"role": "assistant", "content": (
            "I've read pkg/io.py. The file imports load_defaults from pkg._compat on line 3. "
            "I was about to edit the export list."
        )},
    ])
    conductor.register_snapshot("fix-cli", [
        {"role": "user", "content": "Fix CLI entry points in pkg/cli.py. Uses argparse and pkg.commands only."},
        {"role": "assistant", "content": "Reading pkg/cli.py. Pure argparse CLI with pkg.commands imports. No _compat dependency."},
    ])

    # =========================================================
    # PHASE 1: Create blocker — real LLM pause assessments
    # =========================================================
    blocker = await conductor.create_blocker(
        reason="pkg/_compat.py was refactored — load_defaults() renamed to get_defaults(). All importers will fail.",
        root_cause_paths=["pkg/_compat.py"],
        initiating_task_id="fix-compat",
        declared_by="fix-compat",
    )

    assert blocker.status == BlockerStatus.FIXING

    # fix-io depends on _compat → paused
    assert tc.tasks["fix-io"].status == "paused", f"fix-io: expected paused, got {tc.tasks['fix-io'].status}"
    # fix-cli independent → still running
    assert tc.tasks["fix-cli"].status == "running", f"fix-cli: expected running, got {tc.tasks['fix-cli'].status}"

    # Verify checkpoint saved = verdict.conversation (snapshot + Q + YES)
    io_task = tc.tasks["fix-io"]
    assert io_task.pause_checkpoint is not None, "Paused task must have checkpoint"
    checkpoint_messages = json.loads(io_task.pause_checkpoint)
    assert isinstance(checkpoint_messages, list)
    # Checkpoint should be longer than original snapshot (snapshot + blocker Q + YES answer)
    assert len(checkpoint_messages) > 2, (
        f"Checkpoint should contain snapshot + Q + answer, got {len(checkpoint_messages)} messages"
    )
    assert io_task.pause_verdict is not None, "Paused task must have verdict reason"

    # Verify resolver spawned
    assert blocker.fix_task_id is not None
    assert "fix-io" in team_run._cancelled_runs
    assert "fix-cli" not in team_run._cancelled_runs

    # =========================================================
    # PHASE 2: Resolve blocker → resume
    # =========================================================
    await conductor.on_fix_complete(
        blocker.id, "Restored load_defaults as alias for get_defaults in pkg/_compat.py"
    )
    assert tc.tasks["fix-io"].status == "ready"
    assert not conductor.has_active_blocker()

    # =========================================================
    # PHASE 3: Resumed agent run (real LLM)
    #
    # Per the fork diagram, the resumed agent gets:
    #   messages = checkpoint (snapshot + blocker Q + YES answer)
    #   + a new user message with resume notification
    #
    # The checkpoint conversation may contain tool_use/tool_result
    # blocks from the pause assessment runner. The real executor
    # normalizes these via build_initial_messages() → ConversationMessage.
    # For the test, we strip tool blocks and keep only user/assistant
    # text messages to create a valid conversation for the API.
    # =========================================================
    def _sanitize_checkpoint(messages: list[dict]) -> list[dict]:
        """Extract valid user/assistant text messages from checkpoint."""
        clean = []
        for msg in messages:
            role = msg.get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content")
            # Skip messages with tool_use/tool_result content blocks
            if isinstance(content, list):
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block["text"])
                    elif isinstance(block, str):
                        text_parts.append(block)
                if text_parts:
                    clean.append({"role": role, "content": "\n".join(text_parts)})
            elif isinstance(content, str) and content.strip():
                clean.append({"role": role, "content": content})
        return clean

    resume_messages = _sanitize_checkpoint(checkpoint_messages)
    assert len(resume_messages) >= 2, f"Should have at least original snapshot messages, got {len(resume_messages)}"

    resume_notification = (
        "## RESUME AFTER BLOCKER FIX\n"
        f"Your task was paused because: {io_task.pause_verdict}\n"
        "The root cause has been fixed. Continue your work from where you left off.\n\n"
        f"## Your task\n{io_task.task}\n\nScope: {', '.join(io_task.scope_paths)}"
    )

    result = await run_trigger(
        agent_name="test:blocker_resume",
        messages=resume_messages,
        system_prompt="You are a developer agent. Your task was paused due to a blocker and has now resumed.",
        prompt=resume_notification,
        tools=[PostNoteTool()],
        api_client=api_client,
        max_tokens_per_turn=500,
    )

    # Resumed agent should post a note acknowledging the resume and continuing
    assert result.tool_name == "post_note", f"Resumed agent should post_note, got {result.tool_name}"
    note_content = result.tool_input.get("content", "")
    assert len(note_content) > 10, "Resumed agent should produce meaningful content"


# ---------------------------------------------------------------------------
# Test 4: Full lifecycle — resolve failure → paused tasks cancelled
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_full_lifecycle_resolve_failure_cancels_paused(api_client):
    """
    Resolver fails → all paused tasks cancelled, NOT resumed.
    Real LLM for pause assessments.
    """
    tc = FakeTaskCenter()
    team_run = FakeTeamRun(tc, api_client)

    tc.add_task(FakeTaskRecord(
        id="fix-schema", status="failed", agent_name="developer",
        task="Fix schema validation", scope_paths=["src/schema.py"],
    ))
    tc.add_task(FakeTaskRecord(
        id="fix-api", status="running", agent_name="developer",
        task="Fix API handlers — uses schema.DateTimeField for all datetime validation",
        scope_paths=["src/api/handlers.py"],
    ))
    tc.add_task(FakeTaskRecord(
        id="fix-events", status="running", agent_name="developer",
        task="Fix event processor — calls schema.validate() on every incoming event",
        scope_paths=["src/events/processor.py"],
    ))

    conductor = Conductor(team_run)
    conductor.register_snapshot("fix-api", [
        {"role": "user", "content": "Fix API handlers. Uses schema.DateTimeField for datetime validation."},
        {"role": "assistant", "content": "The API handler imports DateTimeField from src/schema.py on line 5. Core dependency."},
    ])
    conductor.register_snapshot("fix-events", [
        {"role": "user", "content": "Fix event processor. Calls schema.validate() on every event payload."},
        {"role": "assistant", "content": "Event processor imports validate from src/schema.py. Every event goes through it."},
    ])

    # Create blocker with real LLM assessments
    blocker = await conductor.create_blocker(
        reason="src/schema.py DateTimeField strict mode breaks all callers passing ISO strings.",
        root_cause_paths=["src/schema.py"],
        initiating_task_id="fix-schema",
        declared_by="fix-schema",
    )

    assert tc.tasks["fix-api"].status == "paused"
    assert tc.tasks["fix-events"].status == "paused"

    # Checkpoints saved
    assert tc.tasks["fix-api"].pause_checkpoint is not None
    assert tc.tasks["fix-events"].pause_checkpoint is not None

    # Resolver fails
    await conductor.on_fix_failed(blocker.id, "Cannot restore backward compat without breaking new callers")

    # Both cancelled — NOT resumed
    assert tc.tasks["fix-api"].status == "cancelled"
    assert tc.tasks["fix-events"].status == "cancelled"
    assert not conductor.has_active_blocker()


# ---------------------------------------------------------------------------
# Test 5: Blocker merging — overlapping paths merge, re-assess new scope
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_blocker_merge_overlapping_paths(api_client):
    """Second blocker with overlapping paths merges into existing one."""
    tc = FakeTaskCenter()
    team_run = FakeTeamRun(tc, api_client)

    tc.add_task(FakeTaskRecord(id="task-a", status="failed", scope_paths=["pkg/schema.py"]))
    tc.add_task(FakeTaskRecord(id="task-b", status="failed", scope_paths=["pkg/schema.py"]))
    tc.add_task(FakeTaskRecord(
        id="task-c", status="running", agent_name="developer",
        task="Working on CLI with no schema dependency", scope_paths=["pkg/cli.py"],
    ))
    conductor = Conductor(team_run)
    conductor.register_snapshot("task-c", [
        {"role": "user", "content": "Fix CLI in pkg/cli.py. No schema dependency."},
        {"role": "assistant", "content": "CLI uses only argparse. No schema imports."},
    ])

    b1 = await conductor.create_blocker(
        reason="schema.py DateTimeField broken",
        root_cause_paths=["pkg/schema.py"],
        initiating_task_id="task-a",
    )
    first_id = b1.id

    b2 = await conductor.create_blocker(
        reason="schema.py validation mode changed",
        root_cause_paths=["pkg/schema.py", "pkg/schema_utils.py"],
        initiating_task_id="task-b",
    )

    assert b2.id == first_id, "Should merge into existing blocker"
    assert "pkg/schema_utils.py" in b2.root_cause_paths
    assert "pkg/schema.py" in b2.root_cause_paths
    assert len(conductor.active_blockers()) == 1


# ---------------------------------------------------------------------------
# Test 6: Subtree assessment — child tasks of running expandable siblings
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_pause_assessment_reaches_subtree_children(api_client):
    """
    Assessment scope is siblings + their entire subtrees.
    A RUNNING child task deep in a subtree should be assessed.
    """
    tc = FakeTaskCenter()
    team_run = FakeTeamRun(tc, api_client)

    # Initiating task
    tc.add_task(FakeTaskRecord(
        id="fix-compat", status="failed", scope_paths=["pkg/_compat.py"],
    ))
    # Sibling expandable node (EXPANDED, not RUNNING — should NOT be assessed)
    tc.add_task(FakeTaskRecord(
        id="io-lane", status="expanded", agent_name="team_planner",
        task="IO lane", scope_paths=["pkg/io/"],
    ))
    # Child of io-lane: RUNNING, depends on _compat
    tc.add_task(FakeTaskRecord(
        id="fix-io-core", status="running", agent_name="developer",
        parent_id="io-lane",
        task="Fix IO core — imports load_defaults from pkg._compat",
        scope_paths=["pkg/io/core.py"],
    ))
    # Child of io-lane: RUNNING, no _compat dependency
    tc.add_task(FakeTaskRecord(
        id="fix-io-utils", status="running", agent_name="developer",
        parent_id="io-lane",
        task="Fix IO utils — pure utility functions, no _compat imports",
        scope_paths=["pkg/io/utils.py"],
    ))

    conductor = Conductor(team_run)
    conductor.register_snapshot("fix-io-core", [
        {"role": "user", "content": "Fix IO core in pkg/io/core.py. Imports load_defaults from pkg._compat."},
        {"role": "assistant", "content": "Reading pkg/io/core.py. It imports load_defaults from pkg._compat on line 2."},
    ])
    conductor.register_snapshot("fix-io-utils", [
        {"role": "user", "content": "Fix IO utils in pkg/io/utils.py. Pure utility functions."},
        {"role": "assistant", "content": "Reading pkg/io/utils.py. Only imports math and os. No _compat dependency."},
    ])

    blocker = await conductor.create_blocker(
        reason="pkg/_compat.py refactored — load_defaults renamed to get_defaults.",
        root_cause_paths=["pkg/_compat.py"],
        initiating_task_id="fix-compat",
        declared_by="fix-compat",
    )

    # fix-io-core: RUNNING child that depends on _compat → should be paused
    assert tc.tasks["fix-io-core"].status == "paused", (
        f"fix-io-core should be paused (imports _compat), got {tc.tasks['fix-io-core'].status}"
    )
    # fix-io-utils: RUNNING child with no _compat dep → should stay running
    assert tc.tasks["fix-io-utils"].status == "running", (
        f"fix-io-utils should NOT be paused (no _compat dep), got {tc.tasks['fix-io-utils'].status}"
    )
    # io-lane: EXPANDED (not running) → never assessed, stays as-is
    assert tc.tasks["io-lane"].status == "expanded"
