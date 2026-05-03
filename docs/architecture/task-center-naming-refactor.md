# Task Center Naming Refactor — Mission / Episode / Attempt / HarnessGraph

**Status:** Proposed
**Goal:** Rename the task-center hierarchy so every layer has a single, LLM-readable noun with a distinct semantic role. Reduce overloaded concepts (`TaskSegment` carrying both "mission slice" and "retry container", `HarnessGraph` carrying both "lifecycle DTO" and "task DAG") into named layers that read cleanly to humans and to the LLMs that consume context blocks.

## 1. Motivation

Today's hierarchy:

```
ComplexTaskRequest → TaskSegment → HarnessGraph → Task
```

Three problems:

1. **`TaskSegment` is overloaded.** It carries *both* the persistent goal-bearing slice of mission work *and* the retry container that holds a sequence of `harness_graph_ids`. Retry-within-segment and continuation-across-segments are conceptually different "next" semantics and currently live on the same DTO.
2. **`HarnessGraph` is overloaded.** It carries *both* the lifecycle (stage / status / fail_reason / planner_task_id / evaluator_task_id / continuation_goal) *and* the structural DAG (`generator_task_ids`).
3. **Names don't carry semantic weight for LLMs.** `complex_task_request`, `segment`, `harness_graph` are weakly differentiated in training data. Block kinds like `complex_task_goal` and `segment_goal` don't self-describe ("complex what?" "segment of what?").

## 2. New Hierarchy

```
Mission → Episode → Attempt → HarnessGraph → Task
```

| Layer        | Role                                                                                              | Replaces                |
|--------------|---------------------------------------------------------------------------------------------------|-------------------------|
| `Mission`    | Top-level project intent. Holds `mission_goal` and chain of episodes.                              | `ComplexTaskRequest`    |
| `Episode`    | One mission slice with own goal. Self-terminating; may extend via continuation. Owns retry chain. | `TaskSegment`           |
| `Attempt`    | One planner→generator→evaluator cycle. Mirrors today's `HarnessGraph` DTO.                         | `HarnessGraph` (DTO)    |
| `HarnessGraph` | Pure structural DAG of generator tasks within an Attempt.                                         | `generator_task_ids` tuple |
| `Task`       | Atomic action (planner / generator-N / evaluator).                                                | `Task`                  |

### Why these names

- **`Mission`** — top-level intent in PM/agent literature, distinct from "request" (transactional) or "goal" (overloaded).
- **`Episode`** — self-contained chunk that ends, then *itself* decides whether a successor exists. Strong RL training-data prior ("episode + continuation"). "Phase" was rejected because it implies a numbered slot in a known sequence; episodes are terminal-by-default.
- **`Attempt`** — one try. Names the lifecycle aspect today's `HarnessGraph` already plays, without overloading the DAG noun.
- **`HarnessGraph`** — kept as a name, but narrowed to mean *the DAG structure* (nodes + edges), not the lifecycle wrapper.
- **`Task`** — unchanged.

## 3. Two distinct "next" semantics, now on different layers

| Semantics                       | Layer       | Trigger                                  | Goal                                  |
|---------------------------------|-------------|------------------------------------------|---------------------------------------|
| Within-Episode retry            | `Attempt`   | Attempt failed, retry budget remains     | Same `episode_goal`                   |
| Cross-Episode continuation      | `Episode`   | Episode closes with `continuation_goal`  | New `episode_goal` (from prior note)  |

Today both collapse onto `TaskSegment`, which is why the responsibilities feel muddled. After the rename:

- A failed Attempt → `EpisodeManager` invokes Planner again with `failed_attempt_landscape` context → new Attempt appended to `episode.attempts`.
- A closed Episode with `continuation_goal` set on its terminal Attempt → Mission spawns next Episode with `episode_goal = prior.terminal_attempt.continuation_goal`.

## 4. Final DTOs

```python
class MissionStatus(StrEnum):
    OPEN = "open"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

@dataclass(frozen=True, slots=True)
class Mission:
    id: str
    mission_goal: str
    status: MissionStatus
    episode_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class EpisodeStatus(StrEnum):
    OPEN = "open"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

class EpisodeCreationReason(StrEnum):
    INITIAL = "initial"
    CONTINUATION = "continuation"

@dataclass(frozen=True, slots=True)
class Episode:
    id: str
    mission_id: str
    episode_sequence_no: int
    episode_goal: str
    creation_reason: EpisodeCreationReason
    status: EpisodeStatus
    attempt_ids: tuple[str, ...]      # retry chain
    retry_budget: int
    closure_report: ClosureReport | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class AttemptStage(StrEnum):
    PLANNING = "planning"
    GENERATING = "generating"
    EVALUATING = "evaluating"
    CLOSED = "closed"

class AttemptStatus(StrEnum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"

class AttemptFailReason(StrEnum):
    PLANNER_FAILED = "planner_failed"
    GENERATOR_FAILED = "generator_failed"
    EVALUATOR_FAILED = "evaluator_failed"
    STARTUP_FAILED = "startup_failed"

@dataclass(frozen=True, slots=True)
class Attempt:
    id: str
    episode_id: str
    attempt_sequence_no: int
    stage: AttemptStage
    status: AttemptStatus
    planner_task_id: str | None
    task_specification: str | None        # planner output
    evaluation_criteria: tuple[str, ...]
    harness_graph: HarnessGraph           # generator-task DAG
    evaluator_task_id: str | None
    continuation_goal: str | None         # partial-plan handoff to next Episode
    fail_reason: AttemptFailReason | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @property
    def is_closed(self) -> bool:
        return self.stage == AttemptStage.CLOSED

    @property
    def has_partial_continuation(self) -> bool:
        return self.continuation_goal is not None


@dataclass(frozen=True, slots=True)
class HarnessGraph:
    """Pure structural DAG of generator tasks within an Attempt."""
    generator_task_ids: tuple[str, ...]
    edges: tuple[TaskEdge, ...]           # explicit dependencies (today: implicit)
```

