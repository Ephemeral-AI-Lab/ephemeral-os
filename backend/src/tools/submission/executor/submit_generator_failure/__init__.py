"""Package for the `submit_generator_failure` tool."""

import sys

from . import submit_generator_failure as _impl

sys.modules[__name__] = _impl
