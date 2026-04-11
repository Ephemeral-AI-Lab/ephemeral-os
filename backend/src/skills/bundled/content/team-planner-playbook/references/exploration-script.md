# Exploration Script

Use this reference only on fresh benchmark roots or any turn that still lacks clear ownership.

## Workflow

1. Must start with one narrow `ci_workspace_structure(path=...)` pass on the deepest shared production directory or package already implied by the prompt failures.
2. Must follow with `ci_scoped_status(scope_paths=[...])` on exactly one existing production path from that listing. If the listing already reveals a concrete file candidate, prefer that exact file over a directory packet.
3. Must use code intelligence to seed likely owners from live symbols, package structure, and the scoped packet before naming scout slices.
4. Must translate benchmark failure evidence into production-owner slices before scout launch. Failing test paths stay evidence only.
5. Any exact production file or package named in reasoning, scout input, or plan output must already exist in the current live workspace listing or scoped packet.
6. If another failure family sits outside the current anchor, must branch through the nearest production directory or package for that family after the first anchor, not by widening the first anchor to cover everything at once.
7. If a similar-looking filename is absent from the live listing, keep that owner slice unresolved and scout the nearest existing production boundary instead of inventing a sibling file.
8. If more than one owner slice is still unresolved after the anchor, the next planning action must be a scout wave, not more local file-level exploration or final DAG synthesis.
9. Must launch scouts only after that live anchor exists.
10. Must keep each scout on one distinct unresolved owner slice.
11. Must stop exploring once the current plan layer can name ready work plus residual boundaries.

## Scout fanout strategy

1. Must fan out by distinct production-owner slices, not by raw failing-test count.
2. The first wave should match the real unresolved frontier. Often that is 3-6 scouts, but it may be 1 when only one slice is genuinely unclear.
3. If more slices are unresolved than the current layer can responsibly carry, must launch the most diagnostic disjoint subset now and park the rest behind child planners or a later wave.
4. Must keep every scout narrow enough that it answers one ownership question.
5. Must launch another wave only when the first wave returns partial ownership and several disjoint owner slices are still unresolved.
6. Must stop fanout as soon as the next plan layer can name the dominant owner slices, residual boundaries, and at least one ready leaf lane.

## Few-shot examples

If the live anchor shows failures that plausibly map to `pkg/io/`, `pkg/schema/`, and `pkg/compat/`, the first wave should be three scouts:

- Scout 1: `target_paths=["pkg/io"]`
- Scout 2: `target_paths=["pkg/schema"]`
- Scout 3: `target_paths=["pkg/compat"]`

Must not split that into one scout per failing test file.
Must not collapse those three owner slices into one omnibus scout.
Must stop after that wave if it already identifies the dominant owner slice and the residual boundary.

If benchmark failures mention `pkg/io/tests/test_hdf.py`, `pkg/io/tests/test_parquet.py`, `pkg/tests/test_groupby.py`, and `pkg/tests/test_config.py`, the root anchor should look like:

- `ci_workspace_structure(path="pkg/io")`
- `ci_scoped_status(scope_paths=["pkg/io"])`

Then scout the production-owner slices such as `pkg/io/hdf.py`, `pkg/io/parquet/`, `pkg/groupby.py`, and `pkg/config.py`.
Do not anchor on `pkg`, `pkg/tests`, or the failing test files once those production candidates are already visible.

If most failures already cluster under `pkg/dataframe/io/`, but a few others later point to `pkg/dataframe/groupby.py`, `pkg/config.py`, or `pkg/cli.py`, the first anchor must still stay inside the deeper shared subtree:

- Right: `ci_workspace_structure(path="pkg/dataframe/io")`
- Right: `ci_scoped_status(scope_paths=["pkg/dataframe/io/hdf.py"])`
- Later: branch with another production-side listing to reach `pkg/dataframe/groupby.py`, `pkg/config.py`, or `pkg/cli.py`

- Wrong: `ci_workspace_structure(path="pkg")`
- Wrong: `ci_scoped_status(scope_paths=["pkg/dataframe/io", "pkg/dataframe/groupby.py", "pkg/config.py", "pkg/cli.py"])`

The first anchor is a cold-start probe, not a census of every family in the benchmark.

If the first anchor is already inside `pkg/io`, but another failing family is only named by `pkg/tests/test_groupby.py`, the next discovery step must branch through a production-side listing such as `ci_workspace_structure(path="pkg")` or `ci_workspace_structure(path="pkg/dataframe")`.
If that listing shows `pkg/groupby.py` or `pkg/groupby/`, anchor there.
Never call `ci_scoped_status(scope_paths=["pkg/tests/test_groupby.py"])` as a substitute for missing production mapping.

If the anchor points to `pkg/groupby.py`, `pkg/io/parquet/`, `pkg/io/json.py`, `pkg/config.py`, and `pkg/compat.py`, do not collapse the last three into one "misc" planner just because they are small.
Scout them separately until live evidence shows that two or more really converge on the same production owner.

If the prompt plus the first scoped packet already expose exact file candidates like `pkg/config.py`, `pkg/compat.py`, and `pkg/cli.py`, do not widen the first status packet to `pkg` or submit another packet covering all three.
Use the existing anchor to launch one scout per exact file or park the overflow behind a child planner.

If benchmark failures mention `pkg/tests/test_config.py`, `pkg/tests/test_cli.py`, or `pkg/tests/test_compat.py` outside the first anchor, branch through `ci_workspace_structure(path="pkg")` and then anchor exact production paths like `pkg/config.py`, `pkg/cli.py`, or `pkg/compat.py` when they exist.
Never use those benchmark test files as fallback owner slices.

If benchmark failures mention `pkg/tests/test_utils_dataframe.py`, but the live listing shows `pkg/utils.py` and does not show `pkg/utils_dataframe.py`, do not invent `pkg/utils_dataframe.py`.
Keep the owner unresolved until a scout maps the existing production surface such as `pkg/utils.py` or another listed dataframe helper.
The benchmark test basename is evidence only; it is not proof that a same-named production file exists.

If the live anchor confirms `pkg/io/hdf.py` as the dominant owner and a child branch still needs deeper mapping inside `pkg/io/parquet/`, emit one direct developer lane for HDF and park parquet behind a child planner.
Do not hold the ready HDF lane hostage just because parquet is still exploratory.

If the anchor shows several plausible owners but no scout has run yet, do not load final-plan references and do not draft JSON from reasoning alone.
The next step must look like `run_subagent(agent_name="scout", input={"target_paths":["pkg/io/parquet"]}, task_note="Map parquet owner slice")`, repeated for the other unresolved slices.
Only after those scout briefs return may the planner load decomposition guidance and finalize the DAG.

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
