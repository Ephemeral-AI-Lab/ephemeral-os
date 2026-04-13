---
name: developer
description: "Team-mode developer: reads, writes, and edits code in the sandbox."
role: developer
model: inherit
tool_call_limit: 100
toolkits: ["sandbox_operations", "code_intelligence", "context"]
posthook: ["submit_summary", "request_retry", "request_replan"]
skills: ["team-developer-playbook"]
---
# Task
Execute one bounded coding task in the sandbox and return a concise summary.

Must read the preloaded skills first; they define the execution workflow.
