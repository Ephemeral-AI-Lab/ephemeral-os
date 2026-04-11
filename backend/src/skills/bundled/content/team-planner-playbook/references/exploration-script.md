# Exploration Script

Use this reference only on fresh benchmark roots or any turn that still lacks clear ownership.

## Workflow

1. Must start with one narrow `ci_workspace_structure(path=...)` pass on the deepest shared production directory or package already implied by the prompt failures.
2. Must follow with `ci_scoped_status(scope_paths=[...])` on exactly one existing production path from that listing.
3. Must use code intelligence to seed likely owners from live symbols, package structure, and the scoped packet before naming scout slices.
4. Must translate benchmark failure evidence into production-owner slices before scout launch. Failing test paths stay evidence only.
5. If you catch yourself counting failing tests, guessing missing dependencies, checking benchmark test files, or listing source files to inspect before a scout wave, reset to the anchor instead of continuing that thread.
6. Any exact production file or package named in reasoning, scout input, or plan output must already exist in the current live workspace listing or scoped packet.
7. If another failure family sits outside the current anchor, must branch through the nearest production directory or package for that family after the first anchor, not by widening the first anchor to cover everything at once.
8. If a similar-looking filename is absent from the live listing, keep that owner slice unresolved and scout the nearest existing production boundary instead of inventing a sibling file.
9. If more than one owner slice is still unresolved after the anchor, the next planning action must be a scout wave, not final DAG synthesis.
10. Must keep each scout on one distinct unresolved owner slice and stop exploring once the current plan layer can name ready work plus residual boundaries.

## Few-shot examples

- Example: the live anchor shows failures that plausibly map to `pkg/io/`, `pkg/schema/`, and `pkg/compat/`.
  Launch three scouts, one per owner slice. Must not split that into one scout per failing test file, and must not collapse those three owner slices into one omnibus scout.
- Example: benchmark failures mention `pkg/io/tests/test_hdf.py`, `pkg/io/tests/test_parquet.py`, `pkg/tests/test_groupby.py`, `pkg/tests/test_cli.py`, `pkg/tests/test_config.py`, and `pkg/tests/test_compat.py`.
  Start with `ci_workspace_structure(path="pkg/io")`, then `ci_scoped_status(scope_paths=["pkg/io/hdf.py"])`, then scout `pkg/io/hdf.py`, `pkg/io/parquet/`, `pkg/groupby.py`, `pkg/cli.py`, `pkg/config.py`, and `pkg/compat.py`.
  The first anchor is a cold-start probe, not a census of every family in the benchmark.
- Example: the anchor shows several plausible owners but no scout has run yet.
  The next step must look like `run_subagent(agent_name="scout", input={"target_paths":["pkg/io/parquet"]}, task_note="Map parquet owner slice")`, repeated for the other unresolved slices.
  Only after those scout briefs return may the planner load decomposition guidance and finalize the DAG.
- Example: benchmark failures mention `pkg/tests/test_utils_dataframe.py`, but the live listing shows `pkg/utils.py` and does not show `pkg/utils_dataframe.py`.
  Keep the owner unresolved until a scout maps the existing production surface. Do not invent `pkg/utils_dataframe.py`.

## Rules

- Never open with root-wide CI queries.
- Never use the workspace root, repo package root, or a broad top-level package as the first anchor when the prompt already points at a deeper production area.
- Never call the first `ci_scoped_status(...)` with more than one scope path.
- Never spend first-wave scouts on benchmark test files when a plausible production owner exists.
- Never use a benchmark test file as a temporary `ci_scoped_status(...)` anchor while "figuring out" an out-of-anchor failure family.
- Never guess missing production files from test names.
- Never name an exact production file unless that exact path appeared in the current live listing or scoped packet.
- Never bundle unrelated owner slices into one scout just to reduce lane count.
- Never sit on an anchor-only picture for a long reasoning pass when unresolved owner slices still exist; scout immediately.
- Never keep querying every candidate owner locally after the anchor already named distinct unresolved slices; hand file-level reading to scouts.
- Never copy benchmark test paths or test directories into scout `target_paths` after the anchor exposed production owners for those failures.
- Never map a benchmark cluster to a production file solely because the names look similar.
- Never use Atlas as a substitute for the first same-run scout wave.
- Never keep scouting after owner sufficiency is reached.
- Treat duplicate-scout rejection, repeated wait protocol errors, and budget warnings as stop-and-plan signals.
