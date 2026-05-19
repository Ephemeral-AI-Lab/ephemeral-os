"""Package for the `submit_execution_failure` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_execution_failure` and `tools...submit_execution_failure.submit_execution_failure` resolve to the same module —
keeps monkeypatching `tools...submit_execution_failure.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_execution_failure as _impl

sys.modules[__name__] = _impl
