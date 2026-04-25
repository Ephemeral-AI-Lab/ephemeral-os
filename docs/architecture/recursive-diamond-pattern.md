# Recursive Diamond Pattern

**Status:** Draft / brainstorm
**Last updated:** 2026-04-25

A proposal for handling large, complex, exploration-heavy tasks by composing
recursive (Executor → DAG → Evaluator) units. Intended to address limits of
the current EphemeralOS planning model where a tree-of-DAGs plus replan is
sensitive to initial planning quality and inflexible when complexity is only
discovered mid-execution.

---

## 1. Motivation

The current EphemeralOS execution model is a **tree of DAGs with replan**:

- A planner emits a plan up front.
- Failures or shortfalls trigger a replan of the affected subtree.

Two recurring issues:

1. **Initial-plan dependence.** Outcome quality is bounded by how well the
   first plan anticipated the work. Underestimated complexity forces costly
   replans of large subtrees.
2. **Replan brittleness.** Replan recomputes a subtree from a state that has
   already partially executed; reconciling prior work with a new plan is
   non-trivial and frequently discards useful progress.

The pattern below pushes planning *into* execution: each executor commits only
to as much plan as it is confident in, and a mandatory **Evaluator** at every
level gates completion and drives bounded iteration.

---

## 2. Core concept: the Diamond

The atomic unit is a **diamond**:

```
        ╔════════════════════╗
        ║                    ║
        ║   [Executor]       ║   head — receives the task
        ║       │            ║
        ║       ▼            ║
        ║   ┌── DAG ──┐      ║   body — children may themselves be diamonds
        ║   │  ...    │      ║
        ║   └────┬────┘      ║
        ║        ▼           ║
        ║   [Evaluator]      ║   tail — gates completion
        ║                    ║
        ╚════════════════════╝
```

A diamond has exactly three structural slots: an Executor head, a DAG body,
and an Evaluator tail. **Trivial** tasks bypass the body — the Executor emits
completion directly, and no DAG/Evaluator is created.

Diamonds compose: any node inside a DAG body may itself be the head of a
nested diamond (its own Executor → DAG → Evaluator), opaque to the parent.

---

## 3. Roles

Only two roles. **Scouts and other read-only sub-agents are out of scope for
this draft** and may be added later as decision aids; they do not change the
diamond structure.

### 3.1 Executor

Receives a task. Decides how to dispose of it via a fixed three-way decision
tree (see §4) and emits exactly one of three submit verbs.

### 3.2 Evaluator

Replaces what an earlier draft called "Advisor." Renamed because the role has
**flow authority** — it gates completion and spawns continuation work — which
is incompatible with the read-only "advisor" connotation used elsewhere in
the codebase.

An Evaluator is bound to a specific DAG. It:

- Reads the originating handoff note, acceptance criteria, and the
  completion notes of every direct child of the DAG.
- Validates whether the work satisfies the criteria.
- Emits exactly one of two submit verbs: `submit_continue_to_work` or
  `submit_task_completion`.

A single Evaluator instance **persists across continuation cycles** for its
DAG (see §6). It is not respawned on each cycle.

---

## 4. Executor decision tree

Every Executor invocation runs the same three-way classification:

```
                    [task arrives]
                          │
                          ▼
                    ┌──────────┐
                    │ Executor │ ◄── inherits prior context
                    └────┬─────┘     on continuation cycles
                         │
                         ▼
                  ╭───────────────╮
                  │   trivial?    │
                  ╰───┬───────┬───╯
                     YES      NO
                      │       │
                      ▼       ▼
             submit_task_   ╭──────────────────────╮
             completion     │ can I enumerate a    │
                            │ full plan covering   │
                            │ the full AC now?     │
                            ╰───┬──────────────┬───╯
                               YES             NO
                                │              │
                                ▼              ▼
                     submit_full_         submit_partial_
                     plan_handoff         plan_handoff
```

The two complex branches differ on **plan completeness**, not on subjective
confidence:

- `submit_full_plan_handoff`: the Executor can enumerate every step from here
  to satisfying the full acceptance criteria, without needing to see
  intermediate results.
- `submit_partial_plan_handoff`: the Executor can plan steps `1..k`
  confidently; steps `k+1..n` depend on what `1..k` reveals.

This distinction is structural and the Evaluator branches on it
deterministically (§5).

---

## 5. Submit verbs

### 5.1 Executor verbs

```
submit_task_completion(
    completion_note,
)

submit_full_plan_handoff(
    dag,
    acceptance_criteria,        # the only AC; dag covers all of it
    handoff_note,
)

submit_partial_plan_handoff(
    dag,
    full_acceptance_criteria,        # the full target
    covered_acceptance_criteria,     # what THIS dag is committed to
    deferred_rationale,              # why the rest is deferred
    handoff_note,
)
```

The implicit gap on a partial handoff is `full \ covered`, computable
mechanically by the Evaluator.

### 5.2 Evaluator verbs

```
submit_continue_to_work(
    gap_analysis,        # what is missing or wrong
    inherited_context,   # passed to the next Executor
)

submit_task_completion(
    completion_note,     # bubbles up to the parent diamond
)
```

