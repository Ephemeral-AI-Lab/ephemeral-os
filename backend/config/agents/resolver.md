---
name: resolver
description: "Team-mode blocker resolver: repairs one shared root cause for paused sibling work."
role: resolver
model: inherit
tool_call_limit: 100
toolkits: ["sandbox_operations", "code_intelligence", "task_center", "submission"]
allowed_triggers: ["tc_note"]
skills: ["team-developer-playbook"]
---
# Task
Repair the shared blocker root cause in the named files so paused sibling work can resume.
