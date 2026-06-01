from task_center_runner.core import bootstrap


def test_real_agent_bootstrap_profile_root_points_to_agent_profiles() -> None:
    root = bootstrap._PROFILE_ROOT

    assert root.is_dir()
    assert (root / "main" / "planner.md").is_file()
