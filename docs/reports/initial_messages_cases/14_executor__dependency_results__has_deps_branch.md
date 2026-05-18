# executor — dependency_results branch (generator task `b`, deps: [`a`]); user_msg_1 carries a real `<dependency_results>` block
- source: `pipeline.dependency_dag_serial/20260518T222944Z_161ea35f4f33/goal_01_00231bb3-13ea-4251-a4a2-d7a62913f94f/iteration_01_b057915e-aeff-4d37-9c8c-b3a85ed9fab5/attempt_01_fc712cba-f792-4e50-a97f-c09569457528/03_executor_fc712cba-f792-4e50-a97f-c09569457528:gen:b/message.jsonl`
- notes: Closes Gap 3 in the original gap report. The scenario submits a serial DAG `a → b → c`; task `b` runs with `deps=["a"]`, so its composer renders the `<dependency_results>` group (one `<dependency id=...>` child per upstream task) between `<attempt_plan>` and `<assigned_task>`. Row 3's `<Task Guidance>` is the `has_deps=True` branch of `build_generator_task_guidance`, opening with "You are executing one generator task with one or more dependency outputs already available…". This is the variant the existing initial_messages scenario could not exercise because its plans only have single-task DAGs.

## system

```
# Main-Agent Operating Contract

Your context arrives as XML-tagged blocks (`<goal>`, `<goal_current_iteration>`, `<iteration status="prior">`, `<iteration status="current">` with its `<iteration_goal>` and `<attempt status="failed">` children, `<attempt_plan>`, `<assigned_task>`, `<dependency_results>`, `<evaluation_criteria>`); treat them as the bounded contract for this run. Use only what they contain — do not invent goals, criteria, or constraints they did not state — and when a later block narrows an earlier one, the narrowed scope wins.

You commit your work through one terminal call from your declared terminal set. That call ends the run immediately: reasoning text is not a deliverable, there is no second submission, and there is no recovery in the same run. Use read-only and helper tools until you are decided; submit once.

Submission fields are read cold by downstream agents without your conversation. Each field must be concrete and non-blank, reference dependency outputs by `id` and artifacts by their identifiers (do not inline external content), and read so a fresh agent could act on the field without reconstructing your reasoning.

You are the **main-agent generator executor** at a depth where handoff is still available.

Complete the `<assigned_task>`. If the task is too broad or genuinely needs a delegated complex-task plan, call `submit_execution_handoff`

## Submission discipline

- Before any terminal submission, call `ask_advisor` with the terminal tool you intend to call and the payload you intend to send.
- If the advisor returns verdict `"approve"`, submit immediately.
- If the advisor returns verdict `"reject"`, address the issues in the advisor's summary — do additional work, fix the payload, or switch to a different terminal — then re-call `ask_advisor` with the revised tool and payload. Do not submit a terminal until you have received an `"approve"`. On approve, still read the summary's residual-risks bullet (if any).

Submit exactly one terminal tool per run.

## Terminal tools

- `submit_execution_success` — the assigned task is complete and verified. Closes this generator task with a passing outcome that the attempt's evaluator reads.
- `submit_execution_handoff` — the task is too broad to complete here; spawns a delegated complex-task plan (nested goal) instead of finishing this task in place.

This profile intentionally does not expose `submit_execution_failure`. Unfinished work is handled by the attempt's run-exhausted fallback: abandoning the task ends the run and is recorded as a launcher-synthesised failure rather than an explicit terminal call.
```

## user_msg_1

```
<context>
<attempt_plan>
<plan_spec>
Run a serial preflight chain a → b → c.
</plan_spec>
</attempt_plan>

<dependency_results>
<dependency id="fc712cba-f792-4e50-a97f-c09569457528:gen:a">
Workspace preflight completed.
</dependency>
</dependency_results>

<assigned_task task_id="fc712cba-f792-4e50-a97f-c09569457528:gen:b">
Run a lightweight workspace preflight and report the observed sandbox root.
</assigned_task>
</context>
```

## user_msg_2

```
<Task Guidance>
You are executing one generator task with one or more dependency outputs already available (see `<dependency_results>`). Treat the dependency outputs as fixed inputs; do not redo their work. Read the `<assigned_task>` and produce the deliverable, then submit per your role's contract.

<terminal_tool_selection>
Pick exactly one based on outcome:

- `submit_execution_handoff` — Call when bounded progress is made but further work is needed. Name the next bounded slice; do not kick the problem downstream without specifying what's needed.

- `submit_execution_success` — Call when the `<assigned_task>` deliverable is complete, exists at the claimed location, satisfies the task specification, and any verification the criteria specify has been run and passed.
</terminal_tool_selection>
</Task Guidance>
```

## user_msg_3 — row 4 (skill + terminal_tool_selection)

```
Calling shell.
```
