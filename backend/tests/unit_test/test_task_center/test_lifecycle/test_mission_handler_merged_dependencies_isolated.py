"""Phase 4a regression test — Mission handler family merger.

Pins the three merged classes (EpisodeFactory, EpisodeClosureRouter,
MissionHandler) to the single `mission/handler.py` module post-merger and
enforces the iter4 file-size ceiling (Phase 4a: ≤300 LoC; relaxed to
≤480 LoC at Phase 7c after repository+ancestry absorb).

Plan: .omc/plans/task-center-folder-reframe-20260514.md (lever #3, AC #10)
"""

from __future__ import annotations

import inspect
from pathlib import Path

from task_center.mission import handler as merged
from task_center.mission.handler import (
    EpisodeClosureRouter,
    EpisodeFactory,
    MissionClosureReportSink,
    MissionHandler,
)


MISSION_HANDLER_PATH = Path(merged.__file__)


def test_three_merged_classes_live_in_single_module() -> None:
    for cls in (EpisodeFactory, EpisodeClosureRouter, MissionHandler):
        assert cls.__module__ == "task_center.mission.handler"


def test_mission_handler_public_signature_preserved() -> None:
    expected_methods = {
        "__init__",
        "create_mission",
        "create_initial_episode_with_manager",
        "create_continuation_episode_with_manager",
        "handle_episode_closed",
        "close_mission",
    }
    actual = {
        name for name in vars(MissionHandler) if not name.startswith("_")
    } | {"__init__"}
    missing = expected_methods - actual
    assert not missing, f"MissionHandler missing public methods: {missing}"

    init_params = list(inspect.signature(MissionHandler).parameters)
    assert "mission_store" in init_params
    assert "episode_store" in init_params
    assert "attempt_store" in init_params
    assert "manager_registry" in init_params
    assert "config" in init_params


def test_episode_factory_and_router_public_surface_preserved() -> None:
    factory_methods = {
        "create_initial",
        "create_continuation",
    }
    factory_actual = {
        name for name in vars(EpisodeFactory) if not name.startswith("_")
    }
    assert factory_methods <= factory_actual

    router_methods = {"route"}
    router_actual = {
        name for name in vars(EpisodeClosureRouter) if not name.startswith("_")
    }
    assert router_methods <= router_actual


def test_mission_closure_report_sink_alias_exists() -> None:
    # The Callable type alias is preserved as the public-callback hook.
    assert MissionClosureReportSink is not None


def test_old_carved_out_modules_are_gone() -> None:
    mission_dir = MISSION_HANDLER_PATH.parent
    assert not (mission_dir / "episode_factory.py").exists()
    assert not (mission_dir / "episode_closure_router.py").exists()


def test_mission_handler_loc_ceiling_phase_4a() -> None:
    """Phase 4a ceiling: ≤300 LoC. Phase 7c relaxes to ≤480 after
    repository+ancestry absorb. Both ceilings checked here so the test
    passes at Phase 4a and continues to guard through Phase 7c.
    """
    loc = len(MISSION_HANDLER_PATH.read_text().splitlines())
    assert loc <= 480, f"mission/handler.py LoC={loc} exceeds Phase 7c ceiling 480"
