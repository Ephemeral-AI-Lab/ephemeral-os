# Phase 01 - Implementation Plan

Companion to [`phase-01-mission-episode-attempt-model.md`](./phase-01-mission-episode-attempt-model.md).
This document is the actionable build plan: folder layout, files, classes,
function signatures, migration steps, test plan, and build waves.

It does not redefine the durable model; it implements it.

---

## 1. Scope

Phase 01 ships durable state and skeletal lifecycle services. It does **not**
change execution behavior — Phase 02 wires the orchestrator to the new model.

Deliverables:

1. New persistence records: `MissionRecord`, `EpisodeRecord`,
   `AttemptRecord` (replacing the legacy `TaskCenterAttemptRecord`).
2. Three new stores returning **frozen dataclass DTOs** (not raw rows, not
   plain dicts) — an evolution of `model_store.py`'s "always serialize" stance.
3. Domain DTOs and enums under `task_center/domain/`.
4. Skeletal lifecycle services nested by entity:
   `task_center/mission/segment/attempt/`.
5. `EpisodeClosureReport` contract from `EpisodeManager` to
   `MissionHandler`.
6. Schema migration via `db/engine.py` auto-migrate hooks.
7. Tests covering Phase 01 exit criteria.

Not in scope (deferred to later phases):

- Wiring `AttemptOrchestrator` to actually run planner/generator/evaluator
  (Phase 02).
- Tool-gate enforcement on `submit_full_plan` / `submit_partial_plan` /
  `request_mission_solution` (Phase 03).
- Full final-report delivery to `requested_by_task_id` (Phase 04).
- Context-engine summary IDs in `attempted_plan_history` entries (Phase 06).

---

## 2. Coherence verification

Phase 01 spec is coherent with Phase 00 target architecture and the workflow
overview. The cross-document map:

| Concept | Phase 00 | Workflow overview | Phase 01 | Verdict |
| --- | --- | --- | --- | --- |
| Three-axis split (`Mission` / `Episode` / `Attempt`) | Defined | Restated | Persistence + DTOs created | OK |
| Ownership `Handler -> Manager -> Orchestrator` | Runtime layers | Layer responsibilities | Creators of records | OK |
| `requested_by_task_id` is the parent link | Stated | Stated | Field defined as authoritative parent | OK |
| Ordered `episode_ids`, `attempt_ids` | Implied | Implied | Made authoritative; `get_attempt_count` derived from list | OK |
| `continuation_goal` from passing graph only | Stated | Stated | Invariant: never inherited from prior failed graphs | OK |
| `EpisodeClosureReport` outcomes | Defined | Restated | Identical shape; outcome union with three variants | OK |
| Retry stays inside same segment | Stated | Stated as horizontal axis | Manager invariant | OK |
| Recursive `request_mission_solution` is not a child segment | Stated | Diagrammed | Encoded by `requested_by_task_id` only | OK |
| No `ROOT` creation reason | Stated | Implicit | Removed from creation-reason table | OK |
| Per-graph evidence belongs to context engine | Stated | Stated | Phase 01 stores only structural state | OK |
| Phase scope = persistence + typed state | — | — | "Should not change execution until Phase 02" | OK |

Two seams worth being explicit about in this plan:

1. `EpisodeClosureReport.attempted_plan_history[].attempt_summary_id`
   is produced by the context engine (Phase 06). Phase 01 lays out the field
   but populates it as `None`. The harness layer only stores `attempt_id`
   plus the structural state.
2. Phase 01 does not yet wire the
   `AttemptOrchestrator -> EpisodeManager -> MissionHandler`
   callbacks. Phase 01 ships the `handle_*_closed` methods as testable units
   that Phase 02 will call.

---

## 3. Workflow diagrams

### 3a. Three axes of progression

```mermaid
flowchart LR
    subgraph Origin["Origin axis"]
        E["Executor task"] -- "request_mission_solution(goal)" --> CTR["Mission"]
    end
    subgraph Vertical["Vertical continuation"]
        CTR --> S1["Episode 1<br/>goal = request.goal"]
        S1 -. "passing graph<br/>continuation_goal != null" .-> S2["Episode 2<br/>goal = S1.continuation_goal"]
        S2 -. "..." .-> SN["Episode N"]
    end
    subgraph Horizontal["Horizontal retry"]
        S1 --> H11["Attempt 1.1<br/>failed"]
        H11 -- "attempt budget remains" --> H12["Attempt 1.2<br/>passed"]
    end
```

### 3b. Lifecycle handoffs (Phase 01 records the durable state at each arrow)

```mermaid
sequenceDiagram
    participant Exec as Executor task E
    participant Handler as MissionHandler
    participant Store as Stores<br/>(persistence)
    participant Mgr as EpisodeManager(S)
    participant Orch as AttemptOrchestrator(H)<br/>(Phase 02)

    Exec->>Handler: request_mission_solution(goal)
    Handler->>Store: insert Mission C<br/>(requested_by_task_id=E, status=open)
    Handler->>Store: insert Episode S1<br/>(sequence_no=1, goal=C.goal,<br/>creation_reason=initial)
    Handler->>Store: append S1.id to C.episode_ids
    Handler->>Mgr: spawn manager(S1)
    Mgr->>Store: insert Attempt H1<br/>(attempt_sequence_no=1,<br/>stage=planning, status=running)
    Mgr->>Store: append H1.id to S1.attempt_ids
    Mgr->>Orch: start(H1)

    Note over Orch: Phase 02 runs planner -> DAG -> evaluator
    Orch->>Mgr: handle_attempt_closed(H1)

    alt graph passed
        Mgr->>Store: copy H1.continuation_goal -> S1.continuation_goal
        Mgr->>Store: close S1 (status=succeeded)
        Mgr->>Handler: EpisodeClosureReport
        alt continuation_goal == null
            Handler->>Store: close C succeeded
            Handler->>Exec: deliver final report (Phase 04)
        else continuation_goal != null
            Handler->>Store: insert Episode S2<br/>(sequence_no=2, creation_reason=partial_continuation)
            Handler->>Mgr: spawn fresh manager(S2)
        end
    else graph failed AND budget remains
        Mgr->>Store: insert Attempt H2 (attempt_sequence_no=2)
        Mgr->>Orch: start(H2)
    else graph failed AND budget exhausted
        Mgr->>Store: close S1 (status=failed)
        Mgr->>Handler: EpisodeClosureReport(attempt_plan_failed)
        Handler->>Store: close C failed
        Handler->>Exec: deliver final report (Phase 04)
    end
```

