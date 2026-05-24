"""Environment isolation tests for daemon ``overlay.run``.

Host environment variables (secrets, tokens, etc.) must not leak into the user
command. Only an explicit minimal allow-list plus any caller-supplied ``env``
should be visible to the child process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sandbox.daemon.rpc.dispatcher import dispatch_envelope_async
from sandbox.ephemeral_workspace.shell_contract import CommandExecRequest, ShellProcessResult
from sandbox.layer_stack import LayerStack, WriteLayerChange
from sandbox.overlay.layout import LayerPathsLayout


def _source(tmp_path: Path, name: str, content: bytes) -> str:
    path = tmp_path / "sources" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


async def _run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    command: tuple[str, ...],
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    manager = LayerStack(tmp_path / "stack")
    manager.publish_changes(
        [
            WriteLayerChange(
                path=".seed",
                source_path=_source(tmp_path, "seed", b"seed\n"),
            )
        ]
    )
    monkeypatch.setattr(
        "sandbox.daemon.handler.overlay._run_overlay_command",
        _fake_env_runner,
    )
    result = await dispatch_envelope_async(
        {
            "op": "overlay.run",
            "args": {
                "layer_stack_root": manager.storage_root.as_posix(),
                "request_id": "env-test",
                "command": list(command),
                "cwd": ".",
                "env": dict(env or {}),
                "timeout_seconds": 10,
            },
        }
    )
    return (
        int(result["exit_code"]),
        Path(str(result["stdout_ref"])).read_text(encoding="utf-8"),
        Path(str(result["stderr_ref"])).read_text(encoding="utf-8"),
    )


def _fake_env_runner(
    *,
    spec: LayerPathsLayout,
    request: CommandExecRequest,
    run_dir: str | Path,
    timings: dict[str, float],
) -> ShellProcessResult:
    from sandbox.daemon.handler.overlay import _OVERLAY_COMMAND_POLICY

    run_path = Path(run_dir)
    stdout_ref = run_path / "stdout.bin"
    stderr_ref = run_path / "stderr.bin"
    stdout_ref.parent.mkdir(parents=True, exist_ok=True)
    command_env = _OVERLAY_COMMAND_POLICY.command_environment(request.env)
    command = tuple(request.command)

    exit_code = 0
    stdout = ""
    if command == ("printenv", "AWS_ACCESS_KEY_ID"):
        stdout = command_env.get("AWS_ACCESS_KEY_ID", "")
        exit_code = 0 if stdout else 1
    elif command[:2] == ("sh", "-c") and "AWS_ACCESS_KEY_ID-unset" in command[2]:
        stdout = "|".join(
            command_env.get(key, "unset")
            for key in (
                "AWS_ACCESS_KEY_ID",
                "ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
            )
        )
    elif command[:2] == ("sh", "-c") and "$MY_VAR" in command[2]:
        stdout = command_env.get("MY_VAR", "")
    elif command[:2] == ("sh", "-c") and "printf ok" in command[2]:
        stdout = "ok"
    else:  # pragma: no cover - guard for future commands in this file
        raise AssertionError(f"unexpected test command: {command!r}")

    stdout_ref.write_text(stdout, encoding="utf-8")
    stderr_ref.write_text("", encoding="utf-8")
    timings["command_exec.run_command_s"] = 0.0
    return ShellProcessResult(
        exit_code=exit_code,
        stdout_ref=str(stdout_ref),
        stderr_ref=str(stderr_ref),
        mounted_workspace_root=spec.workspace_root,
        mount_mode="private_namespace",
    )


@pytest.mark.asyncio
async def test_host_secrets_do_not_leak_into_user_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-leaked")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leaked")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-leaked")

    exit_code, stdout, _stderr = await _run(
        tmp_path,
        monkeypatch,
        command=(
            "sh",
            "-c",
            "printf '%s|%s|%s' "
            "\"${AWS_ACCESS_KEY_ID-unset}\" "
            "\"${ANTHROPIC_API_KEY-unset}\" "
            "\"${OPENAI_API_KEY-unset}\"",
        ),
    )

    assert exit_code == 0
    assert stdout == "unset|unset|unset"


@pytest.mark.asyncio
async def test_printenv_does_not_expose_host_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-leaked")

    exit_code, stdout, _stderr = await _run(
        tmp_path,
        monkeypatch,
        command=("printenv", "AWS_ACCESS_KEY_ID"),
    )

    # printenv returns 1 when the requested var is unset; assert that and
    # confirm no value was printed.
    assert exit_code == 1
    assert stdout == ""


@pytest.mark.asyncio
async def test_caller_env_is_visible_to_user_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-leaked")

    exit_code, stdout, _stderr = await _run(
        tmp_path,
        monkeypatch,
        command=("sh", "-c", "printf '%s' \"$MY_VAR\""),
        env={"MY_VAR": "caller-value"},
    )

    assert exit_code == 0
    assert stdout == "caller-value"


@pytest.mark.asyncio
async def test_path_is_present_so_basic_commands_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The minimal env must include PATH (or POSIX builtin sh resolution
    # must work) so callers can keep invoking commands like ``printf``,
    # ``sh``, ``printenv`` without explicitly supplying PATH every time.
    exit_code, stdout, _stderr = await _run(
        tmp_path,
        monkeypatch,
        command=("sh", "-c", "printf ok"),
    )

    assert exit_code == 0
    assert stdout == "ok"
