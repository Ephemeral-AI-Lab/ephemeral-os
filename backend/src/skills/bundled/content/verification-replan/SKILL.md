---
name: verification-replan
description: Failure-triage contract for verifier agents that need to request replan.
---

# Verification Replan

Use this skill only after verification fails. Triage failures for replan. Never create fix tasks yourself.

## Conditional references

- Must load `triage-format` when you need to produce a manual FAIL summary because `submit_task_summary(type='request_replan')` is unavailable.
- Must load `triage-format` when multiple failing clusters need to be grouped into one structured report.

## Workflow

1. Cluster failing tests by root cause.
2. Map each cluster to the likely owner surface and, when available, the sibling task that touched it.
3. Preserve one root-cause packet per cluster with `observed_failure`, `first_boundary`, and `hypothesis`.
4. Classify each cluster as `implementation_bug`, `integration_gap`, `missing_coverage`, `systemic_runtime`, or `transient_runtime`.
5. Keep pass-to-pass regressions explicit even when fail-to-pass targets are still red.

## Action rules

- If `submit_task_summary(type='request_replan')` is available, use it for any failure.
- If the needed tool is absent, emit the same triage in the final FAIL summary.
- Must keep `REPLAN_REASON`, `FAIL_TO_PASS`, `ROOT_CAUSE_PACKET`, one `CLUSTER:` block per root cause, and `PASS_TO_PASS` results explicit.

## Hard rules

1. Inspect and report evidence without editing files.
2. Stay specific.
3. Group by root cause.
4. Preserve regression context.
5. Never emit vague summaries such as "tests failed".