### 3c. State machines per entity

```text
Mission:    open ──┬─► succeeded
                              ├─► failed
                              └─► cancelled

Episode:           open ──┬─► succeeded
                              ├─► failed
                              └─► cancelled

Attempt stage:    planning ─► generating ─► evaluating ─► closed
                          │            │
                          └────────────┴──► closed (failed early)

Attempt status:   running ──┬─► passed
                                 └─► failed
                                       (fail_reason ∈ {
                                         planner_failed,
                                         generator_failed,
                                         evaluator_failed
                                       })
```

### 3d. Closure report routing

```mermaid
flowchart TD
    GraphClosed["Attempt closes"] --> Mgr["EpisodeManager.<br/>handle_attempt_closed"]
    Mgr --> Passed{"passed?"}
    Passed -- "yes" --> CopyGoal["copy graph.continuation_goal -> segment"]
    CopyGoal --> CloseSeg["close segment succeeded"]
    CloseSeg --> HasCont{"continuation_goal<br/>not null?"}
    HasCont -- "yes" --> SuccCont["EpisodeClosureReport<br/>= success_continue(goal)"]
    HasCont -- "no" --> Term["EpisodeClosureReport<br/>= terminal_success"]

    Passed -- "no" --> Budget{"attempts < budget?"}
    Budget -- "yes" --> NextG["create next Attempt<br/>in same segment"]
    Budget -- "no" --> CloseFail["close segment failed"]
    CloseFail --> Failed["EpisodeClosureReport<br/>= attempt_plan_failed(history)"]

    SuccCont --> Handler["MissionHandler.<br/>handle_segment_closed"]
    Term --> Handler
    Failed --> Handler

    Handler --> Outcome{"outcome"}
    Outcome -- "success_continue" --> NewSeg["create continuation Episode"]
    Outcome -- "terminal_success" --> Win["close request succeeded<br/>+ final report -> requested_by_task_id"]
    Outcome -- "attempt_plan_failed" --> Lose["close request failed<br/>+ final report -> requested_by_task_id"]
```

---

## 4. Folder layout

The runtime is organized along two axes:

- **Stateless plumbing** (DTOs + enums) is hoisted to a flat `domain/` folder.
- **Stateful lifecycle services** (handler / manager / orchestrator) are
  nested by entity ownership so the file tree mirrors Phase 00's diagram.

Persistence stays flat under `db/` because SQLAlchemy's `Base.metadata`
discovery and `db/engine.py` auto-migration expect it there.

```text
backend/src/task_center/
├── __init__.py
├── exceptions.py                         # GraphInvariantViolation
├── domain/                               # frozen DTOs + enums
│   ├── __init__.py
│   ├── mission.py           # Mission, MissionStatus,
│   │                                     #   MissionCloseReport (small, lives inline)
│   ├── episode.py                   # Episode, EpisodeStatus,
│   │                                     #   EpisodeCreationReason
│   ├── attempt.py                  # Attempt, AttemptStage,
│   │                                     #   AttemptStatus, AttemptFailReason
│   └── segment_closure_report.py         # EpisodeClosureReport, ClosureOutcome union,
│                                         #   AttemptedPlanEntry
│
├── mission/                 # owns: request lifecycle
│   ├── __init__.py                       # public surface re-exports
│   ├── handler.py                        # MissionHandler
│   ├── invariants.py                     # request-level invariants
│   ├── config.py                         # HarnessLifecycleConfig
│   ├── segment_manager_registry.py       # one EpisodeManager per open segment
│   │
│   └── segment/                          # owns: segment lifecycle
│       ├── __init__.py
│       ├── manager.py                    # EpisodeManager
│       ├── invariants.py                 # segment-level invariants
│       ├── attempt_count.py              # public get_attempt_count helper
│       │
│       └── attempt/                # owns: graph lifecycle
│           ├── __init__.py
│           ├── orchestrator.py           # AttemptOrchestrator (skeleton)
│           └── invariants.py             # graph-level invariants
│
└── harness_agents/                       # EXISTING; unchanged in Phase 01
    └── executor/
```

Persistence:

```text
backend/src/db/
├── models/
│   ├── task_center.py                    # EDIT: keep request/run/task records;
│   │                                     #       remove TaskCenterAttemptRecord
│   ├── mission.py           # NEW
│   ├── episode.py                   # NEW
│   └── attempt.py                  # NEW (replaces legacy schema)
├── stores/
│   ├── __init__.py                       # EDIT: register new stores
│   ├── task_center_store.py              # EDIT: drop harness-graph methods
│   ├── mission_store.py     # NEW
│   ├── episode_store.py             # NEW
│   └── attempt_store.py            # NEW
└── engine.py                             # EDIT: extend _DROPPED_COLUMNS,
                                          #       add legacy table drop hook
```

Tests mirror source:

```text
backend/tests/task_center/
├── domain/
│   ├── test_mission_dto.py
│   ├── test_episode_dto.py
│   ├── test_attempt_dto.py
│   └── test_segment_closure_report.py
├── persistence/
│   ├── test_mission_store.py
│   ├── test_episode_store.py
│   └── test_attempt_store.py
└── lifecycle/
    ├── test_mission_handler.py
    ├── test_episode_manager.py
    ├── test_attempt_count.py
    └── test_invariants.py
```

