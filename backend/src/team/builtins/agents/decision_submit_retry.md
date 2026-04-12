---
name: decision_submit_retry
description: "Decision posthook: submit or retry."
model: inherit
toolkits: ["posthook_submit_retry"]
skills: ["team-posthook-decision-playbook"]
agent_type: posthook
---
# Task
Evaluate the work-phase output and decide: submit summary or retry. Call exactly ONE of your available tools.

Must read the preloaded skills first; they define the decision workflow.
