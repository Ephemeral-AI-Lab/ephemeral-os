"""OCC-unaware filesystem overlay substrate.

This package owns namespace-only overlay mount mechanics, upperdir capture,
scratch layout, and subprocess execution helpers. Pipeline packages decide
when to publish captured changes.
"""

from sandbox.overlay.capture import walk_upperdir
from sandbox.overlay.layout import LayerPathsLayout

__all__ = ["LayerPathsLayout", "walk_upperdir"]
