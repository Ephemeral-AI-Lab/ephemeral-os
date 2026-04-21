# Scout Launch Contract
Use this reference immediately before the first scout wave. For the entry/root planner, this is the first exploration reference after the main playbook; do not do Task Center graph/detail/note setup before it.

## Task/Goal

- You are about to launch the first useful scout wave.

## Avoid

1. **No test-path scouting:** Do not scout benchmark tests, verification targets, `*/tests/*`, `test_*.py`, unconfirmed test-derived paths, or missing test-derived paths when production owners exist. Put literal test paths in task prose and scout production owners instead.
2. **No disproved exact files:** Do not scout an exact file after symbol/structure evidence disproves it and shows a live directory or nested owner for the same family.
3. **No broad bundles:** Do not bundle unrelated exact files or the whole first-wave ledger into one scout; one unresolved production owner slice per scout.
4. **No repeated scout loops:** Do not launch second waves to repair weak notes, prove cold files, or re-check ownership when CI/file notes already provide usable boundaries. Carry uncertainty into task specs.
5. **No redundant scouts:** Do not launch scouts when the assigned task already names concrete owner files and the child lane split; read inherited task/file notes and submit the DAG.

## Workflow

1. Scrub `target_paths` first: every entry should be a live production owner file/directory unless tests are explicitly the owner surface. Put test paths in the scout input `context` as verification evidence, not in `target_paths`.
2. Call `run_subagent(agent_name="scout", input={"target_paths": [...], "context": "..."})` with one unresolved owner slice per scout.
3. Queue the whole useful wave before any progress check or wait.
4. After the wave, read scout findings with `read_file_note(file_path="...")` for each exact scout `target_paths` entry you launched. Do not drop file extensions, reuse an unrelated prior path, or skip a scout path. Scouts/subagents are not Task Center tasks. Do not call `read_task_graph()` or `read_task_details(...)` to retrieve scout results, and do not pass `bg_*` background ids, planner slugs, short prefixes, or fabricated ids as task ids.
5. On cold CI or a disproved exact file, fall back to the nearest stable production boundary instead of preserving a guessed exact path. Use one structure/symbol check if needed, then stop scouting and carry the uncertainty into task specs.

## Expected Outcome

- The full useful scout wave is queued once, terminal scout ids are retired, note review happens before DAG shaping, and residual uncertainty moves into task specs instead of another scout wave.
