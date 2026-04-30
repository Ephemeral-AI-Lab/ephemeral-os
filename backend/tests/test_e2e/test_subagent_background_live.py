"""Legacy TaskCenter live workflow tests.

The old TaskCenter runtime has been removed and will be rebuilt separately, so
the live workflow coverage is intentionally disabled for now.
"""

from __future__ import annotations

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.live,
    pytest.mark.skip(reason="Legacy TaskCenter runtime removed"),
]


def test_legacy_task_center_live_workflow_removed() -> None:
    """Placeholder for the rebuilt TaskCenter live workflow."""
