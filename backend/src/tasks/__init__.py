"""Task exports."""

from ephemeralos.tasks.local_agent_task import spawn_local_agent_task
from ephemeralos.tasks.local_shell_task import spawn_shell_task
from ephemeralos.tasks.manager import BackgroundTaskManager, get_task_manager
from ephemeralos.tasks.stop_task import stop_task
from ephemeralos.tasks.types import TaskRecord, TaskStatus, TaskType

__all__ = [
    "BackgroundTaskManager",
    "TaskRecord",
    "TaskStatus",
    "TaskType",
    "get_task_manager",
    "spawn_local_agent_task",
    "spawn_shell_task",
    "stop_task",
]
