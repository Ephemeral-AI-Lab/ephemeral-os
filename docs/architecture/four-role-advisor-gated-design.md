# Four-Role Task Graph with Advisor Gating

**Status:** Design (in flight)
**Supersedes (in part):** `gan-task-graph-v1.md`, `phased-executor-evaluator-tree-v1.md`
**Related:** `runtime-behavioral-shaping.md`, `gan-task-graph-recovery-cap.md`

This document describes a redesign of the task-graph harness from three
roles (planner, executor, evaluator) to four roles (planner, executor,
verifier, evaluator) with all high-stakes terminals gated by an
**advisor** consultation.

---

## 1. Motivation

The existing architecture lands solid execution, but three rough edges
have accumulated:

1. **The evaluator is overloaded.** It is the closure gate, the
   inline-fix repair shop, the partial-plan replan trigger, and the
   adversarial probe runner. The role's `agent.md` carries plan-shape
   branching logic (REPLAN_AFTER) that is mechanical, not judgmental, and
   should not depend on an LLM getting it right.
2. **There is no second-LLM check at the highest-leverage decisions.**
   `request_plan`, plan submission, and verification success each gate
   downstream work worth many child tasks. Today these decisions rest on
   one prompt's discipline.
3. **Mid-graph verification has no first-class support.** A planner that
   wants to checkpoint mid-DAG has to express it as a recursive partial
   plan — overkill for what should be a single LLM-hop sweep.

The proposed design separates these concerns by:

- splitting "verify one node" (verifier) from "evaluate the planning unit"
  (evaluator),
- moving plan-shape branching out of agent prompts and into deterministic
  lifecycle code,
- introducing an **advisor** role that gates the highest-stakes terminals,
- adding a fix-executor recovery primitive scoped to verifier failures.

---

## 2. The Four Roles

### 2.1 Planner

Decomposes a goal into a DAG. Read-only investigation + scout dispatch.

**Terminals (both gated):**
- `submit_full_plan(task_dep_graphs, task_details, evaluation_specification)`
- `submit_partial_plan(task_dep_graphs, task_details, what_to_do_next, evaluation_specification)`

**Context fields:** `root_goal`, `request_plan_note`. (Includes parent
goal — the planner needs the anchor to resist drift.)

**Authority change vs today:** the planner no longer authors a separate
`evaluator_note`; the auto-spawned evaluator's input is
`evaluation_specification` directly.

### 2.2 Executor

Owns one DAG node and produces one terminal call.

**Terminals:**
- `submit_task_success(summary)` — un-gated
- `submit_task_failure(faliure, summary)` — un-gated
- `request_plan(task_detail)` — gated

**Context fields:** `task_input`, `dep summaries`. **No `root_goal`** —
intentional anti-drift. Executors execute the spec they were given, not
adjudicate against the root goal.

### 2.3 Verifier (NEW)

Mid-graph node-scoped verification. Validates DAG dependencies against
the node's verification specification.

**Terminals (both gated):**
- `submit_verification_success(summary)`
- `submit_verification_failure(faliure, summary)`

**Context fields:** `task_input` (the verification spec), `dep summaries`.
Same shape as executor — same scoping, different prompt. **No
`root_goal`.**

