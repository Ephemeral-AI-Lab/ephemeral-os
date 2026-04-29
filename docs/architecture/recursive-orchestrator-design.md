# Recursive Orchestrator Design

**Status:** Design (in flight)
**Supersedes:** Section 10 of `four-role-advisor-gated-design.md` (Task Center Task-Creation Workflow)
**Companion:** `four-role-advisor-gated-design.md` (four-role + advisor-gating remain as specified there)

This document specifies the **recursive orchestrator pattern** for task
creation and graph lifecycle. Each `HarnessGraph` is owned by a single
graph-scoped `Orchestrator` that handles all creation and closure for
that graph. Orchestrators recurse via graphs spawning graphs, never by
calling each other directly.

---

## 1. Overview

A run is a tree of `HarnessGraph` instances. Each graph contains one
planner, a DAG of generators (executors and verifiers), and one
auto-spawned evaluator. Each graph reports a single
success/partial-success/failure summary onto its `root_task` and then
goes silent.

Three pieces hold this together:

| Piece | Role |
|---|---|
| `Orchestrator(graph_id, tc)` | Graph-scoped facade. Creates and closes one graph. |
| `RunController(tc)` | Run-level coordinator. Owns `root_exec` (the one task with no graph). |
| `TaskCenter` | Single store + persistence layer. Exposes `_create_<role>` primitives. |

Lifecycle modules under `harness_agents/<role>/lifecycle.py` collapse
to thin runtime-dispatcher routers — they look up the calling task's
`Orchestrator` and call the matching method.

---

## 2. The Orchestrator Pattern

### 2.1 Per-graph ownership

Every `HarnessGraph` has exactly one orchestrator. The orchestrator is
a transient frozen-dataclass view bound to a `graph_id` and a
`TaskCenter` reference; it has no state of its own. Constructing one
is essentially free.

```python
@dataclass(frozen=True)
class Orchestrator:
    graph_id: HarnessGraphId
    tc: "TaskCenter"
```

Two ways to obtain an orchestrator:

1. **`Orchestrator.spawn(...)`** — opens a *new* graph + planner, returns
   the orchestrator for it. Side-effecting.
2. **`tc.orchestrator(graph_id) -> Orchestrator`** — pure view of an
   *existing* graph. Used by the runtime dispatcher when routing
   terminal calls.

### 2.2 Recursion via graph-spawning, not method-calls

Orchestrators never reach into another graph. The bridge between
`Orchestrator(G_n)` and `Orchestrator(G_{n+1})` is the data link
`G_{n+1}.root_task_id` — a task id that lives in `G_n`'s DAG (or, for
the very first graph, points at `root_exec`).

When `G_{n+1}` closes, its orchestrator writes a summary onto its
`root_task` and propagates that task's terminal status. From `G_n`'s
perspective this looks identical to any node-level state change.

### 2.3 Two construction paths, two spawn sites

Spawn-sites collapse to two — both call `Orchestrator.spawn(...)`:

| # | Spawn site | Caller | `root_task_id` | `prior_graph_id` |
|---|---|---|---|---|
| **A** | `executor.request_plan` | runtime dispatcher | `executor.id` | `None` |
| **B** | partial-plan closure | `Orchestrator.close_partial_success` | `self.graph.root_task_id` | `self.graph_id` |

Case A applies uniformly to root and in-graph executors — there is no
special "open the first graph" code path. Root_exec is just an
executor whose `harness_graph_id` happens to be `None`.

### 2.4 The single root asymmetry

Root_exec has no parent graph, so when `G_1` closes there is no parent
graph to propagate into. This is handled by exactly one conditional
inside the closure path (Section 10.4). Everywhere else — tool surface,
dispatch, spawn — root_exec is treated identically to any executor.

---

## 3. The Generator Role-Class

In-DAG nodes are *generators*: they produce evidence consumed by the
evaluator.

```python
GeneratorRole = Literal["executor", "verifier"]

TaskRole = Literal["planner", "executor", "verifier", "evaluator", "advisor"]
# Generator is a role-class, not a stored role. TaskRole is unchanged.
```

Conceptual DAG shape:

