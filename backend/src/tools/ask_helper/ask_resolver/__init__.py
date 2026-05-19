"""Package for the `ask_resolver` tool.

`__init__.py` re-exports the impl module so that
`tools...ask_resolver` and `tools...ask_resolver.ask_resolver` resolve to the same module —
keeps monkeypatching `tools...ask_resolver.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import ask_resolver as _impl

sys.modules[__name__] = _impl
