# executor — dependency_results branch (generator task `b`, deps: [`a`]); user_msg_1 carries a real `<dependency_results>` block
- source: `pipeline.dependency_dag_serial/20260518T164232Z_da47a06c86c8/goal_01_ff9582e2-270a-4485-8719-7e7f1288d90b/iteration_01_e8248a0e-b609-4ae6-98fe-c7fd361a0503/attempt_01_80414020-070f-4fe9-b1ef-17cedb22aad5/03_executor_80414020-070f-4fe9-b1ef-17cedb22aad5:gen:b/message.jsonl`
- notes: Closes Gap 3 in the original gap report. The scenario submits a serial DAG `a → b → c`; task `b` runs with `deps=["a"]`, so its composer renders the `<dependency_results>` group (one `<dependency id=...>` child per upstream task) between `<attempt_plan>` and `<assigned_task>`. The role_instruction (row 3) is the `has_deps=True` branch of `generator_instruction`, opening with "This task has dependencies on other generator tasks…". This is the variant the existing initial_messages scenario could not exercise because its plans only have single-task DAGs.

## system

```
You are the **main-agent generator executor** at a depth where handoff is still available.

Complete the `Assigned Task`. If the task is too broad or genuinely needs a delegated complex-task plan, call `submit_execution_handoff`

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
<attempt_plan>
<plan_spec>
Run a serial preflight chain a → b → c.
</plan_spec>
</attempt_plan>

<dependency_results>
<dependency id="80414020-070f-4fe9-b1ef-17cedb22aad5:gen:a">
Workspace preflight completed.
</dependency>
</dependency_results>

<assigned_task task_id="80414020-070f-4fe9-b1ef-17cedb22aad5:gen:b">
Run a lightweight workspace preflight and report the observed sandbox root.
</assigned_task>
```

## user_msg_2

```
You are executing one generator task with one or more dependency outputs already available (see `<dependency_results>`). Treat the dependency outputs as fixed inputs; do not redo their work. Read the `<assigned_task>` and produce the deliverable, then submit per your role's contract.

# Terminal tools you may call

Pick exactly one based on outcome:

- `submit_execution_handoff` — Call when bounded progress is made but further work is needed. Name the next bounded slice; do not kick the problem downstream without specifying what's needed.

- `submit_execution_success` — Call when the assigned task's deliverable is complete, exists at the claimed location, satisfies the task specification, and any verification the criteria specify has been run and passed.

# Your task

Execute the role described above. Before any terminal submission, call ask_advisor with your chosen tool_name and intended payload. Submit your chosen terminal only after the advisor returns "approve".
```

## user_msg_3 — row 4 (skill + terminal_selection)

```
Calling shell.
```
