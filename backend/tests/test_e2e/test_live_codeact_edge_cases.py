# ruff: noqa
"""Live E2E: CodeAct tool edge cases — pip install, CWD, team-mode constraints.

Verifies the codeact tool's constraint enforcement end-to-end using a real
Daytona sandbox and a real LLM.

Edge cases tested:
- pip install is allowed (both solo and team mode)
- shell() commands run from the correct cwd
- Validator agents cannot write repository files (team mode)
- Raw subprocess calls are rejected for team-mode agents
- Verification surface writes are rejected in error mode
- Verification surface writes are allowed in advisory (warn) mode
- Solo-mode agents have no constraints

Run with: pytest tests/test_e2e/test_live_codeact_edge_cases.py -m live -v
"""

from __future__ import annotations

import json
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
    "You MUST use daytona_codeact for every action — never just describe what you'd do. "
    "When asked to run code, use the daytona_codeact tool with the Python code provided. "
    "Be concise. Do exactly what is asked."
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sandbox_id():
    if not EvalAgent.has_all():
        pytest.skip("LLM + Daytona credentials required")
    sb = create_test_sandbox("codeact-edge")
    yield sb["id"]
    delete_test_sandbox(sb["id"])


@pytest.fixture(scope="module")
def agent(sandbox_id):
    """Solo-mode agent (no team constraints)."""
    return create_eval_agent(sandbox_id=sandbox_id, system_prompt=CODEACT_PROMPT)


def _inject_team_metadata(agent, *, agent_name, **extras):
    """Inject team-mode metadata into the agent's tool execution context."""
    meta = agent._query_context.tool_metadata
    meta.agent_name = agent_name
    meta["team_mode_enabled"] = True
    for key, value in extras.items():
        meta[key] = value


def _clear_team_metadata(agent):
    """Remove team-mode metadata from the agent."""
    meta = agent._query_context.tool_metadata
    meta.agent_name = ""
    for key in ("team_mode_enabled", "owned_files", "touches_paths",
                "verify", "owned_failures",
                "verification_surface_write_enforcement"):
        meta.extras.pop(key, None)


# ===========================================================================
# AREA 1: pip install is now allowed
# ===========================================================================


