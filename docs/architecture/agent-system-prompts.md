# Agent System Prompts — Spec-Driven, Mode-Aware

Companion to `agent-team-coordination.md`. That doc defines the role
boundaries (who sees what, who terminalizes how). This doc defines:

1. **Labeled-heading envelopes** — the wire format every payload that crosses
   a role boundary uses.
2. **Launch contexts** — typed dataclasses (one per role) that own the
   envelope format and own what each role is allowed to see.
3. **Mode taxonomy** — per role, the explicit decision table that picks one of
   the available terminal tools.
4. **Tool-surface playbook** — when to use sync vs. backgrounded `shell`,
   when to fan out `run_subagent`, when `ci_query_symbol` beats `grep`.
5. **Per-role system prompts** — the strings installed in
   `backend/src/agents/builtins.py`.

The graph is **recursive**: any executor (or evaluator) may call
`launch_plan_handoff` to spawn a child planning unit. Children can decompose
further, so a planner does not need to fully understand or plan every detail
of large facets up front — assigning a single child and letting it
re-decompose is the right move when a facet is too big for one planner pass.

---

## 1. Labeled-Heading Envelopes

Every payload that crosses a role boundary is a single string. To survive
that flattening, every payload uses the same `## ALLCAPS_LABEL` heading
format. Sections are mandatory unless marked `(optional)`.

### 1.1 TaskSpec — what an executor child receives as `task_inputs[id]`

```
## GOAL
<one sentence: the outcome that makes this task DONE>

## ACCEPTANCE CRITERIA
- <verifiable predicate>
- <...>

## INPUTS
- workspace_paths: <comma-separated paths the planner already located>
- upstream_artifacts: <ids of `needs` whose summaries you receive at dispatch>
- prior_findings: <inline pointer to scout findings the planner attached>

## CONSTRAINTS
- <forbidden touches; invariants to preserve>

## VERIFICATION PLAN
- <command(s) the executor should run before submitting success>
- <expected pass signal>

## OUT OF SCOPE
- <work belonging to a sibling — name the sibling id>

## RISKS / UNKNOWNS (optional)
```

### 1.2 PlannerLaunchBrief — what the planner reads (assembled by the runtime)

```
## CALLER_ROLE
executor | evaluator

## CALLER_INPUT
<verbatim input of the task that called launch_plan_handoff>

## PARENT_GOAL
<the planning unit's parent task input — the goal this graph must close>

## REQUESTED_GAP
<the `task_detail` the calling executor or evaluator passed>

## PRIOR_PLANNER_HANDOFFS
<each prior planner's handoff_summary in this graph chain>

## COMPLETED_CHILD_SUMMARIES
<DONE siblings — recovery only>

## FAILED_CHILD_SUMMARIES
<FAILED siblings — recovery only>

## DEPENDENCY_BLOCKED_SUMMARIES
<dependency-blocked siblings — recovery only>
```

### 1.3 PlanHandoffSummary — what the planner emits as `handoff_summary`

```
## PLAN_SHAPE
full | partial

## TOPOLOGY
<one label from the §3.2 palette: fan-out | diamond | pipeline | map+reduce |
 spike+gap | probe+gated | two-track | recovery-slice | bisect | canary+bulk |
 hybrid:<a>+<b> | custom:<one-line>>

## COVERAGE_MAP
- <child_id>: covers <which facet of PARENT_GOAL>

## CONFIDENCE_BOUNDARY
- HIGH: [<child_id>, ...]
- EXPLORATORY: [<child_id>, ...]

## GAP (partial plans only)
- <what is NOT yet planned and why>
- recommended_next_move: <"after children complete, replan using their
  findings" | "if HIGH children pass, goal is met; otherwise launch recovery
  planner" | "block; need human input">

## EVALUATOR_FOCUS
- run: <commands the evaluator should execute>
- verify: <observable checks beyond test exit codes>
- skip: <work the evaluator should NOT redo>
```

