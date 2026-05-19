"""Package for the `run_subagent` tool.

`__init__.py` re-exports the impl module so that
`tools...run_subagent` and `tools...run_subagent.run_subagent` resolve to the same module —
keeps monkeypatching `tools...run_subagent.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import run_subagent as _impl

sys.modules[__name__] = _impl
