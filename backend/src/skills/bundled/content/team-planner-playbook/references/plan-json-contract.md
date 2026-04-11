# Plan JSON Contract

Use this reference immediately before emitting final plan JSON.

## Shape rules

- Must emit `{"items": [...], "rationale": "..."}`.
- Must keep each item on the runtime `WorkItemSpec` shape.
- Must use `agent_name` only for registered workers: `developer`, `validator`, or `team_planner`.
- Must use `local_id` for the lane label such as `compat_fix` or `validate_misc`.
- Must use only `kind: "atomic"` or `kind: "expandable"`.
- Must put `owned_files`, `owned_failures`, `verify`, `verification`, `touches_paths`, and similar execution details under `payload`.
- Must keep `deps` as a top-level item field.
- Must keep `briefings` at the item top level.
- Must emit each `local_id` only once.

## Failure-surface rules

- Must keep `owned_failures` exact to the benchmark surface.
- When one root lane owns many failures from the same benchmark file, prefer that exact benchmark file path over dumping dozens of node ids into the root DAG.
- When one leaf lane is already narrow to one or a few exact prompt nodes, keep those exact node ids.
- Must keep `verify` aligned with the chosen benchmark surface.

## Never do

- Never put a human task name into `agent_name`.
- Never use `kind: "developer"` or `kind: "validator"`.
- Never bury `deps` inside `payload`.
- Never dump dozens of same-file pytest nodes into one root-plan `owned_failures` list when the exact benchmark file path names the same surface.

## Few-shot examples

- Example: three ready misc slices plus one terminal check.
  Emit `developer` items with `local_id` values like `compat_fix`, `config_cli_fix`, and `utils_fix`.
  Emit one `validator` item with `local_id: "validate_misc"` and top-level `deps` on those developer `local_id` values.
  Do not emit `agent_name: "fix_compat_make_bytes_tuple_version"` or `kind: "developer"`.
- Example: one ready HDF lane plus residual parquet work.
  Emit `{"agent_name":"developer","local_id":"hdf_fix","kind":"atomic","payload":{...}}`.
  Emit `{"agent_name":"team_planner","local_id":"parquet_child","kind":"expandable","payload":{...}}`.
  Do not flatten payload keys like `owned_files` or `verification` beside `agent_name`.
- Example: five unrelated small owner slices remain after HDF and parquet are split out.
  Keep them as separate developer lanes if the cap allows, or park them behind one residual `team_planner` child with inherited scout briefings.
  Do not merge `json.py`, `cli.py`, `config.py`, `compatibility.py`, and `utils.py` into one atomic `core_misc_fix` developer lane.
- Example: terminal validator for `compat_fix` and `config_fix`.
  Emit `{"agent_name":"validator","local_id":"validate_misc","kind":"atomic","deps":["compat_fix","config_fix"],"payload":{"verify":"pytest ..."}}`.
  Do not place `deps` under `payload`, and do not duplicate the validator block later in the same `items` list.
- Example: one HDF lane covers 32 fail-to-pass targets, all from `pkg/tests/test_hdf.py`.
  Emit `owned_failures:["pkg/tests/test_hdf.py"]` in the root plan and keep `verify:"pytest pkg/tests/test_hdf.py ..."`.
  Do not inline 32 node ids into the root DAG just because the prompt listed them.
- Example: one JSON lane owns only `pkg/tests/test_json.py::test_read_json_chunksize`.
  Emit that exact node id because the lane is already narrow to one concrete benchmark target.
  Do not widen it to the whole file unless the planner truly cannot quote the node verbatim.
