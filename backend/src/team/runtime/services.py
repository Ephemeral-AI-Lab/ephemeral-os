from __future__ import annotations

from dataclasses import dataclass

from team.context.project import ProjectContext
from team.models import BudgetConfig, BudgetState
from team.persistence.run_store import TeamRunStore, build_default_store
from team.runtime.dispatcher import Dispatcher


@dataclass(frozen=True)
class TeamRuntimeServices:
    project_context: ProjectContext
    dispatcher: Dispatcher
    event_store: TeamRunStore


def build_team_runtime_services(
    *,
    team_run_id: str,
    budgets: BudgetConfig,
    budget_state: BudgetState,
    user_request: str,
    goal: str | None = None,
    repo_root: str | None = None,
    event_store: TeamRunStore | None = None,
) -> TeamRuntimeServices:
    project_key = repo_root or ""
    project_context = ProjectContext(
        goal=goal or user_request,
        user_request=user_request,
        repo_root=repo_root or "",
        project_key=project_key,
    )
    store = event_store if event_store is not None else build_default_store()
    dispatcher = Dispatcher(
        team_run_id=team_run_id,
        budgets=budgets,
        budget_state=budget_state,
        event_store=store,
    )
    return TeamRuntimeServices(
        project_context=project_context,
        dispatcher=dispatcher,
        event_store=store,
    )
