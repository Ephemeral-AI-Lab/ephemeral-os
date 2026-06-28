"""observability · placeholder.

Observability is intentionally not implemented this round. It will be verified
THROUGH runtime activity rather than standalone — see observability/README.md.
"""

import pytest


@pytest.mark.skip(
    reason="observability is verified through runtime assertions (not implemented yet)"
)
def test_observability_placeholder():
    pass