### 1.4 ExplorationBrief — what the executor passes to `run_subagent(prompt=...)`

```
## QUESTION
<one sentence — the single thing this scout must answer>

## SCOPE
- paths_to_search: <globs / directories / known anchor files>
- symbols_to_trace: <function names, classes, decorators>

## HARD_LIMITS
- max_files_to_read: <integer>
- forbidden: <"do not propose fixes", "do not summarize unrelated code">

## OUTPUT_SHAPE
Return ## FINDINGS with these fields:
- LOCATIONS: <file:line for every match>
- CURRENT_BEHAVIOR: <what the code does today>
- CHANGE_SURFACE: <files that would need editing if the question implies a fix>
- UNCERTAINTIES: <what you could not determine and why>
```

### 1.5 ExplorationFindings — what the explorer returns

```
## FINDINGS

### LOCATIONS
- path/file.py:123  <one-line context>

### CURRENT_BEHAVIOR
<2–6 lines describing what the code does at those locations>

### CHANGE_SURFACE
- path/file.py: <what would need to change>

### UNCERTAINTIES
- <symbol or path the explorer could not resolve>
```

### 1.6 ExecutorContext — what the runtime renders into the executor's prompt

The runtime wraps every executor's `task.input` with the dependency context
allowed by the information-flow rules:

```
## TASK_INPUT
<the executor's task input — TaskSpec text or free-form>

## DEPENDENCY_SUMMARIES
### <dep_id>
input: <dep's task_input>
summaries:
  - [success] <text>
  - ...
### <dep_id>
...
```

### 1.7 EvaluatorContext — what the runtime renders into the evaluator's prompt

```
## PARENT_GOAL
<the planning unit's parent task input>

## PLANNER_HANDOFF
- [handoff] <planner_id>: <handoff_summary>

## COMPLETED_CHILD_SUMMARIES
- [success] <child_id>: <text>
- [child_success] <child_id>: <text>

## FAILED_CHILD_SUMMARIES
- [failure] <child_id>: <text>
- [child_failure] <child_id>: <text>
- [dependency_blocked] <child_id>: <text>

## TASK_INPUT
<the evaluator's dispatch instruction>
```

### 1.8 EvaluationVerdict — what the evaluator's terminal summary looks like

```
## VERDICT_BASIS
- plan_shape_received: full | partial
- topology_received: <label from the palette>
- children_observed: DONE=<n>, FAILED=<n>, DEP_BLOCKED=<n>

## CHECKS_RUN
- <command 1>  → <pass | fail | n/a>
- adversarial: <probe and result>          ← MANDATORY for submit_task_success

## CONCLUSION
- goal_met: yes | no | partial
- residual_risks: <bulleted list, may be empty>
- in_place_fix_applied: <list of files the evaluator edited, may be empty>
```

If `submit_evaluation_failure`, append `## FAILURE_DETAIL`. If
`launch_plan_handoff`, append `## RECOVERY_REQUEST`.

---

## 2. Launch Contexts (typed in code)

The envelopes above are owned by three dataclasses in
`backend/src/task_center/planning/launch_context.py`:

| Dataclass | Used by | Built when | Stored as |
|---|---|---|---|
| `PlannerLaunchContext` | planner | once at handoff | the planner's `task.input` (rendered text) |
| `ExecutorLaunchContext` | executor | per dispatch | the executor's prompt (rendered at spawn) |
| `EvaluatorLaunchContext` | evaluator | per dispatch | the evaluator's prompt (rendered at spawn) |

Each has a `to_*_prompt()` method that emits the labeled-heading text shown
in §1. Adding or removing a field is a doc-and-code change in one place.

**Why this asymmetry.** The planner's *input is its context* — once the
planner is launched, its prompt is fixed. Executors and evaluators have
work payloads (`task.input`) that exist independently of the live state of
the graph; their context (DONE deps, child summaries) only resolves at
dispatch time, after async completions land.

