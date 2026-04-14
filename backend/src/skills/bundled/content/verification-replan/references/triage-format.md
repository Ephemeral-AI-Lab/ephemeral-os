# Triage Format

Use this reference only when you need a manual FAIL summary or a multi-cluster replan report.

## Required structure

Must emit:

```text
REPLAN_REASON: <short reason>
FAIL_TO_PASS: N/M failing
ROOT_CAUSE_PACKET: {"observed_failure":"...","first_boundary":"...","hypothesis":"..."}
CLUSTER: <root cause summary>
- TEST: <exact test id>
  ERROR: <exact short error summary>
  OWNER: <likely owner surface>
  SIBLING_TASK: <task id or unknown>
PASS_TO_PASS: N/M passing
REGRESSIONS:
- <exact test id>: <exact short error summary>
```

## Rules

- Group failures by root cause, keep test ids exact, keep owner surfaces concrete, and mark an unknown sibling task as `unknown` instead of guessing.
- Keep collection or import crashes visible instead of replacing them with narrower substitute tests.
- Never emit a vague summary such as "tests failed".
