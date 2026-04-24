# ruff: noqa
"""Live E2E: daytona_shell tool edge cases -- pip install, CWD, team-agnostic execution.

Verifies the daytona_shell tool end-to-end using a real Daytona sandbox and a real LLM.

daytona_shell is team-agnostic: it enforces NO team-mode constraints (subprocess bans,
write restrictions, install bans). Those constraints live at the daytona_write_file /
daytona_edit_file layer. These tests verify daytona_shell is truly unconstrained.

Edge cases tested:
- pip install works (solo and team mode)
- shell() commands run from the correct cwd
- All operations are allowed regardless of team metadata
- read/write/shell helpers work correctly
- Error handling (exceptions, timeouts, exit codes, stderr)

Run with: pytest tests/test_e2e/test_live_shell_edge_cases.py -m live -v
"""

from __future__ import annotations

import json
import shlex
import uuid

import pytest

from engine.testing.eval_agent import EvalAgent
from tests.test_e2e.conftest import create_eval_agent, create_test_sandbox, delete_test_sandbox

pytestmark = [pytest.mark.e2e, pytest.mark.live, pytest.mark.asyncio]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CODEACT_PROMPT = (
    "You are a developer with a remote Daytona sandbox. "
    "You MUST use daytona_shell for every action -- never just describe what you'd do. "
    "daytona_shell runs raw shell commands via its command field. "
    "When asked to run Python code, translate it into a shell command such as python3 -c. "
    "Be concise. Do exactly what is asked."
)


def _shell_outputs(result) -> list:
    return [ev for ev in result.tools_completed() if ev.tool_name == "daytona_shell"]


def _assert_shell_completed(result) -> list:
    completed = _shell_outputs(result)
    assert completed, "No daytona_shell completions"
    assert not any(ev.is_error for ev in completed), (
        "daytona_shell returned an error: "
        + "; ".join(ev.output[:500] for ev in completed if ev.is_error)
    )
    return completed


def _prepare_overlay_git_workspace(sandbox_id: str) -> str:
    from tests.test_e2e.conftest import get_sandbox_service

    svc = get_sandbox_service()
    raw_sandbox = svc.get_sandbox_object(sandbox_id)
    home_resp = raw_sandbox.process.exec("pwd", timeout=10)
    home = (getattr(home_resp, "result", "") or "").strip() or "/home/daytona"
    workspace = f"{home}/shell_edge_overlay_repo"
    setup_cmd = " && ".join(
        [
            f"rm -rf {shlex.quote(workspace)}",
            f"mkdir -p {shlex.quote(workspace)}",
            f"cd {shlex.quote(workspace)}",
            "git init -q",
            "git config user.email shell-edge@test.invalid",
            "git config user.name shell-edge",
            "printf '%s\\n' '# shell edge overlay repo' > README.md",
            "git add README.md",
            "git commit -q -m seed",
        ]
    )
    response = raw_sandbox.process.exec(f"bash -lc {shlex.quote(setup_cmd)}", timeout=60)
    if getattr(response, "exit_code", 0) not in (0, None):
        pytest.fail(
            "Failed to prepare git workspace for daytona_shell overlay tests: "
            f"{getattr(response, 'result', '')}"
        )

    labels = dict(getattr(raw_sandbox, "labels", None) or {})
    labels["project_dir"] = workspace
    set_labels = getattr(raw_sandbox, "set_labels", None)
    if not callable(set_labels):
        pytest.skip("Daytona SDK sandbox does not support project_dir labels")
    set_labels(labels)
    return workspace


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sandbox_id():
    if not EvalAgent.has_all():
        pytest.skip("LLM + Daytona credentials required")
    sb = create_test_sandbox("shell-edge")
    _prepare_overlay_git_workspace(sb["id"])
    yield sb["id"]
    delete_test_sandbox(sb["id"])


@pytest.fixture(scope="module")
def agent(sandbox_id):
    """Solo-mode agent (no team constraints)."""
    return create_eval_agent(sandbox_id=sandbox_id, system_prompt=CODEACT_PROMPT)