**Constraints:**
- Cannot be a DAG sink (would conflict with the auto-spawned evaluator).
- Failure spawns a fix-executor; success unblocks dependents.
- Allowed inline-fix edits in narrow categories (typo / missing import /
  wrong constant proven by deps' verification / syntax fix), ≤5 paths,
  no test-file edits, no new files. Anything bigger → submit failure.

### 2.4 Evaluator

End-of-graph closure gate. Auto-spawned by the lifecycle when a plan is
materialized (NOT in `task_dep_graphs`).

**Terminals (both gated):**
- `submit_evaluation_success(summary)`
- `submit_evaluation_failure(faliure, summary)`

**Context fields:** `root_goal`, `request_plan_note`, plan summary,
`evaluation_specification`, all child summaries grouped by kind.

**Authority change vs today:** the evaluator no longer reasons about
plan shape, REPLAN_AFTER, or partial-plan continuation. Its job is one
decision: was the planning unit's goal met? Plan-shape branching happens
in lifecycle code after the evaluator's success.

### 2.5 Role Comparison

| | Planner | Executor | Verifier | Evaluator |
|---|---|---|---|---|
| Position | one per graph | many per graph | many per graph | one per graph (auto) |
| In DAG? | n/a (graph root) | yes | yes | no (auto-spawned) |
| Sees `root_goal` | yes | no | no | yes |
| Sees `dep summaries` | n/a | yes | yes | n/a (sees children by kind) |
| Sees plan summary | no | no | no | yes |
| Closes graph? | no | no | no | yes |
| Edit/shell tools | no | yes | limited | limited |

The pattern: **scoped roles (executor, verifier) get DAG-local context;
graph-spanning roles (planner, evaluator) get graph-level context.**

---

## 3. The Advisor

A separate role that gates high-stakes terminals.

### 3.1 Pattern

The calling agent invokes an advisor as a **tool**, not a terminal:

```python
result = ask_advisor(
    terminal_tool="submit_partial_plan",
    input={...the actual payload...},
    reason="canary needs to land before bulk fan-out is sized",
)
# result == {verdict: "accept" | "reject", reason: str}
```

The runtime spawns an advisor agent with the calling agent's context
object plus the proposed `(terminal_tool, input, reason)`. The advisor
loops over its own logic, then terminates with:

```python
submit_advisor_feedback(verdict: "accept" | "reject", reason: str)
```

That terminal's verdict is returned to the calling agent as the
`ask_advisor` tool result.

### 3.2 Pre-hook enforcement

Each gated terminal has a pre-hook that checks the calling agent's
context for a recent advisor accept matching the call's payload:

```python
def pre_hook_gated_terminal(agent, terminal_tool, payload):
    last = agent.context.last_ask_advisor
    if last is None:
        raise BlockedTerminal("must consult advisor first")
    if last.terminal_tool != terminal_tool:
        raise BlockedTerminal("advisor approved a different terminal")
    if last.input != payload:
        raise BlockedTerminal("payload differs from advisor-approved input")
    if last.verdict != "accept":
        raise BlockedTerminal("advisor rejected this proposal")
    if intervening_tool_calls_since(last) > 0:
        raise BlockedTerminal("state changed since approval; re-consult")
```

**Strict match:** exact equality on serialized `input`. Prevents drift
between what was approved and what was submitted.

**No retry:** rejection is terminal for that proposal. The calling agent
must call a *different* terminal next time. The advisor's
`rejection_reason` becomes guidance ("call submit_task_failure instead
because…"), not a request for rephrasing.

### 3.3 Gated terminals

| Role | Terminal | Gated? |
|---|---|---|
| Executor | submit_task_success | no |
| Executor | submit_task_failure | no |
| Executor | request_plan | **yes** |
| Planner | submit_full_plan | **yes** |
| Planner | submit_partial_plan | **yes** |
| Verifier | submit_verification_success | **yes** |
| Verifier | submit_verification_failure | **yes** |
| Evaluator | submit_evaluation_success | **yes** |
| Evaluator | submit_evaluation_failure | **yes** |
| Advisor | submit_advisor_feedback | no (terminus) |

The classification axis: **terminals that affect multiple tasks or
create new structure are gated; terminals that close one task's own
scope are not.** Executor success/failure is bounded — the verifier or
evaluator above will catch fabrication.

### 3.4 What the advisor sees

The advisor sees exactly what the calling agent could see — the calling
agent's context object, plus the proposed payload and reason. **Not** a
full transcript: the system uses structured context objects per agent,
so the advisor reads from those.

This means the advisor's effectiveness is bounded by what the context
objects capture. If a verifier's context stores only "I ran the tests"
without the actual exit code, the advisor cannot verify the claim. Each
role's context object should capture **concrete artifacts** (commands +
exit codes + stdout tails for shell, paths + content hashes for reads,
diff hashes for writes) so the advisor can cross-check claims against
evidence.

### 3.5 Advisor's tool surface

Just the terminal `submit_advisor_feedback`. No file reads, no shell, no
scouts. If advisors produce shallow feedback, the fix is enriching the
calling agent's context object — not granting the advisor more tools.

---

## 4. The DAG Schema

### 4.1 Old

```python
{"id": "impl_x", "deps": ["impl_y"]}    # role implicit (always executor)
```

### 4.2 New

```python
{"id": "impl_x", "deps": ["impl_y"], "role": "executor" | "verifier"}
```

The planner declares the role of each node it emits. The lifecycle
materializes the right kind of task and dispatches the right
context-builder.

### 4.3 Validation rules

- `role` must be one of the allowed values for in-DAG roles
  (`executor`, `verifier`).
- No cycles.
- Verifier nodes cannot be DAG sinks — would create a redundant gate
  with the auto-spawned evaluator.
- All ids unique.

---

## 5. Plan Terminals

Two terminals replace today's `submit_plan_handoff`:

### 5.1 `submit_full_plan`

```python
submit_full_plan(
    task_dep_graphs: list[{id, deps, role}],
    task_details: dict[id, str],            # per-node task input
    evaluation_specification: str,        # for the auto-spawned evaluator
)
```

Lifecycle:
1. Validate DAG and role assignments.
2. For each node, build its input via deterministic context-builder
   (executor or verifier) and create the task with status PENDING/READY
   per deps.
3. Auto-spawn evaluator with `needs = sinks(DAG)` and
   `input = evaluation_specification`.
4. Mark `graph.plan_shape = "full"`.

### 5.2 `submit_partial_plan`

```python
submit_partial_plan(
    task_dep_graphs: list[{id, deps, role}],
    task_details: dict[id, str],
    what_to_do_next: str,                   # instructions for the next planner
    evaluation_specification: str,
)
```

Same as full plan, plus stores `what_to_do_next` on the harness graph
and marks `graph.plan_shape = "partial"`.

The `what_to_do_next` field is the directive form of today's
`REPLAN_AFTER` — it tells the *next planner* what the tail's planning
should focus on, not just which child to replan after.

---

## 6. Verifier Failure → Fix-Executor

When a verifier (mid-graph or via the evaluator's failure path) emits
`submit_verification_failure`:

1. Advisor gates the failure terminal. If accepted:
2. Verifier transitions to `Status.FIXING` (a new intermediate status,
   not terminal yet).
3. Lifecycle deterministically builds a fix-executor input from:
   - the verifier's failure summary
   - the verifier's task input
   - the verifier's dep summaries
4. Fix-executor spawns with `fix_target_id = verifier_id`.
5. Fix-executor has **2 terminals only** — `submit_task_success` and
   `submit_task_failure`. **No `request_plan`.** Recovery is bounded;
   if the repair is bigger than the fix-executor's scope, it fails.
6. On fix-executor success → verifier re-runs (option F2: trust no
   self-reports; the verifier always has the last word).
7. On fix-executor failure → verifier transitions to `Status.FAILED`,
   dependents fail via `dependency_blocked_descendants`.

The fix-executor's prompt narrows scope (≤5 files, no new files, no
test-file edits, narrow change categories) — same constraints as today's
evaluator inline-fix.

---

## 7. Partial-Plan Continuation

When the auto-spawned evaluator emits `submit_evaluation_success` on a
graph with `plan_shape == "partial"`:

1. Advisor gates the success.
2. Evaluator lifecycle (`handle_evaluation_success`) updates state:
   - Mark planner DONE.
   - Mark evaluator DONE.
   - Append `segment_success` summary to the parent task.
   - **Do not mark parent DONE.**
3. Evaluator lifecycle then delegates creation to task center:
   `TaskCenter.open_continuation_graph(prior_graph_id)`. **The
   lifecycle module does not instantiate the new planner Task or
   HarnessGraph itself** — that is task center's responsibility.
4. `TaskCenter.open_continuation_graph` deterministically builds
   the new planner's input from:
   ```
   ROOT_GOAL: {parent_task.input}
   PRIOR SEGMENTS:
     [walk chain of prior harness graphs, list each graph's
      what_to_do_next + evaluator's success summary]
   CURRENT REQUEST:
     {prior_graph.what_to_do_next}
   ```
   It then creates a new `HarnessGraph` rooted at the same parent task
   and spawns the planner READY.
5. The continuation planner is structurally identical to a fresh
   `request_plan` planner — same context shape, same terminals.

**No agent reasons about plan shape or continuation.** The verifier
verifies; the evaluator evaluates; the lifecycle module *decides a
spawn is needed*; the task center *performs the spawn*. This split is
the elegance the redesign was driven by — see Section 10 for the full
spawn-ownership rule.

The chain terminates when a `plan_shape == "full"` graph in the chain
closes successfully — at that point the parent task is marked DONE and
propagation continues normally.

---

## 8. Context Objects

Five structural shapes, one per role-position:

### 8.1 PlannerLaunchContext (existing)

```python
- root_goal: str
- request_plan_note: str
```

### 8.2 ExecutorLaunchContext (existing)

```python
- task_id, task_input
- harness_graph_id
- completed_dependencies: list[DependencyBundle]
```

### 8.3 VerifierLaunchContext (NEW — `verifier/context.py`)

```python
- task_id, task_input
- harness_graph_id
- completed_dependencies: list[DependencyBundle]   # re-uses executor's
```

Mirrors executor structurally. Re-uses `DependencyBundle` to keep dep
representation single-sourced.

### 8.4 EvaluatorLaunchContext (existing, narrowed)

```python
- task_id, harness_graph_id
- root_goal
- request_plan_note
- handoff_plan_note          # plan summary
- evaluator_note             # → evaluation_specification (rename)
- success_child_summaries
- fail_child_summaries
- blocked_child_summaries
```

The evaluator's `agent.md` shrinks: no more REPLAN_AFTER reasoning, no
more partial-plan branching. Just "did the planning unit's goal get
met?"

### 8.5 FixExecutorLaunchContext (NEW)

Same shape as ExecutorLaunchContext but with the `task_input` constructed
from the verifier's failure summary + the verifier's task_input + the
verifier's dep summaries. Tagged via a `spawn_reason` field so the
prompt can be conditioned (system reminder + pre-hook).

### 8.6 AdvisorLaunchContext (NEW)

```python
- calling_agent_context: <one of the above>
- proposed_terminal_tool: str
- proposed_input: dict
- agent_reason: str
```

The advisor sees exactly what the calling agent saw, plus the proposal.

### 8.7 Position dispatch for verifier

`Task.role == "verifier"` covers both mid-graph verifiers and the
auto-spawned evaluator at the gate. Distinction is structural:

```python
def is_gate_verifier(task: Task, graph: HarnessGraph) -> bool:
    return task.id == graph.evaluator_task_id
```

Position is graph-relational, not task-intrinsic. The role for the
gate is `evaluator`; for in-DAG nodes, it is `verifier`. Two distinct
roles in the data model — no flag-based dispatch needed.

---

## 9. Lifecycle Changes

### 9.1 New status

```python
class Status(str, Enum):
    # ... existing ...
    FIXING = "fixing"   # verifier emitted failure; fix-executor in flight
```

### 9.2 New harness-graph fields

```python
@dataclass
class HarnessGraph:
    # ... existing ...
    plan_shape: Literal["full", "partial", None] = None
    what_to_do_next: str = ""
```

### 9.3 New / changed lifecycle functions (state updates only)

Lifecycle modules under `harness_agents/<role>/lifecycle.py` handle
status transitions, summary appends, and propagation. They **delegate
all agent spawning to the task center** (Section 10).

| Function | Change |
|---|---|
| `executor_lifecycle.submit_task_success` | unchanged |
| `executor_lifecycle.submit_task_failure` | unchanged |
| `executor_lifecycle.handle_request_plan` | renamed for symmetry; calls `tc.open_request_plan_graph` |
| `planner_lifecycle.submit_plan_handoff` | replaced by `handle_full_plan_submission` (calls `tc.materialize_full_plan`) + `handle_partial_plan_submission` (calls `tc.materialize_partial_plan`) |
| `evaluator_lifecycle.handle_evaluation_success` | new (renamed from `submit_task_success`); branches on `plan_shape`, calls `tc.open_continuation_graph` for partial |
| `evaluator_lifecycle.handle_evaluation_failure` | renamed for clarity |
| `verifier_lifecycle.handle_verification_success` | new — marks DONE, notifies dependents |
| `verifier_lifecycle.handle_verification_failure` | new — transitions to FIXING, calls `tc.create_fix_executor` |
| `close_harness_graph_success` | unchanged (called by full-plan evaluator success) |
| `close_harness_graph_partial_success` | new — state updates only; does NOT propagate parent terminal |
| `close_harness_graph_failed` | unchanged |

### 9.4 Pre-hooks

Each gated terminal has a pre-hook that checks for a fresh advisor
accept (Section 3.2). Pre-hooks live in the runtime layer, not in
agent prompts.

---

## 10. Task Center Task-Creation Workflow

**The task center is the single source of truth for `Task` and
`HarnessGraph` creation.** Lifecycle modules update graph state (status
transitions, summary appends, propagation) but never instantiate `Task`
or `HarnessGraph` themselves — they call into task center for that.

### 10.1 Ownership rule

```
Lifecycle modules:                     Task center:
- status transitions                   - Task / HarnessGraph creation
- summary appends                      - input synthesis (deterministic)
- propagation upward                   - persistence + wakeup
```

Lifecycle code never contains `Task(...)` or `HarnessGraph(...)`. When
a lifecycle handler decides task creation is needed, it calls a public
`TaskCenter` method.

**File location.** All task-creation methods are defined on
`TaskCenter` in `backend/src/task_center/runtime/orchestrator.py`. They
live alongside the existing graph mutation helpers (`_new_id`,
`_new_graph_id`, `_persist_*`) because they need access to those
private plumbing methods. The existing
`backend/src/task_center/runtime/spawn.py` retains its narrow role: it
defines `build_production_spawn`, the `SpawnFunc` adapter that
*executes* a READY task by invoking `execute_ephemeral_agent_run`. The
two files use distinct verb families to avoid collision:

| File | Verbs | Responsibility |
|---|---|---|
| `runtime/orchestrator.py` | `_create_*`, `create_*`, `open_*`, `materialize_*` | Add new `Task` / `HarnessGraph` to graph in `PENDING` / `READY`. |
| `runtime/spawn.py` | `spawn` (in `SpawnFunc`) | Run the LLM agent process for a `READY` task and forward events. |

Lifecycle modules (`harness_agents/<role>/lifecycle.py`) are clients of
the first column; the dispatcher loop in `run_query` is the client of
the second column.

### 10.2 Method layout: primitives + composers

The API has two layers:

- **Internal primitives (`_create_<role>`)** — one per role; each adds
  a single `Task` of that role to the graph. Take exactly the fields
  needed to construct that `Task`. No orchestration, no input
  synthesis, no graph creation.
- **Public API (`create_<flavor>` / `open_<flavor>_graph` /
  `materialize_<shape>_plan`)** — composes primitives + builds inputs
  + creates `HarnessGraph` if needed + persists + wakes the dispatcher.

#### 10.2.1 Internal primitives

```python
class TaskCenter:

    def _create_executor(
        self, *,
        input: str,
        harness_graph_id: HarnessGraphId | None,
        needs: frozenset[TaskId],
        status: Status,
    ) -> Task:
        """Add a Task(role='executor') to the graph store. No input synthesis."""

    def _create_planner(
        self, *,
        input: str,
        harness_graph_id: HarnessGraphId,
    ) -> Task:
        """Add a Task(role='planner') to the graph store. Always READY."""

    def _create_verifier(
        self, *,
        input: str,
        harness_graph_id: HarnessGraphId,
        needs: frozenset[TaskId],
        status: Status,
    ) -> Task:
        """Add a Task(role='verifier') to the graph store."""

    def _create_evaluator(
        self, *,
        input: str,
        harness_graph_id: HarnessGraphId,
        needs: frozenset[TaskId],
    ) -> Task:
        """Add a Task(role='evaluator') to the graph store. Always PENDING."""

    def _create_advisor(
        self, *,
        input: str,
        caller_id: TaskId,
    ) -> Task:
        """Add a Task(role='advisor') to the graph store. Transient (no graph)."""
```

The leading underscore signals "primitives — call from within the
public API; do not call from lifecycle code or tests of public flow."
They exist as named entry points so each role's task-construction
invariants live in one place (e.g., `_create_evaluator` always sets
`status=PENDING`; `_create_planner` always sets `status=READY`).

#### 10.2.2 Public API

```python
class TaskCenter:

    # ---- Single-task specializations ----

    def create_root_executor(self, prompt: str) -> Task:
        """First task of a run; called from run_query.
        No harness graph; READY; the user prompt is the task input."""

    def create_fix_executor(
        self, verifier_id: TaskId, failure_summary: str,
    ) -> Task:
        """Bounded recovery from a verifier failure.
        Builds the fix-executor input deterministically from
        the verifier's failure_summary + verifier.input + verifier deps' summaries.
        Tags spawn_reason='fix_verification' so the runtime applies
        the fix-mode prompt fragment + tool-surface restriction (no request_plan)."""

    def create_advisor(
        self,
        caller_id: TaskId,
        terminal_tool: str,
        proposed_input: dict,
        agent_reason: str,
    ) -> Task:
        """Transient gate review for a gated terminal.
        Builds advisor input from (terminal_tool, proposed_input, agent_reason)
        plus the caller's context object. Verdict returned to caller as the
        ask_advisor tool result."""

    # ---- HarnessGraph + planner pairs ----

    def open_request_plan_graph(
        self,
        caller_id: TaskId,
        request_plan_note: str,
    ) -> Task:
        """Open a new HarnessGraph rooted at caller; spawn its planner READY.
        Returns the planner Task."""

    def open_continuation_graph(
        self,
        prior_graph_id: HarnessGraphId,
    ) -> Task:
        """Open a new HarnessGraph chained from prior (sharing root_task_id);
        spawn its planner READY with a deterministic continuation input that
        walks the chain via parent pointers + reads prior_graph.what_to_do_next.
        Returns the planner Task."""

    # ---- Multi-task materializers (within an existing graph) ----

    def materialize_full_plan(
        self,
        planner_id: TaskId,
        task_dep_graphs: list[dict],            # [{id, deps, role}]
        task_details: dict[str, str],           # id -> task input
        evaluation_specification: str,          # for the auto-spawned evaluator
    ) -> None:
        """Add executor + verifier children to the planner's graph (per role
        field), plus the auto-spawned evaluator with needs=sinks(DAG)."""

    def materialize_partial_plan(
        self,
        planner_id: TaskId,
        task_dep_graphs: list[dict],
        task_details: dict[str, str],
        what_to_do_next: str,
        evaluation_specification: str,
    ) -> None:
        """Same as materialize_full_plan, plus stores what_to_do_next on
        the harness graph for the eventual continuation builder to read.
        Sets graph.plan_shape = 'partial'."""
```

#### 10.2.3 What's where: trace from primitive to caller

| Primitive | Used by composer | Composer's caller |
|---|---|---|
| `_create_executor` | `create_root_executor`, `create_fix_executor`, `materialize_full_plan`, `materialize_partial_plan` | run_query, verifier_lifecycle, planner_lifecycle |
| `_create_planner` | `open_request_plan_graph`, `open_continuation_graph` | executor_lifecycle, evaluator_lifecycle |
| `_create_verifier` | `materialize_full_plan`, `materialize_partial_plan` | planner_lifecycle |
| `_create_evaluator` | `materialize_full_plan`, `materialize_partial_plan` | planner_lifecycle |
| `_create_advisor` | `create_advisor` | the `ask_advisor` tool dispatcher |

Reading down the table: every public composer is reachable from exactly
one (or two) lifecycle paths. Reading up: every primitive is exercised
by ≥1 composer in production code.

### 10.3 Trigger → public-method map

| Trigger | TaskCenter call | New agent(s) |
|---|---|---|
| `run_query(prompt)` | `create_root_executor` | root executor |
| `executor.request_plan` | `open_request_plan_graph` | new graph + planner |
| `planner.submit_full_plan` | `materialize_full_plan` | DAG children + auto-evaluator |
| `planner.submit_partial_plan` | `materialize_partial_plan` | DAG children + auto-evaluator |
| `evaluator.submit_evaluation_success` (full) | — (no creation) | graph closes |
| `evaluator.submit_evaluation_success` (partial) | `open_continuation_graph` | new chained graph + planner |
| `evaluator.submit_evaluation_failure` | — (no creation) | graph fails |
| `verifier.submit_verification_success` | — (no creation) | unblock dependents |
| `verifier.submit_verification_failure` | `create_fix_executor` | fix-executor |
| `executor.submit_task_success` | — (no creation) | unblock dependents |
| `executor.submit_task_failure` | — (no creation) | cascade-fail dependents |
| `ask_advisor(...)` (tool) | `create_advisor` | transient advisor |

### 10.4 Layered call flow

```
Agent loop
  ├─> calls a tool (incl. ask_advisor)
  └─> calls a terminal tool
       │
       ├─ Pre-hook: advisor-gate enforcement, role-terminal validity
       │  └─ on fail: BlockedTerminal back to agent
       │
       └─ TaskCenter terminal handler (e.g., submit_evaluation_success)
            │
            ├─ Lifecycle update (in harness_agents/<role>/lifecycle.py):
            │   - status transitions
            │   - summary appends
            │   - propagate_parent_terminal if needed
            │
            └─ If creation needed: tc.<public-method>(...)
                 - builds new agent's input deterministically
                 - creates HarnessGraph (if open_*) or just adds Task(s)
                 - calls _create_<role> primitives internally
                 - persists, sets wakeup
```

### 10.5 Concrete walkthrough — partial-plan closure

```
1. evaluator decides spec is met → calls
       ask_advisor("submit_evaluation_success", {summary}, reason)
   └─ tc.create_advisor(eval_id, "submit_evaluation_success", ...)
        creates transient advisor task, returns verdict=accept

2. evaluator calls submit_evaluation_success(summary)
   └─ Pre-hook checks last_ask_advisor → match → pass

3. tc.submit_evaluation_success(eval_id, summary):
   │
   ├─ evaluator_lifecycle.handle_evaluation_success(tc, eval_id, summary):
   │    eval.summaries.append(...)
   │    tc._mark_terminal(eval, DONE)
   │    if graph.plan_shape == "full":
   │        evaluator_lifecycle.close_harness_graph_success(tc, graph_id, eval_id)
   │        # marks planner DONE, marks parent DONE, propagates up
   │    else:  # partial
   │        evaluator_lifecycle.close_harness_graph_partial_success(tc, graph_id, eval_id)
   │        # marks planner DONE, appends segment_success to parent;
   │        # parent STAYS HANDOFF; no propagate
   │        tc.open_continuation_graph(graph_id)       ← TaskCenter creates graph+planner
   │
   └─ tc._persist_all(); tc._wakeup.set()

4. tc.open_continuation_graph(prior_graph_id):
   prior  = self._graph.get_harness_graph(prior_graph_id)
   parent = self._graph.get(prior.root_task_id)
   new_graph = HarnessGraph(
       id=self._new_graph_id(),
       run_id=self.run_id or "",
       root_task_id=parent.id,                          # SAME parent
       planner_task_id=new_planner_id,
       root_goal=parent.input,
       request_plan_note=self._build_continuation_note(parent, prior),
       prior_graph_id=prior_graph_id,
   )
   new_planner = Task(
       id=new_planner_id,
       role="planner",
       input=build_planner_launch_context(new_graph).to_planner_input(),
       status=Status.READY,
       task_center_harness_graph_id=new_graph.id,
   )
   self._graph.add(new_planner)
   self._graph.add_harness_graph(new_graph)
   # state already marked HANDOFF / DONE by lifecycle; no further state changes here
```

The lifecycle module *decides* a spawn is needed (because
`plan_shape == "partial"`); the task center *performs* it.

---

## 11. Worked Example: 2-Segment Partial Chain

**Prompt:** "Migrate from Pydantic v1 to v2 across the backend."

| Step | Action | State after |
|---|---|---|
| 1 | `run_query(prompt)` | R created (executor, READY, no graph) |
| 2 | R recognizes scope, calls `ask_advisor("request_plan", detail, reason)` → accept → `request_plan(detail)` | R → HANDOFF; G1 created (root_task=R); P1 (planner, READY) |
| 3 | P1 dispatches scouts; sees breakage classes unknown until canary. Calls `ask_advisor("submit_partial_plan", payload, reason)` → accept → `submit_partial_plan(...)` | P1 → HANDOFF; G1.plan_shape="partial"; impl_shim (executor, READY); canary (executor, PENDING); G1-eval (evaluator, PENDING) |
| 4 | impl_shim DONE; canary runs, DONE | G1-eval → READY |
| 5 | G1-eval runs verification; calls `ask_advisor("submit_evaluation_success", summary, reason)` → accept → `submit_evaluation_success(summary)` | G1-eval → DONE; lifecycle dispatches on plan_shape="partial" |
| 6 | `close_harness_graph_partial_success(G1, G1-eval)` runs | P1 DONE; G1-eval DONE; R.summaries += segment_success; R stays HANDOFF; spawn G2 (root_task=R), P2 (planner, READY) with deterministic continuation input |
| 7 | P2 plans full this time, `ask_advisor("submit_full_plan", payload, reason)` → accept → `submit_full_plan(...)` | P2 → HANDOFF; bulk children + G2-eval spawned |
| 8 | All bulk children DONE; G2-eval runs, `ask_advisor("submit_evaluation_success", summary, reason)` → accept → `submit_evaluation_success(summary)` | G2-eval → DONE; lifecycle dispatches on plan_shape="full" |
| 9 | `close_harness_graph_success(G2, G2-eval)` runs | P2 DONE; R.summaries += child_success; R → DONE; propagate (root, no parent) |

End state: R is DONE. R.summaries contains 1 `segment_success` (from
G1) + 1 `child_success` (from G2). The chain is reconstructible by
listing harness graphs whose `root_task_id == R.id`.

---

## 12. What Changes vs Today

**Roles:**
- `evaluator` narrows; new `verifier` role; new `advisor` role.
- Fix-executor is `role="executor"` with a `spawn_reason` tag and a
  narrowed prompt + tool surface (no `request_plan`).

**Terminals:**
- Planner: `submit_plan_handoff` → `submit_full_plan` + `submit_partial_plan`.
- Evaluator: `submit_task_success` → `submit_evaluation_success`.
- Verifier: new `submit_verification_success` + `submit_verification_failure`.
- Advisor: new `submit_advisor_feedback`.
- Tool: new `ask_advisor` (callable from any agent before a gated
  terminal).

**Data model:**
- `TaskRole` literal extended to include `"verifier"`.
- `HarnessGraph` gains `plan_shape` and `what_to_do_next`.
- `Status` gains `FIXING`.
- DAG entries gain `role` field.

**Lifecycle:**
- `close_harness_graph_partial_success` (new path).
- `open_continuation_graph` (deterministic synthesis).
- `create_fix_executor` (deterministic synthesis).
- Pre-hooks for advisor enforcement (one per gated terminal).

**Prompts:**
- Evaluator's `agent.md` shrinks (no plan-shape branching).
- New `verifier/agent.md` (scoped node verification).
- New `advisor/agent.md` (review terminals).
- Existing prompts gain "ask_advisor before terminal" instructions where
  applicable.

---

## 13. Open Questions

1. **Chain depth cap.** Nothing prevents partial → partial → partial
   indefinitely. Plan: notification rule biasing the planner toward full
   at depth ≥ N, plus a pre-hook that hard-blocks `submit_partial_plan`
   at depth ≥ M. Values deferred.

2. **Fix-executor inline-fix scope.** Same (a)–(d) categories as today's
   evaluator inline-fix? Or stricter? Probably same; defer.

3. **Advisor model class.** Stronger than the caller (Claude Code's
   pattern) or same? If a tier system exists, advisor as the higher tier
   is the natural mapping. Defer.

4. **Context fidelity standard.** What concrete artifacts must each
   role's context object capture for advisor effectiveness? Specify
   per-role: shell command + exit code + stdout tail; reads → path +
   content hash; writes → path + diff hash; probes → kind + result.
   Defer to implementation.

5. **Where do retry-budget exhaustions go on a verifier?** With no
   retry, a verifier whose `submit_verification_success` and
   `submit_verification_failure` are both rejected by advisor is stuck.
   Plan: silent termination → `Status.FAILED` for the verifier → graph
   fails. Worth confirming this matches intuition.

6. **Mid-graph verifier dependent-blocking on `Status.FIXING`.** The
   existing `dependency_blocked_descendants` query treats only
   terminal-failure as blocking. With `FIXING` as intermediate, we want
   dependents to *wait*, not fail. Confirm graph queries handle this.

7. **Persistence schema.** Adding `plan_shape` and `what_to_do_next` to
   `HarnessGraph` and `FIXING` to `Status` means the `task_center_store`
   needs migrations. Specify when this lands.

---

## 14. Implementation Sequencing

This redesign is large enough to land in stages. Each stage is
shippable independently and leaves the system in a consistent state.
File targets follow the layer split in Section 10.

1. **Verifier package + TaskRole extension.** *(Done.)* Adds the role
   surface area without wiring it.
   - **Files:** `harness_agents/verifier/{__init__.py,agent.md,distilled_rules.md,context.py,definition.py}`; `model/task.py` (extend `TaskRole` literal).

2. **Lift task creation out of lifecycle modules into `TaskCenter`
   methods.** *Pure refactor; no behavior change.* Introduce the
   primitive + composer layout from Section 10.2 in
   `runtime/orchestrator.py`. Move each existing `Task(...)` /
   `HarnessGraph(...)` instantiation from
   `harness_agents/<role>/lifecycle.py` into the appropriate composer.
   Lifecycle modules become callers, not creators. This is a
   prerequisite for stages 3–7: every subsequent stage adds new
   creation paths, and they should land in the new structure rather
   than perpetuating the old one.
   - **Move from / to:**
     - `executor/lifecycle.py:create_root_executor` → `TaskCenter.create_root_executor` (composer) + `TaskCenter._create_executor` (primitive)
     - `planner/lifecycle.py:request_plan` (graph + planner creation) → `TaskCenter.open_request_plan_graph` (composer; lifecycle still owns the caller's status transition + summary append)
     - `planner/lifecycle.py:submit_plan_handoff` (child + evaluator creation) → `TaskCenter.materialize_full_plan` (today's `submit_plan_handoff` only emits one shape; the partial split lands in stage 4)
   - **Files touched:** `runtime/orchestrator.py` (gains five `_create_<role>` primitives + the `create_root_executor`, `open_request_plan_graph`, `materialize_full_plan` composers); `harness_agents/{executor,planner}/lifecycle.py` (loses `Task(...)` constructors, gains `tc.<composer>(...)` calls).
   - **Verification:** existing tests pass without modification. Add unit tests for each `_create_<role>` primitive that exercises construction directly with synthetic `TaskCenter` state.

3. **Verifier lifecycle.** `submit_verification_success` /
   `submit_verification_failure` lifecycle functions; runtime
   dispatch on the verifier's terminals; `Status.FIXING`.
   - **Files:** new `harness_agents/verifier/lifecycle.py`; `model/task.py` (`Status.FIXING`); `runtime/orchestrator.py` (dispatch in `submit_verification_*` methods on `TaskCenter`).

4. **Plan terminal split.** `submit_full_plan` + `submit_partial_plan`
   alongside `submit_plan_handoff`; new validation; planner agent.md
   updated; backward-compat behavior on the old terminal during
   transition. DAG entry shape gains `role` field.
   - **Files:** `harness_agents/planner/{lifecycle.py,agent.md,definition.py}`; `runtime/orchestrator.py` (`materialize_full_plan` from stage 2 splits into `materialize_full_plan` + `materialize_partial_plan`; the partial composer also writes `plan_shape="partial"` + `what_to_do_next` to the harness graph); `model/harness.py` (HarnessGraph gains `plan_shape`, `what_to_do_next`); graph validation in `graph/dag.py`.

5. **Advisor role + `ask_advisor` tool + pre-hooks.** Smallest possible
   first cut: only one gated terminal (e.g., `submit_full_plan`) wired,
   prove the pre-hook + advisor-spawn flow works end-to-end, then
   expand to the full set.
   - **Files:** new `harness_agents/advisor/{__init__.py,agent.md,distilled_rules.md,context.py,definition.py,lifecycle.py}`; new `tools/ask_advisor.py`; pre-hook layer in `runtime/orchestrator.py` (or new `runtime/pre_hooks.py`); `runtime/orchestrator.py` (`create_advisor` composer + `_create_advisor` primitive).

6. **Partial-plan chain.** `close_harness_graph_partial_success`
   (state-only, in lifecycle); `TaskCenter.open_continuation_graph`
   (deterministic continuation input + new graph + planner).
   - **Files:** `harness_agents/evaluator/lifecycle.py` (branch on `plan_shape`, call `tc.open_continuation_graph`); `runtime/orchestrator.py` (new composer + continuation input builder).

7. **Fix-executor.** `TaskCenter.create_fix_executor`; fix-executor
   prompt conditioning via system reminder + pre-hook (no `request_plan`
   terminal); F2 (re-run verifier) wiring on fix-executor success.
   - **Files:** `runtime/orchestrator.py` (new composer); `harness_agents/executor/agent.md` (system reminder fragment for fix-mode); `harness_agents/verifier/lifecycle.py` (re-run on fix-target success).

8. **Evaluator narrows.** Remove REPLAN_AFTER prose and partial-plan
   branching from the evaluator's prompt; rename
   `submit_task_success` → `submit_evaluation_success` for the
   evaluator role.
   - **Files:** `harness_agents/evaluator/{agent.md,distilled_rules.md,definition.py,lifecycle.py}`; `runtime/orchestrator.py` (rename terminal dispatcher).

9. **Persistence migrations + telemetry updates.** Storage schema for
   new `HarnessGraph` fields, `Status.FIXING`, and the new role.
   - **Files:** `db/stores/task_center_store.py`; migrations directory.