**Information-flow boundaries (mirrors `agent-team-coordination.md` §9).**
Each dataclass is a single place to enforce what each role can NOT see:

- `ExecutorLaunchContext` exposes only `task.needs` dependencies — never
  parent goal, never sibling tasks the executor doesn't depend on.
- `EvaluatorLaunchContext` exposes parent goal + planner handoff + child
  summaries — never recursive evidence from outside this planning unit.
- `PlannerLaunchContext` exposes the launch brief built by the runtime —
  the planner cannot see runtime state that arrives after launch.

---

## 3. Mode Taxonomy and Topology Palette

A "mode" is a terminal-tool choice. Each role's prompt has exactly one
decision table.

### 3.1 Per-role decision tables

**Executor — three modes (executor decides whether the task is atomic).**

| Mode | Terminal | Trigger |
|---|---|---|
| Direct success | `submit_task_success` | One focused effort, work done, verifications hold, no test files edited. |
| Plan handoff | `launch_plan_handoff` | Composite at start OR blocker mid-execution (cross-cutting impact, spec ambiguity, missing infra, contradictory criteria, scope larger than expected, input unparseable). Preserve any partial diff; describe in `STATE_AT_HANDOFF`. |
| Soft fail | `submit_task_failure` | NARROW: well-scoped task, provably cannot succeed, decomposition wouldn't help. If tempted because the task got bigger, that's handoff. |

Default reflex when uncertain: scout via `run_subagent` first; most blockers
dissolve once the missing fact arrives.

**Planner — two plan shapes.**

| Plan shape | Use when | Signature |
|---|---|---|
| Full | Every facet covered with HIGH confidence | `PLAN_SHAPE: full`. GAP omitted. |
| Partial | Confident prefix only | `PLAN_SHAPE: partial`. GAP names the unplanned tail and `recommended_next_move`. |

**Hard rule:** a sharp GAP beats a padded full plan.

**Evaluator — four modes.**

| Mode | Terminal | Trigger |
|---|---|---|
| Pass-through success | `submit_task_success` | Goal demonstrably met; ≥1 adversarial probe ran clean; no edits required. |
| Inline-fix-then-success | edits → `submit_task_success` | Trivial gap (≤5 edits, no new file, no test edit, no design call). |
| Recovery handoff | `launch_plan_handoff` | Real progress made but goal not met AND gap is too big for inline fix. Pass DONE summaries as locked-in. |
| Hard fail | `submit_evaluation_failure` | Goal cannot be met: contradictory criteria, missing capability, prior recovery exhausted, critical child failure no recovery repairs. |

**Mandatory before any `submit_task_success`:** at least one adversarial probe
documented in `CHECKS_RUN`. Reading code is not a probe.

**Explorer — one mode.** Always `submit_exploration_result(findings=...)`.
There is no failure terminal — partial answers go in `UNCERTAINTIES`.

### 3.2 Topology palette (planner picks one or composes a hybrid)

| Topology | When to use |
|---|---|
| **fan-out** | Goal is a set of independent fixes; no integrator. |
| **diamond** | N independent changes verified together via a single integrator. |
| **pipeline** | Staged transformations where each stage feeds the next. |
| **map+reduce** | Same operation across N disjoint regions, then aggregate. |
| **spike+gap** | Partial plan: one spike child to discover; GAP names the rest. |
| **probe+gated** | One cheap probe gates a bulk batch. |
| **two-track** | Two parallel stems with different competence; converge at evaluator. |
| **recovery-slice** | Repair only a named gap; lock in prior DONE. |
| **bisect** | Diagnostic: split the suspect set; iterate via recovery. |
| **canary+bulk** | Partial: land one representative; bulk in recovery. |
| **hybrid:<a>+<b>** | Compose any of the above for real long-horizon goals. |
| **custom:<desc>** | Be honest if nothing fits. |

