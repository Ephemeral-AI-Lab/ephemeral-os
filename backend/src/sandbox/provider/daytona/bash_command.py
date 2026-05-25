"""Build Daytona bash commands and recover their exit codes."""

from __future__ import annotations

import logging
import re
import shlex

logger = logging.getLogger(__name__)

EXIT_MARKER = "__CODEX_EXIT_CODE__="
_UNPARSEABLE_EXIT_WARNED = False

_USER_LOCAL_BIN_EXPORT = 'export PATH="$HOME/.local/bin:$PATH"'
_PROJECT_VENV_BIN_EXPORT = 'if [ -d .venv/bin ]; then export PATH="$PWD/.venv/bin:$PATH"; fi'
_PYTHON3_SHIM = (
    'if ! command -v python >/dev/null 2>&1 && command -v python3 >/dev/null 2>&1; '
    'then python() { command python3 "$@"; }; fi'
)
_TRAILING_TERM_NOISE_RE = re.compile(
    r"(?:\x1b\[[0-9;]*[A-Za-z]|TERM environment variable not set\.)+\s*$"
)


def wrap_bash_command(command: str, *, cwd: str | None = None) -> str:
    cd_command = f"cd {shlex.quote(cwd)}\n" if cwd else ""
    script = (
        f"{_USER_LOCAL_BIN_EXPORT}\n"
        f"{cd_command}"
        f"{_PROJECT_VENV_BIN_EXPORT}\n"
        f"{_PYTHON3_SHIM}\n"
        f"{command}\n"
        "__codex_exit_code=$?\n"
        f'printf "\\n{EXIT_MARKER}%s\\n" "$__codex_exit_code"\n'
        'exit "$__codex_exit_code"'
    )
    return f"env -u LC_ALL bash -o pipefail -lc {shlex.quote(script)}"


def extract_exit_code(
    output: str,
    *,
    fallback_exit_code: int | str | None,
) -> tuple[str, int]:
    """Recover the wrapped command's exit code from sandbox output.

    Returns ``(sanitized_output, exit_code)``. Fails closed: if no
    ``__CODEX_EXIT_CODE__=`` marker is present AND the SDK fallback is missing
    or non-numeric, returns sentinel ``255`` so a failed remote command is not
    silently reported as success.
    """
    global _UNPARSEABLE_EXIT_WARNED
    sanitized = _TRAILING_TERM_NOISE_RE.sub("", output or "").rstrip()
    matches = list(
        re.finditer(rf"\n?{re.escape(EXIT_MARKER)}(-?\d+)", sanitized, flags=re.S)
    )
    if matches:
        marker = matches[-1]
        resolved = int(marker.group(1))
        cleaned = sanitized[: marker.start()]
        if cleaned.endswith("\n"):
            cleaned = cleaned[:-1]
        return cleaned, resolved
    if fallback_exit_code is None:
        if not _UNPARSEABLE_EXIT_WARNED:
            logger.warning(
                "Daytona response missing exit-code marker and SDK exit_code; "
                "reporting failure (sentinel=255)"
            )
            _UNPARSEABLE_EXIT_WARNED = True
        return sanitized, 255
    if isinstance(fallback_exit_code, int):
        return sanitized, fallback_exit_code
    stripped = fallback_exit_code.strip()
    if stripped.lstrip("-").isdigit():
        return sanitized, int(stripped)
    if not _UNPARSEABLE_EXIT_WARNED:
        logger.warning(
            "Unparseable Daytona exit_code=%r; reporting failure (sentinel=255)",
            fallback_exit_code,
        )
        _UNPARSEABLE_EXIT_WARNED = True
    return sanitized, 255


__all__ = ["EXIT_MARKER", "extract_exit_code", "wrap_bash_command"]
