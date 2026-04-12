"""Daytona-specific background launch preparation.

The engine's background launch path is tool-agnostic and does NOT sniff
tool names. When a tool needs physical cancel semantics (i.e. killing an
OS process in a sandbox), that logic lives here rather than in
``engine/core/query.py``. The engine calls
:func:`prepare_background_launch` unconditionally for every background
dispatch; the function is a no-op for tools it does not handle.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Async callback that physically kills the sandbox process.
KillCallback = Callable[[], Coroutine[Any, Any, None]]


def _wrap_command_with_pid_tracking(command: str, task_id: str) -> str:
    """Wrap a shell command to record its PID in a temp file.

    The command runs inside its own session/process group via ``setsid``
    so that cancel can signal the entire tree (wrapper + command + any
    children it spawns). Without this, children of constructs like
    ``cd dir && python run.py`` get orphaned and keep mutating shared
    state after cancel.

    The user command is passed to the inner shell base64-encoded to
    avoid any quoting/escaping footguns: a stray single quote in
    ``command`` would otherwise terminate the ``sh -c '...'`` wrapper
    and allow unintended shell evaluation.
    """
    pid_file = f"/tmp/.eos_bg_{task_id}.pid"
    encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
    # Inside the setsid'd sh: record our own PID (== PGID) then exec the
    # decoded user command. `exec` replaces the shell so signals target
    # the user process directly; the PGID remains stable because exec
    # does not change it.
    # NOTE: `base64 -d` is GNU/BusyBox; the Daytona sandbox runs Linux,
    # so this is portable for our deployment. If the sandbox ever moves
    # to BSD/macOS the flag becomes `-D`.
    inner = (
        f'echo $$ > {pid_file}; '
        f'exec sh -c "$(echo {encoded} | base64 -d)"'
    )
    return f"setsid sh -c '{inner}' < /dev/null"


def _make_kill_callback(sandbox: Any, task_id: str) -> KillCallback:
    """Create a callback that kills the sandbox process for a background task.

    Sends a kill signal to the PID written by the wrapped command.
    """
    pid_file = f"/tmp/.eos_bg_{task_id}.pid"

    async def _kill() -> None:
        try:
            # PID file holds the session leader's PID, which equals the
            # process group ID (set via setsid). `kill -- -PGID` signals
            # every process in the group, killing the whole tree.
            # Guard empty PID explicitly so `kill -- -` isn't invoked
            # with an empty arg (harmless but noisy) when the file is
            # missing or the wrapper shell was killed before it wrote $$.
            kill_script = (
                f"PID=$(cat {pid_file} 2>/dev/null); "
                f'if [ -n "$PID" ]; then '
                f"  kill -TERM -- -$PID 2>/dev/null; "
                f"  sleep 0.2; "
                f"  kill -KILL -- -$PID 2>/dev/null; "
                f"fi; "
                f"rm -f {pid_file}"
            )
            await sandbox.process.exec(kill_script, timeout=5)
        except Exception as exc:
            # Kill failure can leave an orphaned process group — log
            # loud enough to be visible at default log level.
            logger.warning(
                "Failed to kill background process for task %s: %s", task_id, exc
            )

    return _kill


def prepare_background_launch(
    tool_name: str,
    tool_input: dict[str, Any],
    task_id: str,
    sandbox: Any | None,
) -> tuple[dict[str, Any], KillCallback | None]:
    """Return ``(prepared_input, kill_callback)`` for a background launch.

    All tools get their input returned unchanged with ``None`` for the
    callback. The engine calls this unconditionally so it never has to
    know which tools need special handling.
    """
    return tool_input, None
