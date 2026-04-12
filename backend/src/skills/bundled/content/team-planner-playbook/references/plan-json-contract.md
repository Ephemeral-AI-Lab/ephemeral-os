# Plan JSON Contract
Use this reference immediately before emitting final plan JSON.
## Shape rules
- Must emit `{"items": [...], "rationale": "..."}`.
- Finish the benchmark-surface ledger, deps, briefings, and item shapes before loading this reference.
- After this reference loads, the very next assistant content must be the final JSON object only. No recap, no checklist, no "now emit", and no extra prose.
- Must keep each item on the runtime `TaskSpec` shape.
- Never load this reference in parallel with `root-plan-self-check`; wait for that tool call to finish first.
- Must use `agent_name` only for agents listed in the roster injected into your context. If no roster is present, the defaults are `developer`, `validator`, and `team_planner`.
- Must use `local_id` for the lane label such as `compat_fix` or `validate_misc`.
- Do NOT set `kind` — it is auto-inferred from the target agent's role (planner-role → expandable, all others → atomic).
- Must put `owned_files`, `owned_failures`, `verify`, `verification`, `touches_paths`, and similar execution details under `payload`.
- Must keep `deps` as a top-level item field.
- Must keep `briefings` at the item top level.
- Each briefing must be exactly `{"name":"...","source":"artifact","ref":"..."}` or `{"name":"...","source":"inline","inline":"..."}`.
- Atlas or staged refs still use `source:"artifact"` with the `atlas:...` or `scout:...` ref. Never emit `source:"atlas"`, and never pair `source:"inline"` with `ref`.
- Must emit each `local_id` only once.
- Must not submit placeholder scout scaffolds such as `plan-anchor-*`, `*_scout`, or `developer_override`. Scouts are tool calls, not plan items.
- If the submitted layer has 3 or more concrete non-planner lanes, end with one terminal `validator` whose `deps` cover those terminal siblings; do not add per-branch validators at the same parent layer unless a real shared-risk branch cut needs one.
- Planner-role items are expandable (further decomposition); do not use planner-role items as disguised developers or scouts.
- If two exact-file slices arrived through separate scout artifacts, keep them as separate leaves or place them behind one residual child planner. Do not merge them into one atomic developer lane without shared-owner evidence.
## Failure-surface rules
- Before final JSON, freeze a tiny benchmark-surface ledger from the exact prompt paths or ids plus any validator-backed downgrades. Copy only from that ledger into `owned_failures`, `verify`, `verification`, or `reproduction`.
- On any submit retry, edit `owned_failures`, `verify`, `verification`, or `reproduction` only by copying from that frozen ledger or exact validator packet text.
- Must keep `owned_failures` exact to the benchmark surface, even when `owned_files` points at a different production owner.
- When one root lane owns many failures from the same benchmark file, prefer that exact benchmark file path over dumping dozens of guessed or same-family node ids into the root DAG.
- When the prompt already names one or a few exact nodes, keep only those exact nodes or broaden to that same prompt file path; never substitute a sibling node from the same file or owner family.
- Must preserve the exact benchmark file basename and directory segments already quoted by the prompt, scout notes, or earlier planner notes.
- If an inherited parent lane only names an exact benchmark file path, keep that file path until prompt, scout, or validator evidence supplies an exact existing node id.
- Never normalize one benchmark path into a nearby sibling such as `test_utils_dataframe.py` -> `test_utils.py`.
- If validation rejects a guessed benchmark node, first fall back to the exact benchmark file path already named in the prompt or notes.
- If validation rejects a nearby-sibling benchmark path, repair it by restoring the exact prompt basename and directory from the frozen ledger, not by trying another sibling with similar tokens.
- Never replace a rejected benchmark entry with a same-family sibling node or file just because the owned production path looks similar.
- If no exact prompt, parent, scout, or validator-backed benchmark surface exists for one narrow lane after that repair, omit that uncertain `owned_failures` entry instead of guessing another sibling path or same-family node.
- Must keep `verify` aligned with the exact benchmark surface already named by the prompt or validator packet. Never rewrite a benchmark test path or node to mirror the production owner path.
- When a leaf lane already owns one or a few exact pytest nodes, `verify` should usually name those exact nodes instead of the whole benchmark file.
- If the production owner is `pkg/io/json.py` but the prompt surface is `pkg/io/tests/test_json.py`, keep the prompt surface verbatim. Do not synthesize nearby names such as `pkg/io/tests/test_io_json.py`, `pkg/cli/tests/test_cli.py`, or `pkg/pkg/tests/test_cli.py`.
## Few-shot examples
- Example: root scouts already mapped `hdf.py`, `parquet/`, `groupby.py`, and five tiny exact files.
  Emit `developer(hdf_fix)` plus expandable `team_planner` items like `parquet_child` or `groupby_child`, then direct tiny-file developers or one residual child planner for the rest. Do not serialize the whole layer into eight atomic developers only because all owners are known.
- Example: the parquet package still needs internal decomposition, and your draft says `{"agent_name":"developer","local_id":"parquet_fix",...}`.
  Change that lane to `{"agent_name":"team_planner","local_id":"parquet_child",...}` (auto-expandable) or collapse it to one bounded atomic developer if the scope is already leaf-ready.
  Do not target a developer for work that needs further decomposition.
- Example: you queued `root-plan-self-check` and this contract together, or after loading this contract you start saying "Now I need to map test ids" or "Now emit the final JSON".
  That means you loaded the contract too early. Reload the ending chain sequentially if the self-check never finished; otherwise fall back to the prompt-backed benchmark file paths you already have, keep any exact known nodes, and emit the JSON object as the next assistant content.
- Example: a parent lane handed down `pkg/tests/test_io_json.py` with no exact node, and the child draft is tempted to invent `::test_chunksize`.
  Keep `owned_failures:["pkg/tests/test_io_json.py"]` until live evidence names an exact existing node. Do not guess a narrower pytest id just to make the child lane look more precise.
- Example: the prompt named `pkg/io/tests/test_json.py::test_read_json_engine_str[ujson]`, but your draft tried `pkg/io/tests/test_json.py::test_to_json_lines`.
  Keep the exact prompt node, or downgrade only to `pkg/io/tests/test_json.py`. Do not switch to another same-file node just because the owner is `io/json.py`.
- Example: the prompt named `pkg/dataframe/tests/test_groupby.py` or `pkg/dataframe/io/tests/test_parquet.py`, but your draft rewrote them to `pkg/dataframe/io/tests/test_groupby.py` or `pkg/tests/test_parquet.py`.
  Keep both `owned_failures` and `verify` on the exact prompt paths.
- Example: the prompt named `pkg/tests/test_utils_dataframe.py::test_valid_divisions[a-b]`, and a later retry already repaired nearby siblings like `pkg/io/tests/test_json.py` or `pkg/tests/test_cli.py`.
  Reopen the frozen ledger and restore the utils entry exactly: keep the prompt node or downgrade only to `pkg/tests/test_utils_dataframe.py`. Do not guess `pkg/tests/test_utils.py` or `pkg/dataframe/tests/test_utils.py` from the owner `pkg/dataframe/utils.py`.