---

## 5. Files & functions

### 5a. Persistence — SQLAlchemy records

**`backend/src/db/models/mission.py`**

```python
class MissionRecord(Base):
    __tablename__ = "missions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_center_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("task_center_runs.id", ondelete="CASCADE"),
        index=True,
    )
    requested_by_task_id: Mapped[str] = mapped_column(String(96), index=True)
    goal: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))                 # open/succeeded/failed/cancelled
    episode_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    final_outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=lambda: datetime.now(UTC),
                                                 onupdate=lambda: datetime.now(UTC))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                       nullable=True)
```

**`backend/src/db/models/episode.py`**

```python
class EpisodeRecord(Base):
    __tablename__ = "episodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    mission_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("missions.id", ondelete="CASCADE"),
        index=True,
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    creation_reason: Mapped[str] = mapped_column(String(32))    # initial / partial_continuation
    goal: Mapped[str] = mapped_column(Text)
    attempt_budget: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    attempt_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    continuation_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=lambda: datetime.now(UTC),
                                                 onupdate=lambda: datetime.now(UTC))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                       nullable=True)

    __table_args__ = (
        UniqueConstraint("mission_id", "sequence_no",
                         name="uq_episode_request_sequence"),
    )
```

**`backend/src/db/models/attempt.py`** (replaces legacy `TaskCenterAttemptRecord`)

```python
class AttemptRecord(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    episode_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("episodes.id", ondelete="CASCADE"),
        index=True,
    )
    attempt_sequence_no: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(16))                  # planning/generating/evaluating/closed
    status: Mapped[str] = mapped_column(String(16))                 # running/passed/failed
    planner_task_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    task_specification: Mapped[str | None] = mapped_column(Text, nullable=True)
    evaluation_criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    generator_task_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    evaluator_task_id: Mapped[str | None] = mapped_column(String(96), nullable=True)
    continuation_goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    fail_reason: Mapped[str | None] = mapped_column(String(48), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=lambda: datetime.now(UTC),
                                                 onupdate=lambda: datetime.now(UTC))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                       nullable=True)

    __table_args__ = (
        UniqueConstraint("episode_id", "attempt_sequence_no",
                         name="uq_attempt_segment_sequence"),
    )
```

### 5b. Persistence — stores (return frozen DTOs)

**`backend/src/db/stores/mission_store.py`** — `MissionStore(SyncStoreMixin)`

```python
class MissionStore(SyncStoreMixin):
    """CRUD for Mission. Returns frozen Mission DTOs."""

    def insert(self, *,
               task_center_run_id: str,
               requested_by_task_id: str,
               goal: str) -> Mission: ...

    def get(self, request_id: str) -> Mission | None: ...

    def append_episode_id(self, request_id: str,
                          segment_id: str) -> Mission: ...

    def set_status(self, request_id: str, *,
                   status: MissionStatus,
                   final_outcome: dict | None,
                   closed_at: datetime | None = None) -> Mission: ...

    def list_for_executor_task(self,
                               requested_by_task_id: str
                               ) -> list[Mission]: ...

    # Internal: row -> DTO conversion
    def _to_dto(self, record: MissionRecord) -> Mission: ...
```

**`backend/src/db/stores/episode_store.py`** — `EpisodeStore(SyncStoreMixin)`

```python
class EpisodeStore(SyncStoreMixin):
    def insert(self, *,
               mission_id: str,
               sequence_no: int,
               creation_reason: EpisodeCreationReason,
               goal: str,
               attempt_budget: int) -> Episode: ...

    def get(self, segment_id: str) -> Episode | None: ...

    def append_attempt_id(self, segment_id: str,
                        graph_id: str) -> Episode: ...

    def set_continuation_goal(self, segment_id: str,
                              continuation_goal: str | None) -> Episode: ...

    def set_status(self, segment_id: str, *,
                   status: EpisodeStatus,
                   closed_at: datetime | None = None) -> Episode: ...

    def list_for_request(self,
                         mission_id: str) -> list[Episode]:
        """Ordered by sequence_no ascending."""

    def get_by_sequence(self, *,
                        mission_id: str,
                        sequence_no: int) -> Episode | None: ...

    def _to_dto(self, record: EpisodeRecord) -> Episode: ...
```

**`backend/src/db/stores/attempt_store.py`** — `AttemptStore(SyncStoreMixin)`

```python
class AttemptStore(SyncStoreMixin):
    def insert(self, *,
               episode_id: str,
               attempt_sequence_no: int) -> Attempt: ...

    def get(self, graph_id: str) -> Attempt | None: ...

    def set_planner_task_id(self, graph_id: str,
                            planner_task_id: str) -> Attempt: ...

    def set_plan_contract(self, graph_id: str, *,
                          task_specification: str,
                          evaluation_criteria: list[str],
                          continuation_goal: str | None) -> Attempt: ...

    def set_generator_task_ids(self, graph_id: str,
                               task_ids: list[str]) -> Attempt: ...

    def set_evaluator_task_id(self, graph_id: str,
                              evaluator_task_id: str) -> Attempt: ...

    def set_stage(self, graph_id: str,
                  stage: AttemptStage) -> Attempt: ...

    def close(self, graph_id: str, *,
              status: AttemptStatus,
              fail_reason: AttemptFailReason | None,
              closed_at: datetime | None = None) -> Attempt: ...

    def list_for_episode(self, episode_id: str) -> list[Attempt]:
        """Ordered by attempt_sequence_no ascending."""

    def get_by_sequence(self, *,
                        episode_id: str,
                        attempt_sequence_no: int) -> Attempt | None: ...

    def _to_dto(self, record: AttemptRecord) -> Attempt: ...
```

