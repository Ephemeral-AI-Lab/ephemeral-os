# ruff: noqa
"""Live e2e tests: replanner picks cancel_and_redraft with correct scope.

Tests 3, 4, 7, 8, 9, 10 from the replanner decision test plan.
Uses real LLM calls via EvalAgent credentials.

Run with:
    .venv/bin/python -m pytest backend/tests/test_e2e/test_replanner_cancel_redraft_live.py -v
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
    "  cancel_and_redraft — some tasks are stale (wrong files, wrong approach, "
    "invalidated by another task), cancel them and replace with corrected work.\n\n"
    "cancel_and_redraft cancels entire nodes and their subtrees. Replacement tasks "
    "are at the current DAG level only. If a replacement needs further decomposition, "
    "assign it to team_planner.\n\n"
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
        "A sibling task failed. Draft corrective tasks to recover the execution chain.\n"
    )

    lines.append("## Failed task")
    lines.append(f"**Task ID:** {failed_task}")
    if scope_paths:
        lines.append(f"**Scope:** {', '.join(scope_paths)}")
    lines.append(f"**Failure reason:** {failed_reason}\n")

    lines.append("## Sibling statuses (with subtree info)")
    for sib in sibling_statuses:
        status = sib["status"]
        task_id = sib["id"]
        desc = sib.get("desc", "")
        scope = sib.get("scope", "")
        kind = sib.get("kind", "atomic")
        children = sib.get("children", [])
        line = f"- **{task_id}** [{status}] ({kind})"
        if desc:
            line += f": {desc}"
        if scope:
            line += f" (scope: {scope})"
        lines.append(line)
        for child in children:
            c_status = child["status"]
            c_id = child["id"]
            c_desc = child.get("desc", "")
            c_scope = child.get("scope", "")
            c_line = f"  - **{c_id}** [{c_status}]"
            if c_desc:
                c_line += f": {c_desc}"
            if c_scope:
                c_line += f" (scope: {c_scope})"
            lines.append(c_line)
            # Grandchildren
            for gc in child.get("children", []):
                gc_line = f"    - **{gc['id']}** [{gc['status']}]"
                if gc.get("desc"):
                    gc_line += f": {gc['desc']}"
                if gc.get("scope"):
                    gc_line += f" (scope: {gc['scope']})"
                lines.append(gc_line)
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


async def _run_replanner(api_client, prompt: str) -> tuple[str, dict]:
    """Run the replanner and return (tool_name, tool_input)."""
    result = await run_trigger(
        messages=[],
        system_prompt=REPLANNER_SYSTEM,
        prompt=prompt,
        tools=REPLANNER_TOOLS,
        api_client=api_client,
        max_tokens_per_turn=2000,
    )
    return result.tool_name, result.tool_input


# ---------------------------------------------------------------------------
# Test 3: Wrong decomposition — per-consumer split → cancel_and_redraft
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_wrong_decomposition_picks_cancel_and_redraft(api_client):
    """All siblings fail because the plan split by consumer instead of source."""
    prompt = _build_replanner_prompt(
        failed_task="fix-io-compat",
        failed_reason="ImportError: missing symbol 'compat_shim' in pkg/_compat.py. Cannot fix from consumer side.",
        scope_paths=["pkg/io.py"],
        sibling_statuses=[
            {"id": "fix-io-compat", "status": "FAILED", "desc": "Fix compat usage in io module", "scope": "pkg/io.py"},
            {"id": "fix-parser-compat", "status": "FAILED", "desc": "Fix compat usage in parser", "scope": "pkg/parser.py"},
            {"id": "fix-cli-compat", "status": "FAILED", "desc": "Fix compat usage in CLI", "scope": "pkg/cli.py"},
            {"id": "fix-api-compat", "status": "FAILED", "desc": "Fix compat usage in API", "scope": "pkg/api.py"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-io-compat",
             "content": "Cannot fix from consumer side. pkg/io.py imports compat_shim from pkg/_compat.py but that symbol was never exported. The fix must be in pkg/_compat.py itself — no task owns that file."},
            {"author": "developer", "task_id": "fix-parser-compat",
             "content": "Same issue as fix-io-compat. pkg/parser.py imports compat_shim from pkg/_compat.py. The symbol is missing. This is a source-level problem, not a consumer problem."},
            {"author": "developer", "task_id": "fix-cli-compat",
             "content": "FAILED: pkg/cli.py cannot import compat_shim. The plan decomposed by consumer but the real fix is in the shared source pkg/_compat.py."},
            {"author": "developer", "task_id": "fix-api-compat",
             "content": "Same root cause. pkg/_compat.py needs compat_shim exported. All 4 consumer tasks are mis-scoped."},
        ],
    )

    tool_name, tool_input = await _run_replanner(api_client, prompt)
    assert tool_name == "cancel_and_redraft", f"Expected cancel_and_redraft for wrong decomposition, got {tool_name}"
    cancel_ids = set(tool_input.get("cancel_ids", []))
    assert len(cancel_ids) >= 3, f"Expected at least 3 cancellations, got {cancel_ids}"


# ---------------------------------------------------------------------------
# Test 4: One mis-scoped task, rest fine → cancel_and_redraft (local)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_single_misscoped_task_picks_cancel_and_redraft(api_client):
    """One task targets wrong file, others are healthy — narrow cancel scope."""
    prompt = _build_replanner_prompt(
        failed_task="fix-auth",
        failed_reason="File src/auth/helpers.py contains only re-exports. Actual validation logic is in src/auth/middleware.py.",
        scope_paths=["src/auth/helpers.py"],
        sibling_statuses=[
            {"id": "fix-auth", "status": "FAILED", "desc": "Fix auth validation", "scope": "src/auth/helpers.py"},
            {"id": "fix-db", "status": "DONE", "desc": "Fix database connection pooling", "scope": "src/db/pool.py"},
            {"id": "fix-cache", "status": "RUNNING", "desc": "Fix cache invalidation", "scope": "src/cache/invalidator.py"},
            {"id": "fix-logging", "status": "RUNNING", "desc": "Fix structured logging", "scope": "src/logging/formatter.py"},
            {"id": "fix-metrics", "status": "DONE", "desc": "Fix metrics collection", "scope": "src/metrics/collector.py"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-auth",
             "content": "src/auth/helpers.py is a thin re-export module with no validation logic. The actual auth validation lives in src/auth/middleware.py (discovered via imports). Task scope is wrong — need to target middleware.py instead."},
            {"author": "developer", "task_id": "fix-db",
             "content": "Fixed connection pooling. Tests pass. No auth-related files touched."},
            {"author": "developer", "task_id": "fix-cache",
             "content": "Progress: refactoring cache invalidation logic. 70% complete. No overlap with auth."},
            {"author": "developer", "task_id": "fix-logging",
             "content": "Progress: updating log formatters. No issues."},
            {"author": "developer", "task_id": "fix-metrics",
             "content": "Metrics collection fixed. All tests green."},
        ],
    )

    tool_name, tool_input = await _run_replanner(api_client, prompt)
    assert tool_name == "cancel_and_redraft", f"Expected cancel_and_redraft for mis-scoped task, got {tool_name}"
    cancel_ids = set(tool_input.get("cancel_ids", []))
    assert "fix-auth" in cancel_ids, f"Expected fix-auth in cancel_ids, got {cancel_ids}"
    # Should NOT cancel healthy siblings
    assert "fix-db" not in cancel_ids, "Should not cancel completed healthy sibling fix-db"
    assert "fix-cache" not in cancel_ids, "Should not cancel running healthy sibling fix-cache"


# ---------------------------------------------------------------------------
# Test 7: Expandable sibling's subtree all failing → cancel parent node
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_expandable_subtree_all_failing_cancels_parent(api_client):
    """All children of an expandable node failed — cancel the parent node."""
    prompt = _build_replanner_prompt(
        failed_task="fix-compat-base",
        failed_reason="pkg/_compat/base.py does not exist. The module structure assumed by compat-lane is wrong.",
        scope_paths=["pkg/_compat/base.py"],
        sibling_statuses=[
            {"id": "compat-lane", "status": "EXPANDED", "kind": "expandable",
             "desc": "Fix compat module (planned as 3 subtasks)", "scope": "pkg/_compat/",
             "children": [
                 {"id": "fix-compat-base", "status": "FAILED",
                  "desc": "Fix compat base module", "scope": "pkg/_compat/base.py"},
                 {"id": "fix-compat-ext", "status": "FAILED",
                  "desc": "Fix compat extensions (depends on base)", "scope": "pkg/_compat/ext.py"},
                 {"id": "verify-compat", "status": "FAILED",
                  "desc": "Verify compat module", "scope": "pkg/_compat/"},
             ]},
            {"id": "io-lane", "status": "EXPANDED", "kind": "expandable",
             "desc": "Fix IO module", "scope": "pkg/io/",
             "children": [
                 {"id": "fix-io-core", "status": "DONE", "desc": "Fix IO core", "scope": "pkg/io/core.py"},
                 {"id": "verify-io", "status": "DONE", "desc": "Verify IO", "scope": "pkg/io/"},
             ]},
            {"id": "parser-lane", "status": "RUNNING", "kind": "expandable",
             "desc": "Fix parser module", "scope": "pkg/parser/"},
            {"id": "cli-lane", "status": "DONE", "kind": "atomic",
             "desc": "Fix CLI entry points", "scope": "pkg/cli.py"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-compat-base",
             "content": "FAILED: pkg/_compat/base.py does not exist. The compat module is a single file pkg/_compat.py, not a package with submodules. The entire compat-lane decomposition assumed a package structure that doesn't exist."},
            {"author": "system", "task_id": "fix-compat-ext",
             "content": "FAILED: cascaded failure from fix-compat-base dependency."},
            {"author": "system", "task_id": "verify-compat",
             "content": "FAILED: nothing to verify — all upstream tasks failed."},
            {"author": "developer", "task_id": "fix-io-core",
             "content": "Fixed IO core exports. All tests pass."},
            {"author": "developer", "task_id": "verify-io",
             "content": "IO verification passed. All green."},
        ],
    )

    tool_name, tool_input = await _run_replanner(api_client, prompt)
    assert tool_name == "cancel_and_redraft", f"Expected cancel_and_redraft for subtree failure, got {tool_name}"
    cancel_ids = set(tool_input.get("cancel_ids", []))
    # Should cancel the expandable parent OR its children — both are valid
    # since cascade_cancel_recursive handles propagation.
    compat_cancelled = (
        "compat-lane" in cancel_ids
        or cancel_ids & {"fix-compat-base", "fix-compat-ext", "verify-compat"}
    )
    assert compat_cancelled, f"Expected compat-lane or its children in cancel_ids, got {cancel_ids}"
    # Should NOT cancel healthy siblings
    assert "io-lane" not in cancel_ids, "Should not cancel healthy io-lane"
    assert "cli-lane" not in cancel_ids, "Should not cancel healthy cli-lane"


# ---------------------------------------------------------------------------
# Test 8: Child failure reveals parent scope is stale → cancel expandable
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_child_reveals_stale_parent_scope(api_client):
    """A child task discovers the parent's scope assumption is wrong."""
    prompt = _build_replanner_prompt(
        failed_task="fix-session",
        failed_reason="src/auth/session.py does not exist. Session handling was moved to src/middleware/session.py.",
        scope_paths=["src/auth/session.py"],
        sibling_statuses=[
            {"id": "auth-lane", "status": "EXPANDED", "kind": "expandable",
             "desc": "Fix auth module (3 subtasks)", "scope": "src/auth/",
             "children": [
                 {"id": "fix-login", "status": "DONE",
                  "desc": "Fix login flow", "scope": "src/auth/login.py"},
                 {"id": "fix-session", "status": "FAILED",
                  "desc": "Fix session management", "scope": "src/auth/session.py"},
                 {"id": "verify-auth", "status": "PENDING",
                  "desc": "Verify auth module", "scope": "src/auth/"},
             ]},
            {"id": "api-lane", "status": "EXPANDED", "kind": "expandable",
             "desc": "Fix API endpoints", "scope": "src/api/",
             "children": [
                 {"id": "fix-routes", "status": "RUNNING", "desc": "Fix API routes", "scope": "src/api/routes.py"},
                 {"id": "fix-middleware", "status": "RUNNING", "desc": "Fix API middleware", "scope": "src/api/middleware.py"},
             ]},
            {"id": "db-lane", "status": "DONE", "kind": "expandable",
             "desc": "Fix database layer", "scope": "src/db/"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-login",
             "content": "Fixed login flow in src/auth/login.py. Tests pass. Login logic is correctly in src/auth/."},
            {"author": "developer", "task_id": "fix-session",
             "content": "FAILED: src/auth/session.py does not exist. Session handling was moved to src/middleware/session.py during the middleware refactor (3 weeks ago). The auth-lane scope assumed sessions were still in src/auth/ but they are in src/middleware/ now. fix-login succeeded because login.py is still in src/auth/, but the session scope is wrong."},
            {"author": "developer", "task_id": "fix-routes",
             "content": "Progress: updating route definitions. No auth overlap."},
            {"author": "developer", "task_id": "fix-middleware",
             "content": "Progress: fixing API middleware. Note: src/middleware/session.py exists here."},
        ],
    )

    tool_name, tool_input = await _run_replanner(api_client, prompt)
    assert tool_name == "cancel_and_redraft", f"Expected cancel_and_redraft for stale parent scope, got {tool_name}"
    cancel_ids = set(tool_input.get("cancel_ids", []))
    # Should cancel auth-lane or its stale children
    auth_cancelled = (
        "auth-lane" in cancel_ids
        or cancel_ids & {"fix-session", "verify-auth"}
    )
    assert auth_cancelled, f"Expected auth-lane or its stale children in cancel_ids, got {cancel_ids}"
    # Should NOT cancel unrelated lanes
    assert "api-lane" not in cancel_ids, "Should not cancel healthy api-lane"
    assert "db-lane" not in cancel_ids, "Should not cancel completed db-lane"
    # Replacement should exist
    add_tasks = tool_input.get("add_tasks", [])
    assert len(add_tasks) >= 1, "Expected at least one replacement task"


