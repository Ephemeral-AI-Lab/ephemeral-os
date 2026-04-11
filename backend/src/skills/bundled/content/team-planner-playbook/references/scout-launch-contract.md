# Scout Launch Contract

Use this reference immediately before the first scout wave or whenever scout launching stalls.

## Launch workflow

1. Must call `run_subagent(agent_name="scout", input={"target_paths": [...]}, task_note="...")`.
2. Must give each scout one unresolved owner slice, not a bag of unrelated files.
3. Must launch the whole useful wave before waiting when several slices are still unresolved.
4. Must inspect each fresh scout with `check_background_progress(...)` before any `wait_for_background_task(...)`.
5. Must reuse stable scout refs or shared briefings when the same scope already landed in the run.

## Few-shot examples

- Example: the root anchor leaves `pkg/io/hdf.py`, `pkg/io/parquet/`, `pkg/groupby.py`, and `pkg/config.py` unresolved.
  Launch four scouts in one wave, one per slice, because each answers a different ownership question.
  After launch, inspect `bg_*` tasks first; wait only if the plan is blocked on their briefs.
- Example: a child planner inherits a scout ref for `pkg/io/parquet/arrow.py`, but `pkg/io/parquet/fastparquet.py` is still unmapped.
  Reuse the inherited arrow brief and launch one new scout only for `fastparquet.py`.
  Do not relaunch a package-wide parquet scout just because parquet still has open work.
- Example: the first scout launch returns an input-shape error.
  Resend the same scope with structured `input={"target_paths": [...]}`.
  Do not turn that tool error into a new heuristic regrouping step.

## Rules

- Never pass prompt mode to `scout`.
- Never wait on a fresh scout before `check_background_progress(...)`.
- Never launch scouts for benchmark tests when a plausible production owner already exists.
- Never launch Atlas before the scout wave has produced reusable output.
- Never open a second scout on the same slice in the same turn just because the first one is still running.
- Never launch a scout whose entire target stays inside one exact file already covered by an inherited scout or same-turn scout.
