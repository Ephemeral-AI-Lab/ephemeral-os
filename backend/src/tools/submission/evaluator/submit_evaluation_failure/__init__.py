"""Package for the `submit_evaluation_failure` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_evaluation_failure` and `tools...submit_evaluation_failure.submit_evaluation_failure` resolve to the same module —
keeps monkeypatching `tools...submit_evaluation_failure.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_evaluation_failure as _impl

sys.modules[__name__] = _impl