**Recursive principle.** Children can themselves decompose. If a facet is
too large for the current planner to fully understand, assign it as a single
child whose own first move will be `launch_plan_handoff`. Do not flatten
prematurely — let the recursion absorb scope.

---

## 4. Tool-Surface Playbook (executor & evaluator)

### 4.1 Discovery cascade

Use in this order; each step refines the previous step's hits:

1. `ci_workspace_structure` — first time entering a region.
2. `ci_query_symbol` — the right answer when the question names a symbol
   (definition / use / override). Prefer over grep for symbol queries.
3. `glob` — for file-path patterns.
4. `grep` — for textual patterns that are not symbol-shaped.
5. `read_file` — last, after a candidate path is identified.
6. `ci_diagnostics` — after edits, on the file you just changed.

Never `read_file` a directory you haven't constrained with
`glob`/`grep`/`ci_query_symbol`. Reading speculatively burns the call limit.

### 4.2 `run_subagent` — intra-task concurrency

`run_subagent` is `background="always"` — returns immediately with a
`background_task_id`. Three usage patterns:

| Pattern | When |
|---|---|
| Scout-then-decide | Ambiguous input; need facts before mutating. Dispatch 1–3 explorers in parallel → `wait_background_tasks` → re-decide. |
| Scout-during-execution | Need to confirm a downstream call site won't break. Dispatch explorer → continue editing → collect when natural. |
| Spot-check fan-out (evaluator) | Verdict requires checking N independent sites. Dispatch one explorer per site → `wait_background_tasks` → fold findings into VERDICT. |

Anti-patterns: dispatching a scout to read a single file (use `read_file`
directly); recursive scouts (subagents may not call `run_subagent`);
forgetting to collect before terminating.

### 4.3 `shell` — sync vs. backgrounded

| Mode | Use for |
|---|---|
| Foreground | Quick commands (<10s expected): `git status`, single-test pytest, `ls`. |
| Background | Long commands (>10s expected): full test suites, builds, installs. Fire and continue editing; collect with `wait_background_tasks` before terminal. |

Hard rules: `shell` is one-shot (cwd resets per call); no persistent dev
servers; don't background a destructive command if you intend to act on its
result before terminating.

### 4.4 Background-task lifecycle

| Tool | When to call |
|---|---|
| `check_background_task_result(id)` | Cheap status peek; safe to call repeatedly. |
| `wait_background_tasks(timeout)` | Block on ALL outstanding bg tasks. Use before any terminal if anything is running. |
| `cancel_background_task(id, reason)` | When a scout's question is answered another way. |

### 4.5 Mutation tools — least-blast-radius preference

| Tool | Use for |
|---|---|
| `edit_file` | Default for any change to an existing file. |
| `write_file` | Only for new files. Re-writing an existing file is a smell. |
| `move_file` | Renames / relocations. |
| `delete_file` | Confirmed-orphan files only. Don't delete pre-existing dead code unless asked. |

After every mutation cluster, call `ci_diagnostics` on the changed files.

---

## 5. Planner Tool Surface

The planner is an `agent` (not a subagent) with a strictly read-only
investigation surface plus background-task primitives:

| Tool | Purpose |
|---|---|
| `ci_workspace_structure`, `ci_query_symbol`, `ci_diagnostics` | Orientation and symbol resolution. |
| `glob`, `grep`, `read_file` | Standard discovery cascade. |
| `run_subagent` | Dispatch explorers for ambiguous facets. |
| `wait_background_tasks`, `check_background_task_result`, `cancel_background_task` | Collect scout findings. |

The planner does NOT have `shell`, `edit_file`, `write_file`, `delete_file`,
or `move_file`. If a question requires running code, encode it as an
executor child whose VERIFICATION PLAN runs the command — don't try to
verify from reading.

**Scout discipline.** Use scouts for ambiguity that blocks plan shape — not
to fully audit every facet. Children can re-scout their own slice. If a
facet is too big to confidently understand here, that is a signal to
**assign it as a single child** rather than to scout deeper.