### 5c. Domain DTOs

**`backend/src/task_center/domain/mission.py`**

```python
class MissionStatus(StrEnum):
    OPEN       = "open"
    SUCCEEDED  = "succeeded"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


@dataclass(frozen=True, slots=True)
class Mission:
    id: str
    task_center_run_id: str
    requested_by_task_id: str
    goal: str
    status: MissionStatus
    episode_ids: tuple[str, ...]
    final_outcome: dict | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @property
    def is_open(self) -> bool: ...
    @property
    def latest_segment_id(self) -> str | None: ...
    def with_appended_segment(self, segment_id: str) -> "Mission": ...


@dataclass(frozen=True, slots=True)
class MissionCloseReport:
    """Final report attached to requested_by_task_id when the request closes.

    Lives here (not in a separate file) because the shape is small and
    request-local. Phase 04 wires the actual delivery.
    """
    mission_id: str
    requested_by_task_id: str
    outcome: Literal["success", "failed"]
    final_segment_id: str
    final_attempt_id: str
```

**`backend/src/task_center/domain/episode.py`**

```python
class EpisodeStatus(StrEnum):
    OPEN       = "open"
    SUCCEEDED  = "succeeded"
    FAILED     = "failed"
    CANCELLED  = "cancelled"


class EpisodeCreationReason(StrEnum):
    INITIAL               = "initial"
    PARTIAL_CONTINUATION  = "partial_continuation"


@dataclass(frozen=True, slots=True)
class Episode:
    id: str
    mission_id: str
    sequence_no: int
    creation_reason: EpisodeCreationReason
    goal: str
    attempt_budget: int
    status: EpisodeStatus
    attempt_ids: tuple[str, ...]
    continuation_goal: str | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @property
    def is_open(self) -> bool: ...
    @property
    def attempt_count(self) -> int:
        return len(self.attempt_ids)
    @property
    def has_budget_remaining(self) -> bool:
        return self.attempt_count < self.attempt_budget
    @property
    def latest_graph_id(self) -> str | None: ...
```

**`backend/src/task_center/domain/attempt.py`**

```python
class AttemptStage(StrEnum):
    PLANNING    = "planning"
    GENERATING  = "generating"
    EVALUATING  = "evaluating"
    CLOSED      = "closed"


class AttemptStatus(StrEnum):
    RUNNING  = "running"
    PASSED   = "passed"
    FAILED   = "failed"


class AttemptFailReason(StrEnum):
    PLANNER_FAILED = "planner_failed"
    GENERATOR_FAILED              = "generator_failed"
    EVALUATOR_FAILED              = "evaluator_failed"


@dataclass(frozen=True, slots=True)
class Attempt:
    id: str
    episode_id: str
    attempt_sequence_no: int
    stage: AttemptStage
    status: AttemptStatus
    planner_task_id: str | None
    task_specification: str | None
    evaluation_criteria: tuple[str, ...]
    generator_task_ids: tuple[str, ...]
    evaluator_task_id: str | None
    continuation_goal: str | None
    fail_reason: AttemptFailReason | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None

    @property
    def is_closed(self) -> bool: ...
    @property
    def has_partial_continuation(self) -> bool:
        return self.continuation_goal is not None
```

**`backend/src/task_center/domain/segment_closure_report.py`**

```python
@dataclass(frozen=True, slots=True)
class AttemptedPlanEntry:
    attempt_id: str
    attempt_sequence_no: int
    task_specification: str | None
    evaluation_criteria: tuple[str, ...]
    fail_reason: AttemptFailReason | None
    attempt_summary_id: str | None      # Phase 06 fills this
    failure_landscape: dict | None            # Phase 06 fills this


# Discriminated union via Literal "kind" field.
@dataclass(frozen=True, slots=True)
class TerminalSuccess:
    kind: Literal["terminal_success"] = "terminal_success"


@dataclass(frozen=True, slots=True)
class SuccessContinue:
    goal: str
    kind: Literal["success_continue"] = "success_continue"


@dataclass(frozen=True, slots=True)
class AttemptPlanFailed:
    failure_summary: str
    attempted_plan_history: tuple[AttemptedPlanEntry, ...]
    kind: Literal["attempt_plan_failed"] = "attempt_plan_failed"


ClosureOutcome = TerminalSuccess | SuccessContinue | AttemptPlanFailed


@dataclass(frozen=True, slots=True)
class EpisodeClosureReport:
    episode_id: str
    final_attempt_id: str
    outcome: ClosureOutcome
```

### 5d. Lifecycle services

**`backend/src/task_center/exceptions.py`**

```python
class GraphInvariantViolation(Exception):
    """Raised when a harness lifecycle invariant is violated.

    Matches the existing 'GraphInvariantViolation' convention used elsewhere
    in the codebase for hard, non-tolerable harness state breaches.
    """
```

**`backend/src/task_center/mission/config.py`**

```python
@dataclass(frozen=True, slots=True)
class HarnessLifecycleConfig:
    default_attempt_budget: int = 2
```

**`backend/src/task_center/mission/segment/attempt_count.py`**

```python
def get_attempt_count(episode: Episode) -> int:
    """Public helper. Derives attempt count from attempt_ids.

    Phase 01 spec exit criterion: 'Expose a public get_attempt_count helper
    that returns the count derived from attempt_ids rather than storing
    a separate counter.'
    """
    return len(episode.attempt_ids)
```

**`backend/src/task_center/mission/segment/manager.py`** — `EpisodeManager`

The only creator of `Attempt` records inside its owned segment, and the
sole emitter of `EpisodeClosureReport`.

