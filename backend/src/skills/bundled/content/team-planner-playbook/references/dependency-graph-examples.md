# Dependency Graph Examples

Use this reference immediately before final plan JSON when there are 4+ candidate siblings, one dominant owner plus residual single-file slices, or any temptation to create `misc_*`, `remaining_*`, or `core_misc_*` lanes.
Do not load it while the first scout wave still has unlaunched exact-file slices.

## Lane smells

- An atomic lane that owns several unrelated exact files only because each slice is small is under-decomposed.
- Local ids such as `misc`, `remaining`, `assorted`, `core_misc`, or `small_fixes` are a stop signal unless scouts already proved one shared owner.
- If a lane would verify several unrelated test files just to cover bundled leftovers, emit a residual child planner or split direct leaves instead.
- If a parent plan ends as direct developers for every mapped slice plus one terminal validator, depth probably collapsed too early.

## Few-shot examples

- Example: root scouts land `pkg/io/hdf.py`, `pkg/io/parquet/`, `pkg/groupby.py`, `pkg/io/json.py`, `pkg/utils.py`, `pkg/cli.py`, `pkg/config.py`, and `pkg/compat.py`.
  Emit `developer(hdf)` now.
  Emit child planners for `parquet` and `groupby`.
  For the remaining single-file slices, either emit several direct developers if slots remain or one residual child planner whose only job is to schedule `json`, `utils`, `cli`, `config`, and `compat`, then finish with one terminal validator.
  Never emit one atomic developer that owns all five files.
  Never flatten the whole root into eight direct developers plus one validator, and never add per-branch validators at the same parent layer, just because every scout already mapped an owner.

- Example: a child planner inherits scout briefs for `pkg/io/json.py`, `pkg/utils.py`, `pkg/cli.py`, `pkg/config.py`, and `pkg/compat.py`.
  The evidence already maps each file.
  Emit one developer per file, or one residual child planner if the current layer would exceed its cap.
  Do not spend another scout wave and do not create `misc_core_fix`.

- Example: one risky serializer helper in `pkg/schema.py` must land before `pkg/api.py`, `pkg/cli.py`, and `pkg/cache.py`.
  Emit `developer(schema)` first, then one midflight validator on that branch cut, then the dependent developer lanes, then one terminal validator.
  Use deps only on the real shared risk, not to serialize unrelated work.

- Example: one large file `pkg/groupby.py` contains `numeric_only`, `axis_observed`, `tree_reduce`, and `alignment_misc` families.
  At the parent layer, keep `groupby` expandable.
  Inside the child planner, emit family-level developers plus one terminal validator.
  Do not keep `groupby` atomic just because the file path is singular.

- Example: one dominant 30-test owner and five 1-2 test residuals all survive the first scout wave.
  Test counts do not justify bundling.
  Only shared live owner evidence justifies bundling.
  Keep the dominant lane direct, then use either direct residual leaves or a residual child planner.
