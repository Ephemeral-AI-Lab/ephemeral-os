# LangGraph vs. EphemeralOS TaskCenter — Harness Comparison

A side-by-side reading of how each framework structures an agentic system.
Scope: state, control flow, role/variant selection, failure/retry, in-the-loop
gating, segment continuation. Concrete file references on the TC side.

> **Granularity note up front.** A LangGraph *node* is a function (often an LLM
> call, often not). A TC *task* is a full LLM agent run that must exit through
> exactly one terminal-submission tool. Comparing node-to-task one-for-one
> misleads — read this doc as comparing *mechanisms*, not units.

## 1. Axis-by-axis

| Axis | LangGraph | TaskCenter (TC) |
|------|-----------|-----------------|
| **State** | One shared `TypedDict` per graph. Nodes read/write; reducers merge concurrent writes. Mutable, global to the graph. | No shared mutable state. Per-spawn `ContextPacket` — typed `ContextBlock`s with `priority∈{required,high,medium,low}`, built by a `ContextRecipe` for one role. Each agent sees a filtered view; the renderer drops/compresses by priority under token budget. (`backend/src/task_center/context_engine/packet.py`, `recipes/planner.py`) |
| **Topology** | Python at compile time: `StateGraph.add_node/add_edge/add_conditional_edges`. Statically inspectable; changing the DAG means re-compiling. | Runtime: the planner agent **emits the DAG as data** via `submit_full_plan` / `submit_partial_plan` (`planner.md`). Every iteration the planner can resynthesize topology against the current `segment_goal`. |
| **Routing / variants** | `add_conditional_edges(router_fn)` or supervisor patterns built from `Command(goto=...)`. Routing logic is graph code. | `RuleBasedAgentResolver` + named `PredicateRegistry` predicates (`task_center/_core/agent_routing.py`); variants live in agent-definition frontmatter (`agent.md`). E.g. `nested_goal_depth_within_handoff_range` swaps an executor profile for a leaf-executor profile (no handoff terminal). Routing is data-driven, not code-driven. |
| **Failure / retry** | DIY. You wire retries on nodes, or use `Command` to loop. No first-class "this task failed, rewire dependents" primitive. | First-class. A worker `submit_task_summary(type="fail")` routes through `TaskCenter.request_replan`: original task → `replanning`, replanner task spawned, **pending dependents rewired** to the replanner. Invariants enforced — non-pending dependents during rewrite is a hard `GraphInvariantViolation` (`AGENTS.md` §"Replanning specifics"). |
| **In-the-loop gate** | `interrupt()` + `Command(resume=...)` — pauses the graph for **humans**, requires a checkpointer + thread_id. | `ask_advisor` — a **mandatory LLM-judge gate** before any terminal submission (`tools/ask_helper/ask_advisor.py`). Advisor spawns ephemerally, sees the parent's verbatim `user_msg_1`/`user_msg_2` + filtered transcript + the same terminal registry through `advisor_review_focus`, returns `approve`/`reject` via `submit_advisor_feedback`. **No LangGraph equivalent.** |
| **Resolver / repair** | DIY (a node that re-runs the failing step). | `ask_resolver` (`tools/ask_helper/ask_resolver.py`) — edit-capable helper invoked by verifiers when checks fail; same direct-launch shape as the advisor but with file-edit permission and `submit_resolver_result` terminal. |
| **Persistence** | Checkpointer (memory/Postgres/SQLite) keyed on `thread_id`. State machine is replayable. | SQLAlchemy stores for `goal`, `iteration`, `attempt`, `task`, `context_packet` (`task_center/_core/persistence.py`). Granularity is the domain object, not the step. |
| **Inter-segment continuation** | Same thread, re-invoke with a new input; state persists by reducer. | `submit_partial_plan(..., continuation_goal: str)` — prose handed to a **fresh planner** for the next segment. The next planner does **not** see this graph's task contents, only its summary (`planner.md` §"Rules for partial plans"). |
| **Terminal contract** | Implicit — any node return is a "result". | Explicit. Every agent has a declared set of terminals from a single registry (`tools/_terminals/registry.py`) with two views per tool: `selection_guidance` (parent-facing) and `advisor_review_focus` (audit-facing). The ContextComposer auto-appends the parent-facing catalog to `user_msg_2`; the advisor renders the audit view of the same set — the prompts can't drift. |

## 2. Where the architectures actually diverge

### 2.1 Code-as-graph vs. plan-as-data
The user's earlier framing — LangGraph is criticized for inflexibility — maps
to one axis: **LangGraph's DAG is Python code; TC's DAG is the planner's
output**. The trade is real, not aesthetic:

- LangGraph: statically analyzable, type-checkable, deterministic topology;
  any topology change requires a code deploy.
- TC: every iteration a fresh planner picks the DAG against the live segment
  goal and `failed_graph_landscape`. Cheap to retry with a *different* shape.
  Cost: the topology isn't visible until runtime, harder to reason about
  ahead of execution.

