"""Legacy TaskCenter prompt helpers for SWE-EVO benchmark instances."""

from __future__ import annotations

import csv
import functools
import logging
import os
from pathlib import Path
from typing import Any

from benchmarks.sweevo.models import SWEEvoInstance, _REPO_DIR

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_PR_DESCRIPTION_CSV_ENV = "SWEEVO_PR_DESCRIPTIONS_CSV"
_PR_DESCRIPTION_CSV_PATH = (
    _PROJECT_ROOT
    / "backend"
    / "config"
    / "benchmarks"
    / "sweevo_gpt5_2025_08_07_pr_descriptions.csv"
)


@functools.lru_cache(maxsize=8)
def load_pr_description_overrides(csv_path: str) -> dict[str, str]:
    """Load SWE-EVO instance-id to PR-description overrides from a CSV."""
    path = Path(csv_path)
    if not path.exists():
        return {}

    descriptions: dict[str, str] = {}
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                instance_id = str(row.get("test_folder") or "").strip()
                if not instance_id:
                    continue
                descriptions[instance_id] = str(row.get("pr_description") or "")
    except OSError:
        logger.debug("Unable to load SWE-EVO PR descriptions from %s", path, exc_info=True)
        return {}
    return descriptions


def pr_description_for_instance(
    instance: SWEEvoInstance,
    *,
    csv_path: str | os.PathLike[str] | None = None,
) -> str:
    """Return the benchmark prompt description for *instance*."""
    resolved_csv = os.fspath(
        csv_path
        or os.environ.get(_PR_DESCRIPTION_CSV_ENV)
        or _PR_DESCRIPTION_CSV_PATH
    )
    overrides = load_pr_description_overrides(resolved_csv)
    for instance_id in (instance.instance_id, instance.instance_id_swe):
        if instance_id and (description := overrides.get(instance_id, "")).strip():
            return description

    explicit = getattr(instance, "pr_description", "")
    if explicit:
        return explicit
    return instance.problem_statement


def build_sweevo_user_prompt(
    instance: SWEEvoInstance,
    repo_dir: str = _REPO_DIR,
    *,
    csv_path: str | os.PathLike[str] | None = None,
) -> str:
    """Return the SWE-agent-style first user message for a SWE-EVO instance."""
    pr_description = pr_description_for_instance(instance, csv_path=csv_path).strip()
    return (
        f"<Workspace Root>\n"
        f"{repo_dir}\n"
        f"<Workspace Root>\n\n"
        f"I've uploaded a python code repository in the directory {repo_dir}. "
        f"Consider the following PR description:\n"
        f"<pr_description>\n"
        f"{pr_description}\n"
        f"</pr_description>\n\n"
        f"Can you help me implement the necessary changes to the repository so that "
        f"the requirements specified in the <pr_description> are met?\n"
        f"I've already taken care of all changes to any of the test files described "
        f"in the <pr_description>. This means you DON'T have to modify the testing "
        f"logic or any of the tests in any way!\n"
        f"Your task is to make the minimal changes to non-tests files in the "
        f"{repo_dir} directory to ensure the <pr_description> is satisfied."
    )


async def run_sweevo_with_task_center(**_kwargs: Any) -> dict[str, Any]:
    """Fail clearly while the TaskCenter runtime is being rebuilt."""
    raise RuntimeError(
        "The legacy TaskCenter SWE-EVO runner is disabled because "
        "backend/src/task_center has been removed. Rebuild TaskCenter and add "
        "a new benchmark runner before using this path."
    )


__all__ = [
    "build_sweevo_user_prompt",
    "load_pr_description_overrides",
    "pr_description_for_instance",
    "run_sweevo_with_task_center",
]
