# ruff: noqa
"""Live e2e tests: replanner picks add_tasks or declare_blocker.

Tests 1, 2, 5, 6 from the replanner decision test plan.
Uses real LLM calls via EvalAgent credentials.

Run with:
    .venv/bin/python -m pytest backend/tests/test_e2e/test_replanner_add_tasks_blocker_live.py -v
"""

from __future__ import annotations

import pytest

from engine.testing.eval_agent import EvalAgent
from external_trigger.runner import run as run_trigger
from tests.test_e2e.conftest import create_eval_agent
from tools.posthook.toolkit import AddTasksTool, CancelAndRedraftTool, DeclareBlockerTool

pytestmark = [pytest.mark.e2e, pytest.mark.live]

HAS_CREDENTIALS = EvalAgent.has_credentials()

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

REPLANNER_SYSTEM = (
    "You are a replanner agent. A task has failed. Read the failure context, "
    "sibling statuses, and notes, then call exactly ONE action:\n\n"
    "  add_tasks — the plan is fine, just needs more work or a retry.\n"
    "  declare_blocker — a shared dependency is broken, pause siblings.\n"
    "  cancel_and_redraft — some tasks are stale, cancel and replace them.\n\n"
    "Choose based on the evidence. Do not explain your reasoning — just call the tool."
)

REPLANNER_TOOLS = [AddTasksTool(), DeclareBlockerTool(), CancelAndRedraftTool()]

ROSTER = {
    "planner": ["team_planner"],
    "developer": ["developer"],
    "reviewer": ["validator"],
    "explorer": ["scout"],
    "replanner": ["team_replanner"],
}


@pytest.fixture(scope="module")
def agent():
    if not HAS_CREDENTIALS:
        pytest.skip("No LLM credentials configured")
    return create_eval_agent()


@pytest.fixture(scope="module")
def api_client(agent):
    return agent.api_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_replanner_prompt(
    *,
    failed_task: str,
    failed_reason: str,
    sibling_statuses: list[dict],
    notes: list[dict],
    scope_paths: list[str] | None = None,
) -> str:
    """Build the replanner's context prompt from structured scenario data."""
    lines = []

    lines.append("## Your task")
    lines.append(
        f"A sibling task failed. Draft corrective tasks to recover the execution chain.\n"
    )

    lines.append("## Failed task")
    lines.append(f"**Task ID:** {failed_task}")
    if scope_paths:
        lines.append(f"**Scope:** {', '.join(scope_paths)}")
    lines.append(f"**Failure reason:** {failed_reason}\n")

    lines.append("## Sibling statuses")
    for sib in sibling_statuses:
        status = sib["status"]
        task_id = sib["id"]
        desc = sib.get("desc", "")
        scope = sib.get("scope", "")
        line = f"- **{task_id}** [{status}]"
        if desc:
            line += f": {desc}"
        if scope:
            line += f" (scope: {scope})"
        lines.append(line)
    lines.append("")

    lines.append("## Notes from siblings and descendants")
    for note in notes:
        author = note.get("author", "system")
        task_id = note.get("task_id", "unknown")
        content = note["content"]
        lines.append(f"**[{author} on {task_id}]:** {content}")
    lines.append("")

    lines.append("## Available agents")
    for role, names in ROSTER.items():
        lines.append(f"- **{role}**: {', '.join(names)}")

    return "\n".join(lines)


async def _run_replanner(api_client, prompt: str) -> str:
    """Run the replanner and return the tool name it chose."""
    result = await run_trigger(
        agent_name="test:replanner_add_tasks",
        messages=[],
        system_prompt=REPLANNER_SYSTEM,
        prompt=prompt,
        tools=REPLANNER_TOOLS,
        api_client=api_client,
        max_tokens_per_turn=1500,
    )
    return result.tool_name


