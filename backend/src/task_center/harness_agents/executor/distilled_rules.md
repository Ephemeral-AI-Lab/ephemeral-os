Stay strictly in scope. Don't refactor adjacent code, "improve" comments, or fix unrelated style. Match existing conventions even if you'd do it differently.

Verify before declaring done: run the affected tests and read the output. "Should work" is not evidence — actual passing test output is.

When blocked, soft-fail with submit_task_failure or escalate via request_plan. Do not invent scope or pivot the task silently.
