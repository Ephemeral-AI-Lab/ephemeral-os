# Scout Launch Contract

Use this reference immediately before the first scout wave or whenever scout launching stalls.

## Launch workflow

1. Must call `run_subagent(agent_name="scout", input={"target_paths": [...]}, task_note="...")`.
2. Must give each scout one unresolved owner slice, not a bag of unrelated files.
3. Must launch the whole useful wave before waiting when several slices are still unresolved.
4. Must inspect each fresh scout with `check_background_progress(...)` before any `wait_for_background_task(...)`.
5. Must reuse stable scout refs or shared briefings when the same scope already landed in the run.
6. If live evidence isolates two exact files with no shared owner, treat them as two slices even when they came from one failing benchmark family.

## Few-shot examples

- Example: the root anchor leaves `pkg/io/hdf.py`, `pkg/io/parquet/`, `pkg/groupby.py`, and `pkg/config.py` unresolved.
  Launch four scouts in one wave, one per slice, because each answers a different ownership question.
  After launch, inspect `bg_*` tasks first; wait only if the plan is blocked on their briefs.
- Example: the benchmark prompt names `tests/test_groupby.py`, but the root anchor and live symbols point to `pkg/groupby.py`.
  Launch the scout with `target_paths=["pkg/groupby.py"]`.
  Keep `tests/test_groupby.py` only in `owned_failures` or the task note; never copy the test path into the scout scope.
- Example: a child planner inherits a scout ref for `pkg/io/parquet/arrow.py`, but `pkg/io/parquet/fastparquet.py` is still unmapped.
  Reuse the inherited arrow brief and launch one new scout only for `fastparquet.py`.
  Do not relaunch a package-wide parquet scout just because parquet still has open work.
- Example: the same failing benchmark family mentions CLI and compatibility behavior, but the anchor already isolated `pkg/cli.py` and `pkg/compat.py` as separate exact files.
  Launch `target_paths=["pkg/cli.py"]` and `target_paths=["pkg/compat.py"]` as separate scouts, or leave one behind a child planner if slots are tight.
  Do not launch `target_paths=["pkg/cli.py","pkg/compat.py"]` unless live evidence already showed one shared helper boundary those files jointly define.
- Example: the first scout launch returns an input-shape error.
  Resend the same scope with structured `input={"target_paths": [...]}`.
  Do not turn that tool error into a new heuristic regrouping step.

## Rules

- Never pass prompt mode to `scout`.
- Never wait on a fresh scout before `check_background_progress(...)`.
- Never launch scouts for benchmark tests when a plausible production owner already exists.
- Never derive scout `target_paths` by copying failing test paths after the anchor already exposed the production owner.
- Never bundle unrelated exact files into one scout just because they were discovered from the same failing test file or same prompt paragraph.
- Never launch Atlas before the scout wave has produced reusable output.
- Never open a second scout on the same slice in the same turn just because the first one is still running.
- Never launch a scout whose entire target stays inside one exact file already covered by an inherited scout or same-turn scout.
