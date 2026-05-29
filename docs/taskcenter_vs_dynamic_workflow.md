# EphemeralOS TaskCenter vs Claude Code Dynamic Workflows

A design comparison of EphemeralOS's TaskCenter control plane against Claude Code's
Dynamic Workflows (research preview, released 2026-05-28 with Opus 4.8). Focus:
task handoff, context management, planning/replanning, multi-agent coordination,
concurrency, flexibility, and ceiling.

> **What Dynamic Workflows is:** Claude writes a JavaScript orchestration script for
> the task; a background runtime executes it, fanning work across subagents (up to 16
> concurrent, 1,000 total). The plan moves into code — intermediate results live in
> script variables, not the model's context. The script holds the loop and branching;
> once it runs, the model is out of the orchestration loop.

## Core thesis

Both systems share one conviction: **coordination state must not live in the model's
context window.** From there they split on a single axis — **who authors the
orchestration, and whether correctness is enforced or merely offered.**

- **Dynamic Workflows bets on the model.** It lets the model author the orchestration
  as code: maximally flexible topology, but verification, context correctness, and
  concurrency safety are *offered, not enforced* — a script may skip them.
- **TaskCenter bets on the system.** It builds the guarantees into the framework
  (mandatory planner/evaluator gates, role-typed context recipes, OCC, the
  Goal→Iteration→Attempt lifecycle): fixed topology, but verification, context
  discipline, and write-safety are *enforced by construction*.

## Comparison table

| Dimension | Claude Code Dynamic Workflow | EphemeralOS TaskCenter | Edge |
|---|---|---|---|
| Core bet / who authors orchestration | Trust the model — it writes the orchestration as a per-task JS script | Trust the system — framework enforces structure; model fills content | Philosophical |
| Coordination state lives in | JS script variables (volatile, session-scoped) | Persisted DB state + write-once packet store (durable) | TaskCenter |
| Task handoff | Function call → return value into a script variable | `submit_execution_handoff` → child Goal; parent `WAITING_GOAL`; gated closure report | TaskCenter (durable/gated); DW (lighter) |
| Context delivery | Script string-builds each prompt — **pushed** at authoring | ContextEngine **pulls** role-typed packet from current state at launch | TaskCenter |
| Context discipline | Ad-hoc per script | Typed, minimal-highest-value, omission-controlled, priority-compressed | TaskCenter |
| Verification | Emergent — agents refute until converge (idiomatic, optional) | Mandatory evaluator gate per attempt + retry budget (enforced by construction) | TaskCenter |
| Planning model | Full-ahead — all phases planned in one script before execution | Plan-to-near-horizon; G→I→A; defer the rest | TaskCenter (large/uncertain) |
| Replanning | None mid-run; only coarse, discretionary, ungated cross-workflow chaining (ultracode) | Fine-grained, structural, gated: retry / deferred goal / nested | TaskCenter |
| Large-task scale | One up-front script; no re-plan if a sub-task stays large | Recursive nested Goals, each a full gated G→I→A cycle | TaskCenter |
| Flexibility — topology | Arbitrary (debate, vote, tournament, conditional fan-out) in code | Fixed grammar: DAG-then-verify (+ recursion/sequencing) | DW (narrow-class) |
| Flexibility — content | Model authors any prompt/task | Planner authors any tasks + any criteria | Tie |
| Flexibility — temporal/phasing | Phases pre-baked in script (waterfall) | Deferred goal: plan N+1 only after N is verified | TaskCenter |
| Concurrent-write safety | Avoid conflicts — file-disjoint partition / worktrees / locks; git-merge at boundary | OCC — overlay-per-generator, stale-base reject → replan; shared logical workspace | TaskCenter (resolves vs avoids) |
| Workspace modes | Agents read/write/run; worktree or lock isolation | OCC shared + isolated-workspace mode + ephemeral-per-tool-call overlays | TaskCenter (deeper) |
| Crash / interrupt recovery | In-session resume; completed agents return cached results (shipped) | Durable state, but in-flight orchestrator registry is process-local (cross-restart resume = known gap) | **DW** |
| Ceiling | Higher for *fits-in-one-plan / cheap-verify / exploration* (rides the model) | Higher for *large / uncertain* tasks (information horizon, not model, is the limit) | **Regime-dependent** |
| Novelty | Sharper single product insight (plan → script vars, context offload); well-precedented lineage | More distinctive architecture (gated recursive unit + OCC + replan-as-transaction) | Tie (different directions) |
| Productization / ergonomics | Shipped, resumable, saveable-as-command, zero-ceremony | Heavier system to operate | **DW** |
| Best-fit domain | Open-ended research, audits, exploration | Large, correctness-critical multi-agent coding | Different domains |

