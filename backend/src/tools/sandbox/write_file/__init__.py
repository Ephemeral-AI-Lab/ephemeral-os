"""Package for the `write_file` tool.

`__init__.py` re-exports the impl module so that
`tools...write_file` and `tools...write_file.write_file` resolve to the same module —
keeps monkeypatching `tools...write_file.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import write_file as _impl

sys.modules[__name__] = _impl
