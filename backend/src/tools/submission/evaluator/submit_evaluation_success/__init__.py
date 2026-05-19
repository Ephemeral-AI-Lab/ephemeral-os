"""Package for the `submit_evaluation_success` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_evaluation_success` and `tools...submit_evaluation_success.submit_evaluation_success` resolve to the same module —
keeps monkeypatching `tools...submit_evaluation_success.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_evaluation_success as _impl

sys.modules[__name__] = _impl
