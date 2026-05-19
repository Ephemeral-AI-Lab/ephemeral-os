"""Package for the `glob` tool.

`__init__.py` re-exports the impl module so that
`tools...glob` and `tools...glob.glob` resolve to the same module —
keeps monkeypatching `tools...glob.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import glob as _impl

sys.modules[__name__] = _impl
