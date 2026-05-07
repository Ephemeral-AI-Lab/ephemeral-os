"""Handler-per-command modules for the daemon ``api.*`` ops.

Each public verb has its own handler module under this package. Worker-level
shell scaffolding (mount, capture, OCC apply) lives in
:mod:`sandbox.runtime.daemon.service.shell_runner`; the dispatcher imports these
modules and registers their entry functions in ``OP_TABLE``.
"""

from . import edit, health, metrics, read, shell, workspace, write

__all__ = ["edit", "health", "metrics", "read", "shell", "workspace", "write"]