### 2.2 State: shared reducer vs. recipe-composed packet
LangGraph's `TypedDict + reducer` is one *typed shared bag*. Every node may
read the whole bag; reducers resolve concurrent writes. This is where
LangGraph is genuinely stronger on **state transitions** — type-checked
merges, well-defined ordering.

TC has no shared state. Each spawn gets a packet built fresh by a recipe
(e.g. `_planner_build` pulls goal + iteration + every prior failed attempt's
landscape, tags each as a typed `ContextBlock`, sets priorities). What flows
between tasks is *summaries via the dependency graph*, not shared memory.
This avoids cross-task interference but means **TC has no typed inter-task
state contract** — it has typed *context blocks per recipe*, which is a
different ontology.

### 2.3 In-the-loop: humans vs. judges
LangGraph's `interrupt()` exists to bring a **human** into the loop. The
graph literally pauses, the client gets the interrupt value, the human
submits via `Command(resume=...)`, execution continues from the checkpoint.

TC's advisor gate is the same shape — block until external verdict — but the
"external" is **another LLM run** with a deliberately calibrated lenient bar
("approve when tool choice is right and payload plausibly supported …;
reject only on real quality problems"). Mechanically: not an interrupt; it's
a synchronous tool call from inside the parent that spawns a sibling agent
with shared context.

These solve overlapping problems (something must look at this before it
commits) but they are **not interchangeable**. LangGraph has no LLM-judge
primitive; TC has no human-pause primitive.

### 2.4 Failure handling
LangGraph leaves failure recovery to whoever builds the graph. TC bakes
replan into the lifecycle: the moment a worker submits a `fail`, the graph
mutates — original task is preserved as `failed` for the audit trail, a
replanner is parented onto the slot, and `submit_replan` may add corrective
children that are *guaranteed* not to depend on the failed branch. This is
the closest TC analogue to LangGraph's "stronger state transitions" framing:
it is not state-typed, it is *transition-typed* — the legal graph mutations
are enumerated and enforced.

## 3. What TC could borrow from LangGraph (and what it should not)

**Borrow candidate: typed inter-segment contract.**
`submit_partial_plan.continuation_goal` is unstructured prose. A fresh
planner reads it cold. LangGraph's reducer-merged state is the obvious
contrast: a typed object that the next segment receives with semantics, not
just words. The borrow would be **a typed continuation packet** (a small
schema: open requirements, deferred items by id, known constraints,
artifact refs) added alongside the prose. Recipes already speak
`ContextBlock`s; reusing that vocabulary is cheap. The prose stays, but a
machine-readable spine pins it down.

**Borrow candidate: explicit checkpointer-style replay.**
TC persists domain objects but doesn't have LangGraph's "re-invoke from
exactly this checkpoint" affordance. For debugging individual segments this
is worth ~1 day of design.

**Do not borrow: shared mutable state across agents.**
TC's "no shared state, summaries flow via deps" is intentional. Adopting a
LangGraph-style shared bag would re-introduce the cross-agent interference
the current design avoids, and would compete with the per-recipe packet
ontology rather than complement it. The replanner invariants assume each
task's input is a closed function of its declared dependencies; a shared bag
breaks that.

**Do not borrow: code-defined topology.**
The planner-emits-DAG choice is load-bearing for TC's retry story. Failed
attempts feed `failed_graph_landscape` back into the next planner, which
restructures the DAG. Hard-coding the DAG would defeat this.

## 4. What LangGraph could learn from TC

Less interesting for this comparison's purpose, but for symmetry:

1. **Mandatory pre-commit LLM gate.** The `ask_advisor` pattern — every
   terminal must clear a sibling-LLM audit, with the audit prompt rendered
   from the same registry as the action prompt — is a generic discipline,
   not a TC-only one. A LangGraph project could implement it manually
   today; it just isn't built-in.
2. **Two-view tool registry.** One source of truth that renders both the
   actor's "call when …" and the auditor's "verify … flag …" prevents
   prompt drift. Independent of harness; broadly portable.
3. **Replan as a first-class graph mutation.** LangGraph's failure story is
   "loop with `Command`"; TC's enumerated mutations (rewire dependents,
   reparent corrective tasks, cancel stale siblings) are stricter and
   produce cleaner audit trails. Worth porting as a pattern, not as code.

## 5. One-sentence summary

LangGraph is a **typed-state, code-defined graph** harness with strong
state-transition semantics and a human-interrupt loop. TaskCenter is a
**typed-context, runtime-synthesized DAG** harness with first-class
replan/audit semantics and an LLM-judge gate on every terminal submission.
The two would compose well: TC's lifecycle/audit primitives layered on
LangGraph's state-transition spine — but only if TC's "no shared mutable
state" rule is preserved.
