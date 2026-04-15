# Plan: Planning Tools — draft_task_plan + submit_task_plan

## Context

The replanner currently has 3 overlapping posthook tools (`add_tasks`, `cancel_and_redraft`, `declare_blocker`) plus `request_replan` for other agents. This creates unnecessary decision complexity.

This plan **adopts the terminal_tools architecture** from the Hook-Based Agent Lifecycle Redesign (cryptic-seeking-sun.md). No posthooks. Tools write data to the task model, the query loop stops on terminal tools, executor dispatches on task state.

**Goal**: Two planning tools — `draft_task_plan` (preview + validate) and `submit_task_plan` (commit). Plus two new context tools — `read_task_graph` and `read_task_spec`. Unified for planner and replanner.

---

## Design

### 1. Tool surface

| Tool | Terminal? | Available to | Purpose |
|------|-----------|-------------|---------|
| `draft_task_plan` | No | planner, replanner | Validate proposed plan, render ASCII before/after graph, return errors on failure |
| `submit_task_plan` | **Yes** | planner, replanner | Accept JSON of tasks scoped to parent node. Write to task model. Can touch sibling descendants. |
| `declare_blocker` | **Yes** | planner, replanner | Escalate to conductor — signals a blocker that planning cannot resolve |
| `submit_task_summary` | **Yes** | all non-planner roles | Submit success/fail outcome |
| `read_task_graph` | No | all roles | Read current DAG structure (parent's subtree) |
| `read_task_spec` | No | all roles | Read task specifications (own or sibling) |

`declare_blocker` stays — it triggers conductor-level escalation, not executor replan dispatch.
`request_replan` is gone — developer/validator call `submit_task_summary(type="fail")` instead.
`add_tasks` / `cancel_and_redraft` are gone — merged into `submit_task_plan`.

### 2. Terminal tools by role (on `TeamDefinition`)

```python
terminal_tools: dict[str, set[str]] = {
    "planner":    {"submit_task_plan", "declare_blocker"},
    "replanner":  {"submit_task_plan", "declare_blocker"},
    "developer":  {"submit_task_summary"},
    "validator":  {"submit_task_summary"},
    "explorer":   {"submit_task_summary"},
    "scout":      {"submit_task_summary"},
}
```

---

### 3. `draft_task_plan` — preview tool (non-terminal)

Available during main loop. Validates the proposed plan and returns an ASCII before/after graph. Does NOT write to the task model. Does NOT use hashes or caching.

```python
class DraftTaskPlanInput(BaseModel):
    tasks: list[TaskSpec]          # proposed tasks to add
    remove_tasks: list[str] = []   # task IDs to cancel (siblings + their descendants)

class DraftTaskPlanTool(BaseTool):
    name = "draft_task_plan"

    async def execute(self, arguments, context):
        role = get_role(context)
        task = get_task(context)

        # 1. Validate structure (agent names, deps, cycles, budget)
        errors = validate_plan(arguments, context)
        if errors:
            return ToolResult(output=f"Validation failed:\n{errors}", is_error=True)

        # 2. For replanner: scope check — all tasks/removals must be
        #    within parent's subtree (sibling level)
        if role == "replanner":
            scope_errors = validate_sibling_scope(arguments, task.parent_id, context)
            if scope_errors:
                return ToolResult(output=f"Scope violation:\n{scope_errors}", is_error=True)

        # 3. Fetch current sibling state
        current_siblings = await get_siblings(task.parent_id, context)

        # 4. Compute diff
        #    - DONE siblings: always preserved (immutable)
        #    - remove_tasks: cancelled + cascade to descendants
        #    - tasks: new additions
        diff = compute_plan_diff(current_siblings, arguments.tasks, arguments.remove_tasks)

        # 5. Render ASCII before/after
        ascii_before = render_local_graph("BEFORE", current_siblings)
        ascii_after  = render_local_graph("AFTER", diff.projected_siblings)
        ascii_diff   = render_diff_summary(diff)

        # 6. Warnings for destructive actions
        warnings = []
        for t in diff.removed:
            if t.status == "RUNNING":
                warnings.append(f"⚠ {t.id} is RUNNING — will be terminated")
            if t.children:
                warnings.append(f"⚠ {t.id} has {len(t.children)} descendants — will cascade cancel")

        output = f"{ascii_before}\n\n{ascii_after}\n\n{ascii_diff}"
        if warnings:
            output += "\n\nWarnings:\n" + "\n".join(warnings)
        output += "\n\nPlan looks valid. Call submit_task_plan to commit."

        return ToolResult(output=output)
```

**Key**: No hash. No cache. The LLM sees the preview in its conversation, then decides whether to call `submit_task_plan` with the same (or adjusted) arguments. If the graph changed between draft and submit, `submit_task_plan` re-validates and errors if stale.

---

### 4. `submit_task_plan` — commit tool (terminal)

Accepts the full plan JSON scoped to the parent node. Writes to task model. Can cancel siblings and cascade to their descendants.

```python
class SubmitTaskPlanInput(BaseModel):
    tasks: list[TaskSpec]          # tasks to add (scoped to parent's subtree)
    remove_tasks: list[str] = []   # task IDs to cancel (siblings + descendants)

class SubmitTaskPlanTool(BaseTool):
    name = "submit_task_plan"

    async def execute(self, arguments, context):
        role = get_role(context)
        task = get_task(context)

        # 1. Re-validate (same checks as draft_task_plan)
        errors = validate_plan(arguments, context)
        if errors:
            return ToolResult(output=f"Validation failed:\n{errors}", is_error=True)

        if role == "replanner":
            scope_errors = validate_sibling_scope(arguments, task.parent_id, context)
            if scope_errors:
                return ToolResult(output=f"Scope violation:\n{scope_errors}", is_error=True)

        # 2. Build resolved plan
        plan = resolve_plan(arguments, context)

        # 3. Write to task model — executor reads this
        task.resolved_plan = plan

        return ToolResult(output="Plan submitted.")
```

**Planner vs replanner behavior**:
- **Planner**: `tasks` creates the initial plan. `remove_tasks` is typically empty.
- **Replanner**: `tasks` adds to parent's subtree. `remove_tasks` cancels siblings (and recursively cancels their descendants — children, grandchildren, etc.). DONE siblings are immutable and cannot appear in `remove_tasks`.

**Scope rule**: Both `tasks` and `remove_tasks` are scoped to the parent node's immediate children (sibling level), but `remove_tasks` cascades — removing a sibling also cancels all of its descendants.

---

### 5. `read_task_graph` — context tool (non-terminal)

Reads the current DAG structure. Scoped to parent's subtree by default.

```python
class ReadTaskGraphInput(BaseModel):
    scope: Literal["parent", "global"] = "parent"
    include_status: bool = True
    include_deps: bool = True

class ReadTaskGraphTool(BaseTool):
    name = "read_task_graph"

    async def execute(self, arguments, context):
        task = get_task(context)

        if arguments.scope == "parent":
            root = task.parent_id
        else:
            root = None  # full tree

        graph = await fetch_task_graph(root, context)
        ascii = render_local_graph("CURRENT", graph,
                                   show_status=arguments.include_status,
                                   show_deps=arguments.include_deps)
        return ToolResult(output=ascii)
```

Replaces the need for agents to piece together graph state from notes. Gives a clear structural view before planning.

---

### 6. `read_task_spec` — context tool (non-terminal)

Reads task specifications (the briefing text).

```python
class ReadTaskSpecInput(BaseModel):
    scope: Literal["own", "sibling"] = "own"

class ReadTaskSpecTool(BaseTool):
    name = "read_task_spec"

    async def execute(self, arguments, context):
        task = get_task(context)
        if arguments.scope == "own":
            return ToolResult(output=task.task)
        else:
            siblings = await get_siblings(task.parent_id, context)
            specs = [f"[{s.id}] ({s.status}) {s.role}: {s.task}" for s in siblings]
            return ToolResult(output="\n".join(specs))
```

---

### 7. How it flows (replanner example)

```
1. Replanner spawned after developer fails
2. read_task_graph(scope="parent")        → sees current sibling DAG
3. read_task_spec(scope="sibling")        → reads what each sibling was supposed to do
4. read_task_summary(scope="sibling")     → reads outcomes (success/fail)
5. draft_task_plan(tasks=[...], remove_tasks=["task_B"])
   → ASCII before/after + diff + warnings
   → if errors: fix and re-draft
6. submit_task_plan(tasks=[...], remove_tasks=["task_B"])
   → writes to task.resolved_plan
   → query loop exits (terminal tool)
7. Executor reads task.resolved_plan → tc.complete_task()

OR if blocker:
6. declare_blocker(reason="shared dependency broken")
   → query loop exits (terminal tool)
7. Executor escalates to conductor
```

---

### 8. Integration with terminal_tools architecture

This plan inherits the full architecture from cryptic-seeking-sun.md:

- **Query loop**: exits on `terminal_tools` set check (section 1-2 of cryptic-seeking-sun)
- **Runner**: populates `ctx.terminal_tools`, retry loop on TEXT_RESPONSE/RESOURCE_LIMIT (section 6)
- **Executor**: reads `task.resolved_plan` / `task.summary_type`, dispatches (section 7)
- **Task model**: `summary`, `summary_type`, `resolved_plan` fields (section 3)

This plan only specifies the **planning tools** (`draft_task_plan`, `submit_task_plan`, `read_task_graph`, `read_task_spec`). All other tools (`submit_task_summary`, `submit_task_note`, `read_task_note`, `read_task_summary`) and the execution architecture are defined in cryptic-seeking-sun.md.

---

## Files to modify

| File | Change |
|------|--------|
| `tools/submission/toolkit.py` | New: `DraftTaskPlanTool`, `SubmitTaskPlanTool`. Shared validation helpers: `validate_plan`, `validate_sibling_scope`, `compute_plan_diff`, `render_local_graph`, `render_diff_summary`. |
| `tools/context/toolkit.py` | New: `ReadTaskGraphTool`, `ReadTaskSpecTool`. |
| `team/models.py` | Add `resolved_plan` field to Task (if not already present). Add `terminal_tools` to `TeamDefinition`. |
| Agent definitions | Planner/replanner configs: add `draft_task_plan` to available tools. Remove `add_tasks`, `cancel_and_redraft`, `declare_blocker` references. |
| Replanner playbook | Rewrite workflow: `read_task_graph` → `read_task_spec` → `draft_task_plan` → `submit_task_plan`. Remove references to old tools. |

## Deletions

| What | Why |
|------|-----|
| `AddTasksTool` | Merged into `submit_task_plan(tasks=...)` |
| `CancelAndRedraftTool` | Merged into `submit_task_plan(remove_tasks=...)` |
| `RequestReplanTool` | Replaced by `submit_task_summary(type="fail")` for non-planner roles |
| `SubmitReplanInput` (old) | Replaced by `SubmitTaskPlanInput` |
| `_accept_replan_submission` | Logic absorbed into `SubmitTaskPlanTool` |
| `PosthookTools.from_context()` | Gone — tools registered in main toolkit |

## What does NOT change

- `task_center.py` — `complete_task()`, `apply_replan()` unchanged
- `planning/expander.py` — `apply_replan()` unchanged
- `persistence/task_store.py` — `apply_replan_atomic()` unchanged
- `tool_execution.py` — PRE/POST hooks untouched
- `hooks/` — no changes

## Verification

1. **Unit**: `draft_task_plan` returns ASCII graph + errors on invalid plan
2. **Unit**: `submit_task_plan` re-validates, writes `resolved_plan`, errors on scope violation
3. **Unit**: `remove_tasks` cascades to descendants
4. **Unit**: `read_task_graph` renders correct ASCII for parent scope
5. **Integration**: full replanner flow: `read_task_graph` → `draft_task_plan` → `submit_task_plan` → task expansion
6. **Integration**: replanner blocker path: `submit_task_summary(type="fail")` → replan dispatch
7. **Regression**: F2P >= 91%, P2P stable
