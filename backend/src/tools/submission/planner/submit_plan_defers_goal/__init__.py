"""Package for the `submit_plan_defers_goal` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_plan_defers_goal` and `tools...submit_plan_defers_goal.submit_plan_defers_goal` resolve to the same module —
keeps monkeypatching `tools...submit_plan_defers_goal.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_plan_defers_goal as _impl

sys.modules[__name__] = _impl