## 5. Execution Flow

```
Executor receives request
  → creates Mission (mission_goal)
  → Mission spawns Episode 1 (episode_goal = mission_goal, creation_reason=INITIAL)
      → EpisodeManager invokes Planner
          → Planner produces Attempt (HarnessGraph + task_specification + optional continuation_goal)
          → Orchestrator dispatches generator tasks within HarnessGraph
              → Tasks execute (planner → generator-N → evaluator)
          → on Attempt PASSED:
              → Episode closes (succeeded)
          → on Attempt FAILED + retry budget remains:
              → EpisodeManager replans → new Attempt appended (with failed_attempt_landscape context)
          → on Attempt FAILED + budget exhausted:
              → Episode closes (failed)
      → on Episode close:
          → if terminal Attempt has continuation_goal:
              → Mission spawns Episode N+1 (episode_goal = continuation_goal, creation_reason=CONTINUATION)
          → else:
              → Mission completes (succeeded or failed depending on terminal Episode status)
```

## 6. ContextScope changes

```python
@dataclass(frozen=True, slots=True)
class ContextScope:
    mission_id: str                          # was request_id
    episode_id: str | None = None            # was segment_id
    attempt_id: str | None = None            # was harness_graph_id
    task_id: str | None = None
    parent_packet_id: str | None = None
    parent_task_id: str | None = None
```

`harness_graph_id` is removed from scope: HarnessGraph is reachable via `attempt.harness_graph` (1:1 with Attempt). LLM-facing identity for "which try" is `attempt_id`.

## 7. ContextBlockKind renames

| Old                            | New                            | Notes                                                     |
|--------------------------------|--------------------------------|-----------------------------------------------------------|
| `complex_task_goal`            | `mission_goal`                 | top-level intent                                          |
| `segment_goal`                 | `episode_goal`                 | current episode mandate                                   |
| `prior_segment_specification`  | `prior_episode_specification`  | prior episode's task_specification (planner output)       |
| `prior_segment_summary`        | `prior_episode_summary`        | prior episode outcome + continuation_goal                 |
| `failed_graph_landscape`       | `failed_attempt_landscape`     | prior failed Attempt within same Episode (retry context)  |
| `planned_task_spec`            | `planned_task_spec`            | unchanged                                                 |
| `task_specification`           | `task_specification`           | unchanged                                                 |
| `evaluation_criteria`          | `evaluation_criteria`          | unchanged                                                 |
| `dependency_summary`           | `dependency_summary`           | unchanged                                                 |
| `completed_task_summary`       | `completed_task_summary`       | unchanged                                                 |
| `artifact_reference`           | `artifact_reference`           | unchanged                                                 |
| `entry_request`                | `entry_request`                | unchanged                                                 |
| `parent_question`              | `parent_question`              | unchanged                                                 |
| `capability_note`              | `capability_note`              | unchanged                                                 |

## 8. File-tree restructuring

```
backend/src/task_center/
├── complex_task/                  →  mission/
│   ├── handler.py                 →  mission/handler.py
│   ├── handoff.py                 →  mission/handoff.py
│   ├── request.py                 →  mission/dto.py
│   ├── ancestry.py                →  mission/ancestry.py
│   ├── close_report_delivery.py   →  mission/close_report_delivery.py
│   └── validation.py              →  mission/validation.py
│
├── segment/                       →  episode/
│   ├── segment.py                 →  episode/dto.py
│   ├── manager.py                 →  episode/manager.py        # owns retry chain
│   ├── registry.py                →  episode/registry.py
│   ├── closure_report.py          →  episode/closure_report.py
│   └── validation.py              →  episode/validation.py
│
├── harness_graph/                 →  attempt/
│   ├── state.py                   →  attempt/dto.py            # Attempt DTO + enums
│   ├── factory.py                 →  attempt/factory.py
│   ├── orchestrator.py            →  attempt/orchestrator.py
│   ├── orchestrator_registry.py   →  attempt/orchestrator_registry.py
│   ├── runtime.py                 →  attempt/runtime.py
│   ├── launcher.py                →  attempt/launcher.py
│   ├── dispatcher.py              →  attempt/dispatcher.py
│   ├── generator_dag.py           →  attempt/harness_graph.py  # pure DAG type
│   └── validation.py              →  attempt/validation.py
│
└── task/                          →  task/                     # unchanged
```

