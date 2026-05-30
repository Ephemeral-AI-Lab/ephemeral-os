"""Package for the `submit_generator_success` tool."""

import sys

from . import submit_generator_success as _impl

sys.modules[__name__] = _impl
