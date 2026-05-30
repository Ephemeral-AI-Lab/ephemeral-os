"""Package for the `submit_reduction_failure` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_reduction_failure` and `tools...submit_reduction_failure.submit_reduction_failure` resolve to the same module —
keeps monkeypatching `tools...submit_reduction_failure.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_reduction_failure as _impl

sys.modules[__name__] = _impl
