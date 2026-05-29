"""Package for the `multi_edit` tool.

`__init__.py` re-exports the impl module so that
`tools...multi_edit` and `tools...multi_edit.multi_edit` resolve to the same
module — keeps monkeypatching `tools...multi_edit.<name>` working, mirroring
the `edit_file` package layout.
"""

import sys

from . import multi_edit as _impl

sys.modules[__name__] = _impl