```python
class EpisodeManager:
    def __init__(self, *,
                 episode_id: str,
                 segment_store: EpisodeStore,
                 graph_store: AttemptStore,
                 on_segment_closed: Callable[[EpisodeClosureReport], None],
                 # Phase 02 wires the orchestrator factory; Phase 01 keeps it Optional.
                 orchestrator_factory: (Callable[[Attempt],
                                                "AttemptOrchestrator"]
                                        | None) = None):
        ...

    # ---- public API ----

    def create_initial_attempt(self) -> Attempt:
        """Create attempt_sequence_no=1 for the owned segment, append to
        attempt_ids. Phase 01 stops here; Phase 02 will start the
        orchestrator."""

    def create_next_attempt(self, *,
                                  previous_attempt_id: str
                                  ) -> Attempt:
        """Called by Phase 02 after a failed graph if budget remains."""

    def handle_attempt_closed(self, attempt_id: str) -> None:
        """Entry point for the closed-graph callback. Routes to one of:
            _close_segment_passed(graph)
            _retry_or_close_failed(graph)
        """

    def get_attempt_count(self) -> int:
        return get_attempt_count(self._current_segment_snapshot())

    # ---- internal ----

    def _current_segment_snapshot(self) -> Episode: ...

    def _close_segment_passed(self, graph: Attempt) -> None:
        """Copy graph.continuation_goal -> segment.continuation_goal,
        close segment succeeded, emit terminal_success or success_continue."""

    def _retry_or_close_failed(self, graph: Attempt) -> None:
        """If budget remains -> create_next_attempt; else close segment
        failed and emit attempt_plan_failed."""

    def _emit_terminal_success(self, graph: Attempt) -> None: ...
    def _emit_success_continue(self, graph: Attempt) -> None: ...
    def _emit_attempt_plan_failed(self, last_graph: Attempt) -> None: ...

    def _build_attempted_plan_history(self) -> tuple[AttemptedPlanEntry, ...]:
        """Read all graphs in this segment, map to AttemptedPlanEntry list,
        ordered by attempt_sequence_no. attempt_summary_id and
        failure_landscape are filled with None (Phase 06 fills them)."""
```

**`backend/src/task_center/mission/segment_manager_registry.py`**

```python
class EpisodeManagerRegistry:
    """Process-local registry: one EpisodeManager per open Episode."""

    def __init__(self) -> None:
        self._by_segment_id: dict[str, EpisodeManager] = {}

    def register(self, manager: EpisodeManager) -> None: ...
    def get(self, episode_id: str) -> EpisodeManager | None: ...
    def deregister(self, episode_id: str) -> None: ...
    def assert_unique_for_segment(self, episode_id: str) -> None:
        """Raise GraphInvariantViolation if a manager is already registered
        for this segment."""
```

**`backend/src/task_center/mission/handler.py`** — `MissionHandler`

The only creator of `Mission` and `Episode` records, and the
spawner of `EpisodeManager` instances.

```python
class MissionHandler:
    def __init__(self, *,
                 request_store: MissionStore,
                 segment_store: EpisodeStore,
                 graph_store: AttemptStore,
                 manager_registry: EpisodeManagerRegistry,
                 config: HarnessLifecycleConfig,
                 # Phase 04 wires this. Phase 01 keeps it Optional.
                 deliver_close_report: (Callable[[MissionCloseReport], None]
                                        | None) = None):
        ...

    # ---- public API ----

    def create_mission(self, *,
                                    task_center_run_id: str,
                                    requested_by_task_id: str,
                                    goal: str) -> Mission:
        """Create the request from request_mission_solution.
        status=open, empty episode_ids."""

    def create_initial_segment(self, *,
                               mission_id: str
                               ) -> Episode:
        """Create segment 1 with goal=request.goal,
        creation_reason=initial,
        attempt_budget=config.default_attempt_budget.
        Append to request.episode_ids.
        Spawn EpisodeManager(S1) and register it."""

    def create_continuation_segment(self, *,
                                    previous_segment: Episode
                                    ) -> Episode:
        """Pre: previous_segment.status==SUCCEEDED and
        previous_segment.continuation_goal is not None.
        Create segment N+1 with sequence_no=previous+1,
        goal=previous_segment.continuation_goal,
        creation_reason=partial_continuation.
        Append to request.episode_ids.
        Spawn fresh EpisodeManager(SN+1)."""

    def handle_segment_closed(self,
                              report: EpisodeClosureReport) -> None:
        """Route by outcome:
          SuccessContinue(goal) -> create_continuation_segment
          TerminalSuccess        -> close_mission(succeeded)
          AttemptPlanFailed(...) -> close_mission(failed)
        Always deregister the closing segment's manager."""

    def close_mission(self, *,
                                   mission_id: str,
                                   succeeded: bool,
                                   final_segment_id: str,
                                   final_attempt_id: str
                                   ) -> Mission:
        """Persist final_outcome, set status, set closed_at,
        and call deliver_close_report (Phase 04 wires the callback)."""

    # ---- internal ----

    def _spawn_segment_manager(self, segment: Episode) -> EpisodeManager: ...
    def _build_close_report(self, *,
                            request: Mission,
                            outcome: ClosureOutcome,
                            final_segment_id: str,
                            final_attempt_id: str
                            ) -> MissionCloseReport: ...
```

**`backend/src/task_center/mission/segment/attempt/orchestrator.py`** — `AttemptOrchestrator` (skeleton; Phase 02 fills in)

```python
class AttemptOrchestrator:
    """One-graph-run orchestrator. Phase 01 ships the contract surface only;
    Phase 02 implements planner / generator / evaluator wiring."""

    def __init__(self, *,
                 attempt: Attempt,
                 graph_store: AttemptStore,
                 # Wired to EpisodeManager.handle_attempt_closed.
                 on_graph_closed: Callable[[str], None]):
        ...

    def start(self) -> None:
        raise NotImplementedError("Phase 02")

    def handle_planner_terminal(self, plan_submission: object) -> None:
        raise NotImplementedError("Phase 02")

    def handle_generator_terminal(self, *,
                                  task_id: str,
                                  status: str) -> None:
        raise NotImplementedError("Phase 02")

    def handle_evaluator_terminal(self, terminal: object) -> None:
        raise NotImplementedError("Phase 02")

    def close(self, *,
              status: AttemptStatus,
              fail_reason: AttemptFailReason | None,
              continuation_goal: str | None) -> None:
        raise NotImplementedError("Phase 02")
```

