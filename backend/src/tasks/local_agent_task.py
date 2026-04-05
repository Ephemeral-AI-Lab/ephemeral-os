"""Local agent task facade."""

from __future__ import annotations

from pathlib import Path

from tasks.manager import get_task_manager
from tasks.types import TaskRecord


async def spawn_local_agent_task(
    *,
    prompt: str,
    description: str,
    cwd: str | Path,
    model: str | None = None,
    api_key: str | None = None,
    command: str | None = None,
) -> TaskRecord:
    """Spawn a local agent subprocess task."""
    return await get_task_manager().create_agent_task(
        prompt=prompt,
        description=description,
        cwd=cwd,
        model=model,
        api_key=api_key,
        command=command,
    )
