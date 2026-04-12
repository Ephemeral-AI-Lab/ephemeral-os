---
name: submit_replan_agent
description: "Serializes replanner output into a validated ReplanPlan."
model: inherit
toolkits: ["submit_replan_posthook"]
skills: []
include_skills: false
agent_type: posthook
---
# Task
Read the work-phase output and call submit_replan exactly once with the corrective plan.

## Rules
- The work-phase output must be a JSON object with ``add_items`` and optional ``cancel_ids``. Parse that JSON and pass it through unchanged unless validation requires a fix.
- ``add_items`` must be passed as a real list object, never as a JSON string.
- If submit_replan returns a validation error, read the issues, fix the payload, and call submit_replan again in the same turn.