**Invariants modules** (all raise `GraphInvariantViolation`):

`backend/src/task_center/mission/invariants.py`

```python
def assert_request_open(request: Mission) -> None
def assert_segment_can_be_appended(request: Mission,
                                   new_segment: Episode) -> None
def assert_segment_sequence_contiguous(request: Mission,
                                       new_sequence_no: int) -> None
def assert_continuation_segment_predecessor(previous: Episode) -> None
    # previous.status == SUCCEEDED and previous.continuation_goal is not None
def assert_no_root_creation_reason(creation_reason: str) -> None
def assert_segment_id_unique_in_list(request: Mission,
                                     segment_id: str) -> None
```

`backend/src/task_center/mission/segment/invariants.py`

```python
def assert_segment_open(segment: Episode) -> None
def assert_segment_open_for_graph_creation(segment: Episode) -> None
def assert_segment_has_budget(segment: Episode) -> None
def assert_passing_graph_closes_segment(graph: Attempt) -> None
def assert_continuation_goal_only_from_passing_graph(
        graph: Attempt, segment: Episode) -> None
def assert_graph_belongs_to_segment(graph: Attempt,
                                    segment: Episode) -> None
```

`backend/src/task_center/mission/segment/attempt/invariants.py`

```python
def assert_graph_running(graph: Attempt) -> None
def assert_graph_sequence_contiguous(segment: Episode,
                                     new_sequence_no: int) -> None
def assert_evaluator_only_after_quiescence(...) -> None  # Phase 02 detail
def assert_fail_reason_present_on_failure(graph: Attempt) -> None
```

### 5e. Files edited (not created)

| File | Edit |
| --- | --- |
| `db/models/task_center.py` | Drop `TaskCenterAttemptRecord`. Drop `attempts` relationship from `TaskCenterRunRecord`. Keep `task_center_attempt_id` column on `TaskCenterTaskRecord` (semantics now point at `attempts.id`; column type unchanged). |
| `db/models/__init__.py` | Re-export new records. |
| `db/stores/task_center_store.py` | Remove `upsert_attempt`, `list_attempts_for_run`, `_serialize_attempt`. |
| `db/stores/__init__.py` | Add `MissionStore`, `EpisodeStore`, `AttemptStore` to `_EXPORTS`. |
| `db/engine.py` | Add `task_center_attempt` to a new `_LEGACY_TABLES_TO_DROP` set; add a small `_drop_legacy_tables()` helper invoked once after `Base.metadata.create_all()`. See section 6. |

### 5f. Files deferred (touched in later phases)

| File | Phase | Note |
| --- | --- | --- |
| `tools/submission/main_agent/planner/submit_full_plan.py` | Phase 03 | Currently `NotImplementedError` stub. Phase 03 wires it through the active `Attempt`'s planner-task ID; Phase 01 leaves it untouched. |
| `tools/submission/main_agent/planner/submit_partial_plan.py` | Phase 03 | Same. |
| Any tool gate enforcement | Phase 03 | |
| Final-report delivery to `requested_by_task_id` | Phase 04 | `MissionHandler.deliver_close_report` callback stays `None` in Phase 01. |
| `attempt_summary_id` / `failure_landscape` population | Phase 06 | Phase 01 leaves them `None`. |

---

## 6. Database migration plan

The codebase does not use Alembic. Schema changes are applied automatically by
`db/engine.py:initialize_db`, which:

1. Imports `db.models` to populate `Base.metadata`.
2. Calls `Base.metadata.create_all(_engine)` — creates new tables, never drops.
3. Calls `_rename_columns(_engine)` using `_RENAMED_COLUMNS` registry.
4. Calls `_add_missing_columns(_engine)` using `_DROPPED_COLUMNS` registry.

Phase 01 needs three things from this layer:

### 6a. Three new tables

`Base.metadata.create_all` creates them automatically once the new model files
are imported in `db/models/__init__.py`. No manual SQL.

- `missions`
- `episodes`
- `attempts`

### 6b. Drop the legacy `task_center_attempt` table

`create_all` will not drop it. Add a small helper in `db/engine.py`:

```python
_LEGACY_TABLES_TO_DROP: set[str] = {
    "task_center_attempt",
}

def _drop_legacy_tables(engine: Engine) -> None:
    insp = inspect(engine)
    for name in _LEGACY_TABLES_TO_DROP:
        if insp.has_table(name):
            logger.info("Dropping legacy table %s", name)
            with engine.begin() as conn:
                conn.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
```

Invoke it in `initialize_db` *after* `_add_missing_columns`:

```python
Base.metadata.create_all(_engine)
_rename_columns(_engine)
_add_missing_columns(_engine)
_drop_legacy_tables(_engine)        # NEW
```

Order matters: drop the legacy table only after auto-migrations have a chance
to operate on currently-modeled tables.

### 6c. `task_center_tasks.task_center_attempt_id` semantics

The column already exists (`String(96), nullable=True`) and has no FK
constraint on the model side. Phase 01 keeps the column shape; only the
semantic referent changes (it now points at `attempts.id` instead of
`task_center_attempt.id`). No migration step is needed.

If we wanted to add a real FK to `attempts.id`, that would require a
table rebuild on SQLite. **Recommend deferring** the FK addition to a later
phase or skipping it entirely — the existing codebase pattern uses lots of
loose string FKs.

### 6d. Verification

After migration, the database should have:

