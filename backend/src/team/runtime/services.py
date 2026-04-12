from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from team.context.project import ProjectContext
from team.models import BudgetConfig, BudgetState
from team.persistence.run_store import TeamRunStore, build_default_store

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from team.persistence.exploration_memory_store import (
        ExplorationMemoryStore,
        NullExplorationMemoryStore,
    )
    from team.persistence.note_store import NoteStore, NullNoteStore
    from team.runtime.dispatcher import Dispatcher


@dataclass(frozen=True)
class TeamRuntimeServices:
    project_context: ProjectContext
    dispatcher: "Dispatcher"
    event_store: TeamRunStore
    note_store: "NoteStore | NullNoteStore | None" = None
    exploration_memory_store: "ExplorationMemoryStore | NullExplorationMemoryStore | None" = None


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

    note_store: Any = None
    exploration_memory_store: Any = None
    from team.runtime.dispatcher_store import DispatcherStore
    store_driver = DispatcherStore(task_session_factory)

    # Initialize NoteStore for Task Center persistence
    from team.persistence.note_store import NoteStore
    note_store = NoteStore()
    note_store.initialize(task_session_factory)

    # Initialize ExplorationMemoryStore for cross-run cache
    from team.persistence.exploration_memory_store import ExplorationMemoryStore
    exploration_memory_store = ExplorationMemoryStore()
    exploration_memory_store.initialize(task_session_factory)

    # Attach the durable store to the exploration memory singleton
    from tools.memory import get_exploration_memory
    get_exploration_memory().attach_store(exploration_memory_store)

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
        note_store=note_store,
        exploration_memory_store=exploration_memory_store,
    )
