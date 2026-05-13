# Codex Instructions

## Project Overview

EphemeralOS is an open agent harness. The core product is a Python backend that
runs agent loops, tools, skills, memory, providers, sandboxes, and TaskCenter
coordination.

Treat this repository as an active research/runtime codebase: preserve existing
architecture boundaries, add focused tests for behavior changes, and keep docs in
sync when changing user-visible workflows or agent contracts.

## Tech Stack

- Python 3.10+ package managed with `uv`; CI currently tests Python 3.10 and
  3.11.
- Data/persistence: SQLAlchemy, asyncpg/psycopg, optional local Postgres via
  `backend/docker-compose.postgres.yml`; file-based persistence remains a valid
  fallback path.
- Agent/provider layer: Anthropic SDK plus OpenAI-compatible provider support.
- Runtime schemas/config: Pydantic v2, PyYAML, python-dotenv.
- Testing/quality: pytest, pytest-asyncio, ruff, mypy. Mypy is strict for
  `agents.*` via `backend/mypy.ini`.

## Repository Map

- `backend/src/engine/`: agent loop, tool execution, streaming, background
  tasks, runtime notifications.
- `backend/src/tools/`: built-in toolkits, submission tools, core registry.
- `backend/src/task_center/`: mission, episode, attempt, context packet, and
  lifecycle coordination.
- `backend/src/agents/`: agent definition loading, registry, DB-backed builder,
  run tracking, API.
- `backend/src/providers/`: Anthropic-native and OpenAI-compatible provider
  abstractions and API surfaces.
- `backend/src/skills/`: bundled skill discovery/loading, registry, and
  file-backed content model.
- `backend/src/plugins/`: plugin catalog, manifest loading, and LSP runtime.
- `backend/src/db/`: database engine, SQLAlchemy models, and stores.
- `backend/src/live_e2e/`: scenario harnesses, SWE-EVO adapters, and live
  verification utilities.
- `backend/tests/`: unit, integration, e2e, live, and benchmark-oriented tests.

## Common Commands

Use the narrowest command that verifies the change.

```bash
uv sync --extra dev
uv run pytest -q
uv run pytest backend/tests/unit_test/test_task_center -q
uv run ruff check backend/src backend/tests
uv run mypy --config-file backend/mypy.ini backend/src/agents
```

Local Postgres:

```bash
docker compose -f backend/docker-compose.postgres.yml up -d
```

## Coding Rules

- Prefer `rg` and `rg --files` for repository search.
- Do not edit generated or cache content: `node_modules/`, `__pycache__/`,
  `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/`, `.DS_Store`, local DBs, or
  build output.
- Keep Python code type-friendly. `agents.*` is a strict mypy zone;
  do not introduce `Any` or broad ignores unless there is a clear local pattern.
- Keep changes scoped to the task. Avoid broad rewrites, formatting churn, or
  unrelated docs updates.
- Do not revert user changes in a dirty worktree. Inspect overlapping files and
  work with the current state.
- Prefer existing abstractions and stores over ad hoc persistence or string
  parsing.
- When behavior changes, add or update focused tests near the changed module.
- When public CLI/API/agent workflow changes, update `README.md`,
  `CONTRIBUTING.md`, and/or `docs/architecture/` as appropriate.

## Team Runtime Rules

The current team coordination model is planner/worker/replanner based.

- `TaskCenter` owns task graph lifecycle, status transitions, dependency
  readiness, notes, budget counters, persistence transactions, and replan
  application.
- `DispatchQueue` pops ready work and hands it to executors.
- Executors interpret terminal agent results and call `TaskCenter`; they should
  not own graph mutation policy.
- Worker agents complete through `submit_task_summary(type="success" | "fail")`.
- Planners complete through `submit_plan(new_tasks=[...])`.
- Replanners complete through `submit_replan(new_tasks=[...], cancel_ids=[...])`.
- Every team task must exit through exactly one terminal submission path:
  `submit_plan`, `submit_replan`, or `submit_task_summary`.
- `submit_task_plan`, `declare_blocker`, `DeclareBlockerTool`, and conductor
  flows are obsolete. Do not reintroduce them in code, tests, prompts, or docs.

Replanning specifics:

- A worker failure routes through `TaskCenter.request_replan`.
- The original task is marked `replanning`, a replanner task is spawned, and
  pending dependents are rewired from the original task to the replanner.
- A dependent of the failed task with any non-pending status during this rewrite
  is a graph invariant violation.
- `submit_replan` may add corrective tasks only as direct children of the
  replanner. The tool/runtime stamps `parent_id` to the replanner task.
- `cancel_ids` may cancel stale not-completed direct siblings of the replanner,
  including cascaded descendants/dependents.
- Replan-created task deps may target local new tasks or schedulable existing
  tasks that do not already depend on the replanner or original failed task.
- A replanner with no direct child tasks after `submit_replan` becomes `done`
  immediately; one with direct child tasks becomes `expanded` until its direct
  children succeed.
- The original failed task becomes `failed` after successful replan without
  cascading, because pending dependents have already been rewired.

Primary docs for this area:

- `docs/architecture/team-coordination.md`
- `docs/architecture/task-center.md`
- `docs/architecture/terminal-submission-and-external-trigger.md`
- `docs/architecture/replan-workflow-sequence-diagrams.md`

## Testing Guidance

- Backend default test run excludes `e2e` and `live` markers via
  `pyproject.toml`.
- Use targeted tests first, then run broader checks when touching shared runtime
  behavior.
- Live/e2e tests often require API keys or external services; do not assume they
  are runnable in every environment.
- For TaskCenter runtime changes, prioritize tests in
  `backend/tests/unit_test/test_task_center/`, relevant engine tests, and
  architecture docs.
