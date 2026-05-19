"""Package for the `submit_exploration_result` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_exploration_result` and `tools...submit_exploration_result.submit_exploration_result` resolve to the same module —
keeps monkeypatching `tools...submit_exploration_result.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_exploration_result as _impl

sys.modules[__name__] = _impl
