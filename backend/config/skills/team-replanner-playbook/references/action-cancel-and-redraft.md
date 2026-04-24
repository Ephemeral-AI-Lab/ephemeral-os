# Action Reference: Cancel And Redraft

Use after classification and diagnostics show stale same-layer work must be replaced. Final schema lives in `terminal-contract`.

## Decision Flow

```text
Caption: cancel-and-redraft names only stale non-terminal direct siblings.

same parent
  |-- failed request_replan/origin -> preserve; never cancel
  |-- this replanner     -> preserve
  |-- terminal sibling   -> preserve
  |-- live useful sibling -> preserve
  `-- other stale live sibling -> cancel_ids
```

| Candidate | Action |
| --- | --- |
| Other stale non-terminal direct sibling | Add its id to `cancel_ids`. |
| Failed task or original `request_replan` task | Preserve; switch to add-only if this is the only stale item. |
| This replanner | Preserve. |
| Done, failed, cancelled, nested descendant, or dependent | Preserve; cascade handles descendants from the stale root. |
| Replacement for uncancelled sibling scope | Drop or switch to add-only. |

## Build

| Check | Rule |
| --- | --- |
| Cancellation proof | Each `cancel_ids` item is non-terminal and has this replanner's `parent_id`. |
| Replacement scope | Include cancelled sibling scope only when that sibling id is in `cancel_ids`. |
| Original-contract coverage | Every uncompleted goal, acceptance criterion, and scope item from the failed developer/validator contract maps to a new recovery child or an explicitly preserved live owner; blocker-only repair is insufficient. |
| Children | Add only `developer` repair/diagnostic tasks and optional `validator` tasks. |
| Dependencies | Prefer local deps; existing deps need fresh schedulable graph proof. |
| No stale sibling left | Switch to `action-add-tasks` and submit `cancel_ids=[]`. |

Load `terminal-contract`, self-check, then submit exactly one `submit_replan(...)`.
