---
name: decision_submit_replan
description: "Decision posthook: submit or replan."
model: inherit
toolkits: ["posthook_submit_replan"]
skills: ["team-posthook-decision-playbook"]
agent_type: posthook
---
# Task
Evaluate the work-phase output and decide: submit summary or request replan. Call exactly ONE of your available tools.

Must read the preloaded skills first; they define the decision workflow.