# ---------------------------------------------------------------------------
# Test 9: Two expandable siblings with overlapping scope → cancel both
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_overlapping_subtree_scope_cancels_both(api_client):
    """Two sibling subtrees editing the same files with incompatible changes need to be cancelled and unified."""
    prompt = _build_replanner_prompt(
        failed_task="fix-codec-io",
        failed_reason="Conflicting edit: pkg/codec.py was already modified by parser-lane with incompatible changes.",
        scope_paths=["pkg/codec.py"],
        sibling_statuses=[
            {"id": "io-lane", "status": "EXPANDED", "kind": "expandable",
             "desc": "Fix IO encoding pipeline", "scope": "pkg/codec.py, pkg/formats.py",
             "children": [
                 {"id": "fix-codec-io", "status": "FAILED",
                  "desc": "Fix codec for IO path", "scope": "pkg/codec.py"},
                 {"id": "fix-format-io", "status": "FAILED",
                  "desc": "Fix format handling for IO", "scope": "pkg/formats.py"},
                 {"id": "verify-io-encoding", "status": "PENDING",
                  "desc": "Verify IO encoding", "scope": "pkg/codec.py, pkg/formats.py"},
             ]},
            {"id": "parser-lane", "status": "EXPANDED", "kind": "expandable",
             "desc": "Fix parser encoding pipeline", "scope": "pkg/codec.py, pkg/formats.py",
             "children": [
                 {"id": "fix-format-parser", "status": "FAILED",
                  "desc": "Fix format handling for parser", "scope": "pkg/formats.py"},
                 {"id": "fix-codec-parser", "status": "FAILED",
                  "desc": "Fix codec for parser path", "scope": "pkg/codec.py"},
             ]},
            {"id": "cli-lane", "status": "DONE", "kind": "atomic",
             "desc": "Fix CLI entry points", "scope": "pkg/cli.py"},
            {"id": "test-lane", "status": "RUNNING", "kind": "atomic",
             "desc": "Update integration tests", "scope": "pkg/tests/"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-codec-io",
             "content": "FAILED: pkg/codec.py line 50-60 conflicts with parser-lane's changes. IO path needs codec.encode() to accept bytes, but parser path needs str. Both lanes edit pkg/codec.py and pkg/formats.py — the plan gave overlapping ownership to two separate lanes. This is a decomposition error: codec and format work should be in ONE lane, not two."},
            {"author": "developer", "task_id": "fix-format-io",
             "content": "FAILED: pkg/formats.py already modified by parser-lane/fix-format-parser with incompatible format registry. Same overlapping ownership problem as codec."},
            {"author": "developer", "task_id": "fix-codec-parser",
             "content": "FAILED: codec changes conflict with io-lane. Changed encode() to accept str only, but io-lane needs bytes. The plan split is wrong — both lanes own the same files."},
            {"author": "developer", "task_id": "fix-format-parser",
             "content": "FAILED: format changes conflict with io-lane. Both lanes are fighting over pkg/formats.py. Plan decomposition error."},
        ],
    )

    tool_name, tool_input = await _run_replanner(api_client, prompt)
    assert tool_name == "cancel_and_redraft", f"Expected cancel_and_redraft for overlapping scope, got {tool_name}"
    cancel_ids = set(tool_input.get("cancel_ids", []))
    # Should cancel both overlapping lanes or their children
    io_cancelled = "io-lane" in cancel_ids or cancel_ids & {"fix-codec-io", "fix-format-io", "verify-io-encoding"}
    parser_cancelled = "parser-lane" in cancel_ids or cancel_ids & {"fix-format-parser", "fix-codec-parser"}
    assert io_cancelled, f"Expected io-lane or its children in cancel_ids, got {cancel_ids}"
    assert parser_cancelled, f"Expected parser-lane or its children in cancel_ids, got {cancel_ids}"
    # Should NOT cancel unrelated healthy siblings
    assert "cli-lane" not in cancel_ids, "Should not cancel healthy cli-lane"