| Table | State |
| --- | --- |
| `task_center_requests` | unchanged |
| `task_center_runs` | unchanged |
| `task_center_tasks` | unchanged (column `task_center_attempt_id` retained) |
| `agent_runs` | unchanged |
| `task_center_attempt` | **dropped** |
| `missions` | **created** |
| `episodes` | **created** |
| `attempts` | **created** |

---

## 7. Class summary

| Layer | Class | New / Edited | Single responsibility |
| --- | --- | --- | --- |
| Persistence | `MissionRecord` | NEW | SQLA row for `missions` |
| Persistence | `EpisodeRecord` | NEW | SQLA row for `episodes` |
| Persistence | `AttemptRecord` | NEW (replaces legacy) | SQLA row for `attempts` |
| Stores | `MissionStore` | NEW | CRUD; returns `Mission` DTOs |
| Stores | `EpisodeStore` | NEW | CRUD; returns `Episode` DTOs |
| Stores | `AttemptStore` | NEW | CRUD; returns `Attempt` DTOs |
| Stores | `TaskCenterStore` | EDIT | Request/run/task only; harness-graph methods removed |
| Domain | `Mission` | NEW | frozen DTO |
| Domain | `Episode` | NEW | frozen DTO |
| Domain | `Attempt` | NEW | frozen DTO |
| Domain | `AttemptedPlanEntry` | NEW | frozen DTO |
| Domain | `TerminalSuccess` / `SuccessContinue` / `AttemptPlanFailed` | NEW | discriminated union |
| Domain | `EpisodeClosureReport` | NEW | segment -> handler signal DTO |
| Domain | `MissionCloseReport` | NEW | handler -> executor signal DTO |
| Domain | `MissionStatus` / `EpisodeStatus` / `EpisodeCreationReason` / `AttemptStage` / `AttemptStatus` / `AttemptFailReason` | NEW | enums |
| Lifecycle | `HarnessLifecycleConfig` | NEW | runtime config (default attempt budget) |
| Lifecycle | `MissionHandler` | NEW | request boundary; only creator of request + segment |
| Lifecycle | `EpisodeManager` | NEW | per-segment retry; only creator of harness graph in its segment; only emitter of `EpisodeClosureReport` |
| Lifecycle | `AttemptOrchestrator` | NEW (skeleton) | one graph run; behavior in Phase 02 |
| Lifecycle | `EpisodeManagerRegistry` | NEW | one-manager-per-open-segment |
| Exception | `GraphInvariantViolation` | NEW | hard invariant breach |

---

## 8. Test plan

Tests are organized in three layers and target Phase 01 exit criteria
specifically. All tests use an in-memory SQLite via the existing
`SyncStoreMixin` initialize pattern.

### 8a. Domain layer (no DB required)

| File | Tests |
| --- | --- |
| `test_mission_dto.py` | `with_appended_segment` is immutable; `latest_segment_id` returns last id; `is_open` matches status |
| `test_episode_dto.py` | `attempt_count == len(attempt_ids)`; `has_budget_remaining` flips at boundary; `latest_graph_id` returns last id |
| `test_attempt_dto.py` | `has_partial_continuation` matches `continuation_goal`; `is_closed` matches stage |
| `test_segment_closure_report.py` | Each outcome variant constructs; `kind` discriminator parses; `attempted_plan_history` ordered by `attempt_sequence_no` |

### 8b. Persistence layer

For each of the three new stores:

| Test | Purpose |
| --- | --- |
| `test_<store>_insert_returns_dto` | Store returns frozen DTOs, not rows or dicts |
| `test_<store>_get_round_trip` | Persisted DTO equals inserted DTO |
| `test_<store>_list_ordering` | `list_for_*` returns ordered by sequence number |
| `test_<store>_append_id_atomic` | Appending to JSON list does not race within one session |
| `test_<store>_status_transition` | `set_status` updates DTO snapshot |

### 8c. Lifecycle layer

These tests directly mirror Phase 01 spec exit criteria:

| Test | Phase 01 exit criterion |
| --- | --- |
| `test_create_mission_links_executor` | "request_mission_solution creating a request linked to requested_by_task_id" |
| `test_request_records_segments_in_episode_ids` | "each request records created segments in episode_ids" |
| `test_episode_ids_holds_multiple_segments` | "episode_ids can hold multiple Episode ids for one request" |
| `test_continuation_segment_inherits_continuation_goal` | "continuation creating Episode N+1 with goal set from the previous segment's continuation_goal" |
| `test_retry_creates_graph_in_same_segment` | "EpisodeManager retry creates another Attempt in the same segment, not a new segment or request" |
| `test_initial_segment_creates_graph_sequence_1` | "create segment 1 with harness graph sequence 1" |
| `test_passing_graph_closes_segment_and_does_not_retry` | Spec rule: "A passing harness graph always closes the owned segment" |
| `test_passing_graph_with_null_continuation_emits_terminal_success` | Closure-report routing |
| `test_passing_graph_with_continuation_emits_success_continue` | Closure-report routing |
| `test_failed_graph_with_budget_creates_next_graph` | Budget remaining branch |
| `test_failed_graph_without_budget_emits_attempt_plan_failed` | Budget exhausted branch |
| `test_attempted_plan_history_ordered_by_graph_sequence` | Phase 00/01 spec on history payload |
| `test_segment_id_unique_in_request_list` | Spec invariant |
| `test_no_root_creation_reason_accepted` | Spec rule |
| `test_get_attempt_count_derived_from_list` | Spec rule on derived counter |
| `test_segment_manager_registry_enforces_uniqueness` | Spec rule: "Exactly one EpisodeManager instance is active per open segment" |
| `test_continuation_segment_only_from_succeeded_predecessor_with_goal` | Spec invariant |

### 8d. Invariant tests

