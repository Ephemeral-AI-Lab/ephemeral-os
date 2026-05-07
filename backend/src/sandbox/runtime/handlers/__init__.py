"""Handler-per-command dispatch entry points for the runtime ``api.*`` ops.

Each public verb has its own handler module under this package. Worker-level
shell scaffolding (mount, capture, OCC apply) lives in
:mod:`sandbox.runtime.command_exec_server`; here we expose the four host-facing
dispatch entries that ``runtime.server`` registers in ``OP_TABLE``.
"""

from sandbox.runtime.handlers.edit_handler import edit_file
from sandbox.runtime.handlers.metrics_handler import layer_metrics
from sandbox.runtime.handlers.read_handler import read_file
from sandbox.runtime.handlers.shell_handler import shell
from sandbox.runtime.handlers.write_handler import write_file

__all__ = ["edit_file", "layer_metrics", "read_file", "shell", "write_file"]