# ---------------------------------------------------------------------------
# Test 1: Transient timeout, all siblings green → add_tasks
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_transient_timeout_picks_add_tasks(api_client):
    """Isolated sandbox timeout with healthy siblings should produce add_tasks."""
    prompt = _build_replanner_prompt(
        failed_task="fix-io",
        failed_reason="sandbox timeout after 30s during pytest execution",
        scope_paths=["pkg/io.py"],
        sibling_statuses=[
            {"id": "fix-io", "status": "FAILED", "desc": "Fix IO module exports", "scope": "pkg/io.py"},
            {"id": "fix-parser", "status": "DONE", "desc": "Fix parser module", "scope": "pkg/parser.py"},
            {"id": "fix-cli", "status": "DONE", "desc": "Fix CLI entry points", "scope": "pkg/cli.py"},
            {"id": "fix-utils", "status": "RUNNING", "desc": "Fix utility helpers", "scope": "pkg/utils.py"},
            {"id": "fix-config", "status": "RUNNING", "desc": "Fix config loading", "scope": "pkg/config.py"},
        ],
        notes=[
            {"author": "system", "task_id": "fix-io",
             "content": "sandbox timeout after 30s. pytest pkg/tests/test_io.py did not complete. No code changes were made."},
            {"author": "developer", "task_id": "fix-parser",
             "content": "Fixed 3 import sites in pkg/parser.py. All tests pass."},
            {"author": "developer", "task_id": "fix-cli",
             "content": "Patched CLI entry points. pytest pkg/tests/test_cli.py passes."},
            {"author": "developer", "task_id": "fix-utils",
             "content": "Progress: fixed 2 of 3 utility functions. Working on the third."},
            {"author": "developer", "task_id": "fix-config",
             "content": "Reading config module structure. No issues found yet."},
        ],
    )

    tool_name = await _run_replanner(api_client, prompt)
    assert tool_name == "add_tasks", f"Expected add_tasks for transient timeout, got {tool_name}"


# ---------------------------------------------------------------------------
# Test 2: Shared import broken by completed sibling → declare_blocker
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_shared_import_broken_picks_declare_blocker(api_client):
    """Completed sibling broke shared import, RUNNING siblings will hit the same error — declare_blocker."""
    prompt = _build_replanner_prompt(
        failed_task="fix-io",
        failed_reason="ImportError: cannot import name 'load_defaults' from 'pkg._compat'",
        scope_paths=["pkg/io.py"],
        sibling_statuses=[
            {"id": "fix-compat", "status": "DONE", "desc": "Refactor compat module", "scope": "pkg/_compat.py"},
            {"id": "fix-io", "status": "FAILED", "desc": "Fix IO module exports", "scope": "pkg/io.py"},
            {"id": "fix-parser", "status": "RUNNING", "desc": "Fix parser module", "scope": "pkg/parser.py"},
            {"id": "fix-cli", "status": "RUNNING", "desc": "Fix CLI entry points", "scope": "pkg/cli.py"},
            {"id": "fix-api", "status": "RUNNING", "desc": "Fix API response handlers", "scope": "pkg/api.py"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-compat",
             "content": "Refactored pkg/_compat.py. Renamed load_defaults() → get_defaults() for clarity. All compat tests pass."},
            {"author": "developer", "task_id": "fix-io",
             "content": "FAILED: ImportError: cannot import name 'load_defaults' from 'pkg._compat'. pkg/io.py line 3 imports load_defaults. This symbol was renamed by fix-compat."},
            {"author": "developer", "task_id": "fix-parser",
             "content": "Progress: reading parser module structure. About to start importing from pkg._compat. NOTE: parser.py line 7 uses 'from pkg._compat import load_defaults'."},
            {"author": "developer", "task_id": "fix-cli",
             "content": "Progress: about to run tests. cli.py line 2 imports load_defaults from pkg._compat."},
            {"author": "developer", "task_id": "fix-api",
             "content": "Progress: api.py imports load_defaults from pkg._compat on line 5. Haven't hit the error yet but will when tests run."},
        ],
    )

    tool_name = await _run_replanner(api_client, prompt)
    assert tool_name == "declare_blocker", f"Expected declare_blocker for shared import break, got {tool_name}"