A small dedicated module verifies each invariant raises
`GraphInvariantViolation` on the violating input and is silent on valid input.

---

## 9. Build order (waves)

Each wave is independently committable. Tests in each wave verify that wave's
contracts before the next wave begins.

### Wave 1 — Persistence foundation

1. Create `db/models/mission.py`, `episode.py`, `attempt.py`.
2. Edit `db/models/task_center.py` to remove `TaskCenterAttemptRecord` and the `attempts` relationship on `TaskCenterRunRecord`.
3. Edit `db/models/__init__.py` to re-export new records.
4. Add `_LEGACY_TABLES_TO_DROP` and `_drop_legacy_tables` to `db/engine.py`; wire into `initialize_db`.
5. Verify migration locally: existing DB drops legacy table, creates new ones.

### Wave 2 — Stores returning DTOs

1. Create `task_center/domain/` DTOs and enums (4 files).
2. Create `task_center/exceptions.py` with `GraphInvariantViolation`.
3. Create three new stores in `db/stores/` with `_to_dto` helpers.
4. Edit `db/stores/__init__.py` to register new stores; remove obsolete methods from `task_center_store.py`.
5. Run persistence-layer tests (8b).

### Wave 3 — Lifecycle skeleton

1. Create `task_center/mission/` package (handler, config, registry, invariants).
2. Create `task_center/mission/segment/` package (manager, attempt_count, invariants).
3. Create `task_center/mission/segment/attempt/` package (orchestrator skeleton, invariants).
4. Run domain (8a) + lifecycle (8c) + invariant (8d) tests.

### Wave 4 — Integration smoke

1. End-to-end test: simulate a complex-task request through to terminal success
   and through to attempt-plan-failed, *without* the orchestrator (Phase 02
   feature). Substitute a stub orchestrator that closes the graph synchronously
   with passed/failed outcomes.
2. Confirm Phase 01 exit criteria (section 10) are met.

---

## 10. Phase 01 exit criteria mapping

| Phase 01 exit criterion | Verified by |
| --- | --- |
| Runtime can create and load a `Mission` | `test_create_mission_links_executor` + persistence round-trip |
| Runtime can create segment 1 with harness graph sequence 1 | `test_initial_segment_creates_graph_sequence_1` |
| Tests cover `request_mission_solution` creating a request linked to `requested_by_task_id` | `test_create_mission_links_executor` |
| Tests prove each request records created segments in `episode_ids` | `test_request_records_segments_in_episode_ids` |
| Tests prove `episode_ids` can hold multiple `Episode` ids for one request | `test_episode_ids_holds_multiple_segments` |
| Tests cover continuation creating `Episode` N+1 with `goal` set from the previous segment's `continuation_goal` | `test_continuation_segment_inherits_continuation_goal` |
| Tests prove `EpisodeManager` retry creates another `Attempt` in the same segment, not a new segment or request | `test_retry_creates_graph_in_same_segment` |

---

## 11. Risks & open questions

### 11a. Migration risk: existing legacy data in `task_center_attempt`

Dropping the legacy table is destructive. Mitigations:

- Phase 01 is pre-cutover (cutover is Phase 05). Live executions on the new
  schema are not yet possible, so legacy rows are not load-bearing.
- The helper logs the drop. Confirm in dev environments before merging.
- If any environment needs the legacy data preserved for forensic reasons,
  rename the table (`__task_center_attempt_legacy`) instead of dropping;
  keeps the data without conflicting with the new `attempts` table.

### 11b. Stores returning DTOs is a new convention

The existing codebase mixes "return SQLA records" and "return dicts" patterns.
Phase 01 introduces typed frozen DTOs in stores. Risks:

- New consumers may not realize they get DTOs and try to mutate them.
  Mitigation: `frozen=True, slots=True`. Mutation raises `FrozenInstanceError`.
- If we ever add a non-lifecycle consumer (e.g., a UI list endpoint), it could
  bypass invariants enforced in lifecycle services. Mitigation: revisit and
  introduce a Repository wrapper layer if a second consumer appears.

### 11c. `task_center_tasks.task_center_attempt_id` has no real FK

Decision: keep it that way for Phase 01. Adding a real FK on SQLite requires
a table rebuild via `_rebuild_sqlite_table`, which is risky for an existing
production schema. The codebase pattern already uses many loose string FKs.

### 11d. Default `attempt_budget`

Not specified by any Phase 0x doc. Phase 01 sets `default_attempt_budget = 2`
in `HarnessLifecycleConfig`. Budget can be overridden per-request via
configuration injection later (Phase 02 or Phase 04 may surface a knob).

### 11e. `attempt_summary_id` / `failure_landscape` are `None` until Phase 06

`AttemptedPlanEntry` carries these fields but Phase 01 populates them as
`None`. The Phase 06 context engine fills them. Tests should assert the
fields exist and are `None`, not absent.

### 11f. Phase 01 does not deliver the close report

`MissionHandler.deliver_close_report` callback stays `None` in
Phase 01. The handler still persists `final_outcome` and sets request status,
so the close report is *constructible* — Phase 04 wires the actual delivery
to `requested_by_task_id`.

---

## 12. References

- [Phase 00 - Target Architecture](./phase-00-target-architecture.md)
- [Phase 01 - Graph and Attempt Model](./phase-01-mission-episode-attempt-model.md) (the spec this plan implements)
- [Phase 02 - Harness Graph Orchestrator Lifecycle](./phase-02-attempt-orchestrator-lifecycle.md) (consumer of Phase 01 contracts)
- [Phase 04 - Complex Task Spawning](./phase-04-mission-spawning.md)
- [Complex Task Workflow Overview](./mission-episode-attempt-workflow-overview.md)
- `backend/src/db/engine.py` — auto-migration mechanism used by this plan
- `backend/src/db/stores/model_store.py` — closest existing precedent for store-returns-DTO style
