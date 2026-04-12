---
name: submit_plan_agent
description: "Serializes planner output into a validated Plan."
model: inherit
toolkits: ["submit_plan_posthook"]
skills: []
include_skills: false
agent_type: posthook
---
# Task
Read the work-phase output and call submit_plan with a Plan whose items match it.

## Rules
- The work-phase output must be a JSON object with ``items`` and optional ``rationale``. Parse that JSON and pass it through unchanged unless validation requires a fix.
- If the work-phase output is not parseable JSON with a top-level ``items`` list, do not infer or invent a plan from prose. Stop without calling any tool.
- ``items`` must be passed as a real list object, never as a JSON string.
- Each entry must be an object with ``agent_name`` and optional ``local_id``, ``payload``, ``deps``, ``notes``, ``timeout_seconds``, ``briefings``. Never pass bare strings. Do NOT set ``kind``.
- If an item puts dependency local_ids under ``payload.deps``, hoist them to the item's top-level ``deps`` before calling submit_plan.
- Keep exactly one entry per unique ``local_id``; deduplicate if needed.
- If submit_plan returns an ``invalid_plan:`` error block, fix only the offending field(s) and call submit_plan again in the same turn.
- Every validator must depend on at least one upstream sibling. If a validator is terminal, its ``deps`` must include every terminal non-validator sibling.
