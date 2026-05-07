"""Handler-per-command dispatch entry points for the runtime ``api.*`` ops.

Each public verb has its own handler module under this package. Worker-level
shell scaffolding (mount, capture, OCC apply) lives in
:mod:`sandbox.daemon.services.shell_runner`; here we expose the four host-facing
dispatch entries that ``runtime.server`` registers in ``OP_TABLE``.
"""

from sandbox.daemon.handlers.edit_handler import edit_file
from sandbox.daemon.handlers.metrics_handler import layer_metrics
from sandbox.daemon.handlers.read_handler import read_file
from sandbox.daemon.handlers.shell_handler import shell
from sandbox.daemon.handlers.write_handler import write_file

__all__ = ["edit_file", "layer_metrics", "read_file", "shell", "write_file"]
