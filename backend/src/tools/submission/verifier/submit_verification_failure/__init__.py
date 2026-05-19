"""Package for the `submit_verification_failure` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_verification_failure` and `tools...submit_verification_failure.submit_verification_failure` resolve to the same module —
keeps monkeypatching `tools...submit_verification_failure.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_verification_failure as _impl

sys.modules[__name__] = _impl
