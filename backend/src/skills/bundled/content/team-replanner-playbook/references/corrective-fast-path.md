# Corrective Fast Path

Use this reference on benchmark resume/replan turns when the incoming validator packet already names exact failing pytest ids and exact existing owner file(s).

## Goal

Turn a validator-backed failure packet into a corrective JSON payload without re-debugging the same owner cluster from the replanner lane.

## Fast path

1. Check whether ownership is already settled.
   If the validator packet already gives:
   - exact failing pytest ids or exact retry command
   - exact existing owner file(s) from the current checkout or fresh CI confirmation

   then ownership discovery is over for this cluster.

2. Allow at most one live confirmation per cluster.
   The default first live-tool call is `ci_scope_status(scope_paths=[...])` on the exact owner surface or owning directory.
   Use exactly one of:
   - `ci_scope_status(scope_paths=[...])` when the failure touches shared runtime, retry, checkpoint, or any benchmark owner surface that may have drifted
   - one `ci_read_file(...)` on an exact owner file only when you still need one last owner confirmation and the scope anchor is already established or the turn can justify skipping live anchoring entirely

   Do not spend both unless the first is about runtime branch state and the second is on a different unresolved owner surface.

3. Do not reinterpret the benchmark test packet.
   Forbidden follow-up exploration:
   - benchmark test body reads
   - test-header or decorator reads
   - marker or parametrization queries such as `PYARROW_MARK`, `skipif`, or `parametrize`
   - shared router/plumbing reads such as `core.py`, `__init__.py`, or wrapper entry points when the owner file is already known
   - line-by-line patch recipes or message-text rewrites inferred only from replanner reasoning

4. Draft the corrective payload immediately.
   Include:
   - one `developer` item per independent owner cluster
   - exact owner file paths
   - exact failing ids or retry command
   - short symptom notes
   - nearby guardrail or verification targets
   - hypotheses only as hypotheses

   Do not include:
   - `specific_fixes`
   - exact condition rewrites
   - patch recipes framed as proven requirements

5. Treat repeated reads as a protocol failure.
   If you have already reopened the same owner cluster once and can still name the owner plus retry target, emit JSON now. More same-surface reads are evidence that you missed the stop condition.

6. Treat a missing scope anchor as a protocol failure.
   If a benchmark corrective turn opens with `ci_read_file(...)` or symbol queries on the owner files before first calling `ci_scope_status(...)`, that turn is drifting. Re-anchor on the owner surface immediately or emit JSON without further live exploration.

## Good handoff

- "Owner cluster: `dask/dataframe/io/parquet/fastparquet.py` and `dask/dataframe/io/parquet/arrow.py`; failing ids: ...; symptom: nullable dtype / types_mapper routing remains red; guardrail target: nearby parquet nullable-dtype regression slice."

## Bad handoff

- "Read the test marker, test parametrization, and shared plumbing again to work out exactly which branch should change."
- "Tell the developer to change this exact condition and rewrite this exact error string" when no validator packet or sibling artifact proved that edit.
