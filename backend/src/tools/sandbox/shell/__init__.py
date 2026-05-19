"""Package for the `shell` tool.

`__init__.py` re-exports the impl module so that
`tools...shell` and `tools...shell.shell` resolve to the same module —
keeps monkeypatching `tools...shell.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import shell as _impl

sys.modules[__name__] = _impl
