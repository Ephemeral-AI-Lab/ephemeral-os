# Workflow Context Projection - SPEC

Status: Proposed
Date: 2026-06-10
Owner: eos-workflow / eos-tool / eos-engine / eos-db

Scope:
- workflow context entity model
- `spec.md` / `brief.md` projection rendering
- context-file write flow after planner and worker submissions
- generated filesystem layout for workflow, iteration, attempt, plan, and work item context

Companion artifact:
- `docs/plans/workflow_context_projection_renderer.html`

## 1. Intent

The workflow context model should behave like a file system, but the files are not
the durable source of truth. The database owns current workflow state. Context
files are deterministic projections regenerated from a fresh database-loaded
workflow aggregate after lifecycle mutations.

The model is:

```text
delegate_workflow
  -> Workflow
      -> Iteration[]
          -> Attempt[]
              -> Plan
              -> WorkItem[]
```

Every entity can render:

```text
render_spec()  -> spec.md
render_brief() -> brief.md
```

`spec.md` is the fuller projection. `brief.md` is the compact context injection
artifact. A `Success` or `Failed` `brief.md` appends an inline reference to its
own `spec.md` after that brief's local content. `NotStarted` briefs render only
their status line. `Running` briefs may render current content, but do not render
references. References stay where the brief is rendered; the system must not
collect them into a tail references section.

## 2. Core Invariants

1. Database state is authoritative.
2. Context files are overwriteable generated projections.
3. Rendering never reads existing `spec.md` or `brief.md` files.
4. After each mutating tool call, the system commits the database transaction,
   reloads the full workflow aggregate, and renders projections from that fresh
   snapshot.
5. `Workflow`, `Iteration`, `Attempt`, `Plan`, and `WorkItem` each embed a
   shared base value with ID, status, and folder path.
6. `Plan` and `WorkItem` are leaf execution artifacts. Their `brief.md` files
   are prose-like content with no Markdown heading.
7. `Workflow`, `Iteration`, and `Attempt` `brief.md` files are structural
   composition files. They may add headings while composing child brief content.
8. `Attempt::render_spec()` includes the full plan spec and all work item
   briefs.
9. `Attempt::render_brief()` includes the plan brief and only leaf work item
   briefs.
10. `Workflow::render_brief()` does not render `workflow_goal`; it only renders
    workflow status, iteration brief rollups, and terminal reference when
    applicable.
11. `Iteration::render_brief()` does not render `iteration_goal`; it only
    renders iteration status, attempt brief rollups, and terminal reference when
    applicable.
12. `Success` and `Failed` `brief.md` files append their own inline
    `Reference: .../spec.md` below their local content.
13. `NotStarted` `brief.md` files render only `Status: NotStarted`; they do not
    render placeholder prose or references.
14. `Running` `brief.md` files may render current content, but do not render
    references.
15. When a parent file inlines a child `brief.md`, the child reference remains
    in that original child position. References are never collected and moved to
    a tail section.

## 3. Status Model

```rust
enum WorkflowEntityRunStatus {
    NotStarted,
    Running,
    Success,
    Failed,
}
```

Status text is rendered at the top of each projected file:

```md
Status: Running
```

If a plan or work item exists but is still `NotStarted`, its brief renders:

```md
Status: NotStarted
```

## 4. Entity Schema

`WorkflowEntityBase` is an embedded value object, not an inheritance layer.

```rust
struct WorkflowEntityBase<Id> {
    id: Id,
    status: WorkflowEntityRunStatus,
    folder_path: ContextFolderPath,
}
```

Each domain entity embeds the base:

```rust
struct Workflow {
    base: WorkflowEntityBase<WorkflowId>,
    goal: String,
    iterations: Vec<Iteration>,
}

struct Iteration {
    base: WorkflowEntityBase<IterationId>,
    workflow_id: WorkflowId,
    goal: String,
    attempts: Vec<Attempt>,
}

struct Attempt {
    base: WorkflowEntityBase<AttemptId>,
    workflow_id: WorkflowId,
    iteration_id: IterationId,
    plan: Option<Plan>,
    work_items: Vec<WorkItem>,
}

struct Plan {
    base: WorkflowEntityBase<PlanId>,
    workflow_id: WorkflowId,
    iteration_id: IterationId,
    attempt_id: AttemptId,
    plan_spec: Option<String>,
    planner_summary: Option<String>,
    deferred_goal_for_next_iteration: Option<String>,
}

struct WorkItem {
    base: WorkflowEntityBase<WorkItemId>,
    workflow_id: WorkflowId,
    iteration_id: IterationId,
    attempt_id: AttemptId,
    plan_id: PlanId,
    work_item_spec: String,
    needs: Vec<WorkItemId>,
    worker_summary: Option<String>,
    worker_outcome: Option<String>,
}
```

