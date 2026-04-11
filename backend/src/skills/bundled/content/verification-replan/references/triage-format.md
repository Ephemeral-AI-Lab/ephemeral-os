# Triage Format

Use this reference only when you need a manual FAIL summary or a multi-cluster replan report.

## Required structure

Must emit:

```text
REPLAN_REASON: <short reason>
FAIL_TO_PASS: N/M failing

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

- Must group failures by root cause.
- Must keep test ids exact.
- Must keep owner surfaces concrete.
- Must mark an unknown sibling task as `unknown` instead of guessing.
- Never emit a vague summary such as "tests failed".

## One-shot example

If three failing tests all point to the same serializer output shape, emit one `CLUSTER:` block for that serializer bug, not three independent clusters.

Must group by root cause.
Must keep each `TEST:` id exact.
