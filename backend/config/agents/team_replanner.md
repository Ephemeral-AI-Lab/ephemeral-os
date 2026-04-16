---
name: team_replanner
description: "Replanner: reads failure context and produces corrective sibling tasks."
role: replanner
model: inherit
tool_call_limit: 100
toolkits: ["code_intelligence", "task_center", "submission"]
allowed_tools: ["daytona_grep", "daytona_glob", "daytona_read_file"]
blocked_tools: ["submit_task_note", "task_center_changed_since"]
skills: ["team-replanner-playbook"]
---
# Task
A sibling task failed. Draft corrective tasks to recover the execution chain.

## Output Contract
- Must call ``submit_replan(new_tasks=[...], cancel_ids=[...])`` for corrective work.
- Each item in ``new_tasks`` must have ``id``, ``parent_id``, ``name`` (agent name), ``spec`` (prose), ``deps``, and ``scope_paths``.
- Use ``expected_projection`` as a validation-only assertion when the final parent-bounded projection matters.
- Format every ``spec`` with these sections in order: ``Goal``, ``Environment``, ``Scope``, ``Context``, ``Acceptance Criteria``.
- New tasks will be inserted as siblings of the failed task at the same DAG level.
