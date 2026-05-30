# Open Questions

## Workflow Vocabulary Rename (Goal → Workflow) - 2026-05-29
- [ ] Keep `ContextEngineDeps.goal_store` attribute name vs rename to `workflow_store`? — Rename ripples into ~6 recipe files (`deps.goal_store.get(...)`) for zero contract gain. Default chosen: KEEP.
- [ ] Keep filenames `backend/src/db/models/goal.py` and `backend/src/db/stores/goal_store.py` (rename classes only)? — File rename is cosmetic churn. Default chosen: KEEP filenames, rename `GoalRecord`/`GoalStore` classes inside.
- [ ] Defer EventType member-name renames (`GOAL_STARTED`→`WORKFLOW_STARTED`, etc.)? — Audit *values* stay regardless (contract); member-name rename ripples into `scenarios/*.py` + 6 mock test files for cosmetic gain. Default chosen: DEFER to optional follow-up.
- [ ] Physical table rename `goals`→`workflows` + `goal` column + migration — out of chosen scope (data-migration blast radius, zero conceptual gain now). The `engine.py` legacy-drop precedent makes it feasible later if ever wanted. Recorded as optional follow-up.
