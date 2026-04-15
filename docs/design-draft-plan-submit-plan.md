# Plan: Planning Tools — draft_task_plan + submit_task_plan

## Context

The replanner currently has 3 overlapping posthook tools (`add_tasks`, `cancel_and_redraft`, `declare_blocker`) plus `request_replan` for other agents. This creates unnecessary decision complexity.

This plan **adopts the terminal_tools architecture** from the Hook-Based Agent Lifecycle Redesign (cryptic-seeking-sun.md). No posthooks. Tools write data to the task model, the query loop stops on terminal tools, executor dispatches on task state.

**Goal**: Two planning tools — `draft_task_plan` (preview + validate) and `submit_task_plan` (commit). Plus one new context tool — `read_task_graph`. Unified for planner and replanner.

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
class ExistingTaskRef(BaseModel):
    """Reference to a task already in the graph. Only deps can be rewired."""
    id: str                        # must match an existing task ID
    deps: list[str] = []           # updated dependency list

class NewTaskSpec(BaseModel):
    """Full spec for a task the agent is creating."""
    id: str                        # unique ID for the new task
    name: str                      # agent name or role hint (e.g. 'developer', 'validator')
    objective: str                 # prose instruction — the agent's sole briefing
    deps: list[str] = []           # task IDs this depends on
    scope_paths: list[str] = []    # file/dir hints for OCC and note scoping

class DraftTaskPlanInput(BaseModel):
    existing_tasks: list[ExistingTaskRef] = []  # existing tasks to keep/rewire deps
    new_tasks: list[NewTaskSpec] = []            # new tasks to create
    remove_tasks: list[str] = []                 # task IDs to cancel (siblings + descendants)

class DraftTaskPlanTool(BaseTool):
    name = "draft_task_plan"

    async def execute(self, arguments, context):
        role = get_role(context)
        task = get_task(context)

        # 1. Validate existing_tasks IDs exist in graph
        graph_ids = await get_task_ids(task.parent_id, context)
        for ref in arguments.existing_tasks:
            if ref.id not in graph_ids:
                return ToolResult(output=f"Error: existing task '{ref.id}' not found in graph", is_error=True)

        # 2. Validate new_tasks IDs don't collide with existing
        for spec in arguments.new_tasks:
            if spec.id in graph_ids:
                return ToolResult(output=f"Error: new task '{spec.id}' collides with existing task", is_error=True)

        # 3. Validate structure (agent names, deps, cycles, budget)
        errors = validate_plan(arguments, context)
        if errors:
            return ToolResult(output=f"Validation failed:\n{errors}", is_error=True)

        # 4. For replanner: scope check — all tasks/removals within parent's subtree
        if role == "replanner":
            scope_errors = validate_sibling_scope(arguments, task.parent_id, context)
            if scope_errors:
                return ToolResult(output=f"Scope violation:\n{scope_errors}", is_error=True)

        # 5. Fetch current sibling state + compute diff
        current_siblings = await get_siblings(task.parent_id, context)
        diff = compute_plan_diff(current_siblings, arguments)

        # 6. Render ASCII before/after
        ascii_before = render_local_graph("BEFORE", current_siblings)
        ascii_after  = render_local_graph("AFTER", diff.projected_siblings)
        ascii_diff   = render_diff_summary(diff)

        # 7. Warnings for destructive actions
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

**Input schema rationale**: Existing tasks only need `{id, deps}` — no point re-specifying agent/objective/scope for what's already in the graph. New tasks need the full spec. The tool validates: existing IDs must be in the graph, new IDs must not collide. To modify an existing task's agent or objective, cancel + recreate.

---

### 4. `submit_task_plan` — commit tool (terminal)

Accepts the full plan JSON scoped to the parent node. Writes to task model. Can cancel siblings and cascade to their descendants.

```python
class SubmitTaskPlanInput(BaseModel):
    """Same schema as DraftTaskPlanInput."""
    existing_tasks: list[ExistingTaskRef] = []  # existing tasks to keep/rewire deps
    new_tasks: list[NewTaskSpec] = []            # new tasks to create
    remove_tasks: list[str] = []                 # task IDs to cancel (siblings + descendants)

class SubmitTaskPlanTool(BaseTool):
    name = "submit_task_plan"

    async def execute(self, arguments, context):
        role = get_role(context)
        task = get_task(context)

        # 1. Re-validate (same checks as draft_task_plan)
        #    - existing_tasks IDs must exist, new_tasks IDs must not collide
        #    - structure, deps, cycles, budget
        #    - replanner scope check
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
- **Planner**: `new_tasks` creates the initial plan. `existing_tasks` and `remove_tasks` are typically empty.
- **Replanner**: `existing_tasks` keeps/rewires deps on existing siblings. `new_tasks` adds corrective tasks. `remove_tasks` cancels siblings (and recursively cancels their descendants — children, grandchildren, etc.). DONE siblings are immutable and cannot appear in `remove_tasks`.

**Scope rule**: All three lists are scoped to the parent node's immediate children (sibling level), but `remove_tasks` cascades — removing a sibling also cancels all of its descendants.

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

### 6. How it flows (replanner example)

```
1. Replanner spawned after developer fails
2. read_task_graph(scope="parent")        → sees current sibling DAG + task objectives
3. read_task_details(scope="sibling")     → reads outcomes (success/fail) and notes
4. draft_task_plan(existing_tasks=[...], new_tasks=[...], remove_tasks=["task_B"])
   → ASCII before/after + diff + warnings
   → if errors: fix and re-draft
5. submit_task_plan(existing_tasks=[...], new_tasks=[...], remove_tasks=["task_B"])
   → writes to task.resolved_plan
   → query loop exits (terminal tool)
6. Executor reads task.resolved_plan → tc.complete_task()

OR if blocker:
6. declare_blocker(reason="shared dependency broken")
   → query loop exits (terminal tool)
7. Executor escalates to conductor
```

---

### 7. Integration with terminal_tools architecture

This plan inherits the full architecture from cryptic-seeking-sun.md:

- **Query loop**: exits on `terminal_tools` set check (section 1-2 of cryptic-seeking-sun)
- **Runner**: populates `ctx.terminal_tools`, retry loop on TEXT_RESPONSE/RESOURCE_LIMIT (section 6)
- **Executor**: reads `task.resolved_plan` / `task.summary_type`, dispatches (section 7)
- **Task model**: `summary`, `summary_type`, `resolved_plan` fields (section 3)

This plan only specifies the **planning tools** (`draft_task_plan`, `submit_task_plan`, `read_task_graph`). All other tools (`submit_task_summary`, `submit_task_note`, `read_task_note`, `read_task_details`) and the execution architecture are defined in cryptic-seeking-sun.md.

---

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
5. **Integration**: full replanner flow: `read_task_graph` → `read_task_details` → `draft_task_plan` → `submit_task_plan` → task expansion
6. **Integration**: replanner blocker path: `submit_task_summary(type="fail")` → replan dispatch
7. **Regression**: F2P >= 91%, P2P stable
