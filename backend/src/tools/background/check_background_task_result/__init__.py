"""Package for the `check_background_task_result` tool.

`__init__.py` re-exports the impl module so that
`tools...check_background_task_result` and `tools...check_background_task_result.check_background_task_result` resolve to the same module —
keeps monkeypatching `tools...check_background_task_result.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import check_background_task_result as _impl

sys.modules[__name__] = _impl
