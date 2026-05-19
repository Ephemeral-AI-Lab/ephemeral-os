"""Package for the `read_file` tool.

`__init__.py` re-exports the impl module so that
`tools...read_file` and `tools...read_file.read_file` resolve to the same module —
keeps monkeypatching `tools...read_file.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import read_file as _impl

sys.modules[__name__] = _impl