Back-references are intentionally denormalized. They make query tools,
projection writes, and context lookup cheap and unambiguous:

| Entity | Back-references |
| --- | --- |
| `Iteration` | `workflow_id` |
| `Attempt` | `workflow_id`, `iteration_id` |
| `Plan` | `workflow_id`, `iteration_id`, `attempt_id` |
| `WorkItem` | `workflow_id`, `iteration_id`, `attempt_id`, `plan_id` |

## 5. Folder Layout

Paths are workflow-root-relative:

```text
workflow_<workflow_id>/
  spec.md
  brief.md

  iteration_<iteration_id>/
    spec.md
    brief.md

    attempt_<attempt_id>/
      spec.md
      brief.md

      plan_<plan_id>/
        spec.md
        brief.md

      work_item_<work_item_id>/
        spec.md
        brief.md
```

`folder_path` should store this relative path. The physical projector receives a
context root and joins it with `folder_path`.

## 6. Render Contract

Every entity owns its own renderer:

```rust
impl Workflow {
    fn render_spec(&self) -> String;
    fn render_brief(&self) -> String;
}

impl Iteration {
    fn render_spec(&self) -> String;
    fn render_brief(&self) -> String;
}

impl Attempt {
    fn render_spec(&self) -> String;
    fn render_brief(&self) -> String;
}

impl Plan {
    fn render_spec(&self) -> String;
    fn render_brief(&self) -> String;
}

impl WorkItem {
    fn render_spec(&self) -> String;
    fn render_brief(&self) -> String;
}
```

No `WorkflowContextManager`, render policy, or template registry is required for
the first implementation. The aggregate object already contains enough child
state to render the projection. A small file projector may exist, but it only
writes strings returned by entity renderers.

## 7. Render Pseudocode

Helpers:

```rust
fn status_line(status: WorkflowEntityRunStatus) -> String {
    format!("Status: {status}")
}

fn pending_or(value: Option<&str>) -> &str {
    value.unwrap_or("Pending to Run")
}

fn spec_ref(base: &WorkflowEntityBase<impl IdLike>) -> String {
    format!("{}/spec.md", base.folder_path)
}

fn is_terminal(status: WorkflowEntityRunStatus) -> bool {
    matches!(
        status,
        WorkflowEntityRunStatus::Success | WorkflowEntityRunStatus::Failed
    )
}

fn append_reference(md: &mut Markdown, base: &WorkflowEntityBase<impl IdLike>) {
    if is_terminal(base.status) {
        md.line(format!("Reference: {}", spec_ref(base)));
    }
}

fn nest(markdown: String) -> String {
    shift_headings_down_by_one(markdown)
}
```

### 7.1 Workflow

`workflow/spec.md`:

```rust
fn Workflow::render_spec(&self) -> String {
    md.line(status_line(self.base.status));
    md.h1("Workflow Goal");
    md.text(&self.goal);

    for iteration in &self.iterations {
        md.h1(format!("Iteration {}", iteration.base.id));
        md.raw(nest(iteration.render_spec()));
    }

    append_reference(&mut md, &self.base);
    md.finish()
}
```

`workflow/brief.md`:

```rust
fn Workflow::render_brief(&self) -> String {
    if self.base.status == WorkflowEntityRunStatus::NotStarted {
        return status_line(self.base.status);
    }

    md.line(status_line(self.base.status));

    for iteration in &self.iterations {
        md.h1(format!("Iteration {}", iteration.base.id));
        md.raw(nest(iteration.render_brief()));
    }

    append_reference(&mut md, &self.base);
    md.finish()
}
```

### 7.2 Iteration

`iteration/spec.md`:

```rust
fn Iteration::render_spec(&self) -> String {
    md.line(status_line(self.base.status));
    md.h1("Iteration Goal");
    md.text(&self.goal);

    for attempt in &self.attempts {
        md.h1(format!("Attempt {}", attempt.base.id));
        md.raw(nest(attempt.render_spec()));
    }

    md.finish()
}
```

`iteration/brief.md`:

```rust
fn Iteration::render_brief(&self) -> String {
    if self.base.status == WorkflowEntityRunStatus::NotStarted {
        return status_line(self.base.status);
    }

    md.line(status_line(self.base.status));

    for attempt in &self.attempts {
        md.h1(format!("Attempt {}", attempt.base.id));
        md.raw(nest(attempt.render_brief()));
    }

    md.finish()
}
```

### 7.3 Attempt

`attempt/spec.md`:

```rust
fn Attempt::render_spec(&self) -> String {
    if self.base.status == WorkflowEntityRunStatus::NotStarted
        && self.plan.is_none()
        && self.work_items.is_empty()
    {
        return status_line(self.base.status);
    }

    md.line(status_line(self.base.status));

    md.h1("Plan");
    match &self.plan {
        Some(plan) => md.raw(nest(plan.render_spec())),
        None => md.text("Pending to Run"),
    }

    for item in &self.work_items {
        md.h1(format!("Work Item {}", item.base.id));
        md.raw(item.render_brief());
    }

    md.finish()
}
```

`attempt/brief.md`:

```rust
fn Attempt::render_brief(&self) -> String {
    if self.base.status == WorkflowEntityRunStatus::NotStarted {
        return status_line(self.base.status);
    }

    md.line(status_line(self.base.status));

    md.h1("Plan");
    match &self.plan {
        Some(plan) => md.raw(plan.render_brief()),
        None => md.text("Pending to Run"),
    }

    for item in self.leaf_work_items() {
        md.h1(format!("Work Item {}", item.base.id));
        md.raw(item.render_brief());
    }

    append_reference(&mut md, &self.base);
    md.finish()
}
```

Leaf work item detection:

```rust
fn Attempt::leaf_work_items(&self) -> Vec<&WorkItem> {
    self.work_items
        .iter()
        .filter(|candidate| {
            !self.work_items
                .iter()
                .any(|other| other.needs.contains(&candidate.base.id))
        })
        .collect()
}
```

### 7.4 Plan

`plan/spec.md`:

```rust
fn Plan::render_spec(&self) -> String {
    if self.base.status == WorkflowEntityRunStatus::NotStarted
        && self.plan_spec.is_none()
    {
        return status_line(self.base.status);
    }

    md.line(status_line(self.base.status));
    md.h1("Plan Spec");
    md.text(pending_or(self.plan_spec.as_deref()));

    md.h1("Deferred Goal For Next Iteration");
    md.text(self.deferred_goal_for_next_iteration.as_deref().unwrap_or(""));

    md.finish()
}
```

`plan/brief.md`:

```rust
fn Plan::render_brief(&self) -> String {
    if self.base.status == WorkflowEntityRunStatus::NotStarted {
        return status_line(self.base.status);
    }

    md.line(status_line(self.base.status));
    md.text(pending_or(self.planner_summary.as_deref()));
    append_reference(&mut md, &self.base);
    md.finish()
}
```

### 7.5 Work Item

`work_item/spec.md`:

```rust
fn WorkItem::render_spec(&self) -> String {
    md.line(status_line(self.base.status));
    md.h1("Spec");
    md.text(&self.work_item_spec);

    md.h1("Outcome");
    if self.base.status != WorkflowEntityRunStatus::NotStarted {
        md.text(pending_or(self.worker_outcome.as_deref()));
    }

    md.finish()
}
```

`work_item/brief.md`:

```rust
fn WorkItem::render_brief(&self) -> String {
    if self.base.status == WorkflowEntityRunStatus::NotStarted {
        return status_line(self.base.status);
    }

    md.line(status_line(self.base.status));
    md.text(pending_or(self.worker_summary.as_deref()));
    append_reference(&mut md, &self.base);
    md.finish()
}
```

## 8. Mutation And Projection Flow

### 8.1 `delegate_workflow`

```text
begin DB transaction
  create Workflow(status = Running, workflow_goal)
  create Iteration(status = Running, goal = workflow_goal)
  create Attempt(status = NotStarted)
commit version N

load Workflow aggregate from DB at version N
render all spec.md and brief.md projections
write projections to context filesystem
```

### 8.2 Planner Launch

```text
begin DB transaction
  create Plan(status = Running, attempt_id)
  set Attempt.status = Running
commit version N

load Workflow aggregate from DB at version N
render all projections
write projections
```

### 8.3 `submit_plan_outcome`

```text
begin DB transaction
  update Plan:
    status = Success
    plan_spec = input.plan_spec
    planner_summary = input.summary
    deferred_goal_for_next_iteration = input.deferred_goal_for_next_iteration

  create WorkItem[]:
    status = NotStarted
    plan_id = plan.id
    work_item_spec = input.work_item_spec
    needs = input.needs

  keep Attempt.status = Running
commit version N

load Workflow aggregate from DB at version N
render all projections
write projections
```