# ===========================================================================
# AREA 1: pip install is allowed
# ===========================================================================


async def test_pip_install_allowed_solo_mode(agent):
    """Solo-mode agent can run pip install via daytona_shell without error."""
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        "result = shell('pip install --dry-run requests 2>&1 || true')\n"
        "print(result['exit_code'])"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    assert len(completed) >= 1, "No tool completions"

    for ev in completed:
        if ev.tool_name == "daytona_shell":
            assert "ambient runtime environment" not in ev.output, (
                f"pip install should be allowed: {ev.output}"
            )


async def test_pip_install_allowed_with_team_metadata(agent):
    """Even with team metadata injected, pip install is allowed (daytona_shell is team-agnostic)."""
    meta = agent._query_context.tool_metadata
    meta.agent_name = "developer"
    try:
        result = await agent.invoke(
            "Use daytona_shell with this Python code:\n"
            "result = shell('pip install --dry-run requests 2>&1 || true')\n"
            "print(result['exit_code'])"
        )
        assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

        completed = result.tools_completed()
        assert len(completed) >= 1, "No tool completions"

        for ev in completed:
            if ev.tool_name == "daytona_shell":
                assert "ambient runtime environment" not in ev.output, (
                    f"pip install should be allowed in team mode: {ev.output}"
                )
    finally:
        meta.agent_name = ""


# ===========================================================================
# AREA 2: CWD is correctly picked up
# ===========================================================================


