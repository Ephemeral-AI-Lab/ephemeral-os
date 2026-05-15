# 2026-05-15 — Goal/Iteration/Trial tier rename

Atomic, single-PR rename of the TaskCenter tier hierarchy:
`Mission/Episode/Attempt` → `Goal/Iteration/Trial`. See
`docs/plans/2026-05-15-tier-rename-plan.md` for the full plan.

## Database schema changes

Tables (and FK targets) renamed:

| Old | New |
|---|---|
| `missions` | `goals` |
| `episodes` | `iterations` |
| `attempts` | `trials` |

Columns renamed:

| Table | Old | New |
|---|---|---|
| `iterations` | `mission_id` | `goal_id` |
| `iterations` | `attempt_ids` (JSON) | `trial_ids` |
| `iterations` | `attempt_budget` | `trial_budget` |
| `goals` | `episode_ids` (JSON) | `iteration_ids` |
| `trials` | `episode_id` (FK) | `iteration_id` |
| `trials` | `attempt_sequence_no` | `trial_sequence_no` |

`final_outcome` JSON keys (in `goals` rows): `final_episode_id` →
`final_iteration_id`; `final_attempt_id` → `final_trial_id`. The
`final_outcome` column itself is unchanged.

UniqueConstraint names: `uq_episode_request_sequence` →
`uq_iteration_goal_sequence`; `uq_attempt_segment_sequence` →
`uq_trial_iteration_sequence`.

## No alembic migration

There is no automated migration script. SQLAlchemy `create_all` produces
the new schema; the old tables are left in place until the dev runs the
drop script below. The startup gate refuses to proceed until they're
dropped.

## Dev-action required

If you have an existing local database created before this PR, run the
one-shot drop script before next start:

```bash
python -m backend.scripts.drop_legacy_tier_tables --db-url <your-db-url>
```

The script drops `attempts`, then `episodes`, then `missions` (children
before parents) and is idempotent.

If you skip this step, the next `initialize_db()` call raises:

```
RuntimeError: Legacy tier tables ['attempts', 'episodes', 'missions']
present after rename. Run: python -m backend.scripts.drop_legacy_tier_tables
```

## Audit / observability changes

Audit-event JSON keys in the recorder payloads change. External
dashboards or tooling that filter by these keys must update:

| Old key | New key |
|---|---|
| `mission_id` | `goal_id` |
| `episode_id` | `iteration_id` |
| `attempt_id` | `trial_id` |
| `episode_ids` | `iteration_ids` |
| `attempt_ids` | `trial_ids` |
| `attempt_sequence_no` | `trial_sequence_no` |

`task_center_attempt_id` is **unchanged** — it's the legacy task-row
column on the TaskCenter wrapper, not a tier reference.

## SpawnReason values (carve-out)

`SpawnReason` enum values that textually embed the tier noun are
renamed (the rest of the carve-out rationale lives in §2.5.1 of the
plan):

| Old value | New value |
|---|---|
| `"attempt_planner"` | `"trial_planner"` |
| `"attempt_generator"` | `"trial_generator"` |
| `"attempt_evaluator"` | `"trial_evaluator"` |

`"entry_executor"` is unchanged.

## Recorder filesystem layout

Audit recorder dir prefixes change:

| Old | New |
|---|---|
| `mission_<seq>_<id>/` | `goal_<seq>_<id>/` |
| `episode_<seq>_<id>/` | `iteration_<seq>_<id>/` |
| `attempt_<seq>_<id>/` | `trial_<seq>_<id>/` |

Existing audit-trace dirs from prior runs keep their old names.
