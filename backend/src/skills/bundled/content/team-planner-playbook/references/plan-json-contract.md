# Plan JSON Contract
Use this reference immediately before emitting final plan JSON.
## Shape rules
- Must emit `{"items": [...], "rationale": "..."}`.
- After this reference loads, the next planning action must be final JSON text, not `run_subagent(...)` or any other worker-start tool call.
- Must keep each item on the runtime `WorkItemSpec` shape.
- Must use `agent_name` only for registered workers: `developer`, `validator`, or `team_planner`.
- Must use `local_id` for the lane label such as `compat_fix` or `validate_misc`.
- Must use only `kind: "atomic"` or `kind: "expandable"`.
- `developer` and `validator` items must be `atomic`; only `team_planner` items may be `expandable`.
- Must put `owned_files`, `owned_failures`, `verify`, `verification`, `touches_paths`, and similar execution details under `payload`.
- Must keep `deps` as a top-level item field.
- Must keep `briefings` at the item top level.
- Each briefing must be exactly `{"name":"...","source":"artifact","ref":"..."}` or `{"name":"...","source":"inline","inline":"..."}`.
- Atlas or staged refs still use `source:"artifact"` with the `atlas:...` or `scout:...` ref. Never emit `source:"atlas"`, and never pair `source:"inline"` with `ref`.
- Must emit each `local_id` only once.
- Must not submit placeholder scout scaffolds such as `plan-anchor-*`, `*_scout`, or `developer_override`. Scouts are tool calls, not plan items.
- If the submitted layer has 3 or more concrete non-planner lanes, must include a terminal `validator` whose `deps` cover those terminal siblings.
- `team_planner` items should be `expandable`; do not use atomic `team_planner` items as disguised developers or scouts.
- If two exact-file slices arrived through separate scout artifacts, keep them as separate leaves or place them behind one residual child planner. Do not merge them into one atomic developer lane without shared-owner evidence.
## Failure-surface rules
- Before final JSON, build a tiny benchmark-surface ledger from the exact prompt paths or ids plus any validator-backed downgrades. Copy only from that ledger into `owned_failures`, `verify`, `verification`, or `reproduction`.
- Must keep `owned_failures` exact to the benchmark surface, even when `owned_files` points at a different production owner.
- When one root lane owns many failures from the same benchmark file, prefer that exact benchmark file path over dumping dozens of node ids into the root DAG.
- When one leaf lane is already narrow to one or a few exact prompt nodes, keep those exact node ids.
- Must preserve the exact benchmark file basename and directory segments already quoted by the prompt, scout notes, or earlier planner notes.
- If an inherited parent lane only names an exact benchmark file path, keep that file path until prompt, scout, or validator evidence supplies an exact existing node id.
- Never normalize one benchmark path into a nearby sibling such as `test_utils_dataframe.py` -> `test_utils.py`.
- If validation rejects a guessed benchmark node, first fall back to the exact benchmark file path already named in the prompt or notes.
- If validation rejects a nearby-sibling benchmark path, repair it by restoring the exact prompt basename and directory, not by trying another sibling with similar tokens.
- If no exact prompt, parent, scout, or validator-backed benchmark surface exists for one narrow lane after that repair, omit that uncertain `owned_failures` entry instead of guessing another sibling path.
- Must keep `verify` aligned with the exact benchmark surface already named by the prompt or validator packet. Never rewrite a benchmark test path or node to mirror the production owner path.
- When a leaf lane already owns one or a few exact pytest nodes, `verify` should usually name those exact nodes instead of the whole benchmark file.
- If the production owner is `pkg/io/json.py` but the prompt surface is `pkg/io/tests/test_json.py`, keep the prompt surface verbatim. Do not synthesize nearby names such as `pkg/io/tests/test_io_json.py`, `pkg/cli/tests/test_cli.py`, or `pkg/pkg/tests/test_cli.py`.
## Few-shot examples
- Example: root scouts already mapped `hdf.py`, `parquet/`, `groupby.py`, and five tiny exact files.
  Emit `developer(hdf_fix)` plus expandable `team_planner` items like `parquet_child` or `groupby_child`, then direct tiny-file developers or one residual child planner for the rest. Do not serialize the whole layer into eight atomic developers only because all owners are known.
- Example: the parquet package still needs internal decomposition, and your draft says `{"agent_name":"developer","local_id":"parquet_fix","kind":"expandable",...}`.
  Change that lane to `{"agent_name":"team_planner","local_id":"parquet_child","kind":"expandable",...}` or collapse it to one bounded atomic developer if the scope is already leaf-ready.
  Do not submit an expandable `developer`.
- Example: five unrelated small owner slices remain after HDF and parquet are split out.
  Keep them as separate developer lanes if the cap allows, or park them behind one residual `team_planner` child with inherited scout briefings. Do not merge `json.py`, `cli.py`, `config.py`, `compatibility.py`, and `utils.py` into one atomic `core_misc_fix` developer lane.
- Example: you drafted `{"agent_name":"team_planner","local_id":"plan-anchor-hdf","kind":"atomic","payload":{...,"developer_override":"developer"}}`.
  Replace that placeholder with a real explored lane after the scout wave, such as `{"agent_name":"developer","local_id":"hdf_fix","kind":"atomic","payload":{...}}`, and add a terminal validator if the layer now has 3+ concrete non-planner lanes.
- Example: a parent lane handed down `pkg/tests/test_io_json.py` with no exact node, and the child draft is tempted to invent `::test_chunksize`.
  Keep `owned_failures:["pkg/tests/test_io_json.py"]` until live evidence names an exact existing node. Do not guess a narrower pytest id just to make the child lane look more precise.
- Example: the prompt named `pkg/io/tests/test_json.py::test_read_json_engine_str[ujson]`, `pkg/tests/test_cli.py`, and `pkg/tests/test_utils_dataframe.py::test_valid_divisions`.
  Repair the lane by restoring those exact prompt surfaces or downgrading only to those same file paths. Do not invent `test_io_json.py`, `pkg/cli/tests/test_cli.py`, or move `test_utils_dataframe.py` under `io/tests` just because the owner file lives in `io/`.
- Example: the prompt named `pkg/tests/test_cli.py`, but your draft rewrote it to `pkg/cli/tests/test_cli.py` or `pkg/pkg/tests/test_cli.py`.
  Keep both `owned_failures` and `verify` on `pkg/tests/test_cli.py`.
  Do not relocate the benchmark file to mirror the production module or duplicate directory tokens.
- Example: the prompt named `pkg/tests/test_utils_dataframe.py::test_valid_divisions[a-b]`, but your draft accidentally wrote `pkg/tests/test_utils.py::test_valid_divisions`.
  Repair the entry by restoring the exact prompt surface: either keep the exact node id or downgrade to `pkg/tests/test_utils_dataframe.py`. Do not submit `pkg/tests/test_utils.py`, and do not delete the utils failure from `owned_failures`.
