"""OCC-unaware filesystem overlay substrate.

This package owns namespace-only overlay mount mechanics, upperdir capture,
upper/work directory allocation, and subprocess execution helpers. Pipeline packages decide
when to publish captured changes.
"""

from sandbox.overlay.capture import walk_upperdir

__all__ = ["walk_upperdir"]
