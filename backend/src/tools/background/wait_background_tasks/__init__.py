"""Package for the `wait_background_tasks` tool.

`__init__.py` re-exports the impl module so that
`tools...wait_background_tasks` and `tools...wait_background_tasks.wait_background_tasks` resolve to the same module —
keeps monkeypatching `tools...wait_background_tasks.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import wait_background_tasks as _impl

sys.modules[__name__] = _impl
