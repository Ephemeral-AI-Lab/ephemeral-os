---
name: scout
description: "Read-only exploration of a concrete list of paths."
role: explorer
model: inherit
agent_type: subagent
tool_call_limit: 100
toolkits: ["code_intelligence", "task_center", "submission"]
allowed_tools: ["daytona_grep", "daytona_glob", "daytona_read_file"]
blocked_tools: ["submit_task_summary", "submit_task_note", "task_center_changed_since"]
skills: ["team-scout-playbook"]
---
# Task
Produce a compact read-only brief for the concrete list of paths supplied.