# ---------------------------------------------------------------------------
# Test 10: Deep nesting — cancel at correct mid-level depth
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_CREDENTIALS, reason="No credentials")
@pytest.mark.asyncio
async def test_deep_nesting_cancels_mid_level(api_client):
    """Deeply nested failure should cancel the mid-level expandable, not the top-level parent."""
    prompt = _build_replanner_prompt(
        failed_task="fix-handlers",
        failed_reason="Expected Flask request handlers but codebase uses FastAPI. All handler patterns wrong.",
        scope_paths=["src/api/handlers/"],
        sibling_statuses=[
            {"id": "backend-lane", "status": "EXPANDED", "kind": "expandable",
             "desc": "Fix backend services", "scope": "src/",
             "children": [
                 {"id": "api-sublane", "status": "EXPANDED", "kind": "expandable",
                  "desc": "Fix API layer (Flask handlers)", "scope": "src/api/",
                  "children": [
                      {"id": "fix-routes", "status": "DONE",
                       "desc": "Fix Flask route definitions", "scope": "src/api/routes.py"},
                      {"id": "fix-handlers", "status": "FAILED",
                       "desc": "Fix Flask request handlers", "scope": "src/api/handlers/"},
                      {"id": "verify-api", "status": "PENDING",
                       "desc": "Verify API layer", "scope": "src/api/"},
                  ]},
                 {"id": "db-sublane", "status": "EXPANDED", "kind": "expandable",
                  "desc": "Fix database layer (raw SQL)", "scope": "src/db/",
                  "children": [
                      {"id": "fix-queries", "status": "DONE",
                       "desc": "Fix SQL queries", "scope": "src/db/queries.py"},
                      {"id": "fix-migrations", "status": "RUNNING",
                       "desc": "Fix migration scripts", "scope": "src/db/migrations/"},
                  ]},
             ]},
            {"id": "frontend-lane", "status": "RUNNING", "kind": "expandable",
             "desc": "Fix frontend components", "scope": "src/frontend/"},
        ],
        notes=[
            {"author": "developer", "task_id": "fix-routes",
             "content": "Fixed route definitions in src/api/routes.py using Flask patterns. Tests pass. Note: these are Flask-style @app.route decorators."},
            {"author": "developer", "task_id": "fix-handlers",
             "content": "FAILED: Expected Flask request handlers (using request.args, request.json) but the codebase actually uses FastAPI (using Depends(), Query(), Body()). The entire api-sublane was planned assuming Flask, but the framework is FastAPI. fix-routes also used Flask patterns — those route definitions will need to be redone too. db-sublane is unaffected — it uses raw SQL, not the web framework."},
            {"author": "developer", "task_id": "fix-queries",
             "content": "Fixed SQL queries in src/db/queries.py. Tests pass. No web framework dependency."},
            {"author": "developer", "task_id": "fix-migrations",
             "content": "Progress: updating migration scripts. 50% complete. No web framework dependency."},
        ],
    )

    tool_name, tool_input = await _run_replanner(api_client, prompt)
    assert tool_name == "cancel_and_redraft", f"Expected cancel_and_redraft for deep nesting, got {tool_name}"
    cancel_ids = set(tool_input.get("cancel_ids", []))
    # Should cancel api-sublane or its children — NOT backend-lane or db-sublane
    api_cancelled = (
        "api-sublane" in cancel_ids
        or cancel_ids & {"fix-routes", "fix-handlers", "verify-api"}
    )
    assert api_cancelled, f"Expected api-sublane or its children in cancel_ids, got {cancel_ids}"
    assert "backend-lane" not in cancel_ids, "Should NOT cancel top-level backend-lane — db-sublane is healthy"
    assert "db-sublane" not in cancel_ids, "Should NOT cancel healthy db-sublane"
    assert "frontend-lane" not in cancel_ids, "Should NOT cancel unrelated frontend-lane"
