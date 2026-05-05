from __future__ import annotations

from typing import Any

import pytest

from benchmarks.sweevo.task_center_runner import (
    build_sweevo_user_prompt,
    load_pr_description_overrides,
    run_sweevo_with_task_center,
)
from benchmarks.sweevo.models import SWEEvoInstance


_DASK_INSTANCE_ID = "dask__dask_2023.3.2_2023.4.0"


def _instance(**overrides: Any) -> SWEEvoInstance:
    base = {
        "instance_id": _DASK_INSTANCE_ID,
        "repo": "dask/dask",
        "base_commit": "abc123",
        "problem_statement": "Dataset fallback problem statement",
        "patch": "",
        "fail_to_pass": ["dask/tests/test_cli.py::test_config_get"],
        "pass_to_pass": ["dask/tests/test_config.py::test_collect"],
        "docker_image": "example/image",
        "test_cmds": "pytest -q",
        "environment_setup_commit": "",
        "instance_id_swe": _DASK_INSTANCE_ID,
    }
    base.update(overrides)
    return SWEEvoInstance(**base)


def test_sweevo_user_prompt_uses_checked_in_pr_description_csv() -> None:
    load_pr_description_overrides.cache_clear()

    prompt = build_sweevo_user_prompt(_instance(), "/testbed")

    assert prompt.startswith("<Workspace Root>\n/testbed\n<Workspace Root>\n\n")
    assert "<pr_description>\n2023.4.0\n--------" in prompt
    assert "Add a CLI command to ``list`` and ``get`` a value from dask config" in prompt
    assert "Dataset fallback problem statement" not in prompt
    assert "minimal changes to non-tests files in the /testbed directory" in prompt


@pytest.mark.asyncio
async def test_legacy_task_center_sweevo_runner_is_disabled() -> None:
    with pytest.raises(RuntimeError, match="legacy TaskCenter SWE-EVO runner is disabled"):
        await run_sweevo_with_task_center(instance_id=_DASK_INSTANCE_ID)
