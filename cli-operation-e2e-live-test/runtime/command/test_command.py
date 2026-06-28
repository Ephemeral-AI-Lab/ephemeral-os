"""runtime · command: exec inside a persistent session and as a one-shot."""

from runtime.command import helpers as cmd


def test_exec_in_session(workspace_session):
    sandbox_id, ws_id = workspace_session
    result = cmd.exec_command(
        sandbox_id, "echo hello-from-session", workspace_session_id=ws_id
    )
    assert result["exit_code"] == 0, result
    assert "hello-from-session" in result["output"]


def test_exec_one_shot(sandbox):
    result = cmd.exec_command(sandbox, "pwd")
    assert result["exit_code"] == 0, result


# Extension points (kept out of the skeleton to avoid timing-sensitive flakiness):
# start a long-lived command so exec_command returns status "running" with a
# command_session_id, then drive it with helpers.write_command_stdin /
# helpers.read_command_lines.
