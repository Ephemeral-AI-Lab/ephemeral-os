"""Package for the enter_isolated_workspace tool."""

import sys

from . import definition as _impl

sys.modules[__name__] = _impl