`submit_continue_to_work` always spawns **exactly one** Executor (§6).

---

## 6. Continuation mechanic

When an Evaluator emits `submit_continue_to_work`:

1. **Exactly one** Executor (`Executor_2`) is spawned.
2. `Executor_2` inherits: the original handoff note, the original
   acceptance criteria, all prior leaf completion notes, and the
   Evaluator's gap analysis.
3. `Executor_2` re-enters the decision tree at the top — it may itself
   classify the remaining work as trivial, full-plan, or partial-plan.
4. Whatever `Executor_2` produces routes back to the **same Evaluator
   instance** that spawned it.

```
[Evaluator] ── submit_continue_to_work ──► [Executor_2]
     ▲                                          │
     │                                          │ re-enters decision tree
     │                                          │ may itself spawn a
     │                                          │ nested diamond
     └──────── completion bubbles ◄─────────────┘
```

**Why exactly one.** Parallel continuation almost always produces dependent
fixes that conflict. The continuing Executor decides for itself whether the
remaining work is trivial or warrants its own DAG.

**Bound on cycles.** An Evaluator should cap continuation at a small `N`
(e.g. 3). After exhaustion, the Evaluator escalates by performing
`submit_*_handoff` itself — the Evaluator becomes an Executor in the parent's
DAG, surfacing the unresolved gap one level up. This ensures unresolved work
becomes visible at higher scopes rather than burning budget locally.

---

## 7. Evaluator validation flow

Evaluation is deterministic given the verb that originated the DAG:

```
on_dag_complete:
    case submit_full_plan_handoff:
        validate(dag_outputs, acceptance_criteria)
        if pass:
            submit_task_completion
        else:
            submit_continue_to_work(gap = unmet acceptance_criteria)

    case submit_partial_plan_handoff:
        # Step 1: did the DAG deliver what it explicitly promised?
        validate(dag_outputs, covered_acceptance_criteria)
        if not pass:
            submit_continue_to_work(gap = unmet covered_acceptance_criteria)
            return

        # Step 2: is there still a gap against the full target?
        gap = full_acceptance_criteria \ covered_acceptance_criteria
        if gap is empty:
            submit_task_completion
        else:
            submit_continue_to_work(gap)
```

Two-stage validation on partial plans is important: the Evaluator first
holds the DAG to its narrower commitment, then computes the residual gap.
Mixing the two would let a DAG that under-delivered on its own commitment
still appear "partial-but-progressing."

---

## 8. Recursive composition

Any node in a DAG body may itself be a diamond. From the parent's
perspective the node is opaque — it sees one completion note. Internally the
node may have unfolded into its own (Executor → DAG → Evaluator) and even
gone through multiple continuation cycles before completing.

```
            ROOT DIAMOND
            ╔═══════════════════════════════════════════════════════════╗
            ║                                                           ║
            ║   [Executor]                                              ║
            ║      │                                                    ║
            ║      │  trivial         → submit_task_completion ────────►║──► done
            ║      │  complex+certain → submit_full_plan_handoff        ║
            ║      │  complex+uncert. → submit_partial_plan_handoff     ║
            ║      ▼                                                    ║
            ║   ┌─── DAG ────────────────────────────┐                  ║
            ║   │                                    │                  ║
            ║   │   ┌─────┐    ┌──────────────┐      │                  ║
            ║   │   │node │    │ CHILD DIAMOND│      │                  ║
            ║   │   │ A   │    │ (same shape, │      │                  ║
            ║   │   │triv.│    │  recursive)  │      │                  ║
            ║   │   └──┬──┘    └──────┬───────┘      │                  ║
            ║   │      │              │              │                  ║
            ║   └──────┴──────┬───────┘              │                  ║
            ║                 ▼                      │                  ║
            ║            [Evaluator]                 │                  ║
            ║                 │                      │                  ║
            ║      ┌──────────┼──────────┐           │                  ║
            ║      │                     │           │                  ║
            ║  continue_to_work    task_completion   │                  ║
            ║      │                     │           │                  ║
            ║      ▼                     ▼           │                  ║
            ║  [Executor_2]──┐      bubbles up ─────►║──► done
            ║      ▲         │                       │                  ║
            ║      │         │ re-enters decision    │                  ║
            ║      │         │ tree; result returns  │                  ║
            ║      │         │ to SAME Evaluator     │                  ║
            ║      └─────────┘                       │                  ║
            ║                                        │                  ║
            ╚════════════════════════════════════════╧══════════════════╝
```

**Containment.** A child diamond's Evaluator is authoritative *only* for the
child's acceptance criteria. The parent Evaluator independently re-validates
against the parent's criteria. A child's `submit_task_completion` is
necessary for the child's node to be "ready" in the parent's DAG, but not
sufficient for parent-level completion.

---

## 9. Topology invariants

These are the structural rules a Task Center must enforce:

1. **Diamond shape.** Every non-trivial task expands to exactly one Executor
   head, one DAG body, one Evaluator tail. No DAG without an Evaluator. No
   Evaluator without a DAG.
2. **Single Evaluator per DAG.** The Evaluator instance persists across all
   continuation cycles for its DAG.