```
  planner (entry)
        │
        ▼
  ┌────────────────────────┐
  │   generators (DAG)     │   each node: role ∈ {executor, verifier}
  │   exec ─► verif ─► …   │
  └────────────────────────┘
        │   (sinks)
        ▼
  evaluator (gate)
```

The DAG must contain ≥1 generator; all in-DAG nodes must be generators;
verifiers cannot be DAG sinks (would gate-conflict with the
auto-spawned evaluator — see Section 9.3).

---

## 4. Data Model Changes

### 4.1 `HarnessGraph`

Restructured to carry the four structural slots explicitly:

```python
@dataclass
class HarnessGraph:
    id: HarnessGraphId
    run_id: str
    root_task_id: TaskId                  # parent task receiving terminal summary

    # Structural slots — populated as the graph progresses.
    planner: TaskId                       # set at Orchestrator.spawn
    dag_nodes: list[TaskId]               # set at materialize_*_plan; generators only
    evaluator: TaskId | None              # set at materialize_*_plan; auto-spawned at sinks

    # Planning context.
    root_goal: str
    request_plan_note: str
    plan_shape: Literal["full", "partial"] | None = None
    what_to_do_next: str = ""
    prior_graph_id: HarnessGraphId | None = None
```

`fix_executor` tasks are tracked separately via `Task.fix_target_id`
back-pointer; they are not in `dag_nodes`. `dag_nodes` is exactly the
generator set the planner emitted.

### 4.2 `Status`

Add `FIXING` for verifier-recovery state (per the four-role doc §9.1).

```python
class Status(str, Enum):
    # ... existing ...
    FIXING = "fixing"   # verifier emitted failure; fix-executor in flight
```

### 4.3 DAG entry shape

```python
{"id": str, "deps": list[str], "role": GeneratorRole}
```

---

## 5. Orchestrator Class Specification

### 5.1 Constructors

```python
@dataclass(frozen=True)
class Orchestrator:
    graph_id: HarnessGraphId
    tc: "TaskCenter"

    @classmethod
    def spawn(
        cls,
        tc: "TaskCenter",
        *,
        root_task_id: TaskId,
        request_plan_note: str,
        prior_graph_id: HarnessGraphId | None = None,
    ) -> "Orchestrator":
        """Open a new HarnessGraph + spawn its planner READY.

        - `root_task_id`: the task in the parent graph (or `root_exec`)
          that will receive this graph's terminal summary.
        - `request_plan_note`: planner's input prompt.
        - `prior_graph_id`: set for partial-plan continuation chains.

        Side effects:
        - Creates HarnessGraph in store with the given linkage.
        - Creates planner Task (status=READY, role='planner').
        - Persists + sets wakeup.
        """
```

The `__init__` from `@dataclass` is the pure-view constructor:
`Orchestrator(graph_id, tc)` produces a view over an existing graph
with no side effects.

### 5.2 Read accessors

```python
    @property
    def graph(self) -> HarnessGraph: ...
    @property
    def root_task(self) -> Task: ...
    @property
    def planner(self) -> Task: ...
    @property
    def evaluator(self) -> Task | None: ...
    @property
    def dag_nodes(self) -> list[Task]: ...
```

### 5.3 Mutating methods (full surface)

```python
    # ── DAG materialization (planner terminal handlers) ──────────
    def materialize_full_plan(
        self,
        task_dep_graphs: list[dict],
        task_details: dict[str, str],
        evaluation_specification: str,
    ) -> MaterializationFailure | None: ...

    def materialize_partial_plan(
        self,
        task_dep_graphs: list[dict],
        task_details: dict[str, str],
        what_to_do_next: str,
        evaluation_specification: str,
    ) -> MaterializationFailure | None: ...

    # ── Mid-graph fix-executor (verifier failure handler) ────────
    def create_harness_fix_executor(
        self,
        verifier_id: TaskId,
        failure_summary: str,
    ) -> None: ...

    # ── Closure (evaluator terminal handlers) ────────────────────
    def close_success(self, summary: str) -> None: ...
    def close_partial_success(self, summary: str) -> None: ...
    def close_failure(self, summary: str) -> None: ...

    # ── Continuation note synthesis (graph-local helper) ─────────
    def build_continuation_note(self) -> str: ...
```