# ---------------------------------------------------------------------------
# Test 5: Partial success, needs follow-up → add_tasks
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_partial_success_picks_add_tasks(api_client):
    """Task that made progress but left one file undone should produce add_tasks follow-up."""
    prompt = _build_replanner_prompt(
        failed_task="fix-exports",
        failed_reason="Patched 3/4 export sites. Remaining site uses dynamic imports requiring a different approach.",
        scope_paths=["pkg/base.py", "pkg/utils.py", "pkg/core.py", "pkg/advanced.py"],
        sibling_statuses=[
            {"id": "fix-exports", "status": "FAILED",
             "desc": "Fix deprecated export sites across 4 modules", "scope": "pkg/base.py, pkg/utils.py, pkg/core.py, pkg/advanced.py"},
            {"id": "fix-tests", "status": "DONE", "desc": "Update test imports", "scope": "pkg/tests/"},
            {"id": "fix-docs", "status": "RUNNING", "desc": "Update API documentation", "scope": "docs/"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-exports",
             "content": (
                 "Patched pkg/base.py, pkg/utils.py, pkg/core.py — all 3 now use the new export API. "
                 "Tests pass for these 3 files. "
                 "Remaining: pkg/advanced.py uses importlib.import_module() for dynamic loading. "
                 "Cannot do a static rewrite — needs runtime patching at the module __getattr__ level. "
                 "Calling request_replan because this requires a different approach."
             )},
            {"author": "developer", "task_id": "fix-tests",
             "content": "Updated all test imports. Tests pass."},
            {"author": "developer", "task_id": "fix-docs",
             "content": "Progress: updating API reference docs. 60% complete."},
        ],
    )

    tool_name = await _run_replanner(api_client, prompt)
    assert tool_name == "add_tasks", f"Expected add_tasks for partial success follow-up, got {tool_name}"


# ---------------------------------------------------------------------------
# Test 6: Looks isolated, deep notes reveal shared pattern → declare_blocker
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_hidden_shared_pattern_picks_declare_blocker(api_client):
    """Failure that looks isolated until sibling notes reveal a shared schema issue."""
    prompt = _build_replanner_prompt(
        failed_task="fix-serializer",
        failed_reason="ValidationError: field 'created_at' expects datetime, got str in pkg/schema.py",
        scope_paths=["pkg/serializer.py"],
        sibling_statuses=[
            {"id": "fix-serializer", "status": "FAILED",
             "desc": "Fix serializer validation", "scope": "pkg/serializer.py"},
            {"id": "fix-api", "status": "DONE",
             "desc": "Fix API response handling", "scope": "pkg/api/handlers.py"},
            {"id": "fix-events", "status": "RUNNING",
             "desc": "Fix event processing pipeline", "scope": "pkg/events/processor.py"},
            {"id": "fix-models", "status": "RUNNING",
             "desc": "Fix model layer", "scope": "pkg/models/"},
            {"id": "fix-tests", "status": "RUNNING",
             "desc": "Update integration tests", "scope": "pkg/tests/"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-serializer",
             "content": (
                 "FAILED: ValidationError in pkg/schema.py — field 'created_at' expects datetime but receives str. "
                 "The schema.py DateTimeField was changed from auto-coercing to strict mode in a recent commit. "
                 "This affects any code path that passes raw ISO strings to the schema."
             )},
            {"author": "developer", "task_id": "fix-api",
             "content": (
                 "Completed. Note: encountered unexpected str values in datetime fields from pkg/schema.py. "
                 "Worked around it by adding a manual str→datetime cast in the API handler before passing to schema. "
                 "This is a local workaround — the root cause is in pkg/schema.py's strict mode change."
             )},
            {"author": "developer", "task_id": "fix-events",
             "content": (
                 "Progress: seeing unexpected str values in datetime fields when events pass through pkg/schema.py. "
                 "Investigating whether this is a local issue or shared. Events pipeline calls schema.validate() directly."
             )},
            {"author": "developer", "task_id": "fix-models",
             "content": "Progress: model layer refactoring going well. No issues yet — model tests pass."},
            {"author": "developer", "task_id": "fix-tests",
             "content": "Progress: updating test fixtures. 40% complete."},
        ],
    )

    tool_name = await _run_replanner(api_client, prompt)
    assert tool_name == "declare_blocker", f"Expected declare_blocker for hidden shared pattern, got {tool_name}"
