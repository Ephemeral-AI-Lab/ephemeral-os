"""Package for the `grep` tool.

`__init__.py` re-exports the impl module so that
`tools...grep` and `tools...grep.grep` resolve to the same module —
keeps monkeypatching `tools...grep.<name>` working after the
tool was moved into its own package.
"""

import sys

from . import grep as _impl

sys.modules[__name__] = _impl
