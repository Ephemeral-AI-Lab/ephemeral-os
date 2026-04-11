# Task Planning Decomposition

Use this reference only after ownership is already clear enough to draft the DAG.

## Decide atomic vs expandable

1. Make a lane atomic when one owner slice, one patch surface, and one verification family are already clear enough for a leaf worker.
2. Make a lane expandable when it still hides multiple owner slices, region-level decomposition inside one broad file, or more ready work than the current layer should flatten.
3. Make a lane expandable when the owner is already a package, directory, or broad single file and the next useful decision is internal lane shaping rather than direct patching.
4. Make a lane expandable when the alternative atomic lane would own several unrelated exact files merely because each slice is small.
5. Preserve at least one direct ready leaf lane whenever live evidence already supports it, even if sibling branches still need child planners.
6. Treat exact-file pairs as separate owner slices unless scouts already proved one shared helper or boundary that truly owns both.

## DAG shaping rules

- Must split distinct owner clusters into separate execution lanes.
- Must keep ready work concrete and residual work explicit.
- Must use deps only for real sequencing, shared-risk branch cuts, or verification boundaries.
- Must let child planners own their own deeper validation instead of using parent validators as decorative barriers.
- Must add validators only when they reduce uncertainty for concrete lanes.
- Must keep the plan between 2 items and `max_plan_size`.
- Must either keep mapped small-file slices as separate leaves or park them behind a residual child planner.
- Must keep broad package or file slices expandable at the parent layer when flattening them would collapse the DAG into one shallow frontier.
- Never hide unresolved owner clusters behind validator-only coverage.
- Never drop validation or cross-surface coverage just to trim one item.
- Never call a bundled leftovers lane atomic unless one shared live owner explains every file in it.

## Validator heuristic

- Prefer one terminal validator when several concrete lanes converge on the same public surface.
- Add one midflight validator only when it protects a genuinely risky branch cut before later lanes build on it.
- Every validator must depend on at least one upstream non-validator sibling.
- A terminal validator must depend on every terminal non-validator sibling in that layer so it gates the whole ready frontier, not just one branch.
- Recommend not to have more than three validators in a single layer.
- Prefer to a midflight validator when the concrete lane is a long path or logically more risky.

## Few-shot examples

- Example: root evidence clearly isolates `pkg/io/hdf.py`, while `pkg/io/parquet/` and `pkg/groupby.py` still each need their own decomposition.
  Emit `developer(hdf)` now, then two child planners for parquet and groupby.
  Do not collapse parquet and groupby into one residual bucket just because both live under `dataframe/`.
- Example: root scouts map `pkg/io/hdf.py`, `pkg/io/parquet/`, `pkg/groupby.py`, `pkg/io/json.py`, `pkg/utils.py`, `pkg/cli.py`, `pkg/config.py`, and `pkg/compat.py`.
  Emit `developer(hdf)` now.
  Keep `parquet` and `groupby` expandable.
  Put the remaining small slices behind direct leaves or one residual child planner.
  Do not submit eight root developers plus one validator after a fully mapped scout wave.
- Example: one huge `pkg/groupby.py` file contains separate `cov`, `unique`, and `value_counts` regions with different verification families.
  Use a child planner for the file-level region split even though the owner file is singular.
  If the parent already handed down a scout for `pkg/groupby.py`, reuse that brief plus symbol lookup to emit the three lanes directly.
  Do not launch fresh `cov`, `unique`, or `value_counts` scouts on the same file unless one family still lacks real owner evidence.
  Do not force one atomic developer just because the file path is singular.
- Example: `pkg/config.py` and `pkg/compat.py` failures both import the same helper after scouts confirm that helper is the real owner.
  Merge them behind one developer or child planner that targets the shared helper.
  Do not merge them before that live shared-owner evidence exists.
- Example: `pkg/tests/test_cli.py` and `pkg/tests/test_compatibility.py` both fail after one release, and scouts map `pkg/cli.py` plus `pkg/compatibility.py` as separate exact owners.
  Emit two direct developers, or park one behind a residual child planner if the layer is crowded.
  Do not emit one atomic `cli_compat_fix` lane or one scout artifact that pretends `pkg/cli.py|pkg/compatibility.py` is a single subsystem.
- Example: one dominant cluster has 32 targets, two secondary clusters have 11 and 8 targets, and the remaining slices are `cli`, `config`, `compat`, `json`, and `utils` with only 1-4 targets each.
  Emit the dominant lane directly, keep the two secondary clusters separate, and park the residual small slices behind one or more child planners only if live evidence still leaves them unresolved.
  Do not create one atomic "misc fixes" lane just because those residual slices are individually small.
- Example: HDF and parquet are already split, and five remaining single-file production modules each have their own scout brief (`json.py`, `cli.py`, `config.py`, `compatibility.py`, `utils.py`).
  Either keep those five developers separate or put them behind one residual child planner that can schedule them well.
  Do not collapse those unrelated files into one atomic developer just to save root-plan slots.
- Example: four unrelated direct developers converge only at the grading command.
  Prefer one terminal validator or grading lane at the end.
  Do not decorate the graph with paired validator siblings purely for symmetry.
- Example: a risky serializer change lands early and three later lanes depend on its shape.
  Place one midflight validator after that serializer lane, then resume the dependent lanes, then keep one final terminal validator.
