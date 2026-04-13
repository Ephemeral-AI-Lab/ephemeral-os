from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from team.context.project import ProjectContext
from team.models import BudgetConfig, BudgetState
from team.persistence.run_store import TeamRunStore, build_default_store

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from team.persistence.file_change_store import FileChangeStore, NullFileChangeStore
    from team.runtime.dispatcher import Dispatcher


@dataclass(frozen=True)
class TeamRuntimeServices:
    project_context: ProjectContext
    dispatcher: "Dispatcher"
    event_store: TeamRunStore
    file_change_store: "FileChangeStore | NullFileChangeStore | None" = None


def build_team_runtime_services(
    *,
    team_run_id: str,
    budgets: BudgetConfig,
    budget_state: BudgetState,
    user_request: str,
    goal: str | None = None,
    repo_root: str | None = None,
    event_store: TeamRunStore | None = None,
    session_factory: "async_sessionmaker[AsyncSession] | None" = None,
) -> TeamRuntimeServices:
    from team.persistence.team_engine import (
        create_team_engine,
        get_team_session_factory,
    )
    from team.runtime.dispatcher import Dispatcher

    project_key = repo_root or ""
    project_context = ProjectContext(
        goal=goal or user_request,
        user_request=user_request,
        repo_root=repo_root or "",
        project_key=project_key,
    )
    store = event_store if event_store is not None else build_default_store()

    # Team coordination is store-backed only. Bootstrap the shared async
    # engine lazily when the caller did not inject a session factory.
    task_session_factory = session_factory or get_team_session_factory()
    if task_session_factory is None:
        try:
            _, task_session_factory = create_team_engine()
        except RuntimeError as exc:
            raise RuntimeError(
                "Team runtime requires a configured async database. "
                "Set EPHEMERALOS_DATABASE_URL or pass session_factory explicitly."
            ) from exc

    from team.runtime.dispatcher_store import DispatcherStore
    store_driver = DispatcherStore(task_session_factory)

    # In-memory file change tracking — no PostgreSQL dependency.
    from team.persistence.file_change_store import FileChangeStore
    file_change_store: Any = FileChangeStore()

    dispatcher = Dispatcher(
        team_run_id=team_run_id,
        budgets=budgets,
        budget_state=budget_state,
        store=store_driver,
        event_store=store,
    )
    return TeamRuntimeServices(
        project_context=project_context,
        dispatcher=dispatcher,
        event_store=store,
        file_change_store=file_change_store,
    )
