# Task Planning Decomposition

Use this reference only after ownership is already clear enough to draft the DAG.

## Workflow

1. Must split distinct owner clusters into separate execution lanes.
2. Must use expandable child planners only for residual breadth that does not fit cleanly at the current layer.
3. Must attach validators only where they reduce uncertainty for concrete lanes.

## Rules

- Must keep ready work concrete.
- Must keep residual work explicit.
- Must keep validator count at 2 or fewer for the current plan layer.
- Must keep the plan size between 2 and `max_plan_size`.
- Must keep exactly one validator terminal when the current plan layer has validator items.
- Must include that terminal validator when the current plan layer has 3 or more concrete non-`validator` items.
- Must choose validator deps by the branch cut being guarded, not by agent type.
- Must add a second validator only when it buys a real checkpoint before the final end guard.
- Never hide unresolved owner clusters behind validator-only coverage.
- Never drop validation or cross-surface coverage just to trim one item.

## Second-validator heuristic

- Must default to one terminal validator when 3 or more concrete non-`validator` items already fit a single execution wave.
- Must use direct concrete-lane deps for a leaf-slice guard.
- Must add one midflight validator only when it is the right branch cut before the final end guard.
- If a second validator exists, it must be that single midflight checkpoint, not another terminal validator.

## Few-shot examples

- Example: 3-item chain `developer -> developer -> validator`.
  Must use one terminal validator only.
- Example: 6-item chain where items 4-5 build on item 1's risky schema change.
  Must prefer `developer -> developer -> validator -> developer -> developer -> validator`.
- Example: 6-item fanout of unrelated small edits that converge only at the end.
  Must prefer one terminal validator instead of splitting in a decorative midpoint guard.
- Example: `developer_a -> developer_b -> team_planner->validator`.
  Must not add a parent validator that depends on `team_planner` only because the child planner exists.
- Example: residual child planner branch plus one or two direct developer siblings.
  Must let the child planner branch carry its own validator and add a parent validator only if the direct concrete siblings still need one at the current layer.
- Example: `developer_a -> validator_a` plus `developer_b -> validator_b` at the same plan layer.
  Must not use this shape because it leaves two terminal validators instead of one shared end guard.
- Example: residual child planner branch plus one direct developer sibling.
  Must keep validation on the concrete branch cut being checked; do not add a parent validator that depends on `team_planner` only to simulate whole-branch barrier behavior.