**Removed methods** (vs prior drafts):
- `create_harness_planner` — folded into `Orchestrator.spawn`.
- `open_request_plan_subgraph` — runtime dispatcher calls
  `Orchestrator.spawn(...)` directly; root_exec uniformity (Section 2.4).

### 5.4 Result types

```python
@dataclass(frozen=True)
class MaterializationFailure:
    code: str        # "empty_dag" | "duplicate_ids" | "missing_details"
                     # | "unknown_role" | "unknown_dep" | "cycle"
                     # | "verifier_sink"
    message: str     # human-readable; forwarded to the agent as tool result
```

A successful materialization returns `None`. A failed one returns
`MaterializationFailure` so the runtime dispatcher can forward it as a
tool-result failure (Section 9.4).

---

## 6. RunController

```python
# task_center/runtime/run_controller.py

@dataclass
class RunController:
    """Run-level coordinator. Owns root_exec — the one task that lives
    outside any HarnessGraph. After root_exec is created, every
    capability of root_exec routes through the same dispatcher as any
    in-graph executor."""
    tc: "TaskCenter"
    root_task_id: TaskId | None = None

    def start(self, prompt: str) -> Task:
        """Create root_exec via tc._create_executor.
        status=READY, harness_graph_id=None, needs=frozenset(),
        input=prompt."""

    @property
    def root_task(self) -> Task: ...

    def is_done(self) -> bool: ...
    def result(self) -> RunResult: ...
```

There is no `on_root_request_plan`. When root_exec calls
`request_plan`, the same dispatcher that handles every other
executor's `request_plan` runs.

---

## 7. File Layout

```
backend/src/task_center/runtime/
├── task_center.py        # TaskCenter (store + _create_<role> primitives)
├── orchestrator.py       # Orchestrator class (graph-scoped facade)
├── run_controller.py     # RunController (root-level coordinator)
└── spawn.py              # SpawnFunc (unchanged; runs READY tasks)
```

The existing `runtime/orchestrator.py` (which currently houses
`TaskCenter`) is renamed to `task_center.py`. The new
`runtime/orchestrator.py` houses the `Orchestrator` class. Two
distinct modules with non-overlapping verbs.

| File | Verbs | Owns |
|---|---|---|
| `task_center.py` | `_create_<role>`, `_open_graph`, `_persist_*` | Persistent store, primitives, persistence |
| `orchestrator.py` | `spawn`, `materialize_*`, `create_harness_fix_executor`, `close_*` | Graph-scoped lifecycle |
| `run_controller.py` | `start`, `is_done`, `result` | Run-level termination |
| `spawn.py` | `spawn` (in `SpawnFunc`) | Run the LLM agent process for a READY task |

---

## 8. Workflow Walkthroughs

### 8.1 Run start

```
RunController.start(prompt)
  └─ tc._create_executor(input=prompt, harness_graph_id=None,
                         needs=frozenset(), status=READY)
  └─ tc._persist_all(); tc._wakeup.set()

Result: root_exec READY, no graph.
```

### 8.2 Case A — executor.request_plan (root or in-graph; identical)

```
agent calls request_plan(detail)
  │
  ▼
Pre-hook: advisor must have accepted "request_plan" with this payload
  │ (passes — agent already consulted advisor)
  ▼
Runtime dispatch:
  on_executor_request_plan(tc, executor_id, detail):
    executor = tc._graph.get(executor_id)
    executor.status = HANDOFF
    Orchestrator.spawn(
        tc,
        root_task_id=executor_id,
        request_plan_note=detail,
    )
    tc._persist_all(); tc._wakeup.set()

  ── inside Orchestrator.spawn ──
    G_new = tc._open_graph(root_task_id=executor_id,
                           request_plan_note=detail,
                           prior_graph_id=None)
    P_new = tc._create_planner(input=detail,
                               harness_graph_id=G_new.id)
    G_new.planner = P_new.id
    return Orchestrator(G_new.id, tc)
```

The returned orchestrator is discarded — caller doesn't need it.
G_new's planner is now READY; the dispatcher will pick it up.

### 8.3 Case B-full — full-plan closure