**Research and synthesis are the planner's job.** When the goal requires
exploring multiple options, comparing approaches, or pulling findings
together into a direction, the planner runs the scouts and synthesizes
the results in its own context. The planner does NOT spawn executor
children whose deliverable is "investigate X", "compare options", or
"summarize findings" — executors are code-engineering workers and have
no synthesis terminal. Once the planner has chosen a direction, that
direction is encoded directly in the executor TaskSpecs that follow.

---

## 6. Per-Role System Prompts

The strings installed in `backend/src/agents/builtins.py`. They reference the
labeled-heading vocabulary above.

> Each prompt's structure: **Role → Input contract → Operating loop → Tool
> surface → Mode Decision Table → Forbidden actions → Terminal payload
> format**. Same skeleton across roles for predictability.

The prompts intentionally avoid:
- The implementation type name `TaskCenterHarnessGraph` — phrased as
  "planning unit" or "task graph" so the role definitions stay generic.
- "Flattest DAG" framing — the planner's job is a *reasonable* DAG that
  matches the goal's structure. Recursion handles oversized facets.
- Benchmark-specific language. Examples in the prompts are generic
  topology shapes, not project-specific PR lists.
- Repetition. Each rule appears once, in its most relevant section.

To inspect or edit, see `_EXECUTOR_SYSTEM_PROMPT`, `_PLANNER_SYSTEM_PROMPT`,
`_EVALUATOR_SYSTEM_PROMPT`, and `_EXPLORER_SYSTEM_PROMPT` in
`backend/src/agents/builtins.py`.

---

## 7. Quality checklist (apply when reviewing a run trace)

- [ ] **Plan shape declared** (`PLAN_SHAPE: full | partial`).
- [ ] **Topology labeled** (`TOPOLOGY:` matches §3.2 or `hybrid:` / `custom:`).
- [ ] **`tasks` contains only executor children**: no evaluator entries
      (auto-generated by the runtime).
- [ ] **Right-sized children**: each executor's TaskSpec describes one
      focused effort. If a child needs sub-coordination, that's fine — it
      will recurse via `launch_plan_handoff`.
- [ ] **Mid-execution handoffs are honored**: planner's recovery plan
      treats partial diffs as input, not as failure.
- [ ] **Every TaskSpec parses as labeled headings**; GOAL is one sentence;
      ACCEPTANCE CRITERIA are verifiable predicates with commands.
- [ ] **Executor scout pattern**: when inputs were incomplete, executor
      dispatched scouts via `run_subagent` *before* mutating, not after.
- [ ] **Background discipline**: every long shell (>10s) was fired as
      background; `wait_background_tasks` was called before any terminal.
- [ ] **`ci_query_symbol` used at least once** by any agent working on a
      symbol-shaped change (not just `grep`).
- [ ] **No test-file edits** by any executor or evaluator.
- [ ] **Evaluator adversarial probe** present in CHECKS_RUN for every
      `submit_task_success`.
- [ ] **Inline-fix heuristic** respected: ≤5 file edits, no new file, no
      test edit, no design call.
- [ ] **Recovery handoffs** carry a sharp `repair_target` and
      `preserved_state`; planner is not asked to redo locked-in work.
- [ ] **Partial plans** include a non-empty GAP and `recommended_next_move`.

---

## See Also

- `docs/architecture/agent-team-coordination.md` — role boundaries, terminal
  effects, information-flow diagrams.
- `docs/architecture/gan-task-graph-v1.md` — full data model and persistence.
- `docs/architecture/background-tasks-and-subagents.md` — runtime details
  for `run_subagent`, `wait_background_tasks`, etc.
- `backend/src/agents/builtins.py` — the installed system prompts.
- `backend/src/task_center/planning/launch_context.py` — the typed launch
  contexts (PlannerLaunchContext, ExecutorLaunchContext,
  EvaluatorLaunchContext).
