"""Hard tier (HRD-01..10): adversarial host boundary, concurrency, scale,
failure. The host-boundary quartet (HRD-01..05) is Critical — a failure there
is a real escape. HRD-09/10 (scale + restart) run last.
"""

import pytest

from manager.management.export.helpers import cases_for_tier, run_case


pytestmark = [pytest.mark.export, pytest.mark.hard]


@pytest.mark.parametrize("case", cases_for_tier("hard"), ids=lambda case: case["id"])
def test_export_hard_catalog(case, export_preconditions):
    run_case(case)