3. **Dependency-driven launch.** A node launches only when all its DAG
   dependencies have completed.
4. **Evaluator launch.** An Evaluator launches only when all direct children
   of its DAG have completed.
5. **Continuation cardinality.** `submit_continue_to_work` spawns exactly
   one Executor. The continuing Executor's output routes to the spawning
   Evaluator.
6. **Bounded continuation.** Each Evaluator caps continuation cycles at a
   small `N`. On exhaustion, the Evaluator must `submit_*_handoff` upward.
7. **Opacity.** The internal structure of a child diamond is invisible to
   its parent. Only the child Evaluator's completion note crosses the
   boundary.

---

## 10. Comparison

| Pattern | Planning | Quality gate | Iteration unit | Recursion |
|---|---|---|---|---|
| EphemeralOS (current) | Upfront tree of DAGs, replan on failure | Implicit (per-task verifiers) | Replan subtree | Yes, but plan-driven |
| Plan-and-Execute (LangGraph, BabyAGI) | One planner up front | None or single critic | Re-planner pass | No |
| ReAct / Reflexion | Inline | Self-reflection | Single-agent loop | No |
| CrewAI / AutoGen team | Manager dispatches | Manager review | Re-dispatch | Shallow |
| Tree-of-Thought | Branching search | Heuristic pruning | Backtrack | Reasoning only |
| **Recursive Diamond** | **Distributed, partial-OK, runtime-driven** | **Mandatory Evaluator tail per subtree** | **continue_to_work=1 (peer Executor)** | **Yes, runtime-driven** |

The closest cousin is the Generator–Discriminator (GAN-style) harness; the
diamond pattern generalizes that into a tree of generator–critic pairs where
critics can themselves spawn generators.

---

## 11. Properties this design buys

1. **Robust to bad upfront planning.** Each Executor commits only to as much
   plan as it is confident in. Discovered complexity is absorbed by partial
   handoffs and continuation cycles, not by replans of large subtrees.
2. **Locality of correction.** A shortfall is fixed at the same level it was
   detected, by spawning one peer Executor — no upstream replan.
3. **Composable.** Every subtree has the same diamond interface
   (task → completion note), making subtree-level reasoning uniform.
4. **Natural backpressure.** Repeated `continue_to_work` from a single
   Evaluator is a clean signal that the parent plan was wrong — it surfaces
   via cycle-cap escalation rather than implicit timeouts.
5. **Deterministic Evaluator branching.** The submit verb that opened the
   DAG dictates how the Evaluator validates, removing a class of judgment
   ambiguity.

---

## 12. Open questions

These are not blockers but need answers before implementation.

**a. Cycle cap value.** What is `N`? Likely `2`–`3`. Should be configurable
per task class.

**b. Evaluator's information surface.** Today the Evaluator reads only
handoff notes, acceptance criteria, and leaf completion notes. If
Executors write optimistic completion notes, the Evaluator rubber-stamps.
Two options to consider later: (i) Evaluator may issue read-only checks
itself, (ii) require the Executor to attach evidence (test outputs, file
diffs) to the completion note.

**c. Acceptance criteria as the new bottleneck.** This pattern moves the
load-bearing artifact from "initial plan" to "acceptance criteria." Quality
of the criteria determines what the Evaluator can catch. Worth investing
in criteria templates and conventions.

**d. No sibling cross-talk.** Sibling Executors in a DAG cannot see each
other's progress — only the Evaluator sees their convergence. Some classes
of work benefit from shared intermediate state (compare with the current
shared task store). Whether to add a sibling-visible scratch surface, and
how to bound it, is open.

**e. Partial-plan structure.** Should `handoff_note` be free-text or
structured (e.g.
`{committed_steps, speculative_steps, unknowns, deferred_rationale}`)?
Structured form is easier for the Evaluator to reason about; free-text is
easier for Executors to produce.

**f. Escalation semantics.** When an Evaluator escalates after `N` cycles,
its `submit_*_handoff` runs at the parent's DAG level. The parent
Evaluator must know how to fold an escalated child back into its own
gap analysis. Mechanism TBD.

---

## 13. Glossary

- **Diamond:** the atomic unit `(Executor → DAG → Evaluator)`.
- **Executor:** agent that decomposes or completes a task. Emits one of
  `submit_task_completion`, `submit_full_plan_handoff`,
  `submit_partial_plan_handoff`.
- **Evaluator:** agent bound to a DAG that gates completion. Emits one of
  `submit_continue_to_work`, `submit_task_completion`. Persists across
  continuation cycles. Replaces "Advisor" in earlier drafts.
- **Acceptance criteria (AC):** machine-checkable conditions a task must
  satisfy. Set by the Executor that opens the diamond.
- **Covered AC:** the subset of full AC that a partial handoff is committed
  to satisfying in this DAG.
- **Continuation cycle:** one (Evaluator → Executor → … → Evaluator) loop
  triggered by `submit_continue_to_work`.
- **Escalation:** an Evaluator surfacing unresolved work to its parent's DAG
  by performing a handoff itself, after exhausting its continuation cap.
