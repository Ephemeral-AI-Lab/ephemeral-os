# Action Reference: Redraft Via Team Planner

Use after classification shows the corrective work is too wide for a bounded developer repair, or a stale `team_planner` sibling must be replaced with a re-authored subtree. Final schema lives in `terminal-contract`.

## Decision Flow

```text
Caption: a team_planner child is justified only when scope or authorship exceeds a developer lane.

classification + root-cause trace
  |-- scope_expansion, surface spans many seams -> Planner handoff: scope_expansion
  |-- cancelled team_planner sibling, subtree needs redraft -> Planner handoff: planner_redraft
  `-- bounded repair fits a developer -> use action-add-tasks or action-cancel-and-redraft
```

| Trigger | Action |
| --- | --- |
| `Classification: scope_expansion` with a repair surface that cannot be bounded into one developer's `scope_paths` | Add one `team_planner` child with `Planner handoff: scope_expansion`. |
| Cancelling a `team_planner` sibling whose objective remains valid but whose subtree was stale or mis-wired | Add its id to `cancel_ids` and author a fresh `team_planner` child with `Planner handoff: planner_redraft`. |
| Cancelled `team_planner` whose objective collapses to one or two bounded repairs | Prefer developer children; skip the planner handoff. |
| Bounded repair with named production seam | Use `action-add-tasks`; do not spawn a planner. |
| Nested planners covering the same scope | Drop; only one `team_planner` redraft per scope. |

## Build

| Check | Rule |
| --- | --- |
| Justification | `spec.detail` names `Planner handoff: scope_expansion` or `Planner handoff: planner_redraft` plus the evidence that a developer cannot bound the repair. |
| Scope | `scope_paths` defines the redraft boundary; the team_planner will author its own children inside that boundary, not outside it. |
| Dependencies | Prefer local payload ids for ordering against developer diagnostics; existing-id deps require fresh schedulable graph proof. |
| Coverage | Original-contract goals, criteria, and scope that fall inside the redraft boundary are owned by the `team_planner`; items outside stay mapped to developer/validator children or preserved live owners. |
| Cancellation pairing | `planner_redraft` requires the cancelled `team_planner` sibling id to appear in `cancel_ids`. |
| Children | Only one `team_planner` per redraft scope; developer/validator siblings may run alongside for disjoint scope. |

Load `terminal-contract`, self-check, then submit exactly one `submit_replan(...)`.
