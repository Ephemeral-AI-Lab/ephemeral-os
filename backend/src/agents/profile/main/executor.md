---
name: executor
description: Main agent generator executor — thin entry-point routed by nested-mission depth.
model: inherit
agent_kind: executor
dispatchable_by_planner: true
agent_type: agent
context_recipe: generator_v1
variants:
  - when: nested_mission_depth_above_handoff_range
    use: executor_success_failure
    note: "depth >MAX_HANDOFF_DEPTH — leaf executor, no further handoff"
  - when: always
    use: executor_success_handoff
    note: "depth ≤MAX_HANDOFF_DEPTH — handoff allowed"
---