### 8.4 Worker Launch

```text
begin DB transaction
  set selected WorkItem.status = Running
commit version N

load Workflow aggregate from DB at version N
render all projections
write projections
```

### 8.5 `submit_worker_outcome`

```text
begin DB transaction
  update WorkItem:
    status = Success or Failed
    worker_summary = input.summary
    worker_outcome = input.outcome

  if any WorkItem.status == Failed:
    set Attempt.status = Failed
    create retry Attempt(status = NotStarted)

  else if all WorkItems.status == Success:
    set Attempt.status = Success
    set Iteration.status = Success

    if Plan.deferred_goal_for_next_iteration exists:
      create next Iteration(status = Running, goal = deferred_goal)
      create next Attempt(status = NotStarted)
      keep Workflow.status = Running
    else:
      set Workflow.status = Success

  else:
    keep Attempt.status = Running
commit version N

load Workflow aggregate from DB at version N
render all projections
write projections
```

## 9. Physical Projection Writer

The projection writer should stay boring:

```rust
struct WorkflowContextProjector {
    root: PathBuf,
}

impl WorkflowContextProjector {
    fn project(&self, workflow: &Workflow) -> Result<()> {
        write_file(workflow.base.folder_path.join("spec.md"), workflow.render_spec())?;
        write_file(workflow.base.folder_path.join("brief.md"), workflow.render_brief())?;

        for iteration in &workflow.iterations {
            write_file(iteration.base.folder_path.join("spec.md"), iteration.render_spec())?;
            write_file(iteration.base.folder_path.join("brief.md"), iteration.render_brief())?;

            for attempt in &iteration.attempts {
                write_file(attempt.base.folder_path.join("spec.md"), attempt.render_spec())?;
                write_file(attempt.base.folder_path.join("brief.md"), attempt.render_brief())?;

                if let Some(plan) = &attempt.plan {
                    write_file(plan.base.folder_path.join("spec.md"), plan.render_spec())?;
                    write_file(plan.base.folder_path.join("brief.md"), plan.render_brief())?;
                }

                for item in &attempt.work_items {
                    write_file(item.base.folder_path.join("spec.md"), item.render_spec())?;
                    write_file(item.base.folder_path.join("brief.md"), item.render_brief())?;
                }
            }
        }

        Ok(())
    }
}
```

Writes should use a temp file plus atomic rename. The first implementation can
re-render the whole workflow after each mutation. Dirty-subtree projection can
come later if size becomes a real bottleneck.

## 10. Context Loading Policy

The file projections support context loading; they do not define the full policy.
A conservative default is:

```text
planner context:
  workflow/brief.md
  current iteration/brief.md

worker context:
  current attempt/brief.md
  own work_item/spec.md
  dependency work_item/brief.md or spec.md as needed
```

Escalation happens through context read/search tools:

```text
read_workflow_context(workflow_id, path, line_range?)
search_workflow_context(workflow_id, query, scope?)
```

## 11. Acceptance Criteria

- `delegate_workflow` initializes workflow, first iteration, and first attempt.
- Planner launch creates a running plan under the active attempt.
- `submit_plan_outcome` updates the plan and creates not-started work items.
- Not-started plan/work item briefs render only `Status: NotStarted`.
- Worker launch marks a work item running and reprojects context files.
- `submit_worker_outcome` updates work item summary/outcome and reprojects from a
  fresh workflow aggregate.
- Failed work item submission closes the current attempt as failed and creates a
  retry attempt.
- Successful completion of all work items closes the attempt and iteration.
- Deferred goal creates the next iteration and not-started attempt.
- No deferred goal on the final successful iteration closes the workflow as
  success.
- `attempt/spec.md` renders plan spec plus all work item briefs.
- `attempt/brief.md` renders plan brief plus leaf work item briefs only.
- `workflow/brief.md` does not render `workflow_goal`.
- `iteration/brief.md` does not render `iteration_goal`.
- `Success` and `Failed` `brief.md` files append a `Reference: .../spec.md`
  line below their local content.
- `Running` `brief.md` files do not render references.
- `NotStarted` `brief.md` files do not render placeholder prose or references.
- Inlined child brief references remain in place and are not collected into a
  tail references section.
- Projection writes are deterministic from DB state and never read old projected
  file contents.