## Key insights

- **Enforce vs offer is the load-bearing asymmetry.** DW can ship a gated subagent
  primitive but, because the orchestration is model-authored code, can only *hope* a
  script uses it — never *guarantee* it. Enforced structure is a property you only get
  by building it into the framework. This is TaskCenter's moat and DW structurally
  cannot copy it.

- **The ceiling is regime-dependent, and the binding constraint on large tasks is
  information, not model capability.** You cannot correctly plan phase 5 before phase 1
  has run. DW's full-ahead script planning hits an *information ceiling* that no model
  improvement fixes. TaskCenter's plan-to-horizon → execute → verify → replan (deferred
  goal), with recursion into child Goals when a sub-task stays large (nested
  `submit_execution_handoff`), is the only design that folds execution-time information
  back into planning — so for large/uncertain coding it has the *higher* ceiling.

- **Deferred goal exists because of a belief about agent cognition:** agents plan well
  near and badly far, so the system only ever asks for a near-horizon plan, then
  re-plans with real knowledge. Sequential phasing under uncertainty, not waterfall.

- **OCC decouples task decomposition from resource partitioning.** Without OCC, the only
  safe partition key is the resource itself — which is why DW migrations fan out one
  agent per file. OCC lets TaskCenter decompose by *meaning* and reconcile overlapping
  writes optimistically (stale-base reject → replan), rather than forbidding overlap.

- **Context is pulled, not pushed.** TaskCenter's ContextEngine builds each agent's
  role-typed packet from *current* persisted state at launch, so a generator launched
  after a retry automatically sees prior failures. DW's script forwards only what it
  captured at authoring. The discipline is **minimal context of highest value** —
  minimize scope, not signal (a surviving block must still carry enough to act on).

## Bottom line

DW genuinely wins crash-recovery, raw topological flexibility, productization, and the
ceiling for *one-plan / exploration* work. TaskCenter wins enforced verification,
replanning, large-task recursion, concurrency safety, workspace depth, and — corrected —
the ceiling for *large uncertain coding*, because there the limit is
information-at-planning-time, not model capability. They are optimized for different bets
and different domains.

The synthesis that beats both: **model-authored outer orchestration (DW's flexibility)
over TaskCenter's enforced gated units (TaskCenter's correctness).** The offer-vs-enforce
asymmetry means TaskCenter can reach this — adding a flexible orchestration layer over
enforced units is additive — while DW retrofitting *enforced* structure fights its own
core design. TaskCenter's winning move is not to out-flex DW but to become the enforced
execution primitive a flexible orchestrator calls.

## Sources

- [Orchestrate subagents at scale with dynamic workflows — Claude Code Docs](https://code.claude.com/docs/en/workflows)
- [Introducing dynamic workflows in Claude Code](https://claude.com/blog/introducing-dynamic-workflows-in-claude-code)
- [Anthropic Ships Claude Opus 4.8 Alongside Dynamic Workflows (MarkTechPost)](https://www.marktechpost.com/2026/05/28/anthropic-ships-claude-opus-4-8-alongside-dynamic-workflows-and-cheaper-fast-mode-with-workflows-capped-at-1000-subagents/)
- [Claude Code Worktrees Guide (2026): Parallel Agents Without Conflicts](https://www.claudedirectory.org/blog/claude-code-worktrees-guide)

EphemeralOS architecture references: `docs/architecture/task_center/`,
`docs/plans/agent_context_recipes.md`.
