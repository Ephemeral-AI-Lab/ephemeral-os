"""Package for the `submit_resolver_result` tool.

`__init__.py` re-exports the impl module so that
`tools...submit_resolver_result` and `tools...submit_resolver_result.submit_resolver_result` resolve to the same module —
keeps monkeypatching `tools...submit_resolver_result.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import submit_resolver_result as _impl

sys.modules[__name__] = _impl