## 9. Store renames

| Old                              | New                       |
|----------------------------------|---------------------------|
| `ComplexTaskRequestStore`        | `MissionStore`            |
| `TaskSegmentStore`               | `EpisodeStore`            |
| `HarnessGraphStore`              | `AttemptStore`            |
| `task_segment_store` (DB column) | `episode_store`           |

Database migrations: rename tables `complex_task_requests → missions`, `task_segments → episodes`, `harness_graphs → attempts`. Foreign-key columns rename in lockstep (`task_segment_id → episode_id`, `harness_graph_id → attempt_id`, `request_id → mission_id`).

## 10. Migration phases

### Phase 1 — DTO rename (no behavioral change)
- Rename DTO classes (`ComplexTaskRequest → Mission`, `TaskSegment → Episode`, `HarnessGraph → Attempt`).
- Introduce new `HarnessGraph` type as alias for `tuple[str, ...]` (or explicit DAG struct), holding what was `Attempt.generator_task_ids`.
- Rename enums (`HarnessGraphStage → AttemptStage`, etc.).
- Rename store classes and module paths.
- Update all import sites.
- **Verify:** existing tests pass with renamed symbols only.

### Phase 2 — Field renames
- `complex_task_request_id → mission_id` everywhere.
- `task_segment_id → episode_id`.
- `harness_graph_id → attempt_id`.
- `graph_sequence_no → attempt_sequence_no`.
- Update DB schema (table + column rename migrations).
- Update `ContextScope` and `ContextRefs`.
- **Verify:** end-to-end smoke test produces a Mission with at least one Episode containing one Attempt.

### Phase 3 — Block kind renames
- Rename `ContextBlockKind` enum members.
- Update all recipe modules (`recipes/planner.py`, `recipes/evaluator.py`, `recipes/generator.py`, `recipes/graph_landscape.py → recipes/attempt_landscape.py`).
- Update prompt templates that reference block kinds by string.
- **Verify:** context-engine tests pass; sample packets render with new kind strings.

### Phase 4 — Folder restructure
- Move modules per §8 file-tree mapping.
- Update `__init__.py` re-exports.
- Drop transitional aliases.
- **Verify:** full test suite green; grep for old paths returns zero hits in `backend/src/`.

### Phase 5 — Documentation sync
- Update `docs/architecture/task-center-harness-migration/*` to reference new names where they describe current state.
- Update CLAUDE.md / project memory entries that name the old layers.
- Add this document to the canonical refs list.

## 11. Risks & Tradeoffs

- **Test surface is large.** ~30+ test files reference `task_segment`, `harness_graph`, `complex_task_request` by name. Most will be mechanical renames; a few may have string-matching against block-kind values that need careful updating.
- **DB rename downtime.** Table renames in Phase 2 require either a brief lock or a multi-step migration (add new columns → backfill → drop old). Choose based on production cutover policy.
- **`HarnessGraph` keeps the name but changes scope.** Existing readers may assume `HarnessGraph` is the lifecycle DTO; after refactor it's the DAG-only type. Add a doc comment to that effect; consider a one-cycle deprecation alias if external consumers exist.
- **`Attempt` is a fat concept.** It carries planner, generator DAG, evaluator, continuation_goal, fail_reason. Same fan-out as today's `HarnessGraph`, just renamed — but worth flagging that it concentrates a lot of fields. Future split (e.g., extracting evaluator into its own DTO) is possible but out of scope.
- **No behavioral change.** This refactor is name-only. Retry semantics, planner→generator→evaluator flow, continuation-goal handoff all preserve current behavior. Any scope creep into logic changes belongs in a separate phase.

## 12. Out of scope

- Changing planner/evaluator semantics.
- Changing retry budget policy.
- Adding `phase_horizon` / multi-episode look-ahead context blocks (deferred until missions get long enough to need it).
- Restructuring the context engine's recipe graph beyond the renames.

## 13. Open questions

1. **Make `HarnessGraph` edges explicit?** Today the DAG is implicit in `generator_task_ids`. Phase 1 keeps that as-is; Phase 1.5 could introduce `tuple[TaskEdge, ...]` if planners want to emit dependency annotations. Defer until a planner asks for it.
2. **`EpisodeCreationReason` values?** Current code has `TaskSegmentCreationReason` — confirm `INITIAL` and `CONTINUATION` cover all paths, or whether `RECOVERY` / `RESUME` need separate values.
3. **Aliases vs hard rename.** Should Phase 1 ship transitional `TaskSegment = Episode` aliases for one release cycle, or do a hard rename? Recommend hard rename — codebase is internal, blast radius is bounded by the test suite.
