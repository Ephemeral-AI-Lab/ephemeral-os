# Replanner Scout Launch Contract
Use this reference only when the failed task's root cause trace has unresolved seams you cannot justify a corrective fix from, and existing Task Center file notes and CI evidence do not already name those seams. This lane is targeted deep diagnostics: each scout stays narrow on one production code path and one stated trace gap. Wave size scales to the number of distinct trace gaps you can justify, not a fixed cap.

## Task/Goal

- You are about to launch one targeted diagnostic scout per distinct root-cause trace gap. Each scout is narrow (one production file or directory in `target_paths`, one named seam in the scout input `context`); the wave as a whole covers every trace gap you cannot already answer from existing notes.
- Scouts in this lane collect focused root-cause evidence. The replanner, not a child planner and not an empty replan, synthesizes the corrective plan from whatever evidence returns, including partial findings and disproved trace gaps.

## Avoid

- Never launch broad boundary-mapping scouts or scouts with vague scopes; each scout takes exactly one production code path in `target_paths` and one stated trace-gap triplet in the scout input `context`.
- Never launch a scout without a stated trace-gap triplet (one failing test or cluster + one suspected production code path + one named symbol or seam). Speculation scouts are banned; drop the gap if you cannot state the triplet.
- Never launch duplicate scouts, redundant scouts on the same code path, or scouts on paths whose Task Center file notes already contain a root-cause-grade finding; if the note already answers the gap, drop that scout.
- Never pair a production path and its benchmark test in one scout `target_paths` list; scout the production path and keep the failing test path in the scout input `context`.
- Never pass `*/tests/*`, `test_*.py`, an unconfirmed test-derived path, or a missing test-derived path in scout `target_paths`. Tests stay evidence; scouts target production code paths.
- Never use scouts to locate or correct benchmark test path mismatches; scout the production code path instead and carry the literal test path in task prose.
- Never pass an exact file to a scout after `ci_query_symbol(...)` reports no indexed symbols and workspace structure shows a live directory or nested files for the same package family; use the live package boundary in task `scope_paths` instead.
- Never bundle unrelated exact files into one scout; one trace gap per scout, one production code path per scout, split disjoint targets into separate scouts.
- Never check or wait on a scout id after a terminal envelope (`delivered`, `Posted.`, `[COMPLETED]`, `[ALREADY_COMPLETED]`, `[NO TASKS RUNNING]`); read the posted note next.
- Never delegate corrective synthesis to a child `team_planner`, and never submit an empty replan as a fallback for partial or ambiguous scout results. The replanner owns synthesis. The no-production-path empty-replan rule still applies only when no production path exists in the trace or evidence.
- Do not call CI, edit, diagnostics, action-reference, or submission tools on scout-scoped paths while any scout is still running; wait for terminal envelopes and consume the posted notes first.

## Workflow

1. Before launching any scouts, call `read_file_note(file_path="...")` on every production path named by the root cause trace. For any path whose note already contains a root-cause-grade finding (named symbol, exact line range, confirmed repro, or disproven seam), skip scouting that path. Empty note reads are successful freshness checks.
2. Enumerate the distinct trace gaps. For each, state the triplet in one sentence: one failing test id or cluster + one suspected production path + one named symbol or seam. If you cannot state the triplet for a gap, drop it; do not scout it.
3. For each remaining trace gap, call `run_subagent(agent_name="scout", input={"target_paths": ["<exact_production_path>"], "context": "Diagnostic for <trace-gap triplet>; confirm or rule out seam at <symbol> in <path>; post evidence via submit_file_note."})` with exactly one entry in `target_paths` per scout.
   Bad: `target_paths=["pkg/mod.py", "pkg/tests/test_mod.py"]` mixes test with production path. Good: `target_paths=["pkg/mod.py"]` with the failing test named in `context` as verification evidence.
4. Queue the whole useful scout wave in one turn before any progress check or wait. Size the wave to the distinct trace gaps, not a fixed count; never launch duplicates, redundant scouts on the same code path, or gap-less scouts.
5. After each terminal envelope, read scout findings with `read_file_note(file_path="...")` for every exact scout `target_paths` entry you launched. Do not drop file extensions, reuse an unrelated prior path, or skip a scout path. Scouts/subagents are not Task Center tasks. Do not call `read_task_graph()` or `read_task_details(...)` to retrieve scout results, and do not pass `bg_*` background ids, planner slugs, short prefixes, or fabricated ids as task ids.
6. Synthesize the corrective plan from whatever evidence returned, including partial findings and disproved trace gaps. Confirmed seams become pinned corrective `scope_paths` in `action-add-tasks` or `action-cancel-and-redraft`; partial or disproving evidence narrows the corrective task and is cited in each task `spec`'s `2. Task Details:` field. Do not delegate synthesis to a child `team_planner` and do not submit an empty replan unless the no-production-path rule genuinely applies.
7. Do not load an action reference (`action-add-tasks` or `action-cancel-and-redraft`) while any diagnostic scout is still running; finish waits and note reads first, then load the single action reference and proceed to the terminal `submit_replan(...)` call.

## Expected Outcome

- One targeted diagnostic scout per distinct trace gap posted a focused note on an exact production code path, all scout ids are retired after terminal envelopes, and the replanner synthesizes one concrete corrective plan from the combined evidence, including partial or disproving findings, without delegating synthesis or punting via an unjustified empty replan.
