---
name: submit_summary_agent
description: "Serializes worker output into a validated summary."
model: inherit
toolkits: ["submit_summary_posthook"]
skills: []
include_skills: false
agent_type: posthook
---
# Task
Read the work-phase output and call submit_summary exactly once with a concise 1-3 sentence summary of what the worker accomplished. Include an artifact only if the worker produced structured output worth persisting.

## Rules
- If the work-phase output is a JSON object with ``summary`` and optional ``artifact``, use those fields directly.
- If submit_summary returns a validation error, fix the payload and call submit_summary again in the same turn.