async def test_cwd_is_set_in_shell_helper(agent):
    """shell() commands execute from the configured daytona_cwd."""
    result = await agent.invoke(
        "Use daytona_shell to run this exact command: pwd"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = _assert_shell_completed(result)
    shell_outputs_list = [ev.output for ev in completed]

    all_output = " ".join(shell_outputs_list) + " " + result.text
    assert "shell_edge_overlay_repo" in all_output, (
        f"Expected a real cwd path in output: {all_output[:500]}"
    )


async def test_cwd_consistent_across_shell_calls(agent):
    """Multiple shell() calls in one daytona_shell invocation share the same cwd."""
    marker = uuid.uuid4().hex[:8]
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        f"shell('echo {marker} > __cwd_test.txt')\n"
        "result = shell('cat __cwd_test.txt')\n"
        "print('CONTENT:', result['stdout'].strip())\n"
        "shell('rm -f __cwd_test.txt')"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert marker in all_output, (
        f"Marker {marker} not found -- shell() calls may not share cwd: {all_output[:500]}"
    )


# ===========================================================================
# AREA 3: daytona_shell is team-agnostic -- no constraints enforced
# ===========================================================================


async def test_shell_allows_subprocess_with_team_metadata(agent):
    """daytona_shell does not block subprocess calls even with team metadata."""
    meta = agent._query_context.tool_metadata
    meta.agent_name = "developer"
    try:
        result = await agent.invoke(
            "Use daytona_shell with this exact Python code:\n"
            "import subprocess\n"
            "proc = subprocess.run(['echo', 'SUBPROCESS_OK'], capture_output=True, text=True)\n"
            "print(proc.stdout.strip())"
        )
        assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

        completed = result.tools_completed()
        shell_results = [ev for ev in completed if ev.tool_name == "daytona_shell"]

        has_subprocess_rejection = any(
            ev.is_error and "shell(\"...\")" in ev.output
            for ev in shell_results
        )
        assert not has_subprocess_rejection, (
            "daytona_shell should allow subprocess (team-agnostic). Outputs: "
            + "; ".join(ev.output[:300] for ev in shell_results)
        )
    finally:
        meta.agent_name = ""


async def test_shell_allows_writes_with_validator_metadata(agent):
    """daytona_shell does not block writes even with validator team metadata."""
    meta = agent._query_context.tool_metadata
    meta.agent_name = "validator"
    try:
        marker = f"VALIDATOR_{uuid.uuid4().hex[:8]}"
        result = await agent.invoke(
            "Use daytona_shell with this Python code:\n"
            f"write('/tmp/validator_write_{marker}.txt', '{marker}')\n"
        )
        assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

        completed = result.tools_completed()
        shell_results = [ev for ev in completed if ev.tool_name == "daytona_shell"]

        has_write_rejection = any(
            ev.is_error and "must not write" in ev.output
            for ev in shell_results
        )
        assert not has_write_rejection, (
            "daytona_shell should allow validator writes (team-agnostic). Outputs: "
            + "; ".join(ev.output[:300] for ev in shell_results)
        )
    finally:
        meta.agent_name = ""


# ===========================================================================
# AREA 4: read(), write(), shell() helpers work correctly
# ===========================================================================


async def test_read_write_shell_roundtrip(agent):
    """Full daytona_shell helper roundtrip: write() -> shell(cat) -> verify content."""
    marker = f"ROUNDTRIP_{uuid.uuid4().hex[:8]}"
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        f"write('/tmp/shell_rt_{marker}.txt', '{marker}')\n"
        f"result = shell('cat /tmp/shell_rt_{marker}.txt')\n"
        "print('CONTENT:', result['stdout'].strip())\n"
        f"shell('rm -f /tmp/shell_rt_{marker}.txt')"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert marker in all_output, (
        f"Roundtrip marker {marker} not found in output: {all_output[:500]}"
    )


async def test_shell_captures_exit_code(agent):
    """shell() helper captures non-zero exit codes correctly."""
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        "result = shell('exit 42')\n"
        "print('EXIT_CODE:', result['exit_code'])"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert "42" in all_output, (
        f"Expected exit code 42 in output: {all_output[:500]}"
    )


async def test_shell_captures_stderr(agent):
    """shell() helper captures stderr output."""
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        "result = shell('echo STDERR_MARKER >&2')\n"
        "print('STDERR:', result['stderr'].strip())"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert "STDERR_MARKER" in all_output, (
        f"Expected STDERR_MARKER in output: {all_output[:500]}"
    )


# ===========================================================================
# AREA 5: Error handling edge cases
# ===========================================================================


async def test_shell_reports_python_exceptions(agent):
    """Python exceptions in user code are captured and reported."""
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        "raise ValueError('INTENTIONAL_ERROR_42')"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert "INTENTIONAL_ERROR_42" in all_output or "ValueError" in all_output, (
        f"Expected exception to be reported: {all_output[:500]}"
    )


async def test_shell_handles_timeout(agent):
    """shell() with a short timeout correctly reports timeout."""
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        "result = shell('sleep 10', timeout=2)\n"
        "print('EXIT_CODE:', result['exit_code'])\n"
        "print('STDERR:', result['stderr'])"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert "timeout" in all_output.lower() or "-1" in all_output, (
        f"Expected timeout indication in output: {all_output[:500]}"
    )


# ===========================================================================
# AREA 6: Multi-step execution
# ===========================================================================


async def test_multi_step_write_execute_verify(agent):
    """Agent performs write -> execute -> verify in a single daytona_shell call."""
    marker = f"MULTI_{uuid.uuid4().hex[:8]}"
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        f"write('/tmp/multi_{marker}.py', 'print(\"{marker}\")')\n"
        f"result = shell('python3 /tmp/multi_{marker}.py')\n"
        "print('OUTPUT:', result['stdout'].strip())\n"
        f"shell('rm -f /tmp/multi_{marker}.py')"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert marker in all_output, (
        f"Multi-step marker {marker} not found: {all_output[:500]}"
    )


async def test_read_helper_returns_file_content(agent):
    """read() helper returns file content and tracks the read in manifest."""
    marker = f"READ_{uuid.uuid4().hex[:8]}"
    result = await agent.invoke(
        "Use daytona_shell with this Python code:\n"
        f"shell('echo {marker} > /tmp/read_test_{marker}.txt')\n"
        f"content = read('/tmp/read_test_{marker}.txt')\n"
        "print('READ:', content.strip())\n"
        f"shell('rm -f /tmp/read_test_{marker}.txt')"
    )
    assert result.has_tool("daytona_shell"), f"Expected daytona_shell, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert marker in all_output, (
        f"Read marker {marker} not found: {all_output[:500]}"
    )
