"""Package for the `cancel_background_task` tool.

`__init__.py` re-exports the impl module so that
`tools...cancel_background_task` and `tools...cancel_background_task.cancel_background_task` resolve to the same module —
keeps monkeypatching `tools...cancel_background_task.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import cancel_background_task as _impl

sys.modules[__name__] = _impl
