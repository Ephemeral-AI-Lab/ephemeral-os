# 2026-05-16 — Trial → Attempt rename (third tier only)

Reverse leg of the 2026-05-15 tier rename. Only the third tier
(`Trial`) is being renamed back to `Attempt`. The `Goal` and `Iteration`
tiers established on 2026-05-15 are unchanged. See
`2026-05-15-goal-iteration-trial-rename.md` for the prior context.

The post-rename tier hierarchy is: **Goal / Iteration / Attempt**.

## Database schema changes

Tables (and FK targets) renamed:

| Old | New |
|---|---|
| `trials` | `attempts` |

Columns renamed:

| Table | Old | New |
|---|---|---|
| `iterations` | `trial_ids` (JSON) | `attempt_ids` |
| `iterations` | `trial_budget` | `attempt_budget` |
| `attempts` | `trial_sequence_no` | `attempt_sequence_no` |

`final_outcome` JSON keys (in `goals` rows): `final_trial_id` →
`final_attempt_id`. The `final_outcome` column itself is unchanged.

UniqueConstraint name: `uq_trial_iteration_sequence` →
`uq_attempt_iteration_sequence`.

## No alembic migration

There is no automated migration script. SQLAlchemy `create_all` produces
the new schema; the old `trials` table is left in place until the dev
runs the drop script below. The startup gate refuses to proceed until
it's dropped.

## Dev-action required

> **Update 2026-05-30:** the startup gate (`init_db_with_legacy_check`) and the
> `drop_legacy_tier_tables` remediation script were removed. Databases predating
> this migration are no longer auto-detected or remediated — recreate any such DB.

## Audit / observability changes

Audit-event JSON keys in the recorder payloads change. External
dashboards or tooling that filter by these keys must update:

| Old key | New key |
|---|---|
| `trial_id` | `attempt_id` |
| `trial_ids` | `attempt_ids` |
| `trial_sequence_no` | `attempt_sequence_no` |

`task_center_attempt_id` was unchanged through both renames — it's the
legacy task-row column on the TaskCenter wrapper, not a tier reference.

## SpawnReason values (carve-out)

`SpawnReason` enum values that textually embed the tier noun revert:

| Old value | New value |
|---|---|
| `"trial_planner"` | `"attempt_planner"` |
| `"trial_generator"` | `"attempt_generator"` |
| `"trial_evaluator"` | `"attempt_evaluator"` |

No separate top-level executor spawn reason is retained in the current architecture.

## Recorder filesystem layout

Audit recorder dir prefix reverts:

| Old | New |
|---|---|
| `trial_<seq>_<id>/` | `attempt_<seq>_<id>/` |

Existing audit-trace dirs from prior runs keep their old names.
