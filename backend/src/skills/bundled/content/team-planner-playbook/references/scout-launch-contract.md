# Scout Launch Contract
Use this reference immediately before the first scout wave or whenever scout launching stalls.

## Task/Goal

- You are about to launch the first useful scout wave.

## Avoid

- Never launch explorers for benchmark tests when a plausible production owner already exists.
- Never use a scout to locate or correct a benchmark test path mismatch; put the literal test path in task prose and scout the production owner path instead. Never use scouts to locate or correct benchmark test path mismatches; do not use scouts to repair benchmark test paths.
- Never pass `*/tests/*`, `test_*.py`, an unconfirmed test-derived path, or a missing test-derived path in scout `target_paths` when production owners exist.
- Never pass an exact file to a scout after a file-symbol query found no indexed symbols and workspace structure shows a live directory or nested files for that same owner family.
- Never bundle unrelated exact files or the whole first-wave ledger into one explorer.
- Never check or wait on a scout id after it reports `delivered`, `Posted.`, `[COMPLETED]`, `[ALREADY_COMPLETED]`, or `[NO TASKS RUNNING]`. `Posted.` means findings were posted to Task Center notes, not in the envelope.

## Workflow

1. Scrub `target_paths` first: every entry should be a live production owner file/directory unless tests are explicitly the owner surface; do not use scouts to repair benchmark test paths.
2. Call `run_subagent(agent_name="scout", input={"target_paths": [...]}, task_note="...")` with one unresolved owner slice per scout.
3. Queue the whole useful wave before any progress check or wait.
4. After the wave, read notes with default scope. Notes from `run_subagent` scouts live on the current planner task; do not use `scope="sibling"` for them. Use `read_task_note(paths=[...])` for known scopes, or `read_task_note(scope="own", paths=None)` when exact scout paths are unclear after a `Posted.` envelope.
5. On cold CI or a disproved exact file, fall back to the nearest stable production boundary instead of preserving a guessed exact path.

## Expected Outcome

- The full useful scout wave is queued once, terminal scout ids are retired, and note review happens before DAG shaping.
