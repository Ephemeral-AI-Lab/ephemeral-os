"""Smallest runnable check: gateway up (via the autouse fixture) + a structured
list_sandboxes response. Run with ``pytest -m smoke``."""

import pytest

from core.cli import is_error
from manager.management import helpers as mgmt


@pytest.mark.smoke
def test_gateway_responds_with_sandbox_list():
    result = mgmt.list_sandboxes()
    assert not is_error(result), result
    assert isinstance(result.get("sandboxes"), list)
