# Optimized `user_msg_1` and `user_msg_2` Structures

Proposed simplification of the initial messages each agent receives, per launch
position covered in this directory.

- **user_msg_1** — `<context>` envelope around the rendered packet.
- **user_msg_2** — `<Task Guidance>` envelope (or programmatic equivalent for advisor / resolver / explorer).

Content is elided as `...`; only structure / prose-shape changes.

## Scope

The body of `<goal>` (the user's request — `<workspace_root>`, `<pr_description>`, etc.) is **out of scope** for `user_msg_1`. It is treated as opaque payload from the caller.

## Tag dictionary (canonical labels)

Single source of truth for what each tag means. Every "What's in context" bullet draws its label verbatim from this table, so any two cases that reference the same `(tag, semantic-attribute-set)` produce byte-identical bullets. The dictionary lives in code as `TAG_DICTIONARY: list[TagDescriptor]` (proposed location: `backend/src/task_center/context_engine/tag_dictionary.py`). The agent learns the canonical labels once via its role contract / skill.

| `(tag, semantic-attribute-set)` | Canonical label |
|---|---|
| `<goal>` | user's request |
| `<entry_request>` | root delegation envelope |
| `<iteration status="prior">` | previous iteration's work |
| `<iteration status="current">` | active iteration |
| `<iteration_goal>` | active iteration's scope |
| `<accepted_plan>` | prior iteration's accepted plan |
| `<summary>` | prior iteration's summary |
| `<attempt status="prior" verdict="fail">` | failed prior attempt |
| `<attempt status="current">` | active attempt |
| `<plan_spec>` | attempt's plan |
| `<deferred_goal_for_next_iteration>` | scope handed to next iteration |
| `<status_summary>` | generator outcomes summary |
| `<task>` | generator task outcome |
| `<evaluation_criteria>` | criteria the attempt must satisfy |
| `<evaluator_summary>` | evaluator's commentary |
| `<failed_criteria>` | criteria that failed |
| `<assigned_task>` | your assigned task |
| `<dependency>` | upstream task output |

**Semantic vs identity attributes.** Only `status` and `verdict` are *semantic* — they change what a tag means. `iteration_no`, `attempt_no`, `task_id`, `id` are *identity* — they distinguish instances but don't change the label. The dictionary keys on semantic attributes only.

## user_msg_1 — summary of changes

- `<goal_current_iteration>` and `<goal>` → unified as `<goal>`
- `<attempt_plan>` wrapper removed; `<plan_spec>` promoted to parent. `<deferred_goal_for_next_iteration>` is dropped from executor user_msg_1 entirely (planner/evaluator concern); it stays in planner/evaluator views when present
- `<generator_outcomes>` wrapper removed; `<status_summary>` and `<task>` promoted under `<attempt>`
- `<completed_tasks>` wrapper removed; `<task>` elements promoted
- `<dependency_results>` wrapper removed; `<dependency>` elements promoted
- `<evaluator_judgment status="ran" verdict="fail">` collapsed; `verdict` promoted onto `<attempt>`, children inlined
- `<attempt>` is **always** nested under `<iteration>` (structural invariant); evaluator and planner share the same shape
- `<iteration status="current">` is always present (even on iter1 attempt1 when no `<attempt>` blocks exist yet) and always carries its own `<iteration_goal>`; for iteration 1 it is identical to the top-level `<goal>` and rendered as `<iteration_goal>(identical to &lt;goal&gt;)</iteration_goal>`
- `<attempt>` uses the same `status="prior"|"current"` vocabulary as `<iteration>`; failed prior attempts carry `verdict="fail"`. Evaluator at iter1 attempt2 sees attempt 1 as `status="prior"` alongside attempt 2 as `status="current"` inside the same `<iteration>`

**Maximum depth** drops from 5 (`context > iteration > attempt > evaluator_judgment > failed_criteria`) to 4.

## user_msg_2 — summary of changes

Restructured to two terse labeled sections plus a registry-driven terminal block:

- **What's in context** — deterministic outline of the resolved `<context>` tree (algorithm below).
- **What to do** — a single directive per agent, drawn verbatim from `ROLE_DIRECTIVES` (table below).
- **`<terminal_tool_selection>`** — rendered verbatim by `render_terminal_catalog(..., focus="selection_guidance")` from [`backend/src/tools/_terminals/registry.py`](../../../backend/src/tools/_terminals/registry.py).

**Role line removed.** Situational nuance is fully encoded by `<context>` shape (presence of `<attempt status="prior" verdict="fail">`, presence of `<iteration status="prior">`, etc.); the agent reads user_msg_1 to know its situation. A hand-authored Role line duplicates information already present and introduces a drift surface no other section has.

### Role directives (`ROLE_DIRECTIVES`)

Proposed location: `backend/src/task_center/context_engine/role_directives.py`.

| Agent | What to do |
|-------|------------|
| `planner`                  | Plan for `<iteration_goal>`. |
| `executor`                 | Complete `<assigned_task>`. |
| `evaluator`                | Verify the current attempt against `<evaluation_criteria>`. |
| `advisor`                  | Review the parent's pending terminal call. |
| `resolver`                 | Resolve the issues listed in `<issues>`. |
| `explorer`                 | Investigate the parent's question and return concrete findings. |

### "What's in context" algorithm

Deterministic walk of the resolved `<context>` tree. No per-case authoring; the bullet list is a function of the tree.

1. Walk direct children of `<context>` in document order.
2. For each child, look up its `(tag, semantic-attribute-set)` in `TAG_DICTIONARY` → emit `- <tag attrs> — canonical label` as a bullet. Identity attributes (`iteration_no`, `attempt_no`, `task_id`, `id`) are not surfaced.
3. Consecutive siblings matching the same `TagDescriptor` collapse into one bullet.
4. For each child whose tag is in `RECURSE_THROUGH = frozenset({"iteration"})`, recurse one level: emit nested bullets (indented by two spaces) for its children using the same algorithm. Max recursion depth = 2.
5. `<attempt>` is NOT in `RECURSE_THROUGH`. Its body details (`<plan_spec>`, `<task>`, `<evaluation_criteria>`, `<failed_criteria>`, etc.) stay in the XML body and are not duplicated as bullets. The agent reads them from user_msg_1.

Result: an outline mirroring the `<context>` tree's significant structure, capped at two levels.

### Implementation sketch

```python
# context_engine/tag_dictionary.py
class TagDescriptor(BaseModel):
    tag: str
    attr_filter: dict[str, str] | None  # None matches any attrs
    label: str

TAG_DICTIONARY: list[TagDescriptor] = [
    TagDescriptor(tag="goal",          attr_filter=None,                                  label="user's request"),
    TagDescriptor(tag="iteration",     attr_filter={"status": "prior"},                   label="previous iteration's work"),
    TagDescriptor(tag="iteration",     attr_filter={"status": "current"},                 label="active iteration"),
    TagDescriptor(tag="iteration_goal",attr_filter=None,                                  label="active iteration's scope"),
    TagDescriptor(tag="attempt",       attr_filter={"status": "prior", "verdict": "fail"},label="failed prior attempt"),
    TagDescriptor(tag="attempt",       attr_filter={"status": "current"},                 label="active attempt"),
    TagDescriptor(tag="plan_spec",     attr_filter=None,                                  label="attempt's plan"),
    TagDescriptor(tag="assigned_task", attr_filter=None,                                  label="your assigned task"),
    TagDescriptor(tag="dependency",    attr_filter=None,                                  label="upstream task output"),
    # ...
]

RECURSE_THROUGH: frozenset[str] = frozenset({"iteration"})

# context_engine/renderer.py
def render_what_in_context(context_root: XmlElement, max_depth: int = 2) -> str:
    """Walk direct children of <context>; emit bulleted outline.
    Recurse through tags in RECURSE_THROUGH up to max_depth.
    Consecutive same-TagDescriptor siblings collapse to one bullet."""

# task_guidance/builders.py
def build_task_guidance(role: str, context_root: XmlElement, terminals: list[str]) -> str:
    return "\n".join([
        "<Task Guidance>",
        "What's in context:\n" + render_what_in_context(context_root),
        "",
        "What to do:",
        "- " + ROLE_DIRECTIVES[role],
        "",
        "<terminal_tool_selection>",
        render_terminal_catalog(terminals, focus="selection_guidance"),
        "</terminal_tool_selection>",
        "</Task Guidance>",
    ])
```

### Extension model

- Add a new tag → one row in `TAG_DICTIONARY`.
- Add a new role → one row in `ROLE_DIRECTIVES`.
- Add a new launch variant → recipe composes existing children; no new bullets to author.
- Rename a canonical label → one registry edit updates every case.

### Where the detailed playbook lives

- **Role contract (system prompt)** — invariants and hard contracts: terminal tool catalog, submission schemas, authority rules ("use `<evaluation_criteria>` as authority, not your preferences").
- **Skill** (loaded via `Load skill: <role>` as user_msg_3 — already in place for planner; extended to executor and evaluator) — strategic heuristics: "one criterion per item for list-shaped goals", "after failure, diagnose first", "deferred is the next iteration's whole scope, not a backlog dump", "treat `<dependency>` outputs as fixed inputs".
- **`user_msg_2 Task Guidance`** — only situational framing built from the registries: the deterministic context outline plus the one-line directive.

Rationale: contracts and heuristics are different artifacts. Skills support per-variant playbook injection (after-failure heuristics only on retry attempts) where system prompts are static. The pipeline already has the skill slot for planner; extending it is symmetric.

---

## Case 02 — planner, iter1 attempt1 (fresh, no failed attempts)

### user_msg_1

```xml
<context>
  <goal>
    <workspace_root>/testbed</workspace_root>
    <pr_description>...</pr_description>
  </goal>
  <iteration iteration_no="1" status="current">
    <iteration_goal>(identical to &lt;goal&gt;)</iteration_goal>
  </iteration>
</context>
```

### user_msg_2

```
<Task Guidance>
What's in context:
- <goal> — user's request
- <iteration status="current"> — active iteration
  - <iteration_goal> — active iteration's scope

What to do:
- Plan for <iteration_goal>.

<terminal_tool_selection>
- `submit_plan_closes_goal` — Call when this attempt's tasks fully cover the current `<iteration_goal>`. On evaluator PASS, the iteration closes terminally and the goal can succeed.

- `submit_plan_defers_goal` — Call when this attempt delivers a complete, coherent, bounded slice of the current `<iteration_goal>` and a clear remainder exists. The `deferred_goal_for_next_iteration` is the next iteration's whole scope, not a backlog dump.
</terminal_tool_selection>
</Task Guidance>
```

---

## Case 03 / 13 — planner, iter1 attempt2 (after evaluator failure)

### user_msg_1

```xml
<context>
  <goal>
    <workspace_root>/testbed</workspace_root>
    <pr_description>...</pr_description>
  </goal>
  <iteration iteration_no="1" status="current">
    <iteration_goal>(identical to &lt;goal&gt;)</iteration_goal>
    <attempt attempt_no="1" status="prior" verdict="fail">
      <plan_spec>...</plan_spec>
      <status_summary>...</status_summary>
      <task id="...:gen:preflight" status="done">...</task>
      <evaluation_criteria>...</evaluation_criteria>
      <evaluator_summary>...</evaluator_summary>
      <failed_criteria>...</failed_criteria>
    </attempt>
  </iteration>
</context>
```

Drops `<attempt_plan>`, `<generator_outcomes>`, `<evaluator_judgment>`. `<iteration>` retained as `<attempt>`'s parent.

### user_msg_2

```
<Task Guidance>
What's in context:
- <goal> — user's request
- <iteration status="current"> — active iteration
  - <iteration_goal> — active iteration's scope
  - <attempt status="prior" verdict="fail"> — failed prior attempt

What to do:
- Plan for <iteration_goal>.

<terminal_tool_selection>
- `submit_plan_closes_goal` — Call when this attempt's tasks fully cover the current `<iteration_goal>`. On evaluator PASS, the iteration closes terminally and the goal can succeed.

- `submit_plan_defers_goal` — Call when this attempt delivers a complete, coherent, bounded slice of the current `<iteration_goal>` and a clear remainder exists. The `deferred_goal_for_next_iteration` is the next iteration's whole scope, not a backlog dump.
</terminal_tool_selection>
</Task Guidance>
```

---

## Case 04 — planner, iter2 attempt1 (deferred-goal follow-up)

### user_msg_1

```xml
<context>
  <goal>
    <workspace_root>/testbed</workspace_root>
    <pr_description>...</pr_description>
  </goal>
  <iteration iteration_no="1" status="prior">
    <accepted_plan>...</accepted_plan>
    <summary>...</summary>
  </iteration>
  <iteration iteration_no="2" status="current">
    <iteration_goal>...</iteration_goal>
  </iteration>
</context>
```

### user_msg_2

```
<Task Guidance>
What's in context:
- <goal> — user's request
- <iteration status="prior"> — previous iteration's work
  - <accepted_plan> — prior iteration's accepted plan
  - <summary> — prior iteration's summary
- <iteration status="current"> — active iteration
  - <iteration_goal> — active iteration's scope

What to do:
- Plan for <iteration_goal>.

<terminal_tool_selection>
- `submit_plan_closes_goal` — Call when this attempt's tasks fully cover the current `<iteration_goal>`. On evaluator PASS, the iteration closes terminally and the goal can succeed.

- `submit_plan_defers_goal` — Call when this attempt delivers a complete, coherent, bounded slice of the current `<iteration_goal>` and a clear remainder exists. The `deferred_goal_for_next_iteration` is the next iteration's whole scope, not a backlog dump.
</terminal_tool_selection>
</Task Guidance>
```

---

## Case 05 / 06 — executor, no-deps (single-task variants)

Cases 05 and 06 share identical user_msg_1 / user_msg_2 shape; they differ only in payload content.

### user_msg_1

```xml
<context>
  <plan_spec>...</plan_spec>
  <assigned_task task_id="...:gen:preflight">...</assigned_task>
</context>
```

### user_msg_2

```
<Task Guidance>
What's in context:
- <plan_spec> — attempt's plan
- <assigned_task> — your assigned task

What to do:
- Complete <assigned_task>.

<terminal_tool_selection>
- `submit_execution_handoff` — Call when bounded progress is made but further work is needed. Name the next bounded slice; do not kick the problem downstream without specifying what's needed.

- `submit_execution_success` — Call when the `<assigned_task>` deliverable is complete, exists at the claimed location, satisfies the task specification, and any verification the criteria specify has been run and passed.
</terminal_tool_selection>
</Task Guidance>
```

---

## Case 07 — evaluator, iter1 attempt2 (attempt with deferral)

### user_msg_1

```xml
<context>
  <goal>
    <workspace_root>/testbed</workspace_root>
    <pr_description>...</pr_description>
  </goal>
  <iteration iteration_no="1" status="current">
    <iteration_goal>(identical to &lt;goal&gt;)</iteration_goal>
    <attempt attempt_no="1" status="prior" verdict="fail">
      <plan_spec>...</plan_spec>
      <status_summary>...</status_summary>
      <task id="...:gen:preflight" status="done">...</task>
      <evaluation_criteria>...</evaluation_criteria>
      <evaluator_summary>...</evaluator_summary>
      <failed_criteria>...</failed_criteria>
    </attempt>
    <attempt attempt_no="2" status="current">
      <plan_spec>...</plan_spec>
      <deferred_goal_for_next_iteration>...</deferred_goal_for_next_iteration>
      <task id="...:gen:preflight" status="done">...</task>
      <evaluation_criteria>...</evaluation_criteria>
    </attempt>
  </iteration>
</context>
```

### user_msg_2

```
<Task Guidance>
What's in context:
- <goal> — user's request
- <iteration status="current"> — active iteration
  - <iteration_goal> — active iteration's scope
  - <attempt status="prior" verdict="fail"> — failed prior attempt
  - <attempt status="current"> — active attempt

What to do:
- Verify the current attempt against <evaluation_criteria>.

<terminal_tool_selection>
- `submit_evaluation_success` — Call when every entry in `<evaluation_criteria>` is satisfied; the attempt closes successfully and the planner's submission kind determines whether the goal closes or continues.

- `submit_evaluation_failure` — Call when one or more entries in `<evaluation_criteria>` fail. The graph enters retry or failure handling.
</terminal_tool_selection>
</Task Guidance>
```

---

## Case 08 — evaluator, iter2 attempt1 (complete attempt)

### user_msg_1

```xml
<context>
  <goal>
    <workspace_root>/testbed</workspace_root>
    <pr_description>...</pr_description>
  </goal>
  <iteration iteration_no="1" status="prior">
    <accepted_plan>...</accepted_plan>
    <summary>...</summary>
  </iteration>
  <iteration iteration_no="2" status="current">
    <iteration_goal>...</iteration_goal>
    <attempt attempt_no="1" status="current">
      <plan_spec>...</plan_spec>
      <task id="...:gen:preflight" status="done">...</task>
      <evaluation_criteria>...</evaluation_criteria>
    </attempt>
  </iteration>
</context>
```

### user_msg_2

```
<Task Guidance>
What's in context:
- <goal> — user's request
- <iteration status="prior"> — previous iteration's work
  - <accepted_plan> — prior iteration's accepted plan
  - <summary> — prior iteration's summary
- <iteration status="current"> — active iteration
  - <iteration_goal> — active iteration's scope
  - <attempt status="current"> — active attempt

What to do:
- Verify the current attempt against <evaluation_criteria>.

<terminal_tool_selection>
- `submit_evaluation_success` — Call when every entry in `<evaluation_criteria>` is satisfied; the attempt closes successfully and the planner's submission kind determines whether the goal closes or continues.

- `submit_evaluation_failure` — Call when one or more entries in `<evaluation_criteria>` fail. The graph enters retry or failure handling.
</terminal_tool_selection>
</Task Guidance>
```

---

## Case 09 — advisor (invoked by executor pre-submission)

Programmatic — not a `<Task Guidance>` envelope.

### user_msg_2

```
# What's in context
- Parent's original user_msg_1 and transcript (above)
- Pending submission — tool name + arguments
- Terminal catalog — the parent's submission options, with review focus for each

# What to do
- Review the parent's pending terminal call.

## Review dimensions
1. Tool selection — using the parent's context, original task, and transcript as evidence, did the parent pick the right terminal? If not, name the right one.
2. Payload quality — does the transcript actually support the payload's claims? Flag stubs, TODOs, unverified assertions, missed acceptance criteria. Quote transcript lines or contract fragments.

## Calibration
Lenient approve bar: approve when the tool choice is right and the payload is plausibly supported, even if the work isn't pristine. Reject only on real quality problems — wrong terminal, unsupported claims, missing deliverable. If a prior reject is visible in the transcript and the parent ignored it, sharpen the second reject.

## Submit
Call `submit_advisor_feedback` once with:
- verdict: "approve" | "reject"
- summary covering, in order:
  1. Tool selection — "correct" or "should be <other_tool>" with a one-sentence rationale.
  2. Payload backing — what's solid, what's thin or unsupported, with quotes.
  3. Residual risks — issues to weigh on approve, or the single most important fix on reject. "None" if none.
```

---

## Case 10 — resolver (invoked by verifier/evaluator on issues)

Programmatic.

### user_msg_2

```
# What's in context
- Parent transcript above — failing tool calls and context
- <issues> — verifier/evaluator observations

# What to do
- Resolve the issues listed in <issues>.

## How to submit
- Read each issue, consult the transcript, edit files to resolve every issue.
- Summarize what you changed and which issues you resolved.
- Call `submit_resolver_result`.
```

---

## Case 11 — explorer subagent (invoked via `run_subagent`)

Programmatic.

### user_msg_2

```
# What's in context
- Parent's user message above

# What to do
- Investigate the parent's question and return concrete findings.

## Deliver
- File paths, line numbers, specific symbols. No vague hand-waves.
- Missing context the parent will need to act on the findings.
- Obvious areas you skipped.

## Submit
Call `submit_exploration_result`.
```

---

## Case 12 — planner, child goal (delegated from deferring parent)

### user_msg_1

```xml
<context>
  <goal>Resolve the delegated child goal ...</goal>
</context>
```

### user_msg_2

```
<Task Guidance>
What's in context:
- <goal> — user's request

What to do:
- Plan for <iteration_goal>. No defer option — must close in one attempt.

<terminal_tool_selection>
- `submit_plan_closes_goal` — Call when this attempt's tasks fully cover the current `<iteration_goal>`. On evaluator PASS, the iteration closes terminally and the goal can succeed.
</terminal_tool_selection>
</Task Guidance>
```

---

## Case 14 — executor, `has_deps=True` branch

### user_msg_1

```xml
<context>
  <plan_spec>...</plan_spec>
  <dependency id="...:gen:a">...</dependency>
  <assigned_task task_id="...:gen:b">...</assigned_task>
</context>
```

`<dependency_results>` wrapper dropped; multiple `<dependency>` siblings used directly.

### user_msg_2

```
<Task Guidance>
What's in context:
- <plan_spec> — attempt's plan
- <dependency> — upstream task output
- <assigned_task> — your assigned task

What to do:
- Complete <assigned_task>.

<terminal_tool_selection>
- `submit_execution_handoff` — Call when bounded progress is made but further work is needed. Name the next bounded slice; do not kick the problem downstream without specifying what's needed.

- `submit_execution_success` — Call when the `<assigned_task>` deliverable is complete, exists at the claimed location, satisfies the task specification, and any verification the criteria specify has been run and passed.
</terminal_tool_selection>
</Task Guidance>
```

---

## Case 15 — evaluator, iter1 attempt1 (proceeds to `submit_evaluation_failure`)

### user_msg_1

```xml
<context>
  <goal>
    <workspace_root>/testbed</workspace_root>
    <pr_description>...</pr_description>
  </goal>
  <iteration iteration_no="1" status="current">
    <iteration_goal>(identical to &lt;goal&gt;)</iteration_goal>
    <attempt attempt_no="1" status="current">
      <plan_spec>...</plan_spec>
      <task id="...:gen:preflight" status="done">...</task>
      <evaluation_criteria>...</evaluation_criteria>
    </attempt>
  </iteration>
</context>
```

### user_msg_2

```
<Task Guidance>
What's in context:
- <goal> — user's request
- <iteration status="current"> — active iteration
  - <iteration_goal> — active iteration's scope
  - <attempt status="current"> — active attempt

What to do:
- Verify the current attempt against <evaluation_criteria>.

<terminal_tool_selection>
- `submit_evaluation_success` — Call when every entry in `<evaluation_criteria>` is satisfied; the attempt closes successfully and the planner's submission kind determines whether the goal closes or continues.

- `submit_evaluation_failure` — Call when one or more entries in `<evaluation_criteria>` fail. The graph enters retry or failure handling.
</terminal_tool_selection>
</Task Guidance>
```