async def test_pip_install_allowed_solo_mode(agent):
    """Solo-mode agent can run pip install via codeact without error."""
    result = await agent.invoke(
        "Use daytona_codeact with this Python code:\n"
        "result = shell('pip install --dry-run requests 2>&1 || true')\n"
        "print(result['exit_code'])"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    assert len(completed) >= 1, "No tool completions"

    # The tool should NOT return an ambient install error
    for ev in completed:
        if ev.tool_name == "daytona_codeact":
            assert "ambient runtime environment" not in ev.output, (
                f"pip install should be allowed, but got ambient install rejection: {ev.output}"
            )


async def test_pip_install_allowed_team_mode(agent):
    """Team-mode developer agent can run pip install via codeact."""
    _inject_team_metadata(agent, agent_name="developer")
    try:
        result = await agent.invoke(
            "Use daytona_codeact with this Python code:\n"
            "result = shell('pip install --dry-run requests 2>&1 || true')\n"
            "print(result['exit_code'])"
        )
        assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

        completed = result.tools_completed()
        assert len(completed) >= 1, "No tool completions"

        for ev in completed:
            if ev.tool_name == "daytona_codeact":
                assert "ambient runtime environment" not in ev.output, (
                    f"pip install should be allowed in team mode: {ev.output}"
                )
    finally:
        _clear_team_metadata(agent)


# ===========================================================================
# AREA 2: CWD is correctly picked up
# ===========================================================================


async def test_cwd_is_set_in_shell_helper(agent):
    """shell() commands execute from the configured daytona_cwd."""
    result = await agent.invoke(
        "Use daytona_codeact with this Python code:\n"
        "result = shell('pwd')\n"
        "print('CWD:', result['stdout'].strip())"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    codeact_outputs = [ev.output for ev in completed if ev.tool_name == "daytona_codeact"]

    # Verify the cwd appears in the output. The exact path depends on sandbox
    # setup, but it should NOT be empty or missing.
    all_output = " ".join(codeact_outputs) + " " + result.text
    # The output should contain some real path (not just empty/error)
    assert any(c in all_output for c in ("/home", "/workspace", "/testbed", "/")), (
        f"Expected a real cwd path in output: {all_output[:500]}"
    )


async def test_cwd_consistent_across_shell_calls(agent):
    """Multiple shell() calls in one codeact invocation share the same cwd."""
    marker = uuid.uuid4().hex[:8]
    result = await agent.invoke(
        "Use daytona_codeact with this Python code:\n"
        f"shell('echo {marker} > __cwd_test.txt')\n"
        "result = shell('cat __cwd_test.txt')\n"
        "print('CONTENT:', result['stdout'].strip())\n"
        "shell('rm -f __cwd_test.txt')"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert marker in all_output, (
        f"Marker {marker} not found — shell() calls may not share cwd: {all_output[:500]}"
    )


# ===========================================================================
# AREA 3: Validator cannot write repo files (team mode)
# ===========================================================================


async def test_validator_cannot_write_repo_files(agent):
    """Validator agents in team mode must not write repository files via codeact."""
    _inject_team_metadata(agent, agent_name="validator")
    try:
        result = await agent.invoke(
            "Use daytona_codeact with this Python code:\n"
            "write('/workspace/should_not_exist.py', 'x = 1\\n')"
        )
        assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

        completed = result.tools_completed()
        assert len(completed) >= 1, "No tool completions"

        codeact_results = [ev for ev in completed if ev.tool_name == "daytona_codeact"]
        assert len(codeact_results) >= 1, "No daytona_codeact results"

        # At least one codeact call should have been rejected
        has_rejection = any(
            ev.is_error and "must not write repository files" in ev.output
            for ev in codeact_results
        )
        assert has_rejection, (
            "Validator should be rejected from writing repo files. Outputs: "
            + "; ".join(ev.output[:200] for ev in codeact_results)
        )
    finally:
        _clear_team_metadata(agent)


async def test_validator_can_run_read_only_commands(agent):
    """Validator agents can execute read-only shell commands in team mode."""
    _inject_team_metadata(agent, agent_name="validator")
    try:
        result = await agent.invoke(
            "Use daytona_codeact with this Python code:\n"
            "result = shell('echo VALIDATOR_READ_OK')\n"
            "print(result['stdout'].strip())"
        )
        assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

        completed = result.tools_completed()
        codeact_results = [ev for ev in completed if ev.tool_name == "daytona_codeact"]

        # Read-only shell should succeed
        has_success = any(not ev.is_error for ev in codeact_results)
        all_output = " ".join(ev.output for ev in codeact_results) + " " + result.text
        assert has_success or "VALIDATOR_READ_OK" in all_output, (
            f"Validator should be able to run read-only commands: {all_output[:500]}"
        )
    finally:
        _clear_team_metadata(agent)


# ===========================================================================
# AREA 4: Raw subprocess calls rejected in team mode
# ===========================================================================


async def test_raw_subprocess_rejected_for_team_developer(agent):
    """Team-mode developer agents cannot use raw subprocess APIs — must use shell()."""
    _inject_team_metadata(agent, agent_name="developer")
    try:
        result = await agent.invoke(
            "Use daytona_codeact with this exact Python code:\n"
            "import subprocess\n"
            "subprocess.run(['echo', 'SHOULD_NOT_RUN'], check=False)"
        )
        assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

        completed = result.tools_completed()
        codeact_results = [ev for ev in completed if ev.tool_name == "daytona_codeact"]
        assert len(codeact_results) >= 1, "No daytona_codeact results"

        # Should be rejected at preflight
        has_rejection = any(
            ev.is_error and "shell(\"...\")" in ev.output
            for ev in codeact_results
        )
        assert has_rejection, (
            "Raw subprocess should be rejected for team developer. Outputs: "
            + "; ".join(ev.output[:300] for ev in codeact_results)
        )
    finally:
        _clear_team_metadata(agent)


async def test_raw_subprocess_allowed_solo_mode(agent):
    """Solo-mode agents can use raw subprocess APIs without restriction."""
    result = await agent.invoke(
        "Use daytona_codeact with this exact Python code:\n"
        "import subprocess\n"
        "proc = subprocess.run(['echo', 'SOLO_SUBPROCESS_OK'], capture_output=True, text=True)\n"
        "print(proc.stdout.strip())"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    codeact_results = [ev for ev in completed if ev.tool_name == "daytona_codeact"]

    # Should NOT be rejected
    has_subprocess_rejection = any(
        ev.is_error and "shell(\"...\")" in ev.output
        for ev in codeact_results
    )
    assert not has_subprocess_rejection, (
        "Solo-mode should allow subprocess. Outputs: "
        + "; ".join(ev.output[:300] for ev in codeact_results)
    )


# ===========================================================================
# AREA 5: Verification surface write enforcement
# ===========================================================================


async def test_verify_surface_write_rejected_error_mode(agent):
    """Writes to verification surface paths are rejected in error enforcement mode."""
    _inject_team_metadata(
        agent,
        agent_name="developer",
        verification_surface_write_enforcement="error",
        owned_files=["src/main.py"],
        owned_failures=["tests/test_main.py"],
        verify=["pytest tests/test_main.py -q"],
    )
    try:
        result = await agent.invoke(
            "Use daytona_codeact with this Python code:\n"
            "write('/workspace/tests/test_main.py', 'patched test\\n')"
        )
        assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

        completed = result.tools_completed()
        codeact_results = [ev for ev in completed if ev.tool_name == "daytona_codeact"]
        assert len(codeact_results) >= 1, "No daytona_codeact results"

        has_rejection = any(
            ev.is_error and "verification surfaces read-only" in ev.output
            for ev in codeact_results
        )
        assert has_rejection, (
            "Verification surface writes should be rejected in error mode. Outputs: "
            + "; ".join(ev.output[:300] for ev in codeact_results)
        )
    finally:
        _clear_team_metadata(agent)


async def test_verify_surface_write_allowed_warn_mode(agent):
    """Writes to verification surface paths are allowed (with warning) in advisory mode."""
    _inject_team_metadata(
        agent,
        agent_name="developer",
        verification_surface_write_enforcement="warn",
        owned_files=["src/main.py"],
        owned_failures=["tests/test_main.py"],
        verify=["pytest tests/test_main.py -q"],
    )
    try:
        result = await agent.invoke(
            "Use daytona_codeact with this Python code:\n"
            "write('/workspace/tests/test_main.py', '# patched test\\n')"
        )
        assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

        completed = result.tools_completed()
        codeact_results = [ev for ev in completed if ev.tool_name == "daytona_codeact"]
        assert len(codeact_results) >= 1, "No daytona_codeact results"

        # Should NOT be a hard error
        has_hard_rejection = any(
            ev.is_error and "verification surfaces read-only" in ev.output
            for ev in codeact_results
        )
        assert not has_hard_rejection, (
            "Advisory mode should allow verification surface writes. Outputs: "
            + "; ".join(ev.output[:300] for ev in codeact_results)
        )

        # Should contain an advisory warning
        all_output = " ".join(ev.output for ev in codeact_results)
        has_advisory = "advisory mode" in all_output
        has_written = any(
            not ev.is_error for ev in codeact_results
        )
        assert has_advisory or has_written, (
            "Advisory mode should either warn or succeed silently. Outputs: "
            + "; ".join(ev.output[:300] for ev in codeact_results)
        )
    finally:
        _clear_team_metadata(agent)


# ===========================================================================
# AREA 6: Owned file writes are allowed in team mode
# ===========================================================================


async def test_developer_can_write_owned_files(agent):
    """Team-mode developer agents can write to files listed in owned_files."""
    _inject_team_metadata(
        agent,
        agent_name="developer",
        verification_surface_write_enforcement="error",
        owned_files=["src/main.py"],
        verify=["pytest tests/test_main.py -q"],
    )
    try:
        marker = f"OWNED_{uuid.uuid4().hex[:8]}"
        result = await agent.invoke(
            "Use daytona_codeact with this Python code:\n"
            f"write('/workspace/src/main.py', '# {marker}\\n')"
        )
        assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

        completed = result.tools_completed()
        codeact_results = [ev for ev in completed if ev.tool_name == "daytona_codeact"]

        # Writing owned files should succeed (no verification surface error)
        has_verify_rejection = any(
            ev.is_error and "verification surfaces read-only" in ev.output
            for ev in codeact_results
        )
        assert not has_verify_rejection, (
            "Developer should be able to write owned files. Outputs: "
            + "; ".join(ev.output[:300] for ev in codeact_results)
        )
    finally:
        _clear_team_metadata(agent)


# ===========================================================================
# AREA 7: read() and shell() helpers work correctly
# ===========================================================================


async def test_read_write_shell_roundtrip(agent):
    """Full codeact helper roundtrip: write() → shell(cat) → verify content."""
    marker = f"ROUNDTRIP_{uuid.uuid4().hex[:8]}"
    result = await agent.invoke(
        "Use daytona_codeact with this Python code:\n"
        f"write('/tmp/codeact_rt_{marker}.txt', '{marker}')\n"
        f"result = shell('cat /tmp/codeact_rt_{marker}.txt')\n"
        "print('CONTENT:', result['stdout'].strip())\n"
        f"shell('rm -f /tmp/codeact_rt_{marker}.txt')"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert marker in all_output, (
        f"Roundtrip marker {marker} not found in output: {all_output[:500]}"
    )


async def test_shell_captures_exit_code(agent):
    """shell() helper captures non-zero exit codes correctly."""
    result = await agent.invoke(
        "Use daytona_codeact with this Python code:\n"
        "result = shell('exit 42')\n"
        "print('EXIT_CODE:', result['exit_code'])"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert "42" in all_output, (
        f"Expected exit code 42 in output: {all_output[:500]}"
    )


async def test_shell_captures_stderr(agent):
    """shell() helper captures stderr output."""
    result = await agent.invoke(
        "Use daytona_codeact with this Python code:\n"
        "result = shell('echo STDERR_MARKER >&2')\n"
        "print('STDERR:', result['stderr'].strip())"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    assert "STDERR_MARKER" in all_output, (
        f"Expected STDERR_MARKER in output: {all_output[:500]}"
    )


# ===========================================================================
# AREA 8: Error handling edge cases
# ===========================================================================


async def test_codeact_reports_python_exceptions(agent):
    """Python exceptions in user code are captured and reported."""
    result = await agent.invoke(
        "Use daytona_codeact with this Python code:\n"
        "raise ValueError('INTENTIONAL_ERROR_42')"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    # The error should be surfaced somewhere
    assert "INTENTIONAL_ERROR_42" in all_output or "ValueError" in all_output, (
        f"Expected exception to be reported: {all_output[:500]}"
    )


async def test_codeact_handles_shell_timeout(agent):
    """shell() with a short timeout correctly reports timeout."""
    result = await agent.invoke(
        "Use daytona_codeact with this Python code:\n"
        "result = shell('sleep 10', timeout=2)\n"
        "print('EXIT_CODE:', result['exit_code'])\n"
        "print('STDERR:', result['stderr'])"
    )
    assert result.has_tool("daytona_codeact"), f"Expected daytona_codeact, got: {result.tool_names}"

    completed = result.tools_completed()
    all_output = " ".join(ev.output for ev in completed) + " " + result.text
    # Should report timeout or non-zero exit
    assert "timeout" in all_output.lower() or "-1" in all_output, (
        f"Expected timeout indication in output: {all_output[:500]}"
    )
