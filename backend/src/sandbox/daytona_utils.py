"""Back-compat shim — prefer ``sandbox.daytona.*`` for new code.

The original ``daytona_utils`` kitchen-sink module has been carved into
focused sub-modules under :mod:`sandbox.daytona`:

* :mod:`sandbox.daytona.bash`       — bash wrapping + exit-code extraction
* :mod:`sandbox.daytona.exec_files` — remote file I/O via ``process.exec``
* :mod:`sandbox.daytona.paths`      — path resolution helpers
* :mod:`sandbox.daytona.recovery`   — sandbox attach + restart recovery

This module re-exports the prior public surface so existing importers keep
working unchanged. New code should import from the focused sub-modules
directly.
"""

from __future__ import annotations

from sandbox.daytona import _SandboxContext
from sandbox.daytona.bash import (
    _EXIT_MARKER,
    _PROJECT_VENV_BIN_EXPORT,
    _PYTHON3_SHIM,
    _TRAILING_TERM_NOISE_RE,
    _USER_LOCAL_BIN_EXPORT,
    _extract_exit_code,
    _wrap_bash_command,
)
from sandbox.daytona.exec_files import (
    _REMOTE_WRITE_CHUNK_BYTES,
    _build_append_text_file_chunk_command,
    _build_read_text_file_command,
    _build_remove_file_command,
    _build_replace_file_command,
    _build_truncate_text_file_command,
    _build_write_text_file_command,
    _build_write_text_file_commands,
    _delete_file_via_exec,
    _exec_command,
    _read_text_file_via_exec,
    _supports_exec_transport,
    _write_text_file_via_exec,
)
from sandbox.daytona.paths import (
    _get_repo_root,
    _normalized_path,
    _path_error,
    _resolve_path,
)
from sandbox.daytona.recovery import (
    _SANDBOX_RECOVERY_KEY,
    _SANDBOX_RECOVERY_PATTERNS,
    _attach_sandbox_to_context,
    _is_recoverable_sandbox_error,
    _recover_sandbox,
    _require_sandbox,
    _run_with_recovery,
    _sandbox_context_error,
)

__all__ = [
    "_EXIT_MARKER",
    "_PROJECT_VENV_BIN_EXPORT",
    "_PYTHON3_SHIM",
    "_REMOTE_WRITE_CHUNK_BYTES",
    "_SANDBOX_RECOVERY_KEY",
    "_SANDBOX_RECOVERY_PATTERNS",
    "_SandboxContext",
    "_TRAILING_TERM_NOISE_RE",
    "_USER_LOCAL_BIN_EXPORT",
    "_attach_sandbox_to_context",
    "_build_append_text_file_chunk_command",
    "_build_read_text_file_command",
    "_build_remove_file_command",
    "_build_replace_file_command",
    "_build_truncate_text_file_command",
    "_build_write_text_file_command",
    "_build_write_text_file_commands",
    "_delete_file_via_exec",
    "_exec_command",
    "_extract_exit_code",
    "_get_repo_root",
    "_is_recoverable_sandbox_error",
    "_normalized_path",
    "_path_error",
    "_read_text_file_via_exec",
    "_recover_sandbox",
    "_require_sandbox",
    "_resolve_path",
    "_run_with_recovery",
    "_sandbox_context_error",
    "_supports_exec_transport",
    "_wrap_bash_command",
    "_write_text_file_via_exec",
]
