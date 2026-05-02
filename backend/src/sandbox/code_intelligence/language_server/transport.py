"""Backend-availability probe for the LSP child process.

Phase 3.6 rewire: the per-call ``python3 -c 'import jedi; ...'`` shim was
deleted from this module — :class:`LspClient` now talks to a persistent
``basedpyright-langserver`` child via :class:`LspBackendChild` over JSON-RPC
stdio. What remains here is the cheap probe that
``LspClient.ensure_ready(install_missing=True)`` uses to decide whether the
chosen backend is launchable on the current sandbox.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

from sandbox.api.bash import extract_exit_code, wrap_bash_command
from sandbox.api.transport import SandboxTransport
from sandbox.client.async_bridge import run_sync
from sandbox.code_intelligence.language_server.lsp_child import LSP_BACKEND_CHOSEN

logger = logging.getLogger("sandbox.code_intelligence.language_server.client")


class LspTransportMixin:
    _workspace_root: str
    _sandbox: Any
    _transport: SandboxTransport | None
    _sandbox_id: str

    # -- Backend availability -------------------------------------------------

    def _check_python_backend(self) -> bool:
        """Phase 3.6: probe for the chosen LSP launch binary.

        ``LSP_BACKEND_CHOSEN`` was set in Stage A
        (lsp-qualification-spike-result.md). The sandbox-side check uses
        the binary on PATH; the local fallback uses Python ``-c import``
        only as a sanity check that the package landed in the venv.
        """
        if LSP_BACKEND_CHOSEN == "basedpyright":
            local_cmd = ["python3", "-c", "import basedpyright"]
            sandbox_cmd = "command -v basedpyright-langserver"
        else:  # pyright
            local_cmd = ["pyright-langserver", "--help"]
            sandbox_cmd = "command -v pyright-langserver"
        return self._check_backend(local_cmd=local_cmd, sandbox_cmd=sandbox_cmd)

    def _check_backend(self, *, local_cmd: list[str], sandbox_cmd: str) -> bool:
        try:
            if self._transport is not None and self._sandbox_id:
                return (
                    self._run_sandbox_command_exit_code(sandbox_cmd, timeout=10) == 0
                )
            if self._sandbox:
                exit_code = self._run_sandbox_command_exit_code(sandbox_cmd, timeout=10)
                return exit_code == 0
            proc = subprocess.run(
                local_cmd,
                capture_output=True,
                timeout=10,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def _install_python_backend(self) -> bool:
        """Best-effort install of the chosen LSP backend on the sandbox."""
        if self._transport is None and not self._sandbox:
            return False
        if LSP_BACKEND_CHOSEN == "basedpyright":
            cmd = (
                "python3 -m pip install --no-cache-dir --retries 10 "
                "--timeout 300 basedpyright"
            )
        else:  # pyright
            cmd = "npm install -g pyright"
        return self._run_sandbox_install(cmd)

    def _run_sandbox_install(self, command: str) -> bool:
        try:
            exit_code = self._run_sandbox_command_exit_code(command, timeout=600)
            return exit_code == 0
        except Exception:
            logger.debug("LSP backend install failed: %s", command, exc_info=True)
            return False

    def _run_sandbox_command_exit_code(self, command: str, *, timeout: int) -> int:
        """Run a sandbox command and recover its shell exit code."""
        if self._transport is not None and self._sandbox_id:
            transport_result = run_sync(
                self._transport.exec(self._sandbox_id, command, timeout=timeout)
            )
            return transport_result.exit_code
        response = run_sync(
            self._sandbox.process.exec(
                wrap_bash_command(command),
                timeout=timeout,
            )
        )
        result = str(getattr(response, "result", "") or "")
        _cleaned, exit_code = extract_exit_code(
            result,
            fallback_exit_code=getattr(response, "exit_code", None),
        )
        return exit_code
