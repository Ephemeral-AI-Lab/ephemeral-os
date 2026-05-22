from __future__ import annotations

from types import SimpleNamespace

from config import CentralConfig, RunnerConfig, override_central_config
from task_center_runner.environments.sweevo_image import fixtures as sweevo_image_env


def test_sweevo_image_auto_reuse_is_opt_in() -> None:
    with override_central_config(
        CentralConfig(runner=RunnerConfig(sandbox_reuse_mode="fresh"))
    ):
        assert sweevo_image_env._reuse_existing_auto_enabled() is False

    with override_central_config(
        CentralConfig(runner=RunnerConfig(sandbox_reuse_mode="reuse"))
    ):
        assert sweevo_image_env._reuse_existing_auto_enabled() is True


def test_sweevo_image_force_fresh_overrides_reuse() -> None:
    with override_central_config(
        CentralConfig(runner=RunnerConfig(sandbox_reuse_mode="force_fresh"))
    ):
        assert sweevo_image_env._reuse_existing_auto_enabled() is False


def test_workspace_used_sandboxes_are_session_local() -> None:
    first_session = SimpleNamespace()
    second_session = SimpleNamespace()

    first_seen = sweevo_image_env._session_workspace_used_sandboxes(first_session)
    first_seen.add("sandbox-a")

    assert sweevo_image_env._session_workspace_used_sandboxes(first_session) == {
        "sandbox-a"
    }
    assert sweevo_image_env._session_workspace_used_sandboxes(second_session) == set()
