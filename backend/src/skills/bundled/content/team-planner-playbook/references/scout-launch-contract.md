# Scout Launch Contract
Use this reference immediately before the first scout wave or whenever scout launching stalls.

## Launch workflow

1. Must call `run_subagent(agent_name="scout", input={"target_paths": [...]}, task_note="...")` exactly; if a tool error says `input` is missing, retry with that shape instead of prose or `prompt`.
2. Must give each scout one unresolved owner slice, not a bag of unrelated files.
3. Must queue the whole useful wave before any progress check or reaction to early scout output; that means many per-slice calls, not one omnibus scout.
4. Must inspect each fresh scout with `check_background_progress(...)` before any `wait_for_background_task(...)`, and keep doing other ready planning work until the inspected scouts are the last blocker.
5. Must reuse stable scout refs or shared briefings when the same scope already landed in the run, and treat same-turn invalid-scout overlap as a reuse/replan signal instead of a cue to keep re-scouting the exact file.
6. If live evidence isolates two exact files with no shared owner, treat them as two slices even when they came from one failing benchmark family.
7. If the first anchor plus one scoped packet already surfaced the exact owner files, overwrite any stale guessed aliases in the first-wave ledger and launch from that live evidence without more sibling structure passes.
8. If live evidence did not surface an exact file for one family, launch the nearest existing production boundary instead of synthesizing a new exact path from the benchmark test name.
9. If the current layer already has room for the nameable slices, launch them now. Do not hold exact-file slices back for a hypothetical child planner before any scout on that branch exists.
10. Must record the exact `task_id` returned by each `run_subagent` call together with the launched owner scope, and use only those literal ids in later progress checks or waits. Never invent `bg_0`, assume contiguous numbering, or trust a retrospective count more than the actual scout refs.

## Few-shot examples

- Example: the root anchor leaves `pkg/io/hdf.py`, `pkg/io/parquet/`, `pkg/groupby.py`, and `pkg/config.py` unresolved.
  Launch four separate scouts in one wave, one per slice, then inspect the returned task ids before any wait; do not send those four paths in one `run_subagent(...)` call.
- Example: `pkg/tests/test_utils_dataframe.py` was ambiguous at first, but a wider live listing later proves `pkg/dataframe/utils.py` exists.
  Put that family back into the same first-wave ledger as `pkg/dataframe/utils.py`, delete any earlier `pkg/dataframe/utils_dataframe.py` brainstorm, and then launch the real file. Do not shorten it to `pkg/utils.py`, and do not shrink eight benchmark families into seven scout launches.
- Example: the benchmark prompt names `tests/test_utils_dataframe.py`, but no live listing has shown `pkg/utils/dataframe.py`.
  Do not launch that invented target or `pkg/dataframe/utils/dataframe.py`. Launch the nearest existing production boundary, or the already listed `pkg/dataframe/utils.py`, and keep the test path only in `owned_failures` or the task note.
- Example: a `run_subagent` call fails because `input` was omitted or `prompt=null`.
  Retry immediately with `run_subagent(agent_name="scout", input={"target_paths":["pkg/io/json.py"]}, task_note="Map json owner slice")`. Do not recover by stuffing every remaining owner path into one omnibus scout.
- Example: `check_background_progress(...)` reports `run_subagent: scout artifact invalid: missing structured artifact payload` for `pkg/cli.py`, and a retry on that same exact file hits overlap rejection.
  Keep the scope in the ledger as mapped-but-contract-bad, continue planning from the landed owner evidence you already have, and save any refresh of that exact slice for a later turn only if reuse is still insufficient.
- Example: `bg_1` already returned invalid while only `bg_2` is launched and the first-wave list still owes six slices.
  Launch the remaining owed scouts first, record their literal ids, then inspect or repair `bg_1`. Do not jump to `check_background_progress(task_id="bg_3")`, and do not wait on ids that were never returned.
- Example: your first-wave list names `pkg/io/hdf.py`, `pkg/io/json.py`, `pkg/io/parquet/`, `pkg/groupby.py`, `pkg/dataframe/utils.py`, `pkg/cli.py`, `pkg/config.py`, and `pkg/compat.py`, but only seven real `task_id`s came back and `pkg/dataframe/utils.py` has none.
  Do not start progress checks yet. Launch the missing `pkg/dataframe/utils.py` scout, record its literal `task_id`, then inspect progress.

## Rules

- Never pass prompt mode to `scout`.
- Never wait on a fresh or uninspected scout before `check_background_progress(...)`.
- Never launch scouts for benchmark tests when a plausible production owner already exists.
- Never derive scout `target_paths` by copying failing test paths after the anchor already exposed the production owner.
- Never bundle unrelated exact files or the whole first-wave ledger into one scout just because they were discovered from the same failing test file or same prompt paragraph.
- Never launch Atlas before the scout wave has produced reusable output.
- Never open a second scout on the same slice in the same turn just because the first one is still running.
- Never launch a scout whose entire target stays inside one exact file already covered by an inherited scout or same-turn scout.
- Never delay the first scout wave behind extra sibling structure passes once the current anchor already exposed the needed owner files.
- Never start loading decomposition references or progress checks while the first useful scout wave is only partially launched; if your own first-wave list names a slice with no real `task_id`, or an early scout return lands before the wave has ids for every named slice, finish launching the owed slices first.
- Never check background progress on an inferred scout id that was never returned by `run_subagent`.
- Never synthesize a scout target by splitting a benchmark test filename into guessed directories, by carrying forward a disproven brainstorm alias, or by naming an exact production file absent from live evidence.
