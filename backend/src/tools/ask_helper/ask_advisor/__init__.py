"""Package for the `ask_advisor` tool.

`__init__.py` re-exports the impl module so that
`tools...ask_advisor` and `tools...ask_advisor.ask_advisor` resolve to the same module —
keeps monkeypatching `tools...ask_advisor.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import ask_advisor as _impl

sys.modules[__name__] = _impl