```
agent calls submit_evaluation_success(summary)
  │ (advisor accepted)
  ▼
Runtime dispatch:
  on_evaluator_success(tc, eval_id, summary):
    orch = tc.orchestrator(graph_of(eval_id))
    if orch.graph.plan_shape == "full":
        orch.close_success(summary)
    else:
        orch.close_partial_success(summary)

  ── inside close_success(summary) ──
    self.evaluator.status = DONE
    self.evaluator.summaries.append(summary)
    self.planner.status = DONE
    self.root_task.summaries.append(child_success(summary))
    self.root_task.status = DONE
    if self.root_task.harness_graph_id is None:
        # root_exec — RunController.is_done() will see DONE next loop
        return
    else:
        propagate_in_parent_graph(self.tc, self.root_task)
        # unblock dependents in parent graph;
        # if root_task was a sink, parent's evaluator may become READY
    self.tc._persist_all(); self.tc._wakeup.set()
```

Single conditional (`harness_graph_id is None`) handles the root-exec
asymmetry. Everything else is uniform.

### 8.4 Case B-partial — partial-plan closure

```
  ── inside close_partial_success(summary) ──
    self.evaluator.status = DONE
    self.evaluator.summaries.append(summary)
    self.planner.status = DONE
    self.root_task.summaries.append(segment_success(summary))
    # root_task STAYS HANDOFF — chain is not yet terminal
    Orchestrator.spawn(
        self.tc,
        root_task_id=self.graph.root_task_id,
        request_plan_note=self.build_continuation_note(),
        prior_graph_id=self.graph_id,
    )
    self.tc._persist_all(); self.tc._wakeup.set()
```

The continuation graph G' shares root_task with the prior graph G.
Both `segment_success` summaries land on the same parent task. The
chain terminates the first time some graph in it closes via
`close_success` (full plan).

`build_continuation_note` walks back via `prior_graph_id` and
assembles:

```
ROOT_GOAL: {root_task.input}
PRIOR SEGMENTS:
  [each prior graph's what_to_do_next + evaluator success summary]
CURRENT REQUEST:
  {self.graph.what_to_do_next}
```

### 8.5 Case B-fail — failure (full or partial)

```
  ── inside close_failure(summary) ──
    self.evaluator.status = DONE
    self.evaluator.summaries.append(summary)
    self.planner.status = DONE
    self.root_task.summaries.append(child_failure(summary))
    self.root_task.status = FAILED
    if self.root_task.harness_graph_id is None:
        return  # RunController will see FAILED
    else:
        cascade_fail_dependents(self.tc, self.root_task)
    self.tc._persist_all(); self.tc._wakeup.set()
```

A partial-plan graph that fails fails the whole chain. There is no
per-segment retry. (Symmetric with full-plan failure on purpose.)

---

## 9. Materialization Details

### 9.1 Two thin public methods + shared body

```python
def materialize_full_plan(self, dag, details, eval_spec):
    err = self._validate_dag(dag, details)
    if err:
        return err
    self._materialize_dag(dag, details, eval_spec)
    self.graph.plan_shape = "full"
    self.tc._persist_all(); self.tc._wakeup.set()
    return None

def materialize_partial_plan(self, dag, details, what_to_do_next, eval_spec):
    err = self._validate_dag(dag, details)
    if err:
        return err
    self._materialize_dag(dag, details, eval_spec)
    self.graph.plan_shape = "partial"
    self.graph.what_to_do_next = what_to_do_next
    self.tc._persist_all(); self.tc._wakeup.set()
    return None
```

### 9.2 Shared body

```python
def _materialize_dag(self, dag, details, eval_spec):
    # 1. Planner state transition.
    self.planner.status = Status.HANDOFF

    # 2. Per-node task creation in topological order.
    id_to_task: dict[str, Task] = {}
    for node in _topo_sort(dag):
        nid, deps, role = node["id"], node["deps"], node["role"]
        needs = frozenset(id_to_task[d].id for d in deps)
        status = Status.READY if not deps else Status.PENDING
        primitive = {
            "executor": self.tc._create_executor,
            "verifier": self.tc._create_verifier,
        }[role]
        task = primitive(
            input=details[nid],
            harness_graph_id=self.graph_id,
            needs=needs,
            status=status,
        )
        id_to_task[nid] = task
    self.graph.dag_nodes = [t.id for t in id_to_task.values()]

    # 3. Auto-spawn evaluator at sinks.
    sink_ids = _compute_sinks(dag)
    evaluator = self.tc._create_evaluator(
        input=eval_spec,
        harness_graph_id=self.graph_id,
        needs=frozenset(id_to_task[s].id for s in sink_ids),
    )
    self.graph.evaluator = evaluator.id
```

