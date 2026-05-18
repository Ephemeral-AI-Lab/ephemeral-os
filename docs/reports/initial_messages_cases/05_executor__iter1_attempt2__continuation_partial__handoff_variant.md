# executor — iteration 1, attempt 2 (continuation partial; routed to executor_success_handoff variant; generator_instruction: has_deps=False)
- source: `goal_01_1dc1d572-b410-4c5c-8436-e3282e12f36f/iteration_01_a79c7c19-34cf-4bf6-919e-90a85afb9b2f/attempt_02_dc0544d6-cac3-4e75-9932-287f4146d4b0/02_executor_dc0544d6-cac3-4e75-9932-287f4146d4b0:gen:preflight/message.jsonl`

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
# Attempt Plan

Run a workspace preflight probe and continue with the follow-up goal.

# Assigned Task

Run a lightweight workspace preflight and report the observed sandbox root.
```

## user_msg_2

```
You are executing one generator task. This task has no dependencies on other generator tasks in the same attempt. Read the assigned task below and produce the deliverable, then submit per your role's contract.

# Terminal tools you may call

Pick exactly one based on outcome:

- `submit_execution_handoff` — Call when bounded progress is made but further work is needed. Name the next bounded slice; do not kick the problem downstream without specifying what's needed.

- `submit_execution_success` — Call when the assigned task's deliverable is complete, exists at the claimed location, satisfies the task specification, and any verification the criteria specify has been run and passed.

# Your task

Execute the role described above. Before any terminal submission, call ask_advisor with your chosen tool_name and intended payload. Submit your chosen terminal only after the advisor returns "approve".
```
