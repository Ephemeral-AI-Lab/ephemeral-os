# Scout Launch Contract
Use this reference immediately before the first scout wave or whenever scout launching stalls.

## Launch workflow

1. Must call `run_subagent(agent_name="scout", input={"target_paths": [...]}, task_note="...")`.
2. Must give each scout one unresolved owner slice, not a bag of unrelated files.
3. Must launch the whole useful wave before waiting when several slices are still unresolved.
4. Must inspect each fresh scout with `check_background_progress(...)` before any `wait_for_background_task(...)`, and keep doing other ready planning work until the inspected scouts are the last blocker.
5. Must reuse stable scout refs or shared briefings when the same scope already landed in the run.
6. If live evidence isolates two exact files with no shared owner, treat them as two slices even when they came from one failing benchmark family.
7. If the first anchor plus one scoped packet already surfaced the exact owner files, the first scout wave must launch from that evidence without more sibling structure passes.
8. If live evidence did not surface an exact file for one family, launch the nearest existing production boundary instead of synthesizing a new exact path from the benchmark test name.
9. If the current layer already has room for the nameable slices, launch them now. Do not hold exact-file slices back for a hypothetical child planner before any scout on that branch exists.
10. Must record the exact `task_id` returned by each `run_subagent` call together with the launched owner scope, and use only those literal ids in later progress checks or waits. Never invent `bg_0`, assume contiguous numbering, or trust a retrospective count more than the actual scout refs.

## Few-shot examples

- Example: the root anchor leaves `pkg/io/hdf.py`, `pkg/io/parquet/`, `pkg/groupby.py`, and `pkg/config.py` unresolved.
  Launch four scouts in one wave, one per slice, then inspect the returned task ids before any wait.
- Example: `pkg/tests/test_utils_dataframe.py` was ambiguous at first, but a wider live listing later proves `pkg/dataframe/utils.py` exists.
  Put that family back into the same first-wave ledger as `pkg/dataframe/utils.py`. Do not keep the disproven alias, do not shorten it to `pkg/utils.py`, and do not shrink eight benchmark families into seven scout launches.
- Example: the benchmark prompt names `tests/test_utils_dataframe.py`, but no live listing has shown `pkg/utils/dataframe.py`.
  Do not launch that invented target. Launch the nearest existing production boundary, or the already listed `pkg/dataframe/utils.py`, and keep the test path only in `owned_failures` or the task note.
- Example: the launches return `task_id=bg_3` for `pkg/io/json.py`, `task_id=bg_5` for `pkg/cli.py`, and `task_id=bg_6` for `pkg/compat.py`.
  Build a tiny ledger from those literal ids, check those exact ids later, and keep `pkg/compat.py` mapped instead of calling it "needs more scouting" in the plan recap.
- Example: the first wave launched `bg_1`, `bg_3`, `bg_4`, and `bg_7`, and your first join attempt is rejected because some ids were never inspected.
  Call `check_background_progress(...)` on those literal ids, reconcile any completed briefs into the ledger, and keep shaping ready sibling lanes before a wait.
  Do not treat the wait rejection as a transient tool bug, and do not swap in guessed ids just to make the join call pass.
- Example: your first-wave list names `pkg/io/hdf.py`, `pkg/io/json.py`, `pkg/io/parquet/`, `pkg/groupby.py`, `pkg/dataframe/utils.py`, `pkg/cli.py`, `pkg/config.py`, and `pkg/compat.py`, but only seven real `task_id`s came back and `pkg/dataframe/utils.py` has none.
  Do not start progress checks yet. Launch the missing `pkg/dataframe/utils.py` scout, record its literal `task_id`, then inspect progress.

## Rules

- Never pass prompt mode to `scout`.
- Never wait on a fresh or uninspected scout before `check_background_progress(...)`.
- Never launch scouts for benchmark tests when a plausible production owner already exists.
- Never derive scout `target_paths` by copying failing test paths after the anchor already exposed the production owner.
- Never bundle unrelated exact files into one scout just because they were discovered from the same failing test file or same prompt paragraph.
- Never launch Atlas before the scout wave has produced reusable output.
- Never open a second scout on the same slice in the same turn just because the first one is still running.
- Never launch a scout whose entire target stays inside one exact file already covered by an inherited scout or same-turn scout.
- Never delay the first scout wave behind extra sibling structure passes once the current anchor already exposed the needed owner files.
- Never start loading decomposition references while the first useful scout wave is only partially launched; if your own first-wave list names a slice with no real `task_id`, or a once-ambiguous family later resolves to a live exact file, return to `run_subagent(...)` for that proved slice before any progress checks.
- Never check background progress on an inferred scout id that was never returned by `run_subagent`.
- Never synthesize a scout target by splitting a benchmark test filename into guessed directories or exact production files absent from live evidence.
