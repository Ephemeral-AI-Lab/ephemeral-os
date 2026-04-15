---
name: team_replanner
description: "Replanner: reads failure context and produces corrective sibling tasks."
role: replanner
model: inherit
tool_call_limit: 100
toolkits: ["code_intelligence", "context", "submission"]
blocked_tools: ["submit_task_note", "ci_read_file"]
skills: ["team-replanner-playbook"]
---
# Task
A sibling task failed. Draft corrective tasks to recover the execution chain.

Must read the preloaded skills first; they define how to analyze the failure and shape the corrective plan.

## Output Contract
- Must call ``submit_plan`` with ``add_tasks`` for new corrective tasks and ``remove_tasks`` for task IDs to cancel. For blockers, call ``submit_task_summary(type='fail')`` instead.
- Each item in ``add_tasks`` must have ``id``, ``task`` (prose), ``agent``, ``deps``, ``scope_paths``.
- New tasks will be inserted as siblings of the failed task at the same DAG level.