Topological-order creation guarantees `needs` resolves cleanly — a
node's deps already exist as Task objects when we reach it.

### 9.3 Validation rules (Phase 1)

```python
def _validate_dag(self, dag, details) -> MaterializationFailure | None:
    ids = [n["id"] for n in dag]

    if not ids:
        return MaterializationFailure(
            "empty_dag", "DAG must contain at least one generator")
    if len(set(ids)) != len(ids):
        return MaterializationFailure("duplicate_ids", "duplicate node ids")
    if set(ids) != set(details.keys()):
        return MaterializationFailure(
            "missing_details",
            "task_details keys must match DAG ids exactly")

    id_set = set(ids)
    for n in dag:
        if n["role"] not in get_args(GeneratorRole):
            return MaterializationFailure(
                "unknown_role",
                f"node {n['id']} role={n['role']!r} is not a generator role")
        if not set(n["deps"]).issubset(id_set):
            return MaterializationFailure(
                "unknown_dep", f"node {n['id']} references unknown dep")

    if _has_cycle(dag):
        return MaterializationFailure("cycle", "DAG contains a cycle")

    sinks = _compute_sinks(dag)
    bad = [
        nid for nid in sinks
        if next(n for n in dag if n["id"] == nid)["role"] == "verifier"
    ]
    if bad:
        return MaterializationFailure(
            "verifier_sink",
            f"verifier nodes cannot be DAG sinks: {bad}")
    return None
```

`_has_cycle` and `_compute_sinks` are pure helpers in `orchestrator.py`.

### 9.4 Tool-result failure path

When validation returns a `MaterializationFailure`, the runtime
dispatcher forwards it to the agent as a tool-result failure rather
than ending the agent loop:

```python
def on_planner_full_plan(tc, planner_id, dag, details, eval_spec) -> ToolResult:
    orch = tc.orchestrator(graph_of(planner_id))
    err = orch.materialize_full_plan(dag, details, eval_spec)
    if err:
        return ToolResult.failure(
            f"plan rejected ({err.code}): {err.message}")
    return ToolResult.terminal()
```

The agent receives the failure code + message, can correct, and call
`submit_full_plan` again.

### 9.5 Phase 1: lenient advisor-accept handling

For Phase 1, **a `MaterializationFailure` does not consume the advisor
accept token.** The agent retries `submit_full_plan` with a corrected
payload using the same advisor accept. This trades strictness for
implementation simplicity.

Phase 2 will introduce explicit accept-state tracking; failed
materialization will then consume the accept and require re-consult.

---

## 10. Closure Details

### 10.1 Three terminals, three handlers

| Terminal | Orchestrator method |
|---|---|
| `submit_evaluation_success` (graph.plan_shape=full) | `close_success` |
| `submit_evaluation_success` (graph.plan_shape=partial) | `close_partial_success` |
| `submit_evaluation_failure` | `close_failure` |

Dispatch happens in the runtime dispatcher (Section 9.4 of the
companion doc), which inspects `graph.plan_shape` to choose between
`close_success` and `close_partial_success`. Agents never reason about
plan shape.

### 10.2 State transitions per closure

| Step | `close_success` | `close_partial_success` | `close_failure` |
|---|---|---|---|
| evaluator | DONE | DONE | DONE |
| planner | DONE | DONE | DONE |
| root_task summary | child_success | segment_success | child_failure |
| root_task status | DONE | unchanged (HANDOFF) | FAILED |
| spawn next graph | no | yes (continuation) | no |
| propagate up | yes | no (chain continues) | yes (cascade-fail) |

### 10.3 Propagation rule

```python
def propagate_in_parent_graph(tc, task: Task) -> None:
    """task just transitioned to DONE/FAILED.
    If task is in a graph, refresh dependents' READY-ness and check
    whether the parent graph's evaluator is now ready (or whether
    cascade-fail must propagate to its dependents)."""
```

