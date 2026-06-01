"""Collection policy for obsolete pre-workflow lifecycle tests.

These files cover behavior intentionally removed by the TaskCenter -> Workflow
refactor: synthetic root workflows, terminal workflow handoff, WAITING_WORKFLOW,
Planned* DTOs, and root-vs-child close routing. Current workflow lifecycle
coverage lives in the remaining tests and tool-level delegate_workflow tests.
"""

collect_ignore = [
    "test_attempt_orchestrator.py",
    "test_child_workflow_handoff.py",
    "test_entry_bootstrap.py",
    "test_integration_phase02.py",
    "test_integration_smoke.py",
    "test_phase04_deferred_retry.py",
    "test_phase04_workflow_request_start.py",
    "test_plan_dag.py",
    "test_workflow_lifecycle.py",
]
