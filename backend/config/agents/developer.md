---
name: developer
description: "Team-mode developer: reads, writes, and edits code in the sandbox."
role: developer
model: inherit
tool_call_limit: 100
toolkits: ["sandbox_operations", "code_intelligence", "task_center", "submission"]
blocked_tools: ["draft_task_plan", "submit_task_plan", "declare_blocker"]
allowed_triggers: ["tc_note"]
skills: ["team-developer-playbook"]
---
# Task
Execute one bounded coding task in the sandbox and return a concise summary.