When `task` is `root_exec`, this is skipped (`harness_graph_id is None`
short-circuit in the closure body). RunController detects terminal
status on its next event-loop tick.

---

## 11. Phase 1 / Phase 2 Boundary

### 11.1 Phase 1 (this document)

- `Orchestrator` class: `spawn`, `materialize_full_plan`,
  `materialize_partial_plan`, `create_harness_fix_executor`,
  `close_success`, `close_partial_success`, `close_failure`,
  `build_continuation_note`.
- `RunController` class.
- `Generator` role-class.
- `HarnessGraph` data-model changes (`planner`, `dag_nodes`,
  `evaluator`, `plan_shape`, `what_to_do_next`, `prior_graph_id`).
- `Status.FIXING`.
- File layout split (`task_center.py` ⇄ `orchestrator.py`).
- Validation in `materialize_*_plan` only; failure returns
  `MaterializationFailure` as tool result.
- Lenient advisor-accept on materialization failure (token survives).

### 11.2 Deferred to Phase 2

- **Recursion depth caps.** Bound `request_plan` recursion and
  partial-chain depth.
- **Partial-chain depth control.** Block `submit_partial_plan` when
  the parent graph's `plan_shape == "partial"`. Forces partial chains
  to exist at one level only.
- **Strict advisor accept consumption.** Failed materialization burns
  the accept; agent must re-consult before retrying.
- **Pre-hook structural validation.** Move structural checks
  (cycles, verifier-sink) ahead of the advisor so the advisor sees
  only structurally-valid plans.

---

## 12. Open Questions

1. **`_create_*` primitive semantics — staged or immediate?** Section
   9.2 assumes validation runs first and node creation always
   succeeds. Worth confirming `_create_*` mutates the in-memory store
   immediately rather than staging, so a mid-build exception leaves
   no partial graph. (If staged, no rollback needed; if immediate,
   validate-first eliminates the rollback case.)

2. **`fix_executor` placement.** Tracked via `Task.fix_target_id`
   back-pointer, not in `dag_nodes`. Confirm graph-replay queries
   (used by audit/UI) walk both sources when reconstructing run
   history.

3. **Continuation chain depth at runtime.** The chain is
   reconstructible by listing `HarnessGraph` rows where
   `root_task_id == parent.id` and walking `prior_graph_id`. Phase 1
   has no cap; a misbehaving planner could produce arbitrary depth.
   Phase 2 introduces the structural cap (Section 11.2).

4. **`build_continuation_note` content fidelity.** The note walks
   prior graphs via `prior_graph_id` back-links. If the chain is
   long, the note can grow unboundedly. Phase 2 may need a summary
   compaction pass; Phase 1 emits the full chain.

---

## 13. Implementation Sequencing (Phase 1)

The four-role doc Section 14 describes the full implementation
sequence (stages 1–9). This document refines stages 2 and 3:

**Stage 2 (refined):** Lift task creation out of lifecycle modules.
- Rename `runtime/orchestrator.py` → `runtime/task_center.py`.
- Add `runtime/orchestrator.py` with the new `Orchestrator` class.
- Add `runtime/run_controller.py` with `RunController`.
- Move `Task(...)` and `HarnessGraph(...)` constructors out of
  `harness_agents/<role>/lifecycle.py`. Lifecycle modules become
  thin runtime-dispatcher routers.
- Existing terminals route through `Orchestrator.spawn` /
  `materialize_full_plan` / etc.

**Stage 3 (refined):** Verifier lifecycle + `create_harness_fix_executor`.
- `verifier_lifecycle` becomes a router that calls
  `Orchestrator(...).create_harness_fix_executor(...)` on failure
  and routes success to dependent-promotion.
- `Status.FIXING` lands here.

Stages 4–9 from the four-role doc consume the new
`Orchestrator`/`RunController` API as already structured.

---

## 14. References

- `four-role-advisor-gated-design.md` — companion document; the
  four-role + advisor-gating design this orchestrator pattern
  serves.
- `gan-task-graph-recovery-cap.md` — verifier/fix-executor
  recovery semantics.
- `runtime-behavioral-shaping.md` — notification rules used by the
  runtime to bias planner choices.
