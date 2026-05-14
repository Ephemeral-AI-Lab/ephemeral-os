"""Phase 5b regression test — stage dispatch map (lever #7).

After folding stage_strategy.py into dispatcher.py via _STAGE_DISPATCH,
pin the dispatch table so PLAN/CLOSED remain no-ops and GENERATE/EVALUATE
route to their respective dispatcher methods.

Plan: .omc/plans/task-center-folder-reframe-20260514.md (lever #7)
"""

from __future__ import annotations

from task_center.attempt.dispatcher import _STAGE_DISPATCH
from task_center.attempt.state import AttemptStage


def test_stage_dispatch_routes_only_active_stages() -> None:
    assert _STAGE_DISPATCH == {
        AttemptStage.GENERATE: "_dispatch_generating",
        AttemptStage.EVALUATE: "_dispatch_evaluating",
    }
    # PLAN and CLOSED are intentionally absent (no-op stages).
    assert AttemptStage.PLAN not in _STAGE_DISPATCH
    assert AttemptStage.CLOSED not in _STAGE_DISPATCH
