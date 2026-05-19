"""Package for the `edit_file` tool.

`__init__.py` re-exports the impl module so that
`tools...edit_file` and `tools...edit_file.edit_file` resolve to the same module —
keeps monkeypatching `tools...edit_file.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import edit_file as _impl

sys.modules[__name__] = _impl
