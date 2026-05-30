"""Package for the `submit_workflow_handoff` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_workflow_handoff` and `tools...submit_workflow_handoff.submit_workflow_handoff` resolve to the same module —
keeps monkeypatching `tools...submit_workflow_handoff.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_workflow_handoff as _impl

sys.modules[__name__] = _impl
