# MIGRATION MAP — MockSquadRunner → ScenarioEventSource (assembled 2026-05-29)

Source: read-only map workflow wf_1e9e74e7-0c4 (30 agents). Verdicts confirm QUEUE-BRIDGE call_tool shim as the dominant Phase-2 strategy.


## Probe / script port specs


### complex_project_build

Advisor is rate-limited. I have enough verified primary-source evidence to produce the spec. Let me finalize.

Key findings confirmed from the code:
- All 5 `ctx.call_tool` sites are inside wrapper helpers, all NOT at top-level of an async generator (this module is plain `async def`, not async-gen).
- No background calls anywhere (no `background_task_id`, `sandbox_invocation_id`, or `background=True`).
- `_phase0_bootstrap` mutates `ctx.metadata.repo_root/cwd/exec_cwd` — out-of-band but loop-relevant.
- `_phase_d_edit_amplification`/`_phase_e_auto_squash_saturation`/`_phase_f_read_amplification`/`_phase_f_lsp_saturation` use data-dependent loops keyed off `stats` mutated by call_tool results (`_auto_squash_count` reads `result.metadata.timings`).

### complex_project_build — entry functions

| Function | Signature | Dispatch |
|---|---|---|
| `run_complex_project_build_probe` | `async (*, metadata, emit, call_tool, publish, publish_mock_record, record_tool_check, caller, sandbox_id, smoke) -> str` | The only public entry (`__all__`). runner.py imports it lazily inside `MockSquadRunner._run_complex_project_build_probe` (runner.py:1265-1267) and calls it (1270-1280) passing `call_tool=self._call_tool`, `publish=self._publish`, `publish_mock_record=self._publish_mock_record`, `record_tool_check=self._record_tool_check`, `caller=self._caller(metadata)`, `sandbox_id`, and `smoke`. |

`smoke` is the only mode param. `_run_complex_project_build_probe(metadata, emit, *, smoke)` is invoked from `_run_executor` at runner.py:617-619 (`smoke=False`) and 623-625 (`smoke=True`). There is no `index`/separate `mode` param. The module's own `CallTool` type alias (line 79) is defined LOCALLY — it does NOT import from `tool_scripts.py`. Its required call_tool shape is `await call_tool(tool_obj, raw_input, metadata, emit, allow_error=...)` (positional tool/input/metadata/emit + kw `allow_error`), matching `MockSquadRunner._call_tool` (runner.py:1583-1593).

### call_tool sites

Every loop-touching tool call routes through one of 5 thin wrappers (`_write_file`, `_edit_file`, `_read_file`, `_shell`, `_lsp`), each containing exactly one `await ctx.call_tool(...)` (lines 1382, 1404, 1429, 1452, 1491). The phase functions never call `ctx.call_tool` directly — they always go through a wrapper. The wrappers are called a data-dependent number of times (loops sized by fixture count, `_compute_amp_pairs`, the floor while-loops). The table below is per distinct wrapper/tool, not per dynamic invocation:

| # | Wrapper (line) | Tool obj | Key args | allow_error? | BACKGROUND? |
|---|---|---|---|---|---|
| 1 | `_write_file` (1382) | `write_file_tool` | `{file_path, content}` | no | no |
| 2 | `_edit_file` (1404) | `edit_file_tool` | `{file_path, old_text, new_text, description}` | param (`False` default; `True` only at the intentional-conflict site, line 1213) | no |
| 3 | `_read_file` (1429) | `read_file_tool` | `{file_path, start_line:1, end_line:200}` | no | no |
| 4 | `_shell` (1452) | `shell_tool` | `{command, timeout}` | yes (hardcoded `allow_error=True`) | no |
| 5 | `_lsp` (1491) | one of 5 LSP tool objs (`lsp_hover/find_definitions/find_references/query_symbols/diagnostics`) | varies (`file_path`, `line`, `character`, `query`, `include_declaration`) | yes (hardcoded `allow_error=True`) | no |

Distinct `await ctx.call_tool` sites: 5. Dynamic count: hundreds-to-thousands (floor is 250 smoke / 2000 full via `_tool_call_floor`). NO call anywhere passes `background_task_id` or `sandbox_invocation_id`, and no `raw_input` contains `background=True`. Zero background calls.

### out-of-band work (NOT through call_tool)

Direct `sandbox_api.*` / daemon calls (re-home to a ProbeContext-style helper — they never touch the loop):

| Line | Call | Purpose |
|---|---|---|
| 262 | `sandbox_api.shell` | Phase 0 mkdir `/ephemeral-os` (cwd-candidate loop) |
| 392 | `call_daemon_api("api.build_workspace_base", {workspace_root, reset:True})` | Phase 0 OCC/layer-stack workspace REBIND (the load-bearing one) |
| 323, 335, 357 | `sandbox_api.read_file` / `shell` ×3 | Phase 0 bootstrap verification round-trips |
| 664, 690 | `sandbox_api.read_file` | projection-consistency / api-noop-batch reads |
| 700 | `sandbox_api.edit_file` | Phase B no-op batch edit (direct-API path) |
| 990 | `sandbox_api.read_file` | `_resolve_anchor_position` |
| 1147 | `sandbox_api.read_file` | tri-source consistency |
| 1248 | `sandbox_api.edit_file` | intentional-conflict (api) |

`ctx.publish(...)` (event publish, not record): lines 1238, 1274 — both `EventType.SANDBOX_CONFLICT_DETECTED` in `_phase_f_intentional_conflicts`.
`ctx.publish_mock_record(EventType.MOCK_SANDBOX_CHECK_RECORDED, SandboxCheck(...))`: ~17 sites across all phases.
`ctx.record_tool_check(...)`: inside `_write_file`/`_edit_file` wrappers only.
`ctx.caller`: passed into every direct `sandbox_api.*` request (the `caller=ctx.caller` kwarg).

ADDITIONAL out-of-band coupling beyond publish/record/sandbox_api:
- **`ctx.metadata` mutation** (lines 305-307): `_phase0_bootstrap` sets `ctx.metadata.repo_root/cwd/exec_cwd = WORKSPACE_ROOT`. The same `metadata` object is then handed to `ctx.call_tool` on every subsequent loop call (so the toolkit defaults its cwd to `/ephemeral-os`). Under the loop bridge the metadata that the engine loop uses MUST be this same mutated object, or the loop's tool calls will run against `/testbed` and every path/consistency check breaks.
- **`stats` read-back of call_tool results**: `_auto_squash_count` (1538) inspects `result.metadata["timings"]["layer_stack.auto_squash.total_s"]` captured by `_capture_metadata`. `_phase_e`/`_phase_f_read_amplification`/`_phase_f_lsp_saturation` loop *until* counters cross floors. The bridge must feed the loop-normalized `ToolResult` (with `.metadata.timings` preserved) back through each `await call_tool` for these while-loops to terminate — `normalize_result` (scenario_adapter.py:46-51) already copies `block.metadata`, so this is satisfied if the bridge returns it.

### loop-interaction verdict (DECISIVE)

**YES** — for the engine-loop path. Every tool that must flow through the real query loop goes through exactly one of the 5 wrappers' single `await ctx.call_tool(...)`. The probe interacts with the loop ONLY via `call_tool`; everything else (`sandbox_api.*`, `call_daemon_api`, `publish`, `publish_mock_record`, `record_tool_check`, `caller`) is out-of-band and re-homes cleanly to a ProbeContext helper.

One non-call_tool, non-out-of-band coupling exists but does NOT need a new loop interaction: the `ctx.metadata` mutation at lines 305-307 must alias the loop's metadata object. This is a wiring constraint on the bridge (pass the loop's live `tool_metadata`/`RuntimeConfig`-derived metadata as `ctx.metadata`), not a second channel into the loop. No `await call_tool` ever requests background dispatch, so there is no cancel/partial-write path to mirror.

### recommended adaptation

**QUEUE-BRIDGE (zero body change).**

Rationale: This module is a deep, linear, single-task pipeline of ~13 phases whose loop interaction is funneled through 5 trivial wrappers, all sharing one `await ctx.call_tool(tool_obj, raw_input, metadata, emit, allow_error=...)` shape with no positional drift. Rewriting it as a top-level async generator would mean inlining or restructuring every wrapper and every fixture/floor loop (the `result =` value is consumed for stats and consistency assertions), which is large and error-prone for ~1600 LoC. A bridging `call_tool` shim (asyncio.Queue + per-call Future, the existing two-level-coroutine pattern) lets the probe body stay byte-identical: each `await ctx.call_tool(...)` enqueues a ToolCall, the `ScenarioEventSource` drives that as a one-call Turn, and the loop-normalized `ToolResult` resolves the Future.

Hazards for the bridge (all benign here, unlike background_shell):
- **No concurrency/cancellation risk in THIS module**: it is strictly sequential `await`s, no `asyncio.gather`/`create_task` over call_tool, no background dispatch, no task cancellation, no partial-write assertions. (Contrast the background_shell probe, which cancels tasks and asserts on partial writes — that one is the dangerous QUEUE-BRIDGE case, not this.) The only `asyncio` machinery (`_SharedAttemptBootstrap` condition vars) gates the out-of-band `call_daemon_api` rebind, not call_tool, and is only reached via `shared_attempt_bootstrap=True`, which `run_complex_project_build_probe` never passes — that path is dead for this entry point.
- **Result fidelity**: the bridge MUST return a `ToolResult` whose `.output` is JSON-parseable (shell/read parsing in `_shell_stdout`/`_strip_line_number_prefix`) and whose `.metadata.timings` survive (auto-squash floor loop). `normalize_result` already preserves these.
- **`allow_error` semantics**: the loop must NOT hard-fail/abort the turn when the probe passes `allow_error=True` (shell, lsp, intentional-conflict edit expect `is_error=True` results to come back, not exceptions). The bridge/event-source needs an allow-error turn mode so a tool error returns a normalized error `ToolResult` instead of terminating the loop.
- **Metadata aliasing**: bridge must pass the loop's live metadata as `ctx.metadata` so the lines 305-307 cwd mutation is visible to loop-dispatched tools.

HYBRID is unnecessary; REWRITE-AS-GENERATOR is disproportionate to the benefit given the clean single call shape.

### executor action strings it backs

Two strings route to this module via `_run_executor` (runner.py:616-627):
- `"complex_project_build"` → `_run_complex_project_build_probe(metadata, emit, smoke=False)` → `run_complex_project_build_probe(..., smoke=False)`. Emitted by `ComplexProjectBuild.executor_actions` (scenarios/sandbox/complex_project_build.py:112), scenario name `sandbox.complex_project_build`.
- `"complex_project_build_smoke"` → `_run_complex_project_build_probe(..., smoke=True)` → `run_complex_project_build_probe(..., smoke=True)`. Emitted by `ComplexProjectBuildSmoke.executor_actions` (line 138), scenario name `sandbox.complex_project_build_smoke` (registered in scenarios/__init__.py:142).

Note: the visually similar `complex_project_build_shell_edit_lsp*` and `complex_project_build_grep_glob*` actions (runner.py:628-660) route to SEPARATE modules (`complex_project_build_shell_edit_lsp_probe.py`, `complex_project_build_grep_glob_probe.py`) — NOT this one. Those import `CallTool` from `tool_scripts.py`; this module defines its own local `CallTool` and is backed only by the two strings above.

Relevant files (all absolute):
- Module: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/complex_project_build_probe.py`
- Dispatch: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` (lines 616-627, 1258-1280, `_call_tool` 1583-1700)
- Scenario: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/sandbox/complex_project_build.py` (lines 102-138)
- Bridge precedent: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/scenario_adapter.py` (`_executor_script` 176-212, `normalize_result` 40-51)


### cpb_shell_edit_lsp

Advisor is rate-limited. I have thorough primary-source evidence for every subsection. Producing the final PORT SPEC.

### cpb_shell_edit_lsp — entry functions

**`run_complex_project_build_shell_edit_lsp_probe`** (the only public entry; module `__all__` also re-exports the pure helpers `_compute_mixed_amp_pairs`, `METRICS_PATH`, `WORKSPACE_ROOT`)

```python
async def run_complex_project_build_shell_edit_lsp_probe(
    *, metadata: ExecutionMetadata, emit: EmitStreamEvent, call_tool: CallTool,
    publish: PublishEvent, publish_mock_record: PublishMockRecord,
    record_tool_check: RecordToolCheck, caller, sandbox_id: str, smoke: bool,
    shared_attempt_bootstrap: bool = False,
) -> str
```

**Runner dispatch** (`runner.py`):
- Import + call: `runner.py:1518-1534` — `_run_complex_project_build_shell_edit_lsp_probe(self, metadata, emit, *, smoke, shared_attempt_bootstrap=False)` lazily imports and calls the entry, wiring `call_tool=self._call_tool`, `publish=self._publish`, `publish_mock_record=self._publish_mock_record`, `record_tool_check=self._record_tool_check`, `caller=self._caller(metadata)`, `sandbox_id=self._require_sandbox_id(metadata)`.
- `_run_executor` (runner.py:372) dispatches by `action` string at runner.py:628-648:
  - `"complex_project_build_shell_edit_lsp"` → `smoke=False` (628-633)
  - `"complex_project_build_shell_edit_lsp_shared_bootstrap"` → `smoke=False, shared_attempt_bootstrap=True` (634-642)
  - `"complex_project_build_shell_edit_lsp_smoke"` → `smoke=True` (643-648)
- The `smoke` and `shared_attempt_bootstrap` params are the only mode params; there is **no smoke/index loop param** (no `:index` suffix form like the heavy_io worker). `_select_files`/`_select_refactor_passes`/`_select_lsp_expectations` derive everything from `smoke`.

### call_tool sites

Every loop-bound tool call goes through the 5 wrappers imported from the parent probe (`_shell`, `_edit_file`, `_read_file`, `_write_file`, `_lsp`) and `_api_edit_noop_batch` (which is **NOT** a call_tool site — it uses `sandbox_api`, see next section). Each wrapper issues exactly one `await ctx.call_tool(tool_obj, args, ctx.metadata, ctx.emit, allow_error=...)`. The `CallTool` signature (`tool_scripts.py:32-41`): `(tool_obj, raw_input, metadata, emit, *, allow_error=False)` — **no background params exposed.**

| # | Wrapper / site | tool name | key args | allow_error? | background? |
|---|---|---|---|---|---|
| 1 | `_write_file` (parent:1382) | `write_file` | `file_path`, `content` | False (implicit) | No |
| 2 | `_edit_file` (parent:1404) | `edit_file` | `file_path`, `old_text`, `new_text`, `description` | param `allow_error` (default False; True only in `_phase_f_intentional_conflicts`) | No |
| 3 | `_read_file` (parent:1429) | `read_file` | `file_path`, `start_line=1`, `end_line=200` | False (implicit) | No |
| 4 | `_shell` (parent:1452) | `shell` | `command`, `timeout` | **True (always)** | No |
| 5 | `_lsp` (parent:1491) | `lsp.hover` / `lsp.find_definitions` / `lsp.find_references` / `lsp.query_symbols` / `lsp.diagnostics` | `file_path` + `line`/`character`/`query`/`include_declaration`/`wait_for_diagnostics` per call | **True (always)** | No |

**Where this module drives those wrappers** (own-module call_tool sites, all indirect):
- `_apply_shell_edit` → `_shell` (line 545; the `python3 - <<'PY' ... PY` heredoc replacer)
- `_shell_phase_checkpoint` → `_shell` (342); `_phase_e_diagnostic_probe` import-after-repair → `_shell` (449)
- `_apply_logical_edit` → either `_apply_shell_edit`→`_shell` (route `idx%3==2` or `forced_route="shell"`) **or** `_edit_file` (else / `forced_route="edit_file"`) (505-523)
- `_phase_b_mixed_patches`, `_phase_d_mixed_refactor`, `_phase_d_mixed_amplification`, `_phase_e_diagnostic_probe` → many `_apply_logical_edit`
- `_phase_e_diagnostic_probe` → `_write_file` (407)
- `_lsp_semantic_call` → `_lsp` (1050) — fans out to all 5 LSP tools via `_assert_lsp_hover/_definition/_references/_references_for_anchor/_query_symbols/_diagnostics`
- `_phase_f_emit_metrics` → `_write_file` (METRICS, summary) + `_read_file` (METRICS) (1267,1273,1314)
- Inherited parent phases also called from the entry: `_phase0_bootstrap`, `_phase_a_skeleton`, `_phase_f_pytest`, `_phase_f_per_module_imports`, `_phase_f_tri_source_consistency`, `_phase_f_intentional_conflicts` — all use the same 5 wrappers.

**Count:** Static call_tool *site count* (distinct `ctx.call_tool` invocations in source) = **5** (the 5 wrappers); plus the `PreparedToolScriptEngine` path is **not used by this probe** (the probe never imports/instantiates it — that engine is for `full_stack_tool_scripts.py`). At *runtime* the call count is large and data-driven (loops to LSP floors of 40/200 and logical-edit floors of 600/90), but every one routes through those 5 wrappers with no background flag. **Zero background calls. Zero `sandbox_invocation_id`. Zero `background=True`.** (`grep` of this module and the parent both empty.)

### out-of-band work (NOT through call_tool)

Direct `sandbox_api.*` (bypass the loop entirely — re-home to ProbeContext helpers):
- `sandbox_api.read_file(...)` — this module: `_apply_shell_edit` verify-read (570), `_anchor_exists` (754), `_anchor_position` (1102). Each does `stats.api_read_count += 1`.
- Inherited from parent and reachable via the entry's phases: `sandbox_api.shell` (parent `_phase0_bootstrap` mkdir + workspace-exists + cwd probes, 262/335/357), `sandbox_api.read_file` (`_phase0_bootstrap` gitignore 323, `_projection_consistency_check` 664, `_api_edit_noop_batch` 690, `_resolve_anchor_position` 990, `_phase_f_tri_source_consistency` 1147), `sandbox_api.edit_file` (`_api_edit_noop_batch` 700, `_phase_f_intentional_conflicts` 1248).
- `call_daemon_api(...)` — parent `_reset_workspace_base` (391) `api.build_workspace_base` rebind. Reached via `_phase0_bootstrap`.

`publish_mock_record(...)` (audit, no loop) — this module emits `EventType.MOCK_SANDBOX_CHECK_RECORDED` with a `SandboxCheck` at: `_phase_e_diagnostic_probe` import check (463), `_apply_shell_edit` fail + verify (566, 596), `_record_lsp_semantic_check` (1076). Plus all inherited parent phases' `publish_mock_record`.

`publish(...)` — `ctx.publish(EventType.SANDBOX_CONFLICT_DETECTED, metadata=..., payload=...)` only in inherited `_phase_f_intentional_conflicts` (parent:1238, 1274). This module's own bodies do **not** call `publish`.

`record_tool_check(...)` — not called directly in this module; invoked inside the inherited `_write_file`/`_edit_file` wrappers (parent:1390, 1419).

`caller` — used only as the `caller=ctx.caller` field on `ReadFileRequest`/`EditFileRequest`/`ShellRequest` passed to `sandbox_api.*` (i.e. part of the out-of-band path), never as a callable.

Note: `ctx.metadata` mutation happens out-of-band too — parent `_phase0_bootstrap:305-307` rewrites `repo_root`/`cwd`/`exec_cwd` to `/ephemeral-os`. The injected loop must honor that mutation (it changes the cwd of every later toolkit `shell`).

### loop-interaction verdict (DECISIVE)

**YES.** This module interacts with the engine loop *only* via the injected `call_tool` (through the 5 parent wrappers `_shell/_edit_file/_read_file/_write_file/_lsp`). Every other touch — `sandbox_api.read_file/shell/edit_file`, `call_daemon_api`, `publish`, `publish_mock_record`, `record_tool_check`, `caller` — is out-of-band and re-homes cleanly to a ProbeContext-style helper. No site needs a background dispatch, a `sandbox_invocation_id`, a streaming/partial-result hook, or any loop facility beyond "run one tool, return its `ToolResult`." `grep` confirms zero `background`/`sandbox_invocation`/`run_in_background` in both this module and the parent helper module.

### recommended adaptation

**QUEUE-BRIDGE (zero body change).** The module's entire loop surface is the 6 parent helper wrappers, each of which makes exactly one `await ctx.call_tool(tool_obj, args, metadata, emit, allow_error=...)` and returns a single `ToolResult`. A bridging `call_tool` shim (asyncio.Queue + per-call Future, routing one `ToolCall(name,args)` per await through the loop and resolving with the normalized `ToolResult`) preserves this module — and its parent `complex_project_build_probe.py` — byte-identical. Rewriting as a top-level async-generator is infeasible here anyway: the `yield ToolCall` would have to live inside deeply nested helpers (`_apply_shell_edit`, `_lsp_semantic_call`, `_phase_*`), which Python's async-gen-yield-at-top-level rule forbids. The shim is the only adaptation that does not require flattening ~10 phase functions plus the shared parent module.

**Hazards for the bridge (none fatal, but verify):**
- **Strict sequential, no concurrency.** All awaits are serial (no `asyncio.gather`, no `create_task`); the per-call Future model is safe. There is exactly one in-flight call at a time.
- **Mid-call `sandbox_api` interleaving.** `_apply_shell_edit` does `await _shell(...)` (loop) then immediately `await sandbox_api.read_file(...)` (out-of-band) to verify before resolving — the bridge must let the out-of-band read run *between* loop turns without the loop advancing. As long as the shim's "next ToolCall" is only pulled on the next `call_tool` await, this is naturally satisfied.
- **`asyncio.sleep` inside `_assert_lsp_diagnostics` retry loop** (1015) and the diagnostic retry constants — these sleeps happen between loop turns; the bridge must not treat a sleep as turn completion.
- **Exceptions raised after a successful tool call.** `_apply_shell_edit` (567,598), `_record_lsp_semantic_check` (1078), `_anchor_position` (1108,1112), `_phase_f_semantic_lsp_sweep` safety bound (1220) all `raise RuntimeError` *after* the loop returned a good `ToolResult`. The bridge must propagate the probe-side exception out of the driving coroutine (the probe task), not swallow it as a loop error — i.e. the probe coroutine's exception terminates the run, exactly as today.
- **`metadata` mutation by Phase 0** (parent:305-307) must be visible to subsequent loop turns — the shim must pass the *same* (mutated) metadata object the probe holds, not a snapshot.
- **`shared_attempt_bootstrap=True` (3-parallel-agent test).** When driven via `complex_project_build_shell_edit_lsp_shared_bootstrap`, three probe instances run concurrently and rendezvous on a module-global `asyncio.Condition` (`_SHARED_ATTEMPT_BOOTSTRAPS`) in `_shared_attempt_workspace_base`. Each probe is its own loop+bridge; this is cross-*probe* concurrency (independent of the per-probe single-in-flight model) and is the only real concurrency in scope. The bridge must support N independent driver/queue pairs running in parallel without sharing queue state — and the Condition wait (240s/60s timeouts) sits between loop turns, so each bridge must tolerate a long gap with no ToolCall yielded. No task cancellation or partial-write assertion exists here (unlike `background_shell_probe.py`).
- Long shell timeouts (`pytest` 600s at parent:1031; many 180s shell-edits) — the bridge's per-call Future must not impose a shorter deadline than the tool's own timeout.

### executor action strings it backs

Cross-ref `runner.py:_run_executor` (628-648), emitted by scenario `executor_actions()`:
- `"complex_project_build_shell_edit_lsp"` — emitted by `ComplexProjectBuildShellEditLsp.executor_actions` (`scenarios/sandbox/complex_project_build_shell_edit_lsp.py:92`); registry name `sandbox.complex_project_build_shell_edit_lsp`.
- `"complex_project_build_shell_edit_lsp_smoke"` — emitted by `ComplexProjectBuildShellEditLspSmoke.executor_actions` (same file:117); registry `sandbox.complex_project_build_shell_edit_lsp_smoke`. Also used in capacity packs (`scenarios/capacity/pack_catalog.py:167,174,181`).
- `"complex_project_build_shell_edit_lsp_shared_bootstrap"` — emitted only by the test `test_project_build_shell_edit_lsp_three_parallel_agents.py:62` (3-parallel-agent shared-bootstrap scenario); no production scenario class emits it.

File paths: focus module `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/complex_project_build_shell_edit_lsp_probe.py`; shared parent helpers `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/complex_project_build_probe.py`; `CallTool` protocol `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/tool_scripts.py:32`; runner dispatch `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py:628,1510,1583`; scenario classes `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/sandbox/complex_project_build_shell_edit_lsp.py:82`.

**PORT NOTE (load-bearing):** Porting this module REQUIRES the queue-bridge shim to also work for the imported parent helpers (`_shell`, `_edit_file`, `_read_file`, `_write_file`, `_lsp`, `_api_edit_noop_batch`, and all `_phase_*` it pulls from `complex_project_build_probe.py`). The two modules cannot be ported independently — they share the wrapper layer that holds the only `ctx.call_tool` sites.


### cpb_grep_glob

Advisor is rate-limited. I have thoroughly traced all the evidence: the focus module, its parent helper module, the CallTool protocol, the runner dispatch chain, `_call_tool`/`_publish`/`_publish_mock_record` signatures, the executor_actions routing, and the `_executor_script` bridge target. I'll produce the spec from the verified evidence.

### cpb_grep_glob — entry functions

| Entry function | Signature | Returns |
|---|---|---|
| `run_complex_project_build_grep_glob_probe` | `async (*, metadata: ExecutionMetadata, emit: EmitStreamEvent, call_tool: CallTool, publish: PublishEvent, publish_mock_record: PublishMockRecord, record_tool_check: RecordToolCheck, caller, sandbox_id: str, smoke: bool) -> str` | `SUMMARY_PATH` (a `/ephemeral-os/.metrics/summary.json` string) |

This is the **only** public entry (`__all__` exports `METRICS_PATH`, `SUMMARY_PATH`, `WORKSPACE_ROOT`, `run_complex_project_build_grep_glob_probe`). It builds a `ProbeContext` (imported from `complex_project_build_probe`, the dataclass form, NOT the probes.py form) and drives 11 phases sequentially: `_phase0_bootstrap`, `_phase_a_skeleton`, `_phase_b_grep_glob_patches`, `_phase_d_refactor`, `_phase_e_grep_glob_amplification`, `_phase_f_pytest`, `_phase_f_per_module_imports`, `_phase_f_search_sweep`, `_phase_f_tri_source_consistency`, `_phase_f_intentional_conflicts`, `_phase_f_emit_metrics`. Six of these phase helpers are imported from the parent `complex_project_build_probe` module and shared verbatim.

**Runner dispatch (`runner.py`):**
- Import: `complex_project_build_grep_glob_probe` line 1543 (lazy import inside the wrapper method).
- Wrapper method: `_run_complex_project_build_grep_glob_probe(self, metadata, emit, *, smoke)` at runner.py:1536, which passes `call_tool=self._call_tool`, `publish=self._publish`, `publish_mock_record=self._publish_mock_record`, `record_tool_check=self._record_tool_check`, `caller=self._caller(metadata)`, `sandbox_id=self._require_sandbox_id(metadata)`.
- Call sites in `_run_executor` (runner.py:649-660): `action == "complex_project_build_grep_glob"` → `smoke=False`; `action == "complex_project_build_grep_glob_smoke"` → `smoke=True`. No index/mode params — only the boolean `smoke`.

### call_tool sites

Every loop-touching tool call routes through `ctx.call_tool(tool_obj, args_dict, ctx.metadata, ctx.emit, allow_error=...)`. In this module they are reached through thin wrappers — `_grep`/`_glob` are **local** to the focus module; `_edit_file`/`_read_file`/`_write_file`/`_shell`/`_lsp` are **imported from the parent** `complex_project_build_probe`. The table below lists each distinct `await ctx.call_tool(...)` site (the loop-driven calls), grouped by the wrapper that emits it. None pass `background_task_id` or `sandbox_invocation_id`, and no `args` dict sets `background=True`.

| # | Wrapper / site | Tool obj (`.name`) | Key args | allow_error? | Background? |
|---|---|---|---|---|---|
| 1 | `_grep` (this module, L420) | `grep_tool` (`grep`) | `pattern, path, glob_filter, output_mode, head_limit, line_numbers, multiline` | no | no |
| 2 | `_glob` (this module, L450) | `glob_tool` (`glob`) | `pattern, path` | no | no |
| 3 | `_write_file` (parent, L1382) | `write_file_tool` (`write_file`) | `file_path, content` | no | no |
| 4 | `_edit_file` (parent, L1404) | `edit_file_tool` (`edit_file`) | `file_path, old_text, new_text, description` | parameterized (`allow_error`, default False; `True` only at `_phase_f_intentional_conflicts`) | no |
| 5 | `_read_file` (parent, L1429) | `read_file_tool` (`read_file`) | `file_path, start_line=1, end_line=200` | no | no |
| 6 | `_shell` (parent, L1452) | `shell_tool` (`shell`) | `command, timeout` | yes (always `allow_error=True`) | no |
| 7 | `_lsp` (parent, L1491) | rotating LSP tools (`lsp.hover`, `lsp.find_definitions`, `lsp.find_references`, `lsp.query_symbols`, `lsp.diagnostics`) | `file_path` (+ `line/character/query` per tool) | yes (always `allow_error=True`) | no |

**Count of distinct call_tool sites: 7** (`grep`, `glob`, `write_file`, `edit_file`, `read_file`, `shell`, `lsp.*`). At runtime these fire in the **thousands** (full floor `_FULL_TOOL_CALL_FLOOR = 2000`, smoke floor `250`); `_phase_e_grep_glob_amplification` loops to top up via `_compute_amp_pairs`. `_lsp` is reachable only transitively — through `_phase_d_refactor` (imported) and the LSP-saturation path in shared phases — not from a grep_glob-local phase, but it is still a live call_tool tool obj this module backs.

**PreparedToolScriptEngine: not used by this module.** `cpb_grep_glob` contains no `PreparedToolScript` / `ToolScriptStep`; it is pure imperative phase orchestration over `ctx.call_tool`. (PreparedToolScriptEngine backs the `inspect_user_input` / `execute_package` / `recursive_step` / `final_reconciliation` / `verifier_checkpoint` scenario scripts, which are a separate Phase 2 concern.)

### out-of-band work (NOT through call_tool)

These never touch the engine loop and must re-home to a `ProbeContext`-style helper. They come from BOTH this module and the shared parent phases it invokes (`_phase0_bootstrap`, `_phase_d_refactor`, `_phase_f_*`, `_projection_consistency_check`).

Direct `sandbox_api.*`:
- `sandbox_api.shell(...)` — `_phase0_bootstrap` (mkdir candidate-cwd loop, `workspace_exists`, `workspace_cwd`; parent L262/L335/L357).
- `sandbox_api.read_file(...)` — `_phase0_bootstrap` gitignore (L323); `_projection_consistency_check` (L664); `_api_edit_noop_batch` (L690, reached only from parent `_phase_b_apply_patches`, NOT from this module's `_phase_b_grep_glob_patches`); `_resolve_anchor_position` (L990, from `_phase_d_refactor`); `_phase_f_tri_source_consistency` (L1147); `_phase_f_intentional_conflicts` (none — uses edit).
- `sandbox_api.edit_file(...)` — `_api_edit_noop_batch` (L700, parent path only); `_phase_f_intentional_conflicts` api conflict (L1248).
- `call_daemon_api(ctx.sandbox_id, "api.build_workspace_base", {...})` — `_reset_workspace_base` (parent L392), the workspace rebind to `/ephemeral-os`.

`publish(...)` / `publish_mock_record(...)` / `record_tool_check(...)`:
- `ctx.publish_mock_record(EventType.MOCK_SANDBOX_CHECK_RECORDED, SandboxCheck(...))` — dominant out-of-band sink, fired in `_record_search_check` (this module, L564) and across every shared phase (bootstrap checks, projection check, tri-source, per-module imports, intentional conflicts, batch-noop).
- `ctx.publish(EventType.SANDBOX_CONFLICT_DETECTED, metadata=ctx.metadata, payload={...})` — `_phase_f_intentional_conflicts` (parent L1238 and L1274). **Note the keyword shape**: `publish(event_type, metadata=..., payload=...)` matches runner `_publish(event_type, *, metadata, payload, ...)` (runner.py:1995). The new probes.py `ProbeContext._publish(event_type, payload)` has a **different** signature and would not accept `metadata=`.
- `ctx.record_tool_check(name, result)` — fired inside `_grep` (L437) and `_glob` (L458) in this module, and inside `_write_file`/`_edit_file` in the parent. Maps to runner `_record_tool_check` (runner.py:1716).
- `caller(...)` — `ctx.caller` is a passed-in `SandboxCaller` value (not invoked as a function); it is threaded into every `sandbox_api.*` / `call_daemon_api` request above as the `caller=` field. Re-homing requires reconstructing it from metadata (the probes.py `ProbeContext._caller()` already builds a `SandboxCaller` from `metadata`).

### loop-interaction verdict (DECISIVE)

**NO.** The module does NOT interact with the engine only via the injected `call_tool`. While its own grep/glob phases (`_phase_b_grep_glob_patches`, `_phase_e_grep_glob_amplification`, `_phase_f_search_sweep`) interact with the loop purely through `ctx.call_tool` plus out-of-band publish/record, the entry function unconditionally calls **shared parent phases that perform direct `sandbox_api` and daemon work that is genuinely engine-adjacent state**, most critically the workspace rebind:

```python
# complex_project_build_probe.py:391  (_reset_workspace_base, called by _phase0_bootstrap)
rebind = await call_daemon_api(
    ctx.sandbox_id,
    "api.build_workspace_base",
    {"workspace_root": WORKSPACE_ROOT, "reset": True},
    timeout=240,
)
```

and the metadata mutation that follows it:

```python
# complex_project_build_probe.py:305-307  (_phase0_bootstrap)
ctx.metadata.repo_root = WORKSPACE_ROOT
ctx.metadata.cwd = WORKSPACE_ROOT
ctx.metadata.exec_cwd = WORKSPACE_ROOT
```

This rebind+metadata-mutation is a prerequisite for the subsequent `call_tool` shell/edit/read calls to resolve their cwd to `/ephemeral-os` (all tool paths are absolute `/ephemeral-os/...` precisely so the loop's `resolve_tool_sandbox_path` does not rewrite them against `/testbed`). The loop does not do this rebind, so the bridge alone is insufficient — the out-of-band setup must run too. It is still "out-of-band" in the sense that it does not go through `call_tool`, but it is NOT pure publish/record cosmetic work: it mutates the shared `metadata` the loop reads on the very next turn. Confirmed second blocker: `_phase_f_intentional_conflicts` calls `ctx.publish(..., metadata=ctx.metadata, payload=...)` (parent L1238) — a 3-kwarg publish shape the current probes.py `ProbeContext` does not implement.

### recommended adaptation

**QUEUE-BRIDGE (zero body change).**

Rationale: every loop-touching call in this module funnels through exactly one injected `ctx.call_tool(tool_obj, args, metadata, emit, allow_error=...)` seam (7 distinct tool objs, no background calls, no cancellation, no task-spawning, no partial-write races). A `call_tool` shim backed by an `asyncio.Queue` + per-call `Future` lets each `await ctx.call_tool(...)` route a `ToolCall` out through `_executor_script`'s `yield Turn(calls=(call,))` and resolve the future with `normalize_result(blocks)` on resume, keeping all eleven phase bodies — and the six imported parent phases — byte-identical. Rewriting as a top-level async generator is infeasible here anyway: the yields live deep in nested helpers (`_assert_grep_contains` → `_grep` → `call_tool`), exactly the case the "two-level coroutine bridge" exists to handle without restructuring. **No concurrency/cancellation/background hazards** for the bridge: unlike the `background_shell`/cancellation probes, this module passes no `background_task_id`, asserts on no partial writes, and runs strictly sequentially (`_phase_e` and saturation loops are sequential `await`s).

**Required out-of-band wiring (not body changes, but bridge-adjacent obligations):** the QUEUE-BRIDGE only covers `call_tool`. The shim's `ProbeContext` must additionally provide the imported parent module's expected `ProbeContext` surface — `metadata`, `emit`, `publish`, `publish_mock_record`, `record_tool_check`, `caller`, `sandbox_id`, `smoke` (the dataclass form from `complex_project_build_probe`, NOT the leaner probes.py form). Two specific re-home points must be satisfied or these phases break: (1) the Phase-0 `call_daemon_api` rebind + `metadata.repo_root/cwd/exec_cwd` mutation must execute out-of-band before bridged tool calls; (2) the `publish(event_type, metadata=..., payload=...)` 3-kwarg shape used by intentional-conflicts must be supported by the re-homed `publish`. Because this module reuses the parent's `ProbeContext`, porting it should reuse the parent's `ProbeContext` dataclass directly rather than the new probes.py `ProbeContext`.

### executor action strings it backs

From `scenarios/sandbox/complex_project_build_grep_glob.py`:
- `("complex_project_build_grep_glob",)` — class `sandbox.complex_project_build_grep_glob` (`executor_actions` returns it, L100), routed at runner.py:649 → `smoke=False`.
- `("complex_project_build_grep_glob_smoke",)` — class `sandbox.complex_project_build_grep_glob_smoke` (`executor_actions` returns it, L125), routed at runner.py:655 → `smoke=True`.

Both are single-action executor scripts; in the ported path they will reach `_executor_script`'s `PROBE_BUILDERS.get(action)`, which currently raises `NotImplementedError(... "not yet adapted (Phase 2)")` for these two strings. Registry binding: `scenarios/__init__.py:136-137` maps the scenario names to `ComplexProjectBuildGrepGlob` / `ComplexProjectBuildGrepGlobSmoke`.

**Relevant absolute paths:**
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/complex_project_build_grep_glob_probe.py` (focus module)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/complex_project_build_probe.py` (parent: shared `ProbeContext` dataclass, `_edit_file`/`_read_file`/`_write_file`/`_shell`/`_lsp`, all out-of-band `sandbox_api`/`call_daemon_api` phases)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/tool_scripts.py` (CallTool protocol, PreparedToolScriptEngine — not used by this module)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` (dispatch L649-660, wrapper L1536-1558, `_call_tool` L1583, `_publish` L1995, `_publish_mock_record` L2028)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/scenario_adapter.py` (`_executor_script` bridge target L176-212)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/sandbox/complex_project_build_grep_glob.py` (executor_actions strings)


### high_concurrency

Confirmed. The loop appends tool results (including `is_error=True` non-terminal results) to messages and continues looping — there is no abort on a non-terminal tool error. The only loop-exit conditions are `terminal_result` set (TOOL_STOP) or `terminal_submission_failed` (a terminal tool that failed). A non-terminal `is_error=True` result just becomes a `ToolResultBlock` with `is_error=True` and the loop continues to the next turn, where the scenario_adapter will `asend` the normalized result back into the probe. This makes QUEUE-BRIDGE safe for the OCC race. `ToolResultBlock.metadata` is a real field (message.py:44), so metadata fidelity holds provided dispatch populates it.

Here is the port spec.

---

### high_concurrency — entry functions

Three public async entry functions, all in `high_concurrency_probe.py`. None take smoke/mode params; only `worker` takes a runtime `index`.

| Function | Signature (kwargs) | runner.py dispatch |
|---|---|---|
| `run_high_concurrency_seed_probe` | `*, metadata, emit, call_tool, record_tool_check` | `runner.py:1282` `_run_high_concurrency_seed_probe` imports it (L1287) and calls with `call_tool=self._call_tool`, `record_tool_check=self._record_tool_check`. Reached from executor action `"high_concurrency_seed"` (L570-575). |
| `run_high_concurrency_worker_probe` | `*, index, metadata, emit, call_tool, publish, publish_mock_record, record_tool_check` | `runner.py:1298` `_run_high_concurrency_worker_probe(..., *, index)` imports it (L1305) and calls with `call_tool`, `publish=self._publish`, `publish_mock_record=self._publish_mock_record`, `record_tool_check=self._record_tool_check`. Reached from action prefix `"high_concurrency_worker:"`; **index is parsed `int(action.split(":", 1)[1])` at L579**, passed `index=worker_index` (L580-584). |
| `run_high_concurrency_reconcile_probe` | `*, metadata, emit, call_tool, record_tool_check` | `runner.py:1319` `_run_high_concurrency_reconcile_probe` imports it (L1324) and calls with `call_tool`, `record_tool_check`. Reached from action `"high_concurrency_reconcile"` (L587-591). |

All three return `str` (an artifact path). `WORKER_COUNT` is imported from the scenario module (`high_concurrency_layerstack_overlay_occ`); `DATA_FILES_PER_WORKER=1`, `CONFLICT_WORKER_COUNT=4`, `READS_PER_WORKER=1`.

### call_tool sites

Every site routes through the injected `call_tool` (either directly or via the local `_call_checked` wrapper at L307-327, which is NOT its own site — it forwards one `call_tool`). **11 static logical sites.** Zero background.

| # | Fn | Line | Tool | Key args | allow_error? | background? |
|---|---|---|---|---|---|---|
| 1 | seed | L61 | `shell` | `mkdir -p {ROOT}/...`, timeout 120 | False (default) | No |
| 2 | seed | L75 | `write_file` | `shared/conflict.txt`, `owner=seed\nrevision=0\n` | False | No |
| 3 | seed | L87 | `write_file` | `control/seed.json`, json seed_payload | False | No |
| 4 | worker | L126 (via `_call_checked`) | `write_file` | `worker-NN/file-MM.txt`, seed body | False | No |
| 5 | worker | L137 (via `_call_checked`) | `edit_file` | same path, `value=seed…`→`value=NN-MM…`, description | False | No |
| 6 | worker | L157 (via `_call_checked`) | `read_file` | `worker-NN/file-MM.txt`, lines 1-20 | False | No |
| 7 | worker | L342 (in `_maybe_race_conflict`) | `edit_file` | `shared/conflict.txt`, `owner=seed\n`→`owner=worker-NN\n`, description | **True** | No |
| 8 | worker | L196 | `write_file` | `fragments/worker-NN.json`, json summary | False | No |
| 9 | reconcile | L219 (via `_call_checked`) | `read_file` | `fragments/worker-NN.json`, lines 1-200 | False | No |
| 10 | reconcile | L235 (via `_call_checked`) | `write_file` | `SUMMARY_PATH`, json reconcile summary | False | No |
| 11 | reconcile | L248 (via `_call_checked`) | `read_file` | `SUMMARY_PATH`, lines 1-80 | False | No |

**Runtime fan-out** (the loop must yield this many `ToolCall` turns):
- seed: 3.
- worker: `DATA_FILES_PER_WORKER(1)` × (write+edit)=2, + `READS_PER_WORKER(1)` read=1, + 1 conflict edit iff `index < CONFLICT_WORKER_COUNT(4)`, + 1 fragment write = **4 calls (workers ≥4) or 5 calls (workers 0-3)**.
- reconcile: `WORKER_COUNT` fragment reads + 1 summary write + 1 summary read = **WORKER_COUNT + 2**.

`allow_error=True` at **exactly one site** (L342, the shared OCC race). All other sites default `allow_error=False`. **All foreground** (confirmed: `grep background` on this module returns nothing).

### out-of-band work (NOT through call_tool)

No `sandbox_api.*` calls and no `caller(...)` use in this module — confirmed by grep. All out-of-band work re-homes to a `ProbeContext`-style helper (the existing `probes.py::ProbeContext` covers the shape, with one adaptation noted below).

| Mechanism | Site(s) | Detail |
|---|---|---|
| `publish(...)` | L365 | `EventType.SANDBOX_CONFLICT_DETECTED`, kwargs `metadata=metadata, payload={"worker_index", "conflict_reason"}`. **Note:** existing `ProbeContext._publish(event_type, payload)` (probes.py:79) has NO `metadata` kwarg — the conflict publish must adapt to that shape (node already derives identity from `ctx._metadata`). |
| `publish_mock_record(...)` | L375 | `EventType.MOCK_SANDBOX_CHECK_RECORDED` + `SandboxCheck(name="tool.edit_file.high_concurrency.shared_conflict_NN", passed=True, detail, changed_paths)`. Maps to a `ProbeContext`-level publish-check. |
| `publish_mock_record(...)` | L482 (in `_assert_read_contains`) | `MOCK_SANDBOX_CHECK_RECORDED` + `SandboxCheck(name, passed, detail=needle)`. Equivalent to `ProbeContext.assert_read_contains`. |
| `record_tool_check(...)` | seed L73, L84, L96; worker fragment L205; reconcile L244 | `(check_name, ToolResult)` → `SandboxCheck`. Equivalent to `ProbeContext.record_check`. Note: read sites pass `record_tool_check=None`/`check_name=""` (L167, L228, L254) — those skip the record. |

`_maybe_race_conflict` (L330) reads `result.metadata.get("conflict_reason")`, `result.output`, `result.is_error`, `result.metadata.get("changed_paths")` from the `call_tool` result — these are pure post-processing of the loop result, not separate engine interactions.

### loop-interaction verdict (DECISIVE)

**YES.** This module interacts with the engine ONLY via the injected `call_tool`. Everything else is out-of-band: `publish` (1), `publish_mock_record` (2), `record_tool_check` (≥5), plus pure local computation (`_reconcile_summary`, `_worker_summary`, `_capture_metadata`, `_assert_*`, json parsing). There are zero `sandbox_api.*` calls, zero `caller(...)`, zero background ids. No site needs anything from the engine other than executing a tool and returning its `ToolResult`.

### recommended adaptation

**QUEUE-BRIDGE (zero body change).** The `call_tool` invocations live inside the helpers `_call_checked` (L318) and `_maybe_race_conflict` (L342), so a REWRITE-AS-GENERATOR would have to inline/flatten both helpers into the top-level executor generator (Python forbids yielding from a helper) — a large, invasive change to a 524-line module with three entry points. A queue+Future shim keeps every probe body byte-identical: each `await call_tool(tool_obj, raw_input, metadata, emit, allow_error=...)` pushes a `ToolCall` onto the bridge queue and awaits a Future that `_executor_script` resolves with the loop's `normalize_result(blocks)`. (Do NOT drift to HYBRID by re-homing the conflict edit to out-of-band `sandbox_api` the way `probes.py::run_expected_conflict` did — that probe's conflict is *fabricated* (`missing-old-text`); high_concurrency's L342 is a *real* concurrent OCC race and must stay on the agent tool path or the scenario tests nothing.)

**Hazards for the bridge:**

1. **OCC-race expected-error tolerance (PRECONDITION — VERIFIED SAFE).** The L342 conflict edit returns `is_error=True` on ~3 of 4 conflict workers by design, and `_reconcile_summary` hard-asserts `conflict_errors >= 1` (L274). Verified in `loop.py:266-309`: a **non-terminal** `is_error=True` tool result is just appended as a `ToolResultBlock` (`is_error=True`) to `messages` and the loop continues; it does NOT abort. The only exits are `terminal_result` set (TOOL_STOP, L289) and `terminal_submission_failed` (L294) — neither triggered by a non-terminal write/edit error. So the bridge passes the error result back into the probe via `asend`, the worker still reaches its fragment write (L196), and reconcile sees all fragments. **Safe**, but the bridge must NOT raise on a non-terminal `is_error` — it must resolve the Future with the error `ToolResult` so `allow_error=True` semantics survive.
2. **Metadata fidelity.** `_capture_metadata` (L441) and `_worker_summary` read `result.metadata["timings"]` (`layer_stack.auto_squash.depth_before`, `layer_stack.auto_squash.total_s`, `occ.apply.commit_resume_wait_s`), `changed_paths`, `status`, `task_center_task_id`, and `conflict_reason` (L359). `ToolResultBlock.metadata` is a real dict field (`message.py:44`), so `normalize_result` (scenario_adapter L40-51, copies `block.metadata`) preserves it — provided dispatch populates `ToolResultBlock.metadata` from the executed `ToolResult.metadata`. If those keys are dropped end-to-end, the reconcile timing maxes and conflict-reason silently zero/blank out (not a crash, a fidelity regression). Confirm dispatch carries `ToolResult.metadata` → `ToolResultBlock.metadata`.
3. **Bridge cancellation / exception propagation.** The Future+Queue must propagate a loop-side exception or cancellation back to the awaiting probe (set_exception on the Future), or a failed/cancelled turn deadlocks the probe coroutine waiting on a Future that never resolves. Note the old `_call_tool` `CancelledError → sandbox_api.cancel` path (runner.py:1659-1680) is **dead here** — no background ids are ever passed by this module, so there is nothing to cancel; the bridge only needs generic exception/cancel propagation, not background-invocation teardown.
4. **No concurrency hazard within a probe.** Workers run as separate executor agents (separate scenario runs / separate `_executor_script` instances), not concurrent `call_tool` from one body. Each probe body is strictly sequential `await`, so one queue + one in-flight Future per bridge is sufficient. The OCC concurrency is across worker *agents*, owned by the scenario DAG, not by this module.

### executor action strings it backs

From `scenarios/sandbox/high_concurrency_layerstack_overlay_occ.py::executor_actions` (L104-112), cross-referenced to `runner.py:_run_executor` (L570-592):
- `"high_concurrency_seed"` → `run_high_concurrency_seed_probe` (L106-107 / runner L570).
- `"high_concurrency_reconcile"` → `run_high_concurrency_reconcile_probe` (L108-109 / runner L587).
- `"high_concurrency_worker:{index}"` → `run_high_concurrency_worker_probe(index=...)`; the `:{index}` suffix is parsed at runner L579 (`int(action.split(":",1)[1])`), scenario builds it at L112 from the `ACTION high_concurrency_worker index=` context marker (L130).

For the Phase 2 port, these three strings must be registered in `scenario_adapter._executor_script`'s `PROBE_BUILDERS` analog (currently L193-197 raises `NotImplementedError` for unmapped actions). The worker builder needs an `index`-aware factory (parse `action.split(":",1)[1]`), unlike the no-arg seed/reconcile/simple probes.


### heavy_io_zoned

The advisor confirms the data and sharpens the adaptation analysis. The return-value gap is the real discriminator, and it cuts toward QUEUE-BRIDGE. Let me finalize the spec honestly reflecting the toss-up.

### heavy_io_zoned — entry functions

Three public async entry functions, all keyword-only. Dispatched from `runner.py` via lazy import + a thin `_run_*` method, routed in `_run_executor` (runner.py:593-615).

| Function (signature) | runner.py dispatch | Params |
|---|---|---|
| `run_heavy_io_zoned_seed_probe(*, metadata, emit, call_tool, record_tool_check) -> str` | import L1340-1342, call L1344-1349 via `_run_heavy_io_zoned_seed_probe` (L1335); action handler L593-598 | none |
| `run_heavy_io_zoned_worker_probe(*, index, metadata, emit, call_tool, publish, publish_mock_record, record_tool_check) -> str` | import L1358-1360, call L1362-1370 via `_run_heavy_io_zoned_worker_probe` (L1351); action handler L599-609 parses `heavy_io_zoned_worker:N` → `index` | `index: int` (0..WORKER_COUNT-1, guarded L152) |
| `run_heavy_io_zoned_reconcile_probe(*, metadata, emit, call_tool, record_tool_check) -> str` | import L1377-1379, call L1381-1386 via `_run_heavy_io_zoned_reconcile_probe` (L1372); action handler L610-614 | none |

No smoke/mode params. `index` is the only mode-like parameter (worker fan-out). All three `return` an artifact path (`control_path` / `fragment-NN.json` / `SUMMARY_PATH`).

### call_tool sites

Total: **11** `await call_tool(...)` sites. Every site is the plain positional form `call_tool(tool, args, metadata, emit)` — NO `allow_error`, NO `background_task_id`, NO `sandbox_invocation_id`. **Zero background calls.**

| # | Fn | Line | tool | key args | allow_error | background |
|---|---|---|---|---|---|---|
| 1 | seed | 103 | `shell` | `mkdir -p` ROOT/FRAGMENTS/perf_load_tracked/build/OUTSIDE; `timeout:120` | no | no |
| 2 | seed | 128 | `write_file` | `control/seed.json` (seed control payload) | no | no |
| 3 | worker | 163 | `shell` | `_long_write_command(zone_dir)` (dd loop, 11×3MB); `timeout:SHELL_TIMEOUT_S`=180 | no | no |
| 4 | worker | 177 | `shell` | `_readback_command(zone_dir)` (ls/du); `timeout:60` | no | no |
| 5 | worker | (3 repeated) | `shell` | write per zone — loop over `ZONE_NAMES` (3) ⇒ sites 3&4 each run ×3 | no | no |
| 6 | worker | 255 | `write_file` | `fragments/worker-NN.json` (per-worker summary payload) | no | no |
| 7 | reconcile | 345 | `shell` | `python3 - <<PY` heredoc aggregating fragments → `summary.json`; `timeout:180` | no | no |
| 8 | reconcile | 357 | `read_file` | `SUMMARY_PATH`, `start_line:1,end_line:200` | no | no |

Static call-site count = 4 in `worker` body (L163, L177 inside a 3-iteration `for zone` loop, + L255). Per-worker runtime tool invocations = `2*len(zone_results)+1 = 7`. Seed = 2, reconcile = 2. (Static distinct sites across the module: 8; per the loop, worker contributes 7 runtime calls.)

### out-of-band work (NOT through call_tool)

No `sandbox_api.*` calls anywhere. No `caller(...)`. Out-of-band re-homing targets:

- `record_tool_check(name, result)` — seed L117, L137; worker L169, L183, L264; reconcile L351, L363. Re-homes to `ProbeContext.record_check`.
- `publish_mock_record(EventType.MOCK_SANDBOX_CHECK_RECORDED, SandboxCheck(...))` — worker **L199-215** only. **Bespoke shape**: `name=f"heavy_io_zoned.merge.{zone}.worker_NN"`, custom `detail` (`observed_file_count`/`observed_kib`/`expected_file_count`), `passed=merged_ok`, `changed_paths` pulled from `readback_result.metadata["changed_paths"]`. `ProbeContext.record_check(name, result)` (probes.py:99) CANNOT reproduce this — only the private `_publish_check(SandboxCheck)` (probes.py:86) can. A new public ProbeContext method is required.
- `publish` (PublishEvent) — injected into worker but **immediately `del publish` at L154** ("reserved for future per-zone conflict signaling"). Dead today; drop on port.

### loop-interaction verdict (DECISIVE)

**YES.** This module interacts with the engine loop ONLY through the injected `call_tool` (11 sites, all plain shell/write_file/read_file, no background, no allow_error). Everything else is out-of-band: `record_tool_check`, the single custom `publish_mock_record`, and the dead `publish`. No `sandbox_api.*`, no direct OCC/lease/caller use. Nothing requires anything beyond `call_tool` + the ProbeContext-style helpers.

### recommended adaptation

**HYBRID — lean QUEUE-BRIDGE, but a clean REWRITE is also defensible (genuine toss-up).** Rationale: this is the cleanest heavy probe — no cancellation, no partial-write assertions, no task races (unlike `background_shell`), and zero background calls — so the generator rewrite that the 3 ported probes used would work mechanically. BUT a rewrite is NOT a "clean fit": it incurs three concrete re-homing costs the trivial ported probes never exercised, and the first one (return value) tilts the decision toward the queue-bridge.

Three port-work items the spec must carry regardless of option:

1. **Artifact return (the discriminator).** All three functions `return <path>` (`control_path` / `fragment-NN.json` / `SUMMARY_PATH`). Async generators cannot `return value` (SyntaxError), and the adapter today hardcodes `artifacts=[probe_ctx.probe_path()]` → the constant `.ephemeralos/sweevo-mock/probe.txt` (scenario_adapter.py:208). A REWRITE reports the WRONG artifact for all three unless a return channel is added (set-on-ctx / sentinel final yield / adapter-side path computation). A QUEUE-BRIDGE keeps each function a coroutine, so the `return path` survives unchanged. This cuts toward queue-bridge.
2. **Parameterized dispatch (cost common to BOTH options, not a tiebreaker).** `PROBE_BUILDERS.get("heavy_io_zoned_worker:5")` misses (the `:N` is not a dict key) and `builder(probe_ctx)` threads no `index`. The adapter needs `:N` parsing + `index` threading into the builder for the worker action.
3. **Custom SandboxCheck (REWRITE-only cost).** The worker's L199 check needs the bespoke `name`/`detail`/`changed_paths` shape that `record_check` can't produce; a rewrite must add a new public ProbeContext method wrapping `_publish_check`. A queue-bridge keeps `publish_mock_record` as an injected callback and pays nothing here.

Net: REWRITE pays gaps 1+3 (new ctx return channel + new custom-check method); QUEUE-BRIDGE pays neither (byte-identical body, return value intact) at the cost of the asyncio.Queue + per-call Future machinery. Given gap #1, recommend QUEUE-BRIDGE for this module unless the team wants every heavy probe normalized to the generator form for consistency, in which case REWRITE is acceptable but must implement gaps 1+3.

**Concurrency/cancellation/background hazards for the bridge: NONE.** No background_task_id/sandbox_invocation_id, no task cancellation, no partial-write assertions, no races. Note only that worker shells are long (per-zone `_long_write_command` ≈ 11×(3MB dd + 3s sleep) ≈ 33s+ wall, `timeout:180`); the bridge's per-call Future simply awaits the loop result — no special handling needed.

### executor action strings it backs

Cross-ref `_run_executor` (runner.py:593-615) and scenario source `heavy_io_zoned_concurrent.py:105-111`:

- `heavy_io_zoned_seed` → `run_heavy_io_zoned_seed_probe`
- `heavy_io_zoned_worker:N` (N = `index`, e.g. `heavy_io_zoned_worker:5`) → `run_heavy_io_zoned_worker_probe(index=N)`
- `heavy_io_zoned_reconcile` → `run_heavy_io_zoned_reconcile_probe`

Source file generating these: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/sandbox/heavy_io_zoned_concurrent.py` (`executor_actions`, L105-111; `WORKER_COUNT` workers + seed + reconcile).

Relevant file paths:
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/heavy_io_zoned_probe.py` (port target)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` (dispatch L593-615, L1335-1386; `_call_tool` L1583-1712)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/scenario_adapter.py` (`_executor_script` asend bridge L176-212; hardcoded artifact L208)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/probes.py` (REWRITE pattern + `ProbeContext`; `record_check` L99, `_publish_check` L86)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/tool_scripts.py` (`CallTool` protocol L32-41)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/sandbox/heavy_io_zoned_concurrent.py` (executor_actions L105-111)


### background_shell

Confirmed. `launch_background_tool` returns a `ToolResultBlock` immediately (it spawns the task into the supervisor and returns a synthetic "started" block — it does NOT await the shell's completion). This is the decisive architectural mismatch. The background_shell probes require `await call_tool(...)` to BLOCK until the shell's real exit_code/stdout/changed_paths are available, and to be cancellable mid-flight. The loop's background path cannot deliver that.

I have all the evidence needed. Producing the port spec.

### background_shell — entry functions

All 13 entries are module-level async functions with an identical keyword-only signature:
`(*, metadata: ExecutionMetadata, emit: EmitStreamEvent, call_tool: CallTool, record_tool_check: RecordToolCheck) -> str`. None take a `smoke`/`index`/`mode` param — `mode` is a *runner-side* selector, not a probe arg.

| Function | Maps from runner mode |
|---|---|
| `run_background_shell_golden_probe` | `golden` |
| `run_background_shell_stop_probe` | `cancel` |
| `run_background_shell_interleave_probe` | `interleave` |
| `run_background_shell_exhaustion_probe` | `exhaustion` |
| `run_background_shell_partial_write_cancel_probe` | `partial_write_cancel` |
| `run_background_shell_maintenance_probe` | `cancel_during_maintenance` |
| `run_background_shell_late_cancel_probe` | `late_cancel_race` |
| `run_background_mixed_fg_bg_same_path_conflict_probe` | `mixed_fg_bg_same_path_conflict` |
| `run_background_heartbeat_loss_probe` | `heartbeat_loss` |
| `run_background_exit_iws_drains_agent_tasks_probe` | `exit_iws_drain` |
| `run_background_engine_restart_no_lease_leak_probe` | `engine_restart_no_lease_leak` |
| `run_background_many_small_writes_probe` | `many_small_writes` |
| `run_background_mixed_op_concurrent_probe` | `mixed_op_concurrent` |

**Dispatch chain (runner.py):** `_run_executor` (372-823) matches a `background_*` executor-action string → calls `self._run_background_shell_probe(metadata, emit, mode=<mode>)` (lines 697-778). `_run_background_shell_probe` (1388-1438) holds a `mode → fn` dict and invokes the chosen fn with `call_tool=self._call_tool, record_tool_check=self._record_tool_check`. No `smoke` flag exists for this module (unlike `complex_project_build_*` probes). Import is the lazy `from task_center_runner.agent.mock import background_shell_probe` at line 1395.

The injected `call_tool` is `runner._call_tool` (1583-1714), whose signature is **wider** than the ported `CallTool` Protocol in `tool_scripts.py` (32-41): it adds `background_task_id` and `sandbox_invocation_id` kwargs. The 3 ported probes' generator path does NOT thread these — so the existing `ScenarioEventSource`/`ToolCall` seam has no way to express them.

### call_tool sites

Every `await call_tool(...)` / `_call_probe_tool(...)` (the latter wraps `call_tool` + a `record_tool_check`). "BG" = passes `background_task_id` (and thus runs the runner's blocking-background path). Tool name is the `tool_obj`. Counts are per distinct call expression (loop bodies counted once with the loop noted).

| # | Probe | Line(s) | Tool | allow_error | BG? (background_task_id / sandbox_invocation_id) |
|---|---|---|---|---|---|
| 1 | golden | 337 (`_one`, ×3 gather) | shell | no | **BG** `_bg_id(golden-{i})` |
| 2 | golden | `_write_summary` 278/290 | shell(mkdir)+write_file | no | no |
| 3 | stop | 403 (`_one`, ×3, wrapped in `wait_for`) | shell | no | **BG** `_bg_id(cancel-{i})` |
| 4 | stop | 450 | shell (post-cancel fg) | no | no |
| 5 | stop | `_write_summary` | shell+write_file | no | no |
| 6 | interleave | 501 (`create_task`) | shell | no | **BG** `_bg_id(interleave-bg)` |
| 7 | interleave | 519 (loop ×5) | shell (fg) | no | no |
| 8 | interleave | `_write_summary` | shell+write_file | no | no |
| 9 | exhaustion | 611 (`_launch_then_cancel`, ×80, `wait_for`) | shell | no | **BG** `_bg_id(exhaust-{i})` |
| 10 | exhaustion | 643 | shell (seed) | no | no |
| 11 | exhaustion | 656 | read_file | no | no |
| 12 | exhaustion | `_write_summary` | shell+write_file | no | no |
| 13 | partial_write | 712 | shell (seed dir) | no | no |
| 14 | partial_write | 737 (`wait_for`) | shell (dd) | no | **BG** `_bg_id(partial-write)` |
| 15 | partial_write | 756 | read_file | **yes** | no |
| 16 | partial_write | `_write_summary` | shell+write_file | no | no |
| 17 | maintenance | 800 | shell (short write) | no | **BG** `_bg_id(maintenance)` |
| 18 | maintenance | 818 | read_file | **yes** | no |
| 19 | maintenance | `_write_summary` | shell+write_file | no | no |
| 20 | late_cancel | 867 | shell | no | **BG** `_bg_id(late-cancel)` |
| 21 | late_cancel | `_write_summary` | shell+write_file | no | no |
| 22 | mixed_conflict | 915 (`create_task` via `_call_probe_tool`) | shell | **yes** | **BG** `_bg_id(mixed-conflict)` |
| 23 | mixed_conflict | 938 | write_file (fg) | **yes** | no |
| 24 | mixed_conflict | 953 | read_file (final) | **yes** | no |
| 25 | mixed_conflict | `_write_summary` | shell+write_file | no | no |
| 26 | heartbeat_loss | 1011 (`create_task`) | shell (protected) | **yes** | **BG** `_bg_id(heartbeat-protected)` + **`sandbox_invocation_id=protected_invocation_id`** |
| 27 | heartbeat_loss | 1032 (`create_task`) | shell (stale) | **yes** | **BG** `_bg_id(heartbeat-stale)` + **`sandbox_invocation_id=stale_invocation_id`** |
| 28 | heartbeat_loss | 1070 | shell (fg) | **yes** | no |
| 29 | heartbeat_loss | 1098 | read_file (protected) | **yes** | no |
| 30 | heartbeat_loss | 1108 | read_file (stale) | **yes** | no |
| 31 | heartbeat_loss | `_write_summary` | shell+write_file | no | no |
| 32 | exit_iws_drain | 1165 (`create_task`) | shell (default) | **yes** | **BG** `_bg_id(iws-default-blocker)` |
| 33 | exit_iws_drain | 1189 | enter_isolated_workspace (blocked) | **yes** | no |
| 34 | exit_iws_drain | 1209 | enter_isolated_workspace (other agent, `iws_metadata`) | **yes** | no |
| 35 | exit_iws_drain | 1228 (inside `manager.launch`) | shell (iws bg, `iws_metadata`) | **yes** | **BG** `task_id=manager.next_alias()` |
| 36 | exit_iws_drain | 1248 | exit_isolated_workspace (blocked) | **yes** | no |
| 37 | exit_iws_drain | 1258 | cancel_background_task | **yes** | no |
| 38 | exit_iws_drain | 1272 | exit_isolated_workspace (after cancel) | **yes** | no |
| 39 | exit_iws_drain | 1285 / 1295 | read_file (default / iws) ×2 | **yes** | no |
| 40 | exit_iws_drain | `_write_summary` | shell+write_file | no | no |
| 41 | engine_restart | 1359 (`create_task`) | shell (abandoned) | **yes** | **BG** `_bg_id(engine-abandon)` + **`sandbox_invocation_id=invocation_id`** |
| 42 | engine_restart | 1396 / 1406 / 1416 / 1426 | read_file, shell(fg), write_file(recovery), read_file ×4 | **yes** | no |
| 43 | engine_restart | `_write_summary` | shell+write_file | no | no |
| 44 | many_small_writes | 1488 | write_file (seed) | no | no |
| 45 | many_small_writes | 1501 (`_background_one`, ×16 create_task) | shell | **yes** | **BG** `_bg_id(many-{i})` |
| 46 | many_small_writes | 1531 / 1541 (loop ×8) | write_file (fg) + read_file | **yes** | no |
| 47 | many_small_writes | 1566 (verify loop) | read_file | **yes** | no |
| 48 | many_small_writes | `_write_summary` | shell+write_file | no | no |
| 49 | mixed_op | 1652 / 1664 | shell(seed) + write_file(seed pytest) | no | no |
| 50 | mixed_op | 1690 (`mixed_tasks`, ×3 create_task) | shell (pytest/pip/edit) | **yes** | **BG** `_bg_id(mixed-{name})` |
| 51 | mixed_op | 1714 | write_file (overlap seed) | no | no |
| 52 | mixed_op | 1725 (`_overlap_one`, ×4 gather) | shell | **yes** | **BG** `_bg_id(overlap-{i})` |
| 53 | mixed_op | 1747 | read_file (overlap final) | **yes** | no |
| 54 | mixed_op | 1762 (`_disjoint_one`, ×4 gather) | shell | **yes** | **BG** `_bg_id(disjoint-{i})` |
| 55 | mixed_op | 1787 (read loop ×4) | read_file | **yes** | no |
| 56 | mixed_op | `_write_summary` | shell+write_file | no | no |

**Count:** ~56 distinct `call_tool` call expressions. **BG (background) call sites: 13 distinct expressions**, several inside `gather`/loop fan-outs (golden ×3, cancel ×3, exhaustion ×80, many ×16, overlap ×4, disjoint ×4, mixed ×3) → hundreds of concurrent background invocations at runtime. **3 BG sites also pass an explicit `sandbox_invocation_id`** (heartbeat protected+stale, engine_restart). `_write_summary` adds 2 foreground calls per probe (mkdir shell + write_file).

### out-of-band work (NOT through call_tool)

Direct `sandbox_api.*` (no loop involvement):
- `sandbox_api.inflight_count(sandbox_id, agent_id)` — `_wait_for_background_drain` (91), `_wait_for_inflight_count` (217), and direct reads at 1118, 1436, 1583.
- `sandbox_api.heartbeat(sandbox_id, [invocation_id])` — `_heartbeat_until` (234).
- `sandbox_api.cancel(...)` — NOT in this module; it lives in `runner._call_tool`'s `CancelledError` handler (1665). Re-homing target: whatever owns the bridged `call_tool` must replicate this on cancel.

Other out-of-band machinery (re-home to a `ProbeContext`-style helper):
- `record_tool_check(...)` — passed in; threaded everywhere via `_call_probe_tool(record_tool_check=...)` and direct calls (e.g. 284, 296, 347). Equivalent to `ProbeContext.record_check`.
- **No** `publish(...)` / `publish_mock_record(...)` / `caller(...)` are used by this module (unlike `complex_project_build_*`). Only `record_tool_check`.
- `BackgroundTaskSupervisor()` constructed in-probe (1206) + `manager.next_alias()` / `manager.launch(...)` / `manager.get_task(...)` (1223-1283) — exit_iws_drain manages its OWN supervisor for the "other agent" iws task, independent of the loop's supervisor.
- `metadata.copy()` + mutation of `agent_name`/`agent_run_id`/`layer_stack_root`/`background_task_manager` (1202-1207) — exit_iws_drain fabricates a second agent identity.

### loop-interaction verdict (DECISIVE)

**NO.** This module does NOT interact with the engine only via the injected `call_tool`-as-the-existing-seam, and the gap is architectural, not cosmetic. Three independent blockers:

1. **Background semantics are incompatible with the loop's background path.** The probes get "background" by passing `background_task_id` to the runner's `_call_tool`, which sets `metadata.with_overrides(background_task_id=..., sandbox_invocation_id=...)` and then **blocks awaiting the real shell result** (exit_code/stdout/changed_paths) — line 1652 `await execute_tool_once(...)`. The engine loop decides background by `tool_call.input.get("background", False)` (`dispatch.py:410`) and, when background, calls `launch_background_tool` which **returns a synthetic "started" `ToolResultBlock` immediately** (`engine/background/dispatch.py`, `launch_background_tool`) without awaiting completion. The `ScenarioEventSource`/`ToolCall` seam (`ToolCall(name, input)`, event_source.py:57) has **no channel for `background_task_id`/`sandbox_invocation_id`** and would route a `{"background": True}` input down the loop's fire-and-forget path. The probes' assertions (`payload.get("exit_code")`, `_shell_metadata(result)["changed_paths"]`, `"foreground-win" in final_content`) require the COMPLETED result, which the loop's background dispatch never returns to the caller.

2. **Cancellation is caller-driven.** stop/exhaustion/partial_write wrap `call_tool` in `asyncio.wait_for(..., timeout=...)` and rely on `runner._call_tool`'s `except asyncio.CancelledError:` block calling `sandbox_api.cancel(sandbox_id, resolved_sandbox_invocation_id)` (1659-1680). The loop never lets a probe `wait_for`/cancel an individual in-flight tool dispatch — the loop owns the await.
   - Exact site: `background_shell_probe.py:403` — `await asyncio.wait_for(call_tool(shell_tool, {...}, ..., background_task_id=_bg_id(...)), timeout=CANCEL_AFTER_S)`.

3. **Direct concurrency + out-of-band sandbox + second supervisor.** `asyncio.create_task`/`asyncio.gather` over `call_tool` (e.g. 359, 500, 632, 1740), plus `sandbox_api.inflight_count`/`heartbeat`/`cancel` and a probe-owned `BackgroundTaskSupervisor` (1206) — none of which the per-turn `yield ToolCall` generator model can express (one yield = one sequential loop turn).

### recommended adaptation

**QUEUE-BRIDGE (zero body change)** — with explicit hazard flags; a pure rewrite-as-generator is infeasible here.

Rationale: The 13 probes are deeply concurrent (gather/create_task fan-outs of up to 80 in-flight tools) and the bodies branch on the *completed* background result; they cannot be flattened into the strictly-sequential `yield ToolCall` generator the 3 ported probes use (one yield = one turn; you cannot `yield` from inside `gather`/`create_task` and you cannot await N tools in one turn that the loop dispatches one-foreground-at-a-time). Keep each probe byte-identical and inject a bridging `call_tool` shim that satisfies the **wider** runner signature (`background_task_id`/`sandbox_invocation_id`/`allow_error`) and routes each call through... — and here is the decisive caveat:

**The queue-bridge CANNOT route background calls through the engine loop and still preserve semantics.** Because the loop's background dispatch returns a synthetic started-block (not the completed result) and the loop owns the await, a faithful bridge for the background path must replicate `runner._call_tool`'s blocking `execute_tool_once(... metadata.with_overrides(background_task_id=...))` + `CancelledError→sandbox_api.cancel` directly, i.e. the bridge IS essentially the runner's `_call_tool` extracted into a standalone callable (HYBRID in spirit: queue-bridge for any future foreground turns, but the background path keeps the runner's direct-dispatch). For Phase 2, the cheapest correct move is to lift `runner._call_tool` (1583-1714) into a shared `ProbeCallTool` helper that these 13 probes call directly, NOT to push them through `ScenarioEventSource`. That preserves the "real tool path" (`execute_tool_once`, real pre-hooks, real OCC) while keeping the blocking+cancel+`sandbox_invocation_id` contract these tests assert on.

Concurrency/cancellation/background hazards for any bridge:
- **Blocking-await contract:** `await call_tool(... background_task_id=...)` MUST block until the shell exits (golden, late_cancel, maintenance read the completed `exit_code`/`changed_paths`). The loop's `launch_background_tool` violates this — do not route BG through the loop.
- **Per-call cancel:** stop/exhaustion/partial_write cancel individual calls via `asyncio.wait_for` and require `sandbox_api.cancel(sandbox_id, sandbox_invocation_id)` on `CancelledError`, plus `current_task.uncancel()` (runner 1663). The bridge must own this; a Queue+Future design must propagate cancel of the awaiting Future into an actual sandbox cancel, or the daemon leaks the in-flight invocation (exhaustion/AC-14 asserts inflight drains).
- **`sandbox_invocation_id` passthrough:** heartbeat_loss + engine_restart pass deterministic invocation ids and then call `sandbox_api.heartbeat`/`inflight_count` on exactly those ids. The bridge must honor the caller-supplied id, not mint a fresh one.
- **Massive fan-out:** exhaustion launches 80 concurrent BG shells; a single shared Queue+single-consumer bridge serializes them and breaks the AC (it asserts the dispatcher executor is NOT shared/serialized). Bridge must allow true concurrency.
- **partial_write asserts on partial state** (`tracked_exists_after_cancel`) and mixed_conflict/mixed_op assert OCC winner/loser (`aborted_version`/`aborted_overlap`/`aborted_lock`) — these depend on real cancel timing and real OCC publish, so the bridge must hit the real tool path, not a mock.
- **Second `BackgroundTaskSupervisor` + fabricated agent identity:** exit_iws_drain builds its own supervisor and a `metadata.copy()` with a different `agent_run_id`/`layer_stack_root`. This bypasses the loop's `context.tool_metadata.background_task_manager` entirely; the loop seam has no hook for it.

### executor action strings it backs

From `runner.py:_run_executor` (697-778), these 13 `executor_actions` strings route here (each → `_run_background_shell_probe(mode=...)`):
`background_shell_golden`, `background_shell_stop`, `background_shell_interleave`, `background_shell_exhaustion`, `background_shell_partial_write_cancel`, `background_shell_stop_during_maintenance`, `background_shell_late_cancel_race`, `background_mixed_fg_bg_same_path_conflict`, `background_heartbeat_loss_reaps_only_stale_bg`, `background_exit_iws_drains_agent_tasks`, `background_engine_restart_no_lease_leak`, `background_many_small_writes_do_not_starve_dispatcher`, `background_mixed_op_concurrent`.

Note: action string ≠ runner mode ≠ probe fn name (three-way rename). Any Phase-2 `PROBE_BUILDERS`-style registry must map all three. The 3 already-ported probes (preflight/sandbox_integrity/final_probe) are keyed by the action string directly in `probes.PROBE_BUILDERS`; for these 13, the action→mode indirection (e.g. `background_shell_stop`→`cancel`, `background_shell_stop_during_maintenance`→`cancel_during_maintenance`) must be preserved or collapsed.

Key file paths: probe module `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/background_shell_probe.py`; runner dispatch `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` (executor 697-778, `_run_background_shell_probe` 1388-1438, `_call_tool` 1583-1714); seam `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/event_source.py` + `scenario_adapter.py`; `CallTool` Protocol `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/tool_scripts.py:32`; loop background decision `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/engine/tool_call/dispatch.py:405-427`; immediate-return background launch `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/engine/background/dispatch.py` (`launch_background_tool`).


### ephemeral_workspace

The advisor is rate-limited. I have thorough primary-source evidence and will finalize. Let me confirm the exact `call_tool` signature mismatch one more time by re-checking the cancellation probe's direct `call_tool(...)` call (lines 469-491) against the `CallTool` Protocol (no `background_task_id`) versus the runner's actual `_call_tool` (has it). This is the decisive cross-module fact.

The cancellation probe calls `call_tool(shell_tool, {...}, metadata, emit, background_task_id=background_task_id)` directly (bypassing the module-local `_call_tool` wrapper), using a kwarg the typed `CallTool` Protocol does not declare. It works today only because runner.py's bound `self._call_tool` accepts it. This is the single hardest site for any port.

Here is the PORT SPEC.

---

### ephemeral_workspace — entry functions

All six are module-level `async def` coroutines (NOT yet async generators). Every signature is keyword-only after `*`. runner.py dispatches them through one helper, `_run_ephemeral_workspace_probe(metadata, emit, *, mode)` (runner.py:1440-1473), which holds a `mode -> func` `dispatch` dict and builds `kwargs={metadata, emit, call_tool=self._call_tool, record_tool_check=self._record_tool_check}`, adding `sandbox_id=sandbox_id` for every mode **except** `same_path_conflict`.

| Function | Signature (keyword-only) | mode key | sandbox_id passed? |
|---|---|---|---|
| `run_ephemeral_all_verbs_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `all_verbs` | yes |
| `run_ephemeral_concurrent_writes_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `concurrent_writes` | yes |
| `run_ephemeral_policy_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `policy` | yes |
| `run_ephemeral_cancellation_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `cancellation` | yes |
| `run_ephemeral_o1_disk_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `o1_disk` | yes |
| `run_ephemeral_same_path_conflict_probe` | `(*, metadata, emit, call_tool, record_tool_check) -> str` (NO `sandbox_id`) | `same_path_conflict` | **no** |

No smoke/index params. `mode` is the only selector. No external Python importer of the functions exists; only the six SUMMARY-path constants (`ALL_VERBS_SUMMARY`, `CONCURRENT_WRITES_SUMMARY`, `POLICY_SUMMARY`, `CANCELLATION_SUMMARY`, `O1_DISK_SUMMARY`, `ROOT`) are imported by the test files. No `PreparedToolScriptEngine` involvement — this module hand-rolls its own `_call_tool` wrapper (lines 803-823) rather than using the script engine.

### call_tool sites

Two layers: a module-local helper `_call_tool(...)` (lines 803-823) wraps the injected `call_tool` and adds the `record_tool_check` audit. `all_verbs` adds a further nested wrapper `call(...)` (lines 72-107) that wraps `_call_tool` plus layer-metric diffing. Every row below is one underlying invocation of the injected `call_tool`. **Total = 18 static call sites; effective ~149 runtime calls** (o1_disk's loop is 100, concurrent's loops are ~22).

| # | Probe | Tool | key args | allow_error | BACKGROUND |
|---|---|---|---|---|---|
| 1 | all_verbs | write_file | `.ephemeralos/.gitignore` | no | no |
| 2 | all_verbs | write_file | module.py | no | no |
| 3 | all_verbs | write_file | `__init__.py` | no | no |
| 4 | all_verbs | read_file | module.py (read_only intent) | no | no |
| 5 | all_verbs | edit_file | module.py alpha→beta | no | no |
| 6 | all_verbs | grep | `VALUE = 'beta'` files_with_matches | no | no |
| 7 | all_verbs | glob | `**/*.py` | no | no |
| 8 | all_verbs | write_file | delete_me.txt | no | no |
| 9 | all_verbs | write_file | opaque_dir/old.txt | no | no |
| 10 | all_verbs | shell | python heredoc: unlink/symlink/opaque-dir/generate | no | no |
| 11 | all_verbs | read_file | generated.txt | no | no |
| 12 | all_verbs (`_write_summary`) | write_file | summary.json | no | no |
| 13 | concurrent_writes | shell | `mkdir -p {root}` | no | no |
| 14 | concurrent_writes | write_file ×8 | `typed-{i}.txt` (asyncio.gather) | no | no |
| 15 | concurrent_writes | shell ×2 | `printf > shell-{i}.txt` (asyncio.gather) | no | no |
| 16 | concurrent_writes | read_file ×10 | readbacks (record_tool_check=None) | no | no |
| 17 | concurrent_writes (`_write_summary`) | write_file | summary.json | no | no |
| 18 | policy | read_file | `/etc/hosts` | no | no |
| 19 | policy | write_file | `/tmp/eph-scratch.txt` | no | no |
| 20 | policy | write_file ×4 | denylist paths (/etc/hosts, /proc/sysrq-trigger, /sys/kernel/printk, /boot/grub.cfg) | **yes** | no |
| 21 | policy (`_write_summary`) | write_file | summary.json | no | no |
| 22 | **cancellation** | **shell** | python heredoc, 200MB fsync loop; **DIRECT `call_tool(...)`, NOT `_call_tool`** | no | **YES — `background_task_id=background_task_id`** |
| 23 | cancellation | read_file | partial.bin | **yes** | no |
| 24 | cancellation | write_file | after_cancel.txt | no | no |
| 25 | cancellation | read_file | after_cancel.txt | no | no |
| 26 | cancellation (`_write_summary`) | write_file | summary.json | no | no |
| 27 | o1_disk | write_file | base.txt seed | no | no |
| 28 | o1_disk | write_file/edit_file/read_file ×100 | loop, `index%3` selects verb | no | no |
| 29 | o1_disk (`_write_summary`) | write_file | summary.json | no | no |
| 30 | same_path_conflict | write_file | shared.txt seed | no | no |
| 31 | same_path_conflict | write_file ×4 | `owner=first-{i}` (asyncio.gather) | **yes** | no |
| 32 | same_path_conflict | read_file + write_file (retry per failed index) | fresh read + `owner=retry-{i}` | no | no |
| 33 | same_path_conflict | read_file | final read | no | no |
| 34 | same_path_conflict (`_write_summary`) | write_file | summary.json | no | no |

The ONLY background call is site #22 (cancellation). It passes `background_task_id` and is wrapped in `asyncio.wait_for(..., timeout=1.0)`, expecting `asyncio.TimeoutError` to trigger cancellation. No `sandbox_invocation_id` is passed by the probe (the runner mints one). No `input has background=True` style calls.

### out-of-band work (NOT through call_tool)

Direct `sandbox_api.*` (do NOT touch the loop):
- `_layer_metrics(sandbox_id)` → `DaemonSandboxTransport().call(sandbox_id, "api.layer_metrics", {}, timeout=60)` (lines 852-858). Called in all_verbs (before + per-`call` after, ~12×) and o1_disk (baseline + per-iteration, ~101×).
- `_runtime_sample(sandbox_id)` → `sandbox_api.raw_exec(sandbox_id, <python heredoc walking /eos-mount-scratch/.../overlay>, timeout=30)` (lines 861-903). Called in concurrent (1×), cancellation (1×), all_verbs (per-`call`), o1_disk (per-10-call sample).
- `policy`: `sandbox_api.raw_exec(sandbox_id, "test -f /tmp/eph-scratch.txt && cat ...", timeout=20)` (line 397) — the `tmp_probe`.
- `cancellation`: `_wait_for_background_drain(metadata)` → `sandbox_api.inflight_count(sandbox_id, agent_id)` polled to 0 with a 15s deadline (lines 906-919). Reads `metadata.sandbox_id` and `metadata.agent_run_id/agent_name`.

Audit/record paths:
- `record_tool_check(...)` is invoked indirectly via the module-local `_call_tool` (line 821-822) which calls `record_tool_check(f"tool.{tool_obj.name}.ephemeral_workspace.{label}", result)`. This is the injected `self._record_tool_check`. → re-homes to `ProbeContext.record_check`.
- No direct `publish(...)` / `publish_mock_record(...)` / `caller(...)` in this module. (Those live in runner's `_call_tool`, which under the new path becomes the loop's own tool-execution + the adapter's normalize step.)

### loop-interaction verdict (DECISIVE)

**NO.** The module is *almost* clean — every functional tool call routes through the injected `call_tool` and everything else is out-of-band `sandbox_api`/`record_tool_check`. But ONE site needs more than the typed `CallTool` contract:

cancellation probe, lines 467-492:
```python
await asyncio.wait_for(
    call_tool(
        shell_tool, {...}, metadata, emit,
        background_task_id=background_task_id,
    ),
    timeout=1.0,
)
```
The `CallTool` Protocol (tool_scripts.py:32-41) declares only `(tool_obj, raw_input, metadata, emit, *, allow_error=False)` — it has NO `background_task_id` parameter. This call compiles today solely because runner's bound `self._call_tool` (runner.py:1583-1593) does accept `background_task_id` / `sandbox_invocation_id` and wires them into `metadata.with_overrides(...)` plus a `CancelledError` handler that calls `sandbox_api.cancel(...)`. A queue-bridge `call_tool` shim that only forwards `(tool, args)` as a `Turn(ToolCall)` cannot carry `background_task_id`, cannot make the loop run it as a true background dispatch, and cannot be cancelled by `wait_for` the way the probe assumes.

### recommended adaptation

**HYBRID.** Sites #1–21, #23–34 are body-identical candidates for **QUEUE-BRIDGE (zero body change)**: pure `await call_tool(tool, args, metadata, emit, allow_error=...)` whose only return-value use is normalized `ToolResult` metadata. A bridging shim (asyncio.Queue + per-call Future feeding `Turn(calls=(ToolCall,))` and resolving with the normalized result) keeps those five probes (all_verbs, concurrent_writes, policy, o1_disk, same_path_conflict) byte-for-byte intact, including the `asyncio.gather` fan-outs — the loop serializes them into turns and the futures resolve in order. The cancellation probe (site #22) must be **REWRITTEN** (or handled by a background-aware variant of the bridge), because (a) `background_task_id` is not in the `CallTool` contract and a plain ToolCall yield cannot express a background dispatch, and (b) the probe relies on `asyncio.wait_for(call_tool(...), timeout=1.0)` raising `CancelledError` *inside* the tool task so the runner's `CancelledError` branch fires `sandbox_api.cancel`. Through the real query loop, background execution is an engine dispatch mode (loop owns the background task), so cancellation must route through the loop's background-stop mechanism, not by cancelling the coroutine `await`.

Hazards for the bridge:
- **Background/cancellation race (site #22):** This is the bridge-killer. Cancelling the bridge future does not cancel the in-flight sandbox shell; the partial-write assertion (`partial_read.is_error` must be true) and the `command_overlay_run_dirs == 0` leak check both depend on the engine actually cancelling the background dispatch and dropping the partial upperdir.
- **Concurrency (sites #14/#15/#31):** `asyncio.gather` issues N `call_tool` awaits concurrently; the bridge must accept multiple pending futures and either batch them into one multi-ToolCall `Turn` or serialize into N turns. The probe's invariants (`typed_sources == {"api_write"}`, `shell_sources == {"overlay_capture"}`, OCC same-path conflicts in same_path_conflict) only hold if the bridge preserves true concurrency or at least the OCC stale-base semantics — a naive serialize-everything bridge would make same_path_conflict produce zero typed conflicts (it asserts `failed_indexes` is non-empty), breaking the test.
- **Drain ordering (cancellation):** `_wait_for_background_drain` and `_runtime_sample` must run *after* the loop has finished cancelling the background task; with a rewrite the probe must await the loop's drain, not just `inflight_count` polling.

### executor action strings it backs

From runner.py `_run_executor` (the `elif action == ...` chain at 661-696), six strings route here, each calling `_run_ephemeral_workspace_probe(metadata, emit, mode=...)`:

| executor_action | mode | scenario class (scenarios/sandbox/ephemeral_workspace.py) | scenario name |
|---|---|---|---|
| `ephemeral_workspace_all_verbs` | all_verbs | EphemeralWorkspaceAllVerbs | `sandbox.ephemeral_workspace_all_verbs` |
| `ephemeral_workspace_concurrent_writes` | concurrent_writes | EphemeralWorkspaceConcurrentWrites | `sandbox.ephemeral_workspace_concurrent_writes` |
| `ephemeral_workspace_same_path_conflict` | same_path_conflict | EphemeralWorkspaceSamePathConflict | `sandbox.ephemeral_workspace_same_path_conflict` |
| `ephemeral_workspace_policy` | policy | EphemeralWorkspacePolicy | `sandbox.ephemeral_workspace_policy` |
| `ephemeral_workspace_cancellation` | cancellation | EphemeralWorkspaceCancellation | `sandbox.ephemeral_workspace_cancellation` |
| `ephemeral_workspace_o1_disk` | o1_disk | EphemeralWorkspaceO1Disk | `sandbox.ephemeral_workspace_o1_disk` |

Relevant absolute paths:
- Module: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/ephemeral_workspace_probe.py`
- Dispatch + `_call_tool`: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` (executor 661-696; `_run_ephemeral_workspace_probe` 1440-1473; `_call_tool` 1583-1714)
- CallTool protocol: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/tool_scripts.py:32-41`
- Ported-probe reference (generator + ProbeContext pattern): `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/probes.py`
- Scenario classes: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/sandbox/ephemeral_workspace.py`
- Tests (consume SUMMARY constants via `run_ephemeral_scenario`): `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox/ephemeral_workspace/`


### plugin_workspace

The advisor is rate-limited. I have read every relevant file directly (the probe module in full, all 6 entry functions, every `call_tool`/`_call_probe_tool`/`_call_recorded_tool` site, every `sandbox_api`/`call_daemon_api` out-of-band call, the runner dispatch, the `_call_tool` signature with `background_task_id`, the scenario_adapter bridge target, and the scenario `executor_actions`). My analysis is grounded in primary source. Let me produce the spec.

### plugin_workspace — entry functions

Six public async entry functions, all keyword-only. runner.py dispatches them via `_run_plugin_workspace_probe(metadata, emit, *, mode)` (runner.py:1475-1508), which imports the module lazily, picks the function from a `mode -> fn` dispatch dict, and calls it with `metadata`, `emit`, `call_tool=self._call_tool`, `record_tool_check=self._record_tool_check`, plus `sandbox_id` for every mode **except** `intent_contract`. There is no `smoke`/`index` param; the only mode param is the `_run_executor` `action` string, mapped 1:1 to a `mode` literal (no fan-out, no index loop).

| entry function | signature (kw-only) | dispatch mode | sandbox_id passed? |
|---|---|---|---|
| `run_plugin_read_only_lsp_refresh_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `read_only_lsp_refresh` | yes |
| `run_plugin_write_allowed_publish_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `write_allowed_publish` | yes |
| `run_plugin_intent_contract_probe` | `(*, metadata, emit, call_tool, record_tool_check) -> str` | `intent_contract` | **no** |
| `run_plugin_iws_policy_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `iws_policy` | yes |
| `run_plugin_setup_failure_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `setup_failure` | yes |
| `run_plugin_service_evict_probe` | `(*, metadata, emit, call_tool, record_tool_check, sandbox_id) -> str` | `service_evict` | yes |

All return the summary-file path string. Internally each call_tool dispatch flows through two private wrappers: `_call_recorded_tool` (adds before/after `_layer_metrics` manifest deltas) and `_call_probe_tool` (the actual `await call_tool(...)` + optional `record_tool_check`). `_write_summary` also routes a `write_file` through `_call_probe_tool`.

### call_tool sites

Every loop-touching tool call routes through `_call_probe_tool` (plugin_workspace_probe.py:615-635), which does exactly one `await call_tool(tool_obj, raw_input, metadata, emit, allow_error=allow_error)`. **No site passes `background_task_id` or `sandbox_invocation_id`; no `raw_input` carries `background=True`. Zero background calls.** All tools are real `BaseTool` objects (not name strings). Counts are static (no smoke gating); the `service_evict` peer-write loop adds 5 fixed iterations.

| # | label | tool object | key args | allow_error | background |
|---|---|---|---|---|---|
| **read_only_lsp_refresh** (10 calls + summary) | | | | | |
| 1 | read_only.seed | write_file_tool | module.py content | no | no |
| 2 | read_only.lsp_warmup | lsp_diagnostics_tool | wait_for_diagnostics=False | no | no |
| 3 | read_only.hover_before | lsp_hover_tool | line=2,char=4 | no | no |
| 4 | read_only.definitions_before | lsp_find_definitions_tool | line=3,char=19 | no | no |
| 5 | read_only.diagnostics_before | lsp_diagnostics_tool | wait=False | no | no |
| 6 | read_only.default_edit | edit_file_tool | old/new_text | no | no |
| 7 | read_only.diagnostics_after | lsp_diagnostics_tool | wait=True | no | no |
| 8 | read_only.normal_read_after | read_file_tool | lines 1-10 | no | no |
| 9 | summary.read_only_lsp_refresh | write_file_tool | summary json | no | no |
| **write_allowed_publish** (3 calls + summary) | | | | | |
| 10 | write_allowed.seed | write_file_tool | target.py | no | no |
| 11 | write_allowed.apply_workspace_edit | lsp_apply_workspace_edit_tool | edit dict | no | no |
| 12 | write_allowed.normal_read_after | read_file_tool | lines 1-5 | no | no |
| 13 | summary.write_allowed_publish | write_file_tool | summary json | no | no |
| **intent_contract** (1 call: summary only) | | | | | |
| 14 | summary.intent_contract | write_file_tool | summary json | no | no |
| **iws_policy** (2 calls + summary) | | | | | |
| 15 | iws.enter | enter_isolated_workspace_tool | layer_stack_root | no | no |
| 16 | iws.exit | exit_isolated_workspace_tool | grace_s=5.0 | **YES** | no |
| 17 | summary.iws_policy | write_file_tool | summary json | no | no |
| **setup_failure** (1 call: summary only) | | | | | |
| 18 | summary.setup_failure | write_file_tool | summary json | no | no |
| **service_evict** (9 calls + summary) | | | | | |
| 19 | service.seed | write_file_tool | service_mod.py | no | no |
| 20 | service.diagnostics_initial | lsp_diagnostics_tool | wait=False | no | no |
| 21-25 | service.peer_write_0..4 | write_file_tool | peer_N.py (×5) | no | no |
| 26 | service.diagnostics_after_peer_publishes | lsp_diagnostics_tool | wait=False | no | no |
| 27 | service.hover_after_peer_refresh | lsp_hover_tool | line=0,char=4 | no | no |
| 28 | service.diagnostics_after_evict | lsp_diagnostics_tool | wait=False | no | no |
| 29 | summary.service_evict | write_file_tool | summary json | no | no |

**Total call_tool sites: 29** (one is allow_error=True: `iws.exit`). No `PreparedToolScriptEngine` use — this module does not touch tool_scripts.py at all.

### out-of-band work (NOT through call_tool)

This module does **not** use `publish(...)`, `publish_mock_record(...)`, or `caller(...)` (those are not in its signature — unlike complex_project_build probes). The out-of-band surface is direct sandbox/daemon/in-process calls:

- `sandbox_api.raw_exec(sandbox_id, command, timeout=30)` — `_runtime_sample` (line 710), runs a heredoc python overlay-dir size walk. Called twice in `write_allowed_publish` (`runtime_before`/`runtime_after`, lines 256/293).
- `call_daemon_api(sandbox_id, op, args, timeout=...)` — daemon RPC, used in:
  - `_layer_metrics` → `api.layer_metrics` (line 665), called twice per `_call_recorded_tool` (before/after).
  - `iws_policy`: `_daemon_error_record` → `api.plugin.status` and `plugin.lsp.hover` (expected-failure path, lines 368/373); plus a direct `api.plugin.status` for `default_status` (line 395).
  - `service_evict`: `api.plugin.ensure` with forced digest churn (line 529).
- **Pure in-process probes (no sandbox at all):**
  - `intent_contract` → `_run_intent_contract_checks`: mutates `op_registry._PENDING`, monkeypatches `overlay_dispatch.run_plugin_op_with_workspace_overlay`, calls `flush_plugin_registrations`, `register_plugin_op`, `exec()`s synthetic plugin code, and `await`s registered handlers directly (lines 733-830). Touches global registry module state.
  - `setup_failure` → `_run_setup_failure_checks`: swaps `plugin_host_dispatch._PLUGIN_MANIFESTS_BY_NAME`, calls `reset_host_dispatch_cache_for_tests()`, and `await plugin_host_dispatch.call_plugin(...)` with injected `install_runner`/`daemon_dispatcher` fakes (lines 833-901). Touches global host-dispatch cache.
- `record_tool_check(...)` is the injected callback (re-homed to ProbeContext-style `record_check`), invoked inside `_call_probe_tool` as `record_tool_check(f"tool.{name}.plugin.{label}", result)`.

### loop-interaction verdict (DECISIVE)

**NO.** The six entries are not pure call_tool consumers. Engine-loop interaction does go solely through the injected `call_tool` (all 29 sites), but the probes require substantial extra capabilities the loop does not provide:

1. `sandbox_id`-scoped out-of-band RPC that is **load-bearing for the assertions**, not just audit. Quote (service_evict, line 529): `evict_ensure = await call_daemon_api(sandbox_id, "api.plugin.ensure", {...}, timeout=60)` — its result is written into the summary and is the entire point of the probe. Same for `iws_policy` `default_status = await call_daemon_api(sandbox_id, "api.plugin.status", ...)` (line 395) and every `_layer_metrics` manifest delta.
2. Two modes (`intent_contract`, `setup_failure`) make **zero** functional call_tool calls (only a final summary write) and instead drive in-process module-global monkeypatching. Quote (line 811): `overlay_dispatch.run_plugin_op_with_workspace_overlay = stub_overlay_runner` and (line 837) `plugin_host_dispatch._PLUGIN_MANIFESTS_BY_NAME = {plugin_name: _fake_manifest(plugin_name)}`.

So: loop interaction is call_tool-only, but the module is far from "everything else is just publish/record" — it has heavy sandbox RPC and in-process global mutation that a ProbeContext must absorb.

### recommended adaptation

**QUEUE-BRIDGE (zero body change).** The probe bodies never `yield`; they only `await call_tool(...)` deep inside `_call_probe_tool`. A bridging `call_tool` shim (asyncio.Queue + per-call Future) lets each `await call_tool(...)` route a `ToolCall` out to the top-level `_executor_script` yield and resolve the Future with the normalized `ToolResult`, leaving all 29 call sites and the entire probe file byte-identical. Rewriting as a generator is infeasible here because the call_tool sites are nested two layers deep (`entry -> _call_recorded_tool -> _call_probe_tool`) and `_write_summary`, and Python forbids `yield` inside those helpers — exactly the constraint the bridge sidesteps.

**Hazards for the bridge (low for this module, but real):**
- **No background/cancellation hazard.** Unlike background_shell, this module passes no `background_task_id`/`sandbox_invocation_id`, never cancels tasks, and has no concurrency — all 29 calls are strictly sequential `await`s. The cancel-on-CancelledError path in `_call_tool` (runner.py:1659) is never exercised here.
- **`allow_error=True` must propagate through the shim.** `iws.exit` (line 384) and the `record_tool_check=None` branch in `_call_recorded_tool` depend on `allow_error` reaching the real `_call_tool` so an error ToolResult is returned (not raised). The shim must forward the `allow_error` kwarg verbatim.
- **The out-of-band `sandbox_id` + global-state work runs during `asend`** (between yields), same as the 3 ported probes — but it is much heavier here (`raw_exec` heredoc, `call_daemon_api`, registry/host-dispatch monkeypatching). The bridge owner (ProbeContext / event-source) must expose `sandbox_id` and `metadata` to these helpers, and the `intent_contract`/`setup_failure` global-state swaps must remain wrapped in their existing try/finally so a mid-probe loop teardown does not leak `op_registry._PENDING` or the patched `overlay_dispatch.run_plugin_op_with_workspace_overlay` / `_PLUGIN_MANIFESTS_BY_NAME`.

### executor action strings it backs

Defined in `scenarios/sandbox/plugin.py` via `_PluginScenarioBase.executor_actions` (returns `(self.action_id,)` when `f"ACTION {action_id}"` is in the context message). These route through `_run_executor` (runner.py:779-814) to `_run_plugin_workspace_probe(mode=...)`:

| executor_actions string (== action_id) | scenario class / registry key | runner mode |
|---|---|---|
| `plugin_read_only_lsp_refresh` | `PluginReadOnlyLspRefresh` / `sandbox.plugin_read_only_lsp_refresh` | `read_only_lsp_refresh` |
| `plugin_write_allowed_publish` | `PluginWriteAllowedPublish` / `sandbox.plugin_write_allowed_publish` | `write_allowed_publish` |
| `plugin_intent_contract` | `PluginIntentContract` / `sandbox.plugin_intent_contract` | `intent_contract` |
| `plugin_iws_policy` | `PluginIwsPolicy` / `sandbox.plugin_iws_policy` | `iws_policy` |
| `plugin_setup_failure` | `PluginSetupFailure` / `sandbox.plugin_setup_failure` | `setup_failure` |
| `plugin_service_evict` | `PluginServiceEvict` / `sandbox.plugin_service_evict` | `service_evict` |

Note for the port: the existing `_executor_script` (scenario_adapter.py:192-208) looks up `PROBE_BUILDERS[action]` and raises `NotImplementedError(f"executor action {action!r} not yet adapted (Phase 2)")` for any unregistered action — so until these 6 strings are wired into a builder (or a queue-bridge dispatch keyed by action_id), all six plugin scenarios fail fast on the event-source path. These actions return a single-element tuple per attempt (no fan-out), so the bridge handles one probe coroutine per executor turn-sequence.


### tool_scripts(PreparedToolScriptEngine + simple scripts)

Both greps resolved. `AUTO_SQUASH_MAX_DEPTH = 100`, so `_auto_squash_seed_steps` produces 104 writes. `ToolScriptResult` consumers use only `.summary`, `.artifact`, and `.script_name` (in runner.py) — never `.results`. The `.results` field is vestigial (carried but never read by any consumer). All consumers are in runner.py's dispatch, which is the adapter's concern. The spec is fully grounded.

---

### tool_scripts — entry functions

Public entry functions (all module-level, all pure data builders that take a `ScenarioContext` and return a `PreparedToolScript` — none touch the loop themselves). Plus the `PreparedToolScriptEngine` which is the *only* loop-touching object.

| Entry | Signature | runner.py import + call site |
|---|---|---|
| `PreparedToolScriptEngine` | `class; __init__(self, call_tool: CallTool)`; `async run(self, script, *, metadata, emit) -> ToolScriptResult` | imported runner.py:101; constructed `self._script_engine = PreparedToolScriptEngine(self._call_tool)` at runner.py:190; `.run(...)` invoked at runner.py:461,470,478,495,504,513,522,531,540,549,557,852 (the engine is SHARED — also runs `full_stack_tool_scripts.py` scripts). |
| `inspect_user_input_script(ctx)` | `(ctx: ScenarioContext) -> PreparedToolScript` | imported runner.py:104; called runner.py:462 (action `inspect_user_input`). |
| `execute_package_script(ctx, *, package_id)` | `(ctx: ScenarioContext, *, package_id: str) -> PreparedToolScript` | imported runner.py:102; called runner.py:471 (action `execute_package:{id}`). |
| `final_reconciliation_script(ctx)` | `(ctx: ScenarioContext) -> PreparedToolScript` | imported runner.py:103; called runner.py:479 (action `final_reconciliation`). |
| `recursive_step_script(ctx)` | `(ctx: ScenarioContext) -> PreparedToolScript` | imported runner.py:105; called runner.py:558 (action `recursive_step`). |
| `verifier_checkpoint_script(ctx)` | `(ctx: ScenarioContext) -> PreparedToolScript` | imported runner.py:106; called runner.py:850 inside `_run_verifier` (lines 846–856) as the non-full-stack branch; ungated readback BEFORE the verifier terminal. |

Also exported (data/result types, no dispatch): `PreparedToolScript`, `ToolScriptResult`, `ToolScriptStep`. Module-private helpers: `_auto_squash_seed_steps`, `_emit_text`, `_stream_run_id`, `_dict_list`, `_find_package`, `_safe_slug`, `_json`. No smoke/index/mode params anywhere in this module — those belong to the heavy probes (`background_shell`, `complex_project_build`, etc.), not here.

### call_tool sites

Every tool call routes through `PreparedToolScriptEngine.run` → `self._call_tool(step.tool, dict(step.args), metadata, emit, allow_error=step.expect_error)` at tool_scripts.py:108. So `allow_error` is driven solely by each step's `expect_error`. The per-script step tables (key arg = primary path; `allow_error` shown only where True):

inspect_user_input_script (tool_scripts.py:133–219):
| # | tool | key args | allow_error |
|---|---|---|---|
| 1 | shell | `mkdir -p .../packages` | — |
| 2 | shell | assert `/testbed/.git` + write workspace-proof | — |
| 3 | write_file | `requirement-ledger.json` | — |
| 4 | write_file | `conflict-probe.txt` = `stable-anchor\n` | — |
| 5 | edit_file | `conflict-probe.txt` old=`missing-anchor\n` (intentional miss) | **True** (`expect_error=True`) |
| 6..109 | write_file ×104 | `_auto_squash_seed_steps`: `{root}/depth/layer-NNN.txt` for `range(AUTO_SQUASH_MAX_DEPTH+4)`, **AUTO_SQUASH_MAX_DEPTH=100 → 104 writes** | — |
| 110 | read_file | `requirement-ledger.json` lines 1–20 | — |
| 111 | shell | `test -s ledger && printf ...` | — |

**inspect_user_input total = 111 call_tool sites** (7 explicit + 104 squash-seed writes).

execute_package_script (tool_scripts.py:236–299) — **5 sites**: shell(mkdir packages) · write_file(evidence json) · edit_file(`"edited":false`→`true`) · read_file(evidence 1–20) · shell(test -s + printf). No `allow_error`.

recursive_step_script (tool_scripts.py:302–364) — **4 sites** (5 when `is_close`, i.e. `"recursive_reconcile" in context_message`): shell(mkdir recursive) · write_file(evidence json) · read_file(evidence 1–20) · [if close] write_file(close-report.json) · shell(test -s + ls). No `allow_error`.

final_reconciliation_script (tool_scripts.py:367–428) — **5 sites**: shell(mkdir root) · write_file(final-{stage}.json) · read_file(stage 1–20) · write_file(final-reconciliation.json) · shell(test -s + find). No `allow_error`.

verifier_checkpoint_script (tool_scripts.py:431–468) — **2 sites**: read_file(checkpoint path 1–20) · shell(test -s + printf + find). No `allow_error`.

**Module call_tool total (single-path counts): 111 + 5 + 4(/5) + 5 + 2 = 127.** Exactly **one** `allow_error=True` site in the entire module: `fabricate-conflict-detection` (step 5 of inspect_user_input).

**BACKGROUND calls: NONE.** No step passes `background_task_id` / `sandbox_invocation_id`; no step's `args` contain `background=True`. Every call is sequential foreground. (Confirmed: `grep background` in tool_scripts.py returns nothing.) `PreparedToolScriptEngine.run` calls `self._call_tool` WITHOUT the `background_task_id`/`sandbox_invocation_id` kwargs runner.py:1591–1592 supports.

### out-of-band work (NOT through call_tool)

The ONLY non-call_tool runtime effect in this module is reporting text via `_emit_text` → `emit(AssistantTextDeltaEvent(...))`, called at tool_scripts.py:96 (script header), :103 (per-step "Running script step…"), :120 (script footer). This is identical in spirit to the engine-driven text the new path already emits; it is NOT a loop tool dispatch.

Explicitly ABSENT (so re-home to ProbeContext is a no-op for this module):
- **`sandbox_api.*`** — none. (`grep sandbox_api` → 0 hits.)
- **`publish(...)` / `publish_mock_record(...)`** — none in this module. (The `_publish_full_stack_script(script_result.script_name, …)` calls live in **runner.py:493,502,…547**, i.e. in the dispatcher AFTER `engine.run` returns — an adapter concern, not this module.)
- **`record_tool_check(...)`** — none. (Equivalence checks like `assert_read_contains` exist only in the probes module; tool_scripts has no result inspection at all — only `expect_error` is enforced, inside `run`.)
- **`caller(...)`** — none.

### loop-interaction verdict (DECISIVE)

**YES.** This module interacts with the engine ONLY via the injected `call_tool` (the `CallTool` protocol, tool_scripts.py:32–41), funneled through the single site `PreparedToolScriptEngine.run` line 108. Everything else is either pure data construction (the 5 `*_script` builders, helpers) or out-of-band reporting (`_emit_text`). There is no `sandbox_api`, no `publish`/`record`/`caller`, no background dispatch, no result-inspection side-channel anywhere. The scripts do not even read tool results — `run` only enforces `expect_error` (line 115–118) and collects them into `ToolScriptResult.results`, which **no consumer reads** (consumers use only `.summary`/`.artifact`/`.script_name`, runner.py:466–563).

### recommended adaptation

**REWRITE-AS-GENERATOR (engine only; the 5 `*_script` data builders stay byte-identical).** Rewrite `PreparedToolScriptEngine.run`'s body as the adapter's loop — `for step in script.steps: result = yield Turn(calls=(ToolCall(step.tool.name, dict(step.args)),))` with the `expect_error` check on the normalized result — exactly mirroring the proven `_executor_script` probe loop (scenario_adapter.py:200–206). A flat sequential for-loop has nothing for a QUEUE-BRIDGE to preserve, so the bridge (asyncio.Queue + Future) is unwarranted overhead here; the bridge's reason for existing is the complex/concurrent imperative bodies (e.g. background_shell), which this module does not have.

DECISIVE rewrite constraint (must be in the impl): **an async generator cannot `return ToolScriptResult(...)`** (PEP 525 — bare `return` only; line 125's `return <value>` becomes a SyntaxError). This is fine because `summary`/`artifact`/`script_name` already live on the `PreparedToolScript` data object the adapter constructs (`inspect_user_input_script(ctx)` etc. are called in the dispatcher, not the engine), and `.results` is vestigial (no consumer reads it). The adapter reads `script.name`/`.summary`/`.artifact` directly off the data object — the same way `_executor_script` reads `PROBE_SUMMARY` + `probe_ctx.probe_path()`. Bonus: because the SAME engine runs the out-of-scope `full_stack_tool_scripts.py` scripts (dispatch 494–555), this one rewrite ports every script-backed action — simple AND heavy — at once.

Hazards for a bridge: **NONE in this module** — zero `background_task_id`, zero `sandbox_api`, zero task cancellation, zero partial-write/race assertions, every step a sequential foreground call. (Background/cancellation hazards belong to `background_shell_probe`, not here.)

### executor action strings it backs

Cross-ref runner.py `_run_executor` (372–823) and `_run_verifier` (831–870):
- `inspect_user_input` → `inspect_user_input_script` (runner.py:460–462). Emitted by `full_case_user_input.py:80-81` (trigger `ACTION inspect_user_input`).
- `execute_package:{package_id}` → `execute_package_script(..., package_id=…)` (runner.py:468–471). Emitted by `full_case_user_input.py:91,96` (default executor action; e.g. `execute_package:generic`).
- `final_reconciliation` → `final_reconciliation_script` (runner.py:477–479). Emitted by `full_case_user_input.py:93`.
- `recursive_step` → `recursive_step_script` (runner.py:556–558). Emitted by `full_case_user_input.py:95`, `pipeline/deferred_parent_planner_terminal_routing.py:134`, `pipeline/nested_goal.py:109`.
- `verify:{checkpoint}` (the script *name*, not an executor_action) → `verifier_checkpoint_script` runs unconditionally inside `_run_verifier` for all NON-`full_stack`/`capacity.full_system_capacity_matrix` scenarios (runner.py:846–852); checkpoint ∈ {`inventory`, `recursive_return`, `final_release`, default} selects the readback path.

Engine-only (NOT this module's builders, but ride the SAME `PreparedToolScriptEngine` so the rewrite covers them — they come from `full_stack_tool_scripts.py` and additionally trigger `_publish_full_stack_script` in the dispatcher): `inspect_full_user_input`, `occ_conflict_matrix`, `overlay_edge_matrix`, `layerstack_squash_lease`, `lsp_refresh_semantics`, `recursive_oversized_matrix`, `full_stack_final_reconciliation`, `capacity_metrics_full_system` (runner.py:485–555), plus the full-stack verifier branch `full_stack_verifier_checkpoint_script` (runner.py:846–850).


### full_stack_tool_scripts

Advisor is rate-limited. My analysis is complete and well-grounded in the source. Here is the port spec.

---

### full_stack_tool_scripts — entry functions

All public entries are pure builders returning a `PreparedToolScript` (a frozen tuple of `ToolScriptStep(label, tool, args, expect_error)`). None of them execute anything; the old runner executes them via `PreparedToolScriptEngine.run(script, metadata=, emit=)` (tool_scripts.py:89-130), which is the sole consumer that issues `await self._call_tool(...)`.

| Entry function (signature) | runner.py dispatch |
|---|---|
| `inspect_full_user_input_script(ctx: ScenarioContext) -> PreparedToolScript` | imported runner.py:89; called runner.py:485-493 under `action == "inspect_full_user_input"`, then `_publish_full_stack_script(name, metadata)` |
| `occ_conflict_matrix_script(ctx) -> PreparedToolScript` | imported :92; called :494-502 under `action == "occ_conflict_matrix"`, then publish |
| `overlay_edge_matrix_script(ctx) -> PreparedToolScript` | imported :93; called :503-511 under `action == "overlay_edge_matrix"`, then publish |
| `layerstack_squash_lease_script(ctx) -> PreparedToolScript` | imported :90; called :512-520 under `action == "layerstack_squash_lease"`, then publish |
| `lsp_refresh_semantics_script(ctx) -> PreparedToolScript` | imported :91; called :521-529 under `action == "lsp_refresh_semantics"`, then publish |
| `recursive_oversized_matrix_script(ctx) -> PreparedToolScript` | imported :94; called :530-538 under `action == "recursive_oversized_matrix"`, then publish |
| `final_reconciliation_script(ctx) -> PreparedToolScript` (imported AS `full_stack_final_reconciliation_script`) | imported :88; called :539-547 under `action == "full_stack_final_reconciliation"`, then publish |
| `verifier_checkpoint_script(ctx) -> PreparedToolScript` (imported AS `full_stack_verifier_checkpoint_script`) | imported :95; called from `_run_verifier` :846-856 — selected when `scenario.name in {"full_stack_adversarial","capacity.full_system_capacity_matrix"}` else falls back to `verifier_checkpoint_script`. Checkpoint param comes from `context_message_field(ctx.context_message,"checkpoint")` |
| `full_stack_metrics_path(ctx) -> str` | pure helper, exported, used internally + read back by `final_reconciliation_script`; no tool dispatch |

No smoke/index/mode params anywhere. Per-call routing keys are read off `ctx.context_message` inside the builders (`slice`, `close`, `checkpoint`, `stage`), never passed as function args. `recursive_oversized_matrix_script` branches on `is_close` (context field `close==true` or `slice==close`) to add the close-report + metric steps; `verifier_checkpoint_script` selects a read path from a `checkpoint` lookup table.

### call_tool sites

There are **NO literal `await call_tool(...)` sites** in this module. Every tool interaction is a declarative `ToolScriptStep`; the engine turns each into one `await self._call_tool(step.tool, step.args, metadata, emit, allow_error=step.expect_error)` (tool_scripts.py:108). Steps using `_metric_steps` are matrix-cell-count-dependent (each is a `write_file` of a `.jsonl` fragment). Below, "fixed" = always present; counts marked `+N×cells` scale with `_subsystem_cells`.

| Script | tool | key args | allow_error? | background? |
|---|---|---|---|---|
| inspect_full_user_input | shell | `mkdir -p {_ROOT} .omc/results … test -d /testbed/.git … workspace-proof` | no | no |
| | write_file ×3 | ledger, package-plan, conflict-probe | no | no |
| | read_file ×2 | ledger, package-plan | no | no |
| | edit_file | conflict-probe `old_text="missing-anchor\n"` | **yes (:143)** | no |
| occ_conflict_matrix | shell ×6 | mkdir; shell-stale-seed; nonzero `exit 7`; tracked+ignored; delete `rm -f`; (see below) | nonzero-shell **yes (:265)** | no |
| | write_file ×~9 | same-path×2, disjoint-a/b, disjoint-edits, overlap, public-write stale, delete-vs-write seed+replace, occ-artifact | no | no |
| | edit_file ×3 | disjoint-head, disjoint-tail (ok); overlap `missing-overlap-anchor` | overlap **yes (:236)** | no |
| | read_file ×~6 | same-path, disjoint-a, public-write, nonzero side-effect, tracked, occ-artifact | no | no |
| | + `_metric_steps("occ", expected={same_file_overlap, nonzero_shell})` → write_file ×cells | no | no |
| overlay_edge_matrix | shell ×4 | overlay-mutation (`python3 - <<PY` heredoc); symlink-inside `ln -s`; symlink-escape `ln -s /tmp/...`; outside-workspace; noop `true` | symlink-inside **yes (:394)**, symlink-escape **yes (:417)** | no |
| | read_file ×~8 | new, modified, deleted (err), deep, special-chars, whiteout, symlink statuses, overlay-artifact | overlay-deleted **yes (:365)** | no |
| | write_file ×3 | symlink_inside.status, symlink_escape.status, overlay-artifact | no | no |
| | + `_metric_steps("overlay", expected={delete_files,symlink_inside,symlink_escape})` ×cells | no | no |
| layerstack_squash_lease | shell ×2 | read-workspace-binding (mkdir+test); shell-cat manifest | no | no |
| | write_file ×(`AUTO_SQUASH_MAX_DEPTH+4` + 4) | manifest-seed, old-snapshot, layer-depth-write-NNN loop, layerstack-artifact | no | no |
| | edit_file ×1 | manifest version=1→2 | no | no |
| | read_file ×2 | manifest-current, layerstack-artifact | no | no |
| | + `_metric_steps("layerstack", manifest_before/after)` ×cells | no | no |
| lsp_refresh_semantics | shell ×2 | mkdir lsp-package; `mv` opened-file rename | no | no |
| | write_file ×~7 | init, model, service, consumer, pyrightconfig, lsp-artifact, (fix via edit) | no | no |
| | edit_file ×3 | fix-diagnostic, edit-signature, edit-return | no | no |
| | read_file ×3 | service-after-plugin-edit, lsp-artifact | no | no |
| | **lsp_diagnostics_tool ×5** | warmup(`wait_for_diagnostics=False`), present, fixed, config, renamed | no | no |
| | **lsp_hover_tool ×2** | initial, updated | no | no |
| | **lsp_find_definitions_tool ×1** | service line3 | no | no |
| | **lsp_find_references_tool ×2** | model line3 | no | no |
| | **lsp_query_symbols_tool ×1** | `query="display_name"` | no | no |
| | **lsp_apply_workspace_edit_tool ×1** | `edit.changes` for `file:///testbed/{service}` | no | no |
| | + `_metric_steps("lsp")` ×cells | no | no |
| recursive_oversized_matrix | shell ×1 | mkdir recursive-root | no | no |
| | write_file ×1 (+1 close) | evidence; (+close-report if `is_close`) | no | no |
| | read_file ×1 (+1 close) | evidence; (+close-report) | no | no |
| | + (if close) `_metric_steps("recursive")` ×cells | no | no |
| final_reconciliation | read_file ×6 | occ, overlay, layerstack, lsp, recursive-close, final, metrics-artifact | no | no |
| | write_file ×1 | final-reconciliation | no | no |
| | shell ×1 | write-canonical-metrics-artifact (`python3 - <<PY` heredoc that rglobs fragments) | no | no |
| verifier_checkpoint | read_file ×1 | checkpoint read path | no | no |
| | shell ×1 | `test -s … && find … head -80` | no | no |

**Count of declarative call_tool steps: ZERO are background; SIX are `allow_error=True` (expect_error).** Total fixed steps ≈ 12 (inspect) + ~24 (occ) + ~18 (overlay) + ~(AUTO_SQUASH_MAX_DEPTH+13) (layerstack) + ~28 (lsp) + ~3-6 (recursive) + 9 (final) + 2 (verifier), plus a variable `_metric_steps` tail per subsystem proportional to matrix-cell count.

### out-of-band work (NOT through call_tool)

**None inside this module.** Confirmed by grep: zero `sandbox_api.*`, zero `publish(...)`, zero `publish_mock_record(...)`, zero `record_tool_check(...)`, zero `caller(...)`, zero `.asend`. The only `publish` token is the metric-dict key string `"occ.commit.publish_layer_s"` (line 1079), not a call.

The single out-of-band side effect associated with these scripts — `_publish_full_stack_script(script_result.script_name, metadata)` emitting `EventType.FULL_STACK_SCRIPT_COMPLETED` — lives in **runner.py:1984-1993**, fired by the dispatcher AFTER the engine finishes, not by the module. In the event-source port this is a post-script publish the executor adapter must emit once the engine drains the script (re-home to `_executor_script` / a `ProbeContext`-style publish, keyed off the script name). All metric/artifact persistence is done *through tool calls* (write_file of `.jsonl` fragments + a final shell `python3` heredoc that rglobs and concatenates fragments), so it already flows through the loop.

### loop-interaction verdict (DECISIVE)

**YES.** This module interacts with the engine ONLY via tool dispatch. There is no `call_tool` callback in the module at all — it is purely declarative `PreparedToolScript` data; the injected `CallTool` is held by `PreparedToolScriptEngine`, not by these builders. There are no `sandbox_api` reads, no audit publishes, no `caller()`, no background tasks, no terminal/submit tools, and no result-branching (no step's args depend on a prior step's `ToolResult` — all paths/contents are computed up front from `ctx`). The only loop coupling is "run these N steps in order, each with its `allow_error` flag." The one non-tool side effect (`FULL_STACK_SCRIPT_COMPLETED`) is already external to the module (in runner.py).

### recommended adaptation

**QUEUE-BRIDGE (zero body change).** Keep `full_stack_tool_scripts.py` byte-identical and keep `PreparedToolScriptEngine` unchanged; inject a *bridging* `call_tool` (the asyncio.Queue + per-call Future shim) so each `await self._call_tool(step.tool, step.args, metadata, emit, allow_error=step.expect_error)` routes a `ToolCall` through the loop and resolves with the normalized `ToolResult`. The executor adapter (`_executor_script` in scenario_adapter.py) maps the script-engine path: for full_stack actions, build the `PreparedToolScript`, then drive the engine on the bridge while the adapter's top-level `yield Turn(calls=(ToolCall,))` services queued calls. This is strictly cleaner than rewriting all 7 builders as generators because (a) they emit hundreds of steps including a `AUTO_SQUASH_MAX_DEPTH+4` write loop and per-cell metric tails — rewriting risks step-order/`expect_error` drift; (b) the engine already centralizes the `if step.expect_error and not result.is_error: raise` assertion (tool_scripts.py:115-118) which the bridge preserves for free.

**Hazards for the bridge (none fatal, all manageable):**
- **`expect_error` semantics must be carried per-call.** The bridge must pass `allow_error=step.expect_error` through to the loop's dispatch so the 6 expect-error steps (lines 143, 236, 265, 365, 394, 417) are NOT treated as hard failures. If the bridge drops `allow_error`, the loop's error handling will diverge from the old engine. This is the single most important parity wire.
- **LSP plugin tools (`lsp_diagnostics/hover/find_definitions/find_references/query_symbols/apply_workspace_edit`) are dispatched as in-loop tools.** They are blocked while isolated-workspace mode is active for an agent (per CLAUDE.md). Not a bridge concurrency hazard, but the port must keep these on the shared-workspace path, never under `enter_isolated_workspace`.
- **No background/cancellation/partial-write race in THIS module** — unlike a `background_shell` probe, there are zero background tasks, zero `sandbox_api` asserts on partial writes, and no result-branching. The bridge's single-flight (one outstanding Future per turn) is sufficient; no need for cancellation handling. The two `python3 - <<PY` heredoc shell steps (overlay mutation, metrics rglob) and the nonzero `exit 7` step run synchronously through the loop like any other shell call.
- **Ordering/back-pressure:** steps are strictly sequential (`for step in script.steps`), so the bridge never has >1 in-flight call; the Queue depth is 1. Safe.

If a queue-bridge is judged too invasive for the adapter, the fallback is **HYBRID**: keep the builders as-is but add a thin generator wrapper that iterates `script.steps` and does `result = yield ToolCall(step.tool.name, step.args)` at top level (re-implementing the engine's `expect_error` assertion inline). This avoids the Queue/Future machinery but requires reimplementing `PreparedToolScriptEngine.run`'s emit/assert loop in the adapter. Prefer QUEUE-BRIDGE unless the bridge's two-level coroutine plumbing is unavailable.

### executor action strings it backs

Cross-ref runner.py `_run_executor` (372-823) and `full_stack_adversarial.py:executor_actions` (97-119):

| executor_actions string | module function |
|---|---|
| `inspect_full_user_input` | `inspect_full_user_input_script` |
| `occ_conflict_matrix` | `occ_conflict_matrix_script` |
| `overlay_edge_matrix` | `overlay_edge_matrix_script` |
| `layerstack_squash_lease` | `layerstack_squash_lease_script` |
| `lsp_refresh_semantics` | `lsp_refresh_semantics_script` |
| `recursive_oversized_matrix` (slice=a / slice=b / slice=close close=true via `ctx.context_message`) | `recursive_oversized_matrix_script` |
| `full_stack_final_reconciliation` | `final_reconciliation_script` (imported as `full_stack_final_reconciliation_script`) |

Plus the verifier path (NOT an executor_action): `full_stack_verifier_checkpoint_script` is invoked from `_run_verifier` (runner.py:846-856) for `VERIFY checkpoint=...` steps when `scenario.name == "full_stack_adversarial"` (also reused by `capacity.full_system_capacity_matrix`); checkpoints seen: `inventory`, `subsystem_wave_guard`, `recursive_wave`, `recursive_final`, `recursive_return`, `final_release`.

Relevant source paths: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/full_stack_tool_scripts.py`, `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/tool_scripts.py` (PreparedToolScriptEngine + CallTool protocol), `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/scenario_adapter.py` (_executor_script seam), `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` (dispatch :485-547, verifier :846-856, _publish_full_stack_script :1984), `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/full_stack_adversarial.py` (executor_actions :97-119).


### capacity_actions(dir)

Advisor is rate-limited. My analysis is complete and grounded in the source. Here is the port spec.

---

### capacity_actions — entry functions

| Function | Signature | How runner.py dispatches it |
|---|---|---|
| `full_system_capacity_metrics_script` | `(ctx: ScenarioContext) -> PreparedToolScript` | Imported at `runner.py:97-99`. Called at `runner.py:548-555` inside `_run_executor`, under `elif action == "capacity_metrics_full_system":`. The returned `PreparedToolScript` is passed to `self._script_engine.run(script, metadata=metadata, emit=emit)` (the shared `PreparedToolScriptEngine` constructed once at `runner.py:190` with `self._call_tool`). `summary`/`artifacts` are taken from the `ToolScriptResult`. |
| `CapacityActionResult` | `@dataclass(frozen=True, slots=True)` with fields `name, summary, artifact_path, expected_errors, counters` | Pure data contract. NOT referenced by runner.py at all — only re-exported in `__init__.py` and exercised by `tests/mock/contracts/test_scenario_suite_imports.py:99`. Not constructed anywhere in production code. |

No smoke/index/mode params: this module is a single builder function plus an unused dataclass. The only entrypoint runner.py actually uses is `full_system_capacity_metrics_script`.

`metrics.py` also defines private module-level helpers (`_tool_counts`, `_dict_list`, `_json`) — pure functions, no I/O.

### call_tool sites

The builder itself contains **zero** `await call_tool(...)`. All tool interaction happens because the builder emits 4 `ToolScriptStep`s, each of which `PreparedToolScriptEngine.run` (`tool_scripts.py:108-114`) turns into exactly one `await self._call_tool(step.tool, dict(step.args), metadata, emit, allow_error=step.expect_error)`.

| # | Step label | Tool name | Key args | allow_error? | Background? |
|---|---|---|---|---|---|
| 1 | `write-capacity-planned-graph` | `write_file` (`write_file_tool`) | `file_path=".metrics/planned_graph.json"`, `content=<json planned_graph>` | No (`expect_error` default `False`) | No |
| 2 | `write-capacity-summary` | `write_file` | `file_path=".ephemeralos/sweevo-mock/capacity/full-system-capacity-summary.json"`, `content=<json summary>` | No | No |
| 3 | `read-capacity-planned-graph` | `read_file` (`read_file_tool`) | `file_path=".metrics/planned_graph.json"`, `start_line=1`, `end_line=80` | No | No |
| 4 | `read-capacity-summary` | `read_file` | `file_path=".ephemeralos/sweevo-mock/capacity/full-system-capacity-summary.json"`, `start_line=1`, `end_line=120` | No | No |

Count: **4 call_tool sites, all foreground, all allow_error=False.** No `background_task_id`, no `sandbox_invocation_id`, no `background=True` in any step args. Tool mix: 2× `write_file`, 2× `read_file`.

### out-of-band work (NOT through call_tool)

**None.** No `sandbox_api.*`, no `publish(...)`, no `publish_mock_record(...)`, no `record_tool_check(...)`, no `caller(...)` anywhere in `metrics.py`, `types.py`, or `__init__.py`. The builder only reads `ctx.matrix_plan`, `ctx.package_plan`, `ctx.requirement_ledger`, `ctx.metadata` (in-memory `ScenarioContext` fields) and serializes JSON. All side effects are funnelled through the 4 tool steps.

Note: the `_publish_full_stack_script(...)` call that wraps the sibling full-stack scripts in `_run_executor` is deliberately absent from the `capacity_metrics_full_system` branch (`runner.py:548-555`), so there is no publish even at the dispatch site for this action.

### loop-interaction verdict (DECISIVE)

**YES.** This module interacts with the engine exclusively via the injected `call_tool` (through `PreparedToolScriptEngine.run`). Everything else is pure in-process computation over `ScenarioContext`. There is no `sandbox_api`, no publish/record, no background, no cancellation, no out-of-band sandbox check. It is the cleanest possible loop-interaction profile in the codebase.

### recommended adaptation

**QUEUE-BRIDGE (zero body change)** — but with a strong note that **REWRITE-AS-GENERATOR is nearly as cheap and is the better fit.**

Rationale: The builder is purely declarative — it returns a `PreparedToolScript` of 4 ordered, independent, foreground, non-error file calls. Nothing in `capacity_actions` needs to change at all; the only thing that must be re-homed is the **execution engine** (`PreparedToolScriptEngine.run`), not the script. Under the new seam the natural port is a single `TurnScript` async generator that iterates `script.steps` and does `result = yield Turn(calls=(ToolCall(step.tool.name, dict(step.args)),))` per step (mirroring `_executor_script` at `scenario_adapter.py:176-212`, which already yields one `ToolCall` per probe call). Because there is no `await call_tool` buried inside a helper here (the call lives in the engine's flat `for step in script.steps` loop, `tool_scripts.py:102-119`), converting it to a top-level `yield` is trivial and does **not** require the asyncio.Queue+Future bridge shim at all. The bridge buys you nothing for this module since the body is already a flat loop you control.

Concurrency/cancellation/background hazards for the bridge: **none.** No background tasks, no task cancellation, no partial-write assertions, no races, no `allow_error` steps (so no expected-error reconciliation logic to preserve). The lone behavioral contract to keep is `PreparedToolScriptEngine`'s `step.expect_error and not result.is_error -> RuntimeError` guard (`tool_scripts.py:115-118`) — irrelevant here since all 4 steps have `expect_error=False`, but it must be preserved for the shared engine that other modules' scripts use.

### executor action strings it backs

Exactly one: **`"capacity_metrics_full_system"`** (matched at `runner.py:548`).

Source of the string: `FullSystemCapacityMatrix.executor_actions` in `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/capacity/full_system_capacity_matrix.py:22-26` returns `("capacity_metrics_full_system",)` when the context message contains `"ACTION capacity_metrics_full_system"`. That task spec is injected by `_final_plan` (`full_system_capacity_matrix.py:45-46`) as the `capacity_metrics_summary` task (`ACTION capacity_metrics_full_system profile=project`), wired between `final_reconciliation_check` and `final_release_guard`. The `CapacityActionResult` dataclass backs **no** executor action string (unused outside the import smoke test).

---

Relevant file paths:
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/capacity_actions/metrics.py`
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/capacity_actions/types.py`
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/capacity_actions/__init__.py`
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/tool_scripts.py` (lines 53-130: `ToolScriptStep`/`PreparedToolScript`/`PreparedToolScriptEngine.run`; lines 32-41: `CallTool` protocol)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` (lines 97-99 import, 190 engine ctor, 548-555 dispatch)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/capacity/full_system_capacity_matrix.py` (action string source)
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/scenario_adapter.py` (lines 176-212: existing `_executor_script` port pattern to mirror)


### auto_squash_commit_resume_probe + batch/conflict helpers (in runner.py)

I now have complete, ground-truth evidence. The already-landed `probes.py` shows exactly the porting pattern:
- `_call_tool(...)` sites → `result = yield ToolCall(name, args)` at TOP LEVEL of an async-gen probe.
- `record_check`/`_publish`/`assert_read_contains` → `ProbeContext` methods (out-of-band).
- `sandbox_api.edit_file` helpers (`_run_batch_edit`/`_run_expected_conflict`) → moved verbatim onto `ProbeContext` as `run_batch_edit`/`run_expected_conflict` (NOT generators).

The auto_squash probe is a pure `_call_tool` + record/publish probe — no `sandbox_api`, no background, no caller. I have everything needed. Let me produce the PORT SPEC.

### `_run_auto_squash_commit_resume_probe` + batch/conflict helpers — entry functions

| Entry function | Signature | runner.py dispatch |
|---|---|---|
| `_run_auto_squash_commit_resume_probe` | `async (self, metadata: ExecutionMetadata, emit: EmitStreamEvent) -> str` | `_run_executor` (runner.py:564-569): `elif action == "auto_squash_commit_resume_probe":` → `summary_path = await self._run_auto_squash_commit_resume_probe(metadata, emit)`; sets `summary="Auto-squash commit-resume probe passed."`, `artifacts=[summary_path]`. No smoke/index/mode param — single fixed variant. Imports: module-level `import sandbox.api as sandbox_api` (line 18) is present in the file but is NOT used by this probe; the probe only uses module-level tool singletons `write_file_tool`, `edit_file_tool`, `read_file_tool`, `shell_tool`. |
| `_run_batch_edit` | `async (self, metadata: ExecutionMetadata, probe_path: str) -> None` | NOT dispatched from `_run_executor`. Called only from `_run_sandbox_integrity_probe` (runner.py:929) at line 983: `await self._run_batch_edit(metadata, probe_path)`. Already ported as `ProbeContext.run_batch_edit(probe_path)` in probes.py:128. |
| `_run_expected_conflict` | `async (self, metadata: ExecutionMetadata, probe_path: str) -> None` | NOT dispatched from `_run_executor`. Called only from `_run_sandbox_integrity_probe` at line 984: `await self._run_expected_conflict(metadata, probe_path)`. Already ported as `ProbeContext.run_expected_conflict(probe_path)` in probes.py:159. |

Note: the two batch/conflict helpers are part of the `sandbox_integrity` action's chain (already-ported probe family), NOT part of `auto_squash_commit_resume_probe`. The auto_squash probe contains no call to them.

### call_tool sites

All sites in `_run_auto_squash_commit_resume_probe` (1083-1257) go through `self._call_tool(...)`. None are background; none pass `background_task_id`/`sandbox_invocation_id`; no input carries `background=True`.

| # | Line | tool | key args | allow_error? | background? |
|---|---|---|---|---|---|
| 1 | 1101 | `write_file_tool` | `file_path={probe_dir}/write-NN.txt`, `content` (loop, `AUTO_SQUASH_MAX_DEPTH+4` iterations) | no | no |
| 2 | 1114 | `write_file_tool` | `file_path={probe_dir}/edit-target.txt`, `content="alpha=old\nbeta=old\n"` | no | no |
| 3 | 1130 | `edit_file_tool` | `file_path=edit_target`, `old_text/new_text` (loop, 2 iterations: alpha, beta), `description` | no | no |
| 4 | 1157 | `read_file_tool` | `file_path` (loop, 4 iterations: first/middle/last/edited), `start_line=1`, `end_line=20` | no | no |
| 5 | 1166 | `shell_tool` | `command=ls/sort/head + cat`, `timeout=60` | no | no |
| 6 | 1180 | `edit_file_tool` | `file_path=edit_target`, `old_text="missing-anchor-text\n"`, `new_text`, `description` | **yes** (`allow_error=True`) | no |
| 7 | 1246 | `write_file_tool` | `file_path={probe_dir}/summary.json`, `content=json.dumps(...)` | no | no |

Static call sites: 7. Dynamic loop-expanded count: `(AUTO_SQUASH_MAX_DEPTH+4)` writes + 1 seed write + 2 edits + 4 reads + 1 shell + 1 conflict-edit + 1 summary write. Every one is a foreground, synchronous-result tool call.

Batch/conflict helpers (`_run_batch_edit`/`_run_expected_conflict`): **zero `call_tool` sites** — they bypass `_call_tool` entirely (see next section).

### out-of-band work (NOT through call_tool)

In `_run_auto_squash_commit_resume_probe`:
- `self._record_tool_check(name, result)` — 12 static sites (1107, 1120, 1143, 1163, 1178, 1255) plus loop expansions. Wraps result into a `SandboxCheck` and publishes `MOCK_SANDBOX_CHECK_RECORDED`.
- `self._publish_mock_record(EventType.MOCK_SANDBOX_CHECK_RECORDED, _sandbox_check)` — line 1206 (the intentional-conflict check).
- `self._publish(EventType.SANDBOX_CONFLICT_DETECTED, metadata=..., payload={"conflict_reason": ...})` — line 1211.
- No `sandbox_api.*` calls. No `self._caller(...)`. No `_require_sandbox_id`/`_absolute_probe_path`. All paths are workspace-relative and flow through tools.
- Pure-Python aggregation over `result.metadata["timings"]` (1217-1230) and `json.dumps` (1250) — neither touches the loop nor the sandbox; stays inline in the generator body.

In `_run_batch_edit` / `_run_expected_conflict` (the helpers):
- `sandbox_id = self._require_sandbox_id(metadata)` (1010, 1049).
- `await sandbox_api.edit_file(sandbox_id, EditFileRequest(path=self._absolute_probe_path(probe_path), edits=(SearchReplaceEdit(...),), caller=self._caller(metadata), description=...), audit_sink=self._sandbox_audit_sink)` (1011, 1050) — **direct sandbox API, never through the engine loop**.
- `self._publish_mock_record(MOCK_SANDBOX_CHECK_RECORDED, SandboxCheck(...))` (1034, 1073).
- `self._publish(SANDBOX_BATCH_EDIT_APPLIED | SANDBOX_CONFLICT_DETECTED, ...)` (1036, 1075).
- These re-home to `ProbeContext` and are already ported verbatim at probes.py:128-189 (`run_batch_edit`, `run_expected_conflict`), using `ctx._require_sandbox_id()`, `ctx._caller()`, `ctx._sink`, `ctx._publish*`.

### loop-interaction verdict (DECISIVE)

- `_run_auto_squash_commit_resume_probe`: **YES.** It interacts with the engine ONLY via `_call_tool` (the future `yield ToolCall`); everything else is out-of-band `_record_tool_check`/`_publish_mock_record`/`_publish` plus pure-Python timing aggregation and `json.dumps`. No `sandbox_api`, no `caller`, no background, no cancellation.
- `_run_batch_edit` / `_run_expected_conflict`: **NO.** They never touch the engine loop at all — they call the sandbox directly:
  - `result = await sandbox_api.edit_file(sandbox_id, EditFileRequest(..., caller=self._caller(metadata), ...), audit_sink=self._sandbox_audit_sink)` (runner.py:1011 and 1050). This is the exact site that "needs something else" than `call_tool`; it is a direct sandbox RPC, which is why the landed port placed it on `ProbeContext`, not in a `ToolCall`-yielding generator.

### recommended adaptation

`_run_auto_squash_commit_resume_probe`: **REWRITE-AS-GENERATOR.** It matches the already-landed pattern exactly: a flat sequence of foreground `_call_tool` calls whose results are consumed immediately, all top-level (the loops at 1098, 1124, 1151 yield at top level, which is legal in an async-gen). Each `result = await self._call_tool(tool, args, metadata, emit[, allow_error=True])` becomes `result = yield ToolCall(name, args[, allow_error=True])`; `_record_tool_check`/`_publish*` move to `ProbeContext` methods (already named `record_check`/`_publish`); the timing aggregation and `json.dumps` stay inline before the final `summary.json` `yield ToolCall("write_file", ...)`. The function returns `summary_path` — port this as the probe's return/last-step convention (the 3 landed probes return `None`; confirm how `scenario_adapter` surfaces the artifact path, since `_run_executor` consumes the return as `artifacts=[summary_path]`). No QUEUE-BRIDGE is warranted: there is no injected-`call_tool`-deep-in-body shape and no body worth keeping byte-identical here. **Hazards: none.** No background tasks, no `asyncio` cancellation, no task-cancel/`uncancel` path (the `sandbox_api.cancel` at runner.py:1665 lives in `_call_tool` itself, not this probe), no partial-write assertions, no races. The `allow_error=True` conflict edit at 1180 is a normal foreground error result, not a cancellation.

`_run_batch_edit` / `_run_expected_conflict`: **ALREADY DONE — out-of-band re-home (not a bridge, not a generator).** They are pure `sandbox_api.edit_file` helpers and were correctly ported verbatim onto `ProbeContext` (probes.py:128-189). When porting the auto_squash probe, do NOT route these through `ToolCall`; they were intentionally moved out-of-band. No bridge hazard because they never enter the loop.

### executor action strings it backs

Cross-ref `_run_executor` (runner.py:372-823):
- `_run_auto_squash_commit_resume_probe` is backed by exactly one action string: **`"auto_squash_commit_resume_probe"`** (runner.py:564).
- `_run_batch_edit` / `_run_expected_conflict` are backed by **no** `executor_actions` string directly. They are sub-steps of the **`"sandbox_integrity"`** action (runner.py:448 → `_run_sandbox_integrity_probe` at 929, which calls them at 983-984), which is itself already covered by the landed `sandbox_integrity_probe` generator + the `ProbeContext.run_batch_edit`/`run_expected_conflict` helpers in probes.py.

Relevant files: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` (probe at 1083-1257, dispatch at 564-569, helpers at 1005-1081, `_call_tool` at 1583-1714, `_record_tool_check` at 1716, `_publish`/`_publish_mock_record` at 1995-2042); `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/probes.py` (landed pattern: `ProbeContext` at 35, `run_batch_edit`/`run_expected_conflict` at 128/159, `sandbox_integrity_probe` generator at 200); `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/tool_scripts.py` (`CallTool` protocol at 32, `PreparedToolScriptEngine` at 83).


## Executor-action catalogue

API Error: Internal server error


## Assertion → graph_summary rewrites


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_correctness.py

API Error: 500 Internal server error. This is a server-side issue, usually temporary — try again in a moment. If it persists, check https://status.claude.com.


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_focused_scenarios.py

I now have full evidence for every assertion. For `attempt_retry_planner_failure`: attempt 1 fails planner validation (TOOL_CALL_ERROR:1, fail_reason=planner_failed), attempt 2 completes the plan + executor + evaluator success. For planner_validation cases: both attempts fail planner validation (TOOL_CALL_ERROR:2, both fail_reason=planner_failed), goal failed.

I have everything needed. Let me produce the final deliverable.

---

# Per-assertion migration: `test_focused_scenarios.py` → `graph_summary`

## Header facts (the task's metadata questions)

- **`EOS_MOCK_EVENT_SOURCE_RUNNER`: NOT set in this file.** Grep confirms it is set only in two sibling contract tests: `test_scenario_loop_runner_planner_submit.py:92` and `test_correctness_via_event_source.py:62`. `test_focused_scenarios.py` is parametrized over all 19 scenarios via `_FOCUSED_CASES` and runs through the default runner (`builder.py:_make_runner`, flag read at `builder.py:32 _EVENT_SOURCE_RUNNER_ENV`). The single-scenario under-flag verification target is the **separate** file `/Users/yifanxu/.../tests/mock/contracts/test_correctness_via_event_source.py`, which sets the flag and runs exactly **one** scenario: `CorrectnessTesting()` (`scenarios/correctness_testing.py`). Do not conflate the two.
- **The "no `ask_advisor` in transcript" assertion is NOT in this file.** It lives in `tests/mock/task_center/test_correctness.py` (HANDOFF cites lines 168-198). Per the migration, that assertion is **INVERTED**: with the real query loop, scripted `ask_advisor` REJECT/approve turns reach the real advisor gate, so real `ask_advisor` turns DO now appear in the transcript. (Flagged here for completeness; the fix belongs to `test_correctness.py`, not `test_focused_scenarios.py`.)

## Two whole-file assertions that DROP (no graph replacement)

These are file-level machinery in `_focused_scenario_contracts.py`, invoked once per case from `assert_focused_scenario_report`:

| Current (contracts file) | Disposition |
|---|---|
| `_assert_ordered_subsequence(scenario.expected_event_sequence, report.seen_event_types)` (lines 39-42, 47-59) | **DELETE.** IMPL_PLAN §4 line 136: "the `_assert_ordered_subsequence` order-check is dropped — the real TaskCenter enforces role ordering." planner→generator→evaluator order is structurally guaranteed by the attempt DAG (`stage_advancer`/`generator_dag`); no graph equivalent. `Scenario.expected_event_sequence` + `RunReport.seen_event_types` are removed in Phase 3. |
| `_assert_event_counts(report, case)` (lines 43, 62-73) — iterates `case.min_event_counts` / `case.absent_events` over `Counter(report.events)` | **REPLACE** with the per-case graph checks below; remove `min_event_counts` / `absent_events` from `FocusedScenarioCase` and the `Counter(report.events)` machinery. |

The graph-shape block (`_assert_graph_shape`, lines 76-89: 1 goal, `goal["status"]`, `iteration_count`, `attempt_count`) **stays unchanged** and already absorbs every `PLANNER_INVOKED:N` (since N == attempt_count, see below).

## Key code-anchored fact behind the mapping

Every attempt creates exactly one `planner` task row **at launch, before the planner runs** (`orchestrator.py:83-103`, `set_planner_task_id`), including planner-failed and startup-failed attempts. Therefore `count_events(PLANNER_INVOKED) == attempt_count` in every case here → folds into the existing `attempt_count` check, no new assertion. Row vocabulary (`task_center_store.py:_serialize_task`, lines 44-60): `role` ∈ {`planner`,`generator`,`evaluator`} (`TaskCenterTaskRole`); `agent_name` ∈ {`planner`,`executor`,`verifier`,`evaluator`}; `status` ∈ {`pending`,`running`,`waiting_goal`,`done`,`failed`,`blocked`} (`TaskCenterTaskStatus`). EXECUTOR vs VERIFIER is `agent_name` within `role=="generator"`. Attempt `fail_reason` ∈ {`planner_failed`,`generator_failed`,`evaluator_failed`,`startup_failed`}. Plan-validation rejects (all `planner_validation.*`) close the attempt with `fail_reason="planner_failed"` and spawn no generator/evaluator rows (`scenarios/planner_validation/__init__.py` docstring; `orchestrator.apply_planner_failure` → `_close_attempt(FAILED, PLANNER_FAILED)`).

## Per-case before/after table

Lines refer to `test_focused_scenarios.py`. "after" = replacement applied across the case's attempts via the new helpers (definitions in the last section). Existing `attempt_count` / `goal_status` / `expected_status` already on each case stay as-is; below shows only what replaces the removed `min_event_counts`/`absent_events`.

| Case (line) | Removed event assertion | Graph-summary replacement |
|---|---|---|
| `pipeline.initial_goal` (24-32) | `EXECUTOR_INVOKED:1, EXECUTOR_SUCCESS:1, EVALUATOR_SUCCESS:1` | `count_role_tasks(att, agent_name="executor") >= 1` and `count_role_tasks(att, agent_name="executor", status="done") >= 1` and `attempt_outcome(att) == ("passed", None)` for the single attempt. |
| `pipeline.iterative_deferral` (33-43) | `PLANNER_DEFERS_GOAL_PLAN:1, PLANNER_COMPLETES_GOAL_PLAN:1, EXECUTOR_SUCCESS:2, EVALUATOR_SUCCESS:2` | iteration 1's attempt has `att["deferred_goal_for_next_iteration"] is not None` (DEFERS); iteration 2's attempt has it `is None` (COMPLETES). Sum over both attempts: `executor done >= 2`; both attempts `attempt_outcome == ("passed", None)` (EVALUATOR_SUCCESS:2). |
| `pipeline.attempt_retry_evaluator_failure` (44-53) | `PLANNER_COMPLETES_GOAL_PLAN:2, EXECUTOR_SUCCESS:2, EVALUATOR_FAILURE:1, EVALUATOR_SUCCESS:1` | both attempts `deferred...is None` (2× COMPLETES); `executor done` summed over attempts `>= 2`; attempt 1 `attempt_outcome == ("failed", "evaluator_failed")` (EVALUATOR_FAILURE); attempt 2 `("passed", None)` (EVALUATOR_SUCCESS). |
| `pipeline.attempt_retry_planner_failure` (54-64) | `PLANNER_INVOKED:2, PLANNER_COMPLETES_GOAL_PLAN:1, TOOL_CALL_ERROR:1, EXECUTOR_SUCCESS:1, EVALUATOR_SUCCESS:1` | `PLANNER_INVOKED:2` → `attempt_count==2` (already). attempt 1 `attempt_outcome == ("failed", "planner_failed")` (TOOL_CALL_ERROR:1 + planner reject) **and** `count_role_tasks(att1, role="generator")==0`. attempt 2: `deferred...is None` (COMPLETES:1), `executor done >= 1`, `attempt_outcome==("passed", None)` (EVALUATOR_SUCCESS:1). |
| `pipeline.attempt_retry_generator_failure` (65-74) | `PLANNER_COMPLETES_GOAL_PLAN:2, EXECUTOR_FAILURE:1, EXECUTOR_SUCCESS:1, EVALUATOR_SUCCESS:1` | both attempts COMPLETES (`deferred...is None`); summed over attempts: `count_role_tasks(agent_name="executor", status="failed") >= 1` (EXECUTOR_FAILURE) and `... status="done" >= 1`; final attempt `attempt_outcome==("passed", None)`. |
| `pipeline.dependency_dag_serial` (75-82) | `EXECUTOR_INVOKED:3, EXECUTOR_SUCCESS:3` | single attempt: `count_role_tasks(att, agent_name="executor") >= 3` and `... status="done" >= 3`. |
| `pipeline.dependency_dag_mixed` (83-90) | `EXECUTOR_INVOKED:7, EXECUTOR_SUCCESS:7` | single attempt: `executor >= 7`, `executor done >= 7`. |
| `pipeline.dependency_dag_parallel` (91-98) | `EXECUTOR_INVOKED:4, EXECUTOR_SUCCESS:4` | single attempt: `executor >= 4`, `executor done >= 4`. |
| `pipeline.dependency_dag_diamond` (99-106) | `EXECUTOR_INVOKED:4, EXECUTOR_SUCCESS:4` | single attempt: `executor >= 4`, `executor done >= 4`. |
| `pipeline.generator_failure_quiescence` (107-117) | `PLANNER_COMPLETES_GOAL_PLAN:2, EXECUTOR_INVOKED:7, EXECUTOR_SUCCESS:6, EXECUTOR_FAILURE:1, EVALUATOR_SUCCESS:1` | both attempts COMPLETES (`deferred...is None`); summed: `executor (any status) >= 7`, `executor done >= 6`, `executor failed >= 1`; final attempt `attempt_outcome==("passed", None)`. |
| `pipeline.dependency_blocked_descendants` (118-129) | `PLANNER_COMPLETES_GOAL_PLAN:2, EXECUTOR_INVOKED:2, EXECUTOR_FAILURE:2` + absent `EVALUATOR_INVOKED` | both attempts COMPLETES; summed `executor (any) >= 2` and `executor failed >= 2`; **absent**: `count_role_tasks(att, role="evaluator")==0` for every attempt (no evaluator task ever spawned). goal/expected already `failed`. (INVOKED `>=2` tolerant of extra blocked rows.) |
| `pipeline.attempt_budget_exhausted` (130-140) | `PLANNER_COMPLETES_GOAL_PLAN:2, EXECUTOR_FAILURE:2` + absent `EVALUATOR_INVOKED` | both attempts COMPLETES; summed `executor failed >= 2`; **absent**: every attempt `count_role_tasks(role="evaluator")==0`. |
| `planner_validation.duplicate_local_id` (141-155) | `PLANNER_INVOKED:2, TOOL_CALL_ERROR:2` + absent `PLANNER_COMPLETES_GOAL_PLAN, EXECUTOR_INVOKED, EVALUATOR_INVOKED` | `PLANNER_INVOKED:2`→`attempt_count==2`. `TOOL_CALL_ERROR:2`→ **every** attempt `attempt_outcome == ("failed", "planner_failed")`. absent COMPLETES → every attempt `att["deferred_goal_for_next_iteration"] is None` AND **no plan persisted**, asserted as `count_role_tasks(role="generator")==0`; absent EXECUTOR/EVALUATOR → `count_role_tasks(role="generator")==0` and `count_role_tasks(role="evaluator")==0` per attempt. |
| `planner_validation.unknown_dep` (156-170) | same as above | identical replacement: `attempt_count==2`; every attempt `("failed","planner_failed")`; `generator==0` and `evaluator==0` rows per attempt. |
| `planner_validation.cycle_in_deps` (171-185) | same as above | identical replacement. |
| `planner_validation.defers_without_deferred_goal` (186-201) | `PLANNER_INVOKED:2, TOOL_CALL_ERROR:2` + absent `PLANNER_COMPLETES_GOAL_PLAN, PLANNER_DEFERS_GOAL_PLAN, EXECUTOR_INVOKED, EVALUATOR_INVOKED` | `attempt_count==2`; every attempt `("failed","planner_failed")`. absent COMPLETES **and** DEFERS → every attempt `att["deferred_goal_for_next_iteration"] is None` AND `count_role_tasks(role="generator")==0` (no plan of either kind persisted); `evaluator==0` rows. |
| `planner_validation.unknown_agent_name` (202-216) | same as duplicate_local_id | identical replacement. |
| `planner_validation.empty_tasks` (217-231) | same as duplicate_local_id | identical replacement. |

Notes that shrink the table: every `PLANNER_INVOKED:N` is fully covered by the existing `attempt_count==N` check (1 planner row per attempt, always). All `min_event_counts` use `>=`, so INVOKED→`count_role_tasks(...)` is a safe lower bound even when blocked-descendant rows inflate the count. SUCCESS/FAILURE require the `status=="done"`/`"failed"` filter (blocked rows carry status `blocked` and are correctly excluded). `absent_events` use `==0`, which is sound because planner-failed and (for blocked/budget cases) failed-generator attempts spawn no evaluator rows.

## Shared helpers needed (add to `_focused_scenario_contracts.py`)

Per §4.1 the migration names `count_role_tasks`, `attempt_outcome`, `recursive_goals`. For THIS file, only `count_role_tasks` and `attempt_outcome` are used. `recursive_goals` is **not exercised by any case here** (no case asserts `RECURSIVE_GOAL_*`), and the `verifier` `agent_name` branch is **not exercised here** either (no `VERIFIER_*` case) — both belong to sibling files (e.g. `test_full_case_user_input.py` already has `_recursive_goal_count`; `test_full_case_user_input._has_multi_dependency_verifier` covers `agent_name=="verifier"`). List all three per the instruction, flagging usage:

```python
def count_role_tasks(
    attempt: dict[str, Any],
    *,
    role: str | None = None,        # TaskCenterTaskRole value: planner/generator/evaluator
    agent_name: str | None = None,  # squad label: planner/executor/verifier/evaluator
    status: str | None = None,      # TaskCenterTaskStatus value: done/failed/blocked/...
) -> int:
    """Count task rows in attempt["tasks"] matching the given filters.

    Replaces *_INVOKED counts (no status), *_SUCCESS (status="done"),
    *_FAILURE (status="failed"). EXECUTOR vs VERIFIER discriminated by
    agent_name within role="generator".
    """
    n = 0
    for task in attempt["tasks"]:
        if role is not None and task["role"] != role:
            continue
        if agent_name is not None and task["agent_name"] != agent_name:
            continue
        if status is not None and task["status"] != status:
            continue
        n += 1
    return n


def attempt_outcome(attempt: dict[str, Any]) -> tuple[str, str | None]:
    """(attempt["status"], attempt["fail_reason"]).

    Replaces EVALUATOR_SUCCESS -> ("passed", None);
    EVALUATOR_FAILURE -> ("failed", "evaluator_failed");
    planner-reject / TOOL_CALL_ERROR -> ("failed", "planner_failed").
    """
    return attempt["status"], attempt["fail_reason"]


def recursive_goals(graph_summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Child goals with origin_kind=='task' (RECURSIVE_GOAL_* equivalent).

    NOT used by test_focused_scenarios.py (no recursive case here); provided
    per §4.1 for the sibling workflow files. Mirrors the existing
    _recursive_goal_count pattern. Each child carries requested_by_task_id +
    status/final_outcome (RECURSIVE_GOAL_REQUESTED vs COMPLETED).
    """
    return [g for g in graph_summary["goals"] if g.get("origin_kind") == "task"]
```

To iterate attempts in each case (the table's "per attempt"/"summed over attempts" phrasing), flatten as the existing `_assert_graph_shape` already does: `attempts = [a for it in goal["iterations"] for a in it["attempts"]]`.

All file paths referenced are absolute above. No files were edited (read-only task).


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_full_case_user_input.py

This is the canonical existing graph_summary-based pattern: `origin_kind`, `requested_by_task_id`, `deferred_goal_for_next_iteration`, iteration count. This confirms my mapping approach. I now have all the evidence needed. The verifier-failure semantic detail: in this scenario `VERIFIER_FAILURE` triggers a replan (deferred continuation iteration), so the store signal is the multi-iteration shape + a verifier task whose status reflects failure. The dynamic verifier DAG outcome lands in the verifier task's `status`/`summaries`.

I have enough to write the complete answer.

---

# Migration analysis: `test_full_case_user_input.py` → graph_summary

## CRITICAL FRAMING CORRECTIONS (read first)

Two premises in the task statement do not match the actual file `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_full_case_user_input.py`:

1. **It does NOT use `FocusedScenarioCase` / `assert_focused_scenario_report` / `_assert_event_counts` / `_assert_ordered_subsequence`.** That machinery lives in `_focused_scenario_contracts.py` and is consumed by `test_focused_scenarios.py`, not this file. This file is a bespoke live regression with hand-rolled assertions. There are **no** `min_event_counts`, `expected_event_sequence`, `absent_events`, or `seen_event_types` references in this file. (The `scenario.expected_event_sequence` tuple lives on the `FullCaseUserInput` scenario class itself, lines 42-52, and is consumed only by the focused-scenario contract path — not by this test.)

2. **The "no ask_advisor in transcript" assertion is NOT in this file.** It is in `test_correctness.py` (lines 174-198: `assert not leaked_tool_uses` for `ask_advisor` tool_use, and `assert not leaked_advisor_results` for `helper_role == "advisor"`). Under the event-source runner that assertion is **INVERTED / must be deleted** — see the dedicated section below.

3. **This test does NOT set `EOS_MOCK_EVENT_SOURCE_RUNNER`.** No `monkeypatch.setenv` for it anywhere in the file. The only files that set it are `tests/mock/contracts/test_scenario_loop_runner_planner_submit.py:92` and `tests/mock/contracts/test_correctness_via_event_source.py:62`. The under-flag verification target for Phase 1 is the **`correctness_testing`** scenario (via `test_correctness_via_event_source.py`), NOT `full_case_user_input`. `full_case_user_input` migrates in Phase 2; to run it under the flag the test must add `monkeypatch.setenv("EOS_MOCK_EVENT_SOURCE_RUNNER", "1")` plus the `_active_mock_model` fixture pattern from `test_correctness_via_event_source.py:31-50`.

So the per-assertion migration below covers the assertions in this file that **actually depend on lifecycle EventTypes** (direct `report.events`/`EventType` reads + the two lifecycle-event `extra_hooks`). All event-free assertions (audit-tree, message.jsonl, sandbox-monitor, daytona workspace, launches/requirement_ledger counts) are preserved unchanged.

## Confirmed store-shape facts (primary source)

- `graph_summary` built by `core/runner.py:_graph_summary` (90-142): `goals[]` → `{id, status, origin_kind, requested_by_task_id, final_outcome, iterations[]}`; iteration → `{id, sequence_no, creation_reason, status, goal, deferred_goal_for_next_iteration, attempts[]}`; attempt → `{id, sequence_no, stage, status, fail_reason, deferred_goal_for_next_iteration, task_ids, tasks}`.
- Task row (`db/stores/task_center_store.py:_serialize_task` 44-60): keys `id, role, agent_name, status, summaries, needs, ...`. Used already by this file: `task.get("agent_name")`, `task["needs"]` (lines 145-147).
- Enum values: `GoalStatus.SUCCEEDED="succeeded"`, `GoalOriginKind.{ENTRY="entry",TASK="task"}`; `AttemptStatus.{RUNNING="running",PASSED="passed",FAILED="failed"}` (note: **no "succeeded"** at attempt level); `AttemptFailReason.EVALUATOR_FAILED="evaluator_failed"`.

---

## Per-assertion before/after table

| # | Lines | Current assertion (lifecycle-event dependent) | graph_summary-based replacement |
|---|---|---|---|
| A | 59 | `count_events(EventType.VERIFIER_FAILURE, name="verifier_failures")` passed as an `extra_hook` | Drop the hook. Assert verifier-failure-driven replan from store shape: a continuation iteration exists whose prior iteration's final attempt deferred. Add: `assert _has_failure_driven_continuation(report.graph_summary)` (helper below) — i.e. root goal has `>= 2` iterations and `iterations[0]["attempts"][-1]["deferred_goal_for_next_iteration"]` is set. (The verifier-failure signal is the multi-iteration/deferred shape already asserted by `_continuation_iterations_follow_partial_attempts`; you may simply delete this hook and rely on existing line-89 + the new EVALUATOR check.) |
| B | 60 | `assert_recursive_goal_closed_before_parent_guard()` passed as an `extra_hook` (reads `state.seen_events` for `RECURSIVE_GOAL_COMPLETED` before the `VERIFIER_SUCCESS` checkpoint=="recursive_return") | Drop the hook. Replace with store-shape ordering proof: the child (recursive, `origin_kind=="task"`) goal reached `status=="succeeded"` (closed), AND the parent's recursive-return verifier task is `done`. Add `assert _recursive_child_closed_and_parent_returned(report.graph_summary)` (helper below). The "closed before parent guard" temporal invariant is enforced by the real TaskCenter (close-report routing); store state showing both child-succeeded and parent-return-task-done is the durable equivalent. |
| C | 86-88 | `assert any(event.type == EventType.PLANNER_DEFERS_GOAL_PLAN for event in report.events)` | `assert any(att["deferred_goal_for_next_iteration"] for goal in report.graph_summary["goals"] for it in goal["iterations"] for att in it["attempts"]), report.graph_summary` (per §4.1: DEFERS ⇒ attempt `deferred_goal_for_next_iteration` is set). |
| D | 91 | `assert any(event.type == EventType.VERIFIER_FAILURE for event in report.events)` | Folded into A. Equivalent store assertion: `assert _has_failure_driven_continuation(report.graph_summary)` (a verifier failure in iteration 1 is what produces the deferred continuation). If a more direct verifier signal is wanted: `assert any(t["agent_name"]=="verifier" and t["status"]=="failed" for g in report.graph_summary["goals"] for it in g["iterations"] for at in it["attempts"] for t in at["tasks"])` — but the continuation-shape check is the robust replacement consistent with §4.1 row "VERIFIER_* → per-task status". |
| E | 98-103 | `recursive_requested = [e for e in report.events if e.type == EventType.RECURSIVE_GOAL_REQUESTED]; assert recursive_requested` | `assert recursive_goals(report.graph_summary), report.graph_summary` (§4.1: REQUESTED ⇒ existence of a child goal with `origin_kind=="task"` + non-None `requested_by_task_id`). |
| F | 104 | `assert _recursive_goal_count(report.graph_summary) >= 1` | **Already graph_summary-based — keep.** (`_recursive_goal_count` at 151-156 counts `origin_kind=="task"` goals; equivalent to new `recursive_goals` helper.) Optionally consolidate to `assert len(recursive_goals(report.graph_summary)) >= 1`. |
| G | 105-110 | `_assert_event_order(report.events, first=RECURSIVE_GOAL_COMPLETED, second=VERIFIER_SUCCESS, second_checkpoint="recursive_return")` | Replace with the child-closed + parent-return-task-done proof from B: `assert _recursive_child_closed_and_parent_returned(report.graph_summary)`. The recursive-return verifier task carries `summaries` with `checkpoint="recursive_return"`; assert it is `status=="done"` and its child goal `status=="succeeded"`. |
| H | 111-116 | `_assert_event_order(report.events, first=VERIFIER_SUCCESS, second=EVALUATOR_INVOKED, first_checkpoint="final_release")` | The evaluator-after-final-verifier ordering is enforced structurally by the attempt DAG (evaluator runs after the generator/verifier DAG completes). Replace with: the final attempt is `passed` (evaluator success) and contains a verifier task with the `final_release` checkpoint that is `done`: `assert _final_attempt_passed_with_final_release(report.graph_summary)` (helper below). |
| I | 89 | `assert _continuation_iterations_follow_partial_attempts(report.graph_summary)` | **Already graph_summary-based — keep unchanged** (125-138). |
| J | 90 | `assert _has_multi_dependency_verifier(report.graph_summary)` | **Already graph_summary-based — keep unchanged** (141-148; reads `task["agent_name"]=="verifier"` and `len(task["needs"])>1`). |
| K | 92-96 | `assert any(item.agent_name=="planner" and item.checks.get("failed_attempts") for item in report.prompt_inspections)` | **Keep unchanged** — prompt_inspections are preserved under the event-source runner (IMPL_PLAN §4: `_inspect_prompt` preserved). Not lifecycle-event dependent. |
| — | 195-289, 292-356 | `_assert_audit_tree_roles`, `_assert_message_jsonl_contains_tool_scripts`, `_assert_parallel_agent_execution` (uses `TOOL_CALL_STARTED/COMPLETED/ERROR` — these are real loop ToolExecution events, NOT lifecycle events), `_assert_sandbox_monitor_events` (SANDBOX_* events from daemon), `_assert_daytona_workspace_tool_state` | **Keep unchanged.** `TOOL_CALL_*` and `SANDBOX_*` are real execution events emitted by the loop/daemon, preserved by the seam (IMPL_PLAN §4: "produced by the REAL tool execution"). Not lifecycle EventTypes. |

### Additional new EVALUATOR_SUCCESS coverage (from `scenario.expected_event_sequence`)
The scenario's `expected_event_sequence` (lines 42-52) ends in `EVALUATOR_SUCCESS`. This file never asserted it directly via `report.events`, but if you want explicit coverage replacing the deleted sequence semantics, add: `assert _final_attempt_passed_with_final_release(...)` (covers it via attempt `status=="passed"`). EVALUATOR_FAILURE retry, if exercised, maps to an attempt with `status=="failed"` and `fail_reason=="evaluator_failed"` per §4.1.

---

## "no ask_advisor in transcript" — INVERTED (in `test_correctness.py`, not this file)

Location: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_correctness.py:174-198`.

```python
# CURRENT (lines 174-185): asserts the synthetic ask_advisor never leaks
leaked_tool_uses = [block for ... if block.get("name") == "ask_advisor"]
assert not leaked_tool_uses, ...
# CURRENT (lines 186-198): asserts no advisor-approval result leaks
leaked_advisor_results = [block for ... if block["metadata"].get("helper_role") == "advisor"]
assert not leaked_advisor_results, ...
```

Under the event-source runner this is **inverted and must be deleted/rewritten**. Rationale (from IMPL_PLAN §3 line 124-125 and §7 line 191): the old `MockSquadRunner._approve_terminal` injected a *synthetic* `ask_advisor`+approval pair into `conversation_messages` purely on per-call `ExecutionMetadata` (never streamed → never on disk), which is exactly what these assertions guarded against leaking. In the new model the advisor gate is reached by a **real scripted `ask_advisor` turn** through the real loop ("scripted `ask_advisor` reaches the real gate the same way", §7 line 191). A real `ask_advisor` turn DOES stream a `tool_use` and DOES land in `message.jsonl`. Therefore:
- Delete `leaked_tool_uses` / `assert not leaked_tool_uses` (174-185).
- Delete `leaked_advisor_results` / `assert not leaked_advisor_results` (186-198), and the stale comment at 168-173.
- If positive coverage is wanted, invert to: `assert any(block.get("name")=="ask_advisor" for ... tool_use ...)` for scenarios that script an advisor turn (e.g. the negative-path gate test `test_advisor_gate_negative_path.py`, which IMPL_PLAN §3 line 124-125 says is now scriptable as real `ask_advisor` turns).

(The `ask_advisor` reference in this file's sibling — there is none in `test_full_case_user_input.py` itself, confirmed by grep.)

---

## Shared helpers needed (add to `_focused_scenario_contracts.py`)

Per IMPL_PLAN §4.1 line 148-149, add `count_role_tasks`, `attempt_outcome`, `recursive_goals`. The specific shapes needed by this file:

```python
def count_role_tasks(graph_summary: dict[str, Any], role: str) -> int:
    """§4.1 INVOKED-count replacement: count tasks of `role` across all attempts."""
    return sum(
        1
        for goal in graph_summary["goals"]
        for it in goal["iterations"]
        for at in it["attempts"]
        for t in at["tasks"]
        if t.get("agent_name") == role or t.get("role") == role
    )

def attempt_outcome(attempt: dict[str, Any]) -> tuple[str, str | None]:
    """§4.1 EVALUATOR_* replacement: (status, fail_reason).
    e.g. ("passed", None) ⇒ evaluator success; ("failed","evaluator_failed") ⇒ eval failure."""
    return attempt["status"], attempt["fail_reason"]

def recursive_goals(graph_summary: dict[str, Any]) -> list[dict[str, Any]]:
    """§4.1 RECURSIVE_GOAL_* replacement: child goals spawned by a task."""
    return [
        goal
        for goal in graph_summary["goals"]
        if goal.get("origin_kind") == "task" and goal.get("requested_by_task_id")
    ]
```

Plus these file-local helpers in `test_full_case_user_input.py` (built on the shared ones; replacing the deleted `_assert_event_order` / `_event_index` machinery at lines 159-192):

```python
def _has_failure_driven_continuation(graph_summary) -> bool:
    # replaces VERIFIER_FAILURE hook (A) + line-91 (D)
    for goal in graph_summary["goals"]:
        its = goal["iterations"]
        if len(its) >= 2 and its[0]["attempts"][-1]["deferred_goal_for_next_iteration"]:
            return True
    return False

def _recursive_child_closed_and_parent_returned(graph_summary) -> bool:
    # replaces assert_recursive_goal_closed_before_parent_guard (B) + order check (G)
    children = recursive_goals(graph_summary)
    if not any(c["status"] == "succeeded" for c in children):
        return False
    return _verifier_task_done_with_checkpoint(graph_summary, "recursive_return")

def _final_attempt_passed_with_final_release(graph_summary) -> bool:
    # replaces VERIFIER_SUCCESS->EVALUATOR_INVOKED order check (H) + EVALUATOR_SUCCESS coverage
    root = next(g for g in graph_summary["goals"] if g["origin_kind"] == "entry")
    final_attempt = root["iterations"][-1]["attempts"][-1]
    status, _ = attempt_outcome(final_attempt)
    return status == "passed" and any(
        t.get("agent_name") == "verifier"
        and t["status"] == "done"
        and any((s or {}).get("checkpoint") == "final_release" for s in t["summaries"])
        for t in final_attempt["tasks"]
    )

def _verifier_task_done_with_checkpoint(graph_summary, checkpoint) -> bool:
    return any(
        t.get("agent_name") == "verifier"
        and t["status"] == "done"
        and any((s or {}).get("checkpoint") == checkpoint for s in t["summaries"])
        for g in graph_summary["goals"]
        for it in g["iterations"]
        for at in it["attempts"]
        for t in at["tasks"]
    )
```

Note: the `summaries`/`checkpoint` extraction (used by G/H) depends on how the ported verifier probe records its checkpoint into the task `summaries` list — verify the checkpoint key name in `scenario_adapter.py:_verifier_script` (line 144-145) / the verifier probe when porting; if the checkpoint is stored under a different key, adjust `(s or {}).get("checkpoint")`. If verifier checkpoint evidence is NOT round-tripped into `summaries`, fall back to the iteration/attempt-shape proofs (B via child `status=="succeeded"` alone; H via final attempt `status=="passed"`), which do not require checkpoint introspection.

## Imports to drop after migration (lines 15-19)
- `from task_center_runner.audit.events import Event, EventType` — only `EventType` was used for lifecycle/`TOOL_CALL_*`/`SANDBOX_*`. **Keep** `EventType` (still needed for `TOOL_CALL_*` in `_assert_parallel_agent_execution` 264-274 and `SANDBOX_*` in `_assert_sandbox_monitor_events` 293-315 + the EventType-value round-trip at 309-314). `Event` is used only as a type hint in `_assert_event_order`/`_event_index`/`_assert_parallel_agent_execution` — keep for the latter.
- `from task_center_runner.hooks.builtins import assert_recursive_goal_closed_before_parent_guard, count_events` — both become unused once hooks A & B are dropped (no remaining `count_events`/`assert_recursive_*` usage). **Remove both imports.**
- `extra_hooks=(...)` at lines 58-61 — drop entirely (becomes `extra_hooks=()` default; the parameter can be omitted from the `run_scenario_on_sweevo_image` call).

## Files referenced
- Test under migration: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_full_case_user_input.py`
- ask_advisor inverted assertion: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_correctness.py:168-198`
- Shared helpers target: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/_focused_scenario_contracts.py`
- graph_summary builder: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/core/runner.py:90-142`
- Task row shape: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/db/stores/task_center_store.py:44-60,213-225`
- §4.1 mapping: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/docs/plans/mock_event_source_IMPL_PLAN.md:139-149`
- Canonical existing graph_summary pattern (origin_kind/requested_by_task_id/deferred): `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_deferred_parent_planner_terminal_routing.py:58-87`
- Under-flag reference test (sets `EOS_MOCK_EVENT_SOURCE_RUNNER`, runs `correctness_testing`): `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/contracts/test_correctness_via_event_source.py:62`


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_initial_messages_capture.py

The advisor is unavailable. I have all the primary-source evidence needed. Let me do one final confirmation of the iteration `status` enum value used for "succeeded" attempts vs the attempt status (which uses `passed`, not `succeeded`), and confirm there are no other lifecycle-event assertions in the target file I might have missed (the grep already showed only lines 89-94 use EventType/Counter in the target test).

The target file `test_initial_messages_capture.py` has exactly ONE block of lifecycle-event assertions: lines 89-94 (the `counts = Counter(...)` block). Everything else (lines 76-87) already asserts on `graph_summary` directly and needs no change. Let me produce the final deliverable.

Here is the complete analysis.

---

# Migration map: `test_initial_messages_capture.py` lifecycle-event assertions → `graph_summary`

## Scope facts (verified)

- **This test does NOT set `EOS_MOCK_EVENT_SOURCE_RUNNER`.** Grep of `task_center_runner/` shows the flag is set only in two files: `tests/mock/contracts/test_scenario_loop_runner_planner_submit.py:92` and `tests/mock/contracts/test_correctness_via_event_source.py:62`. `test_initial_messages_capture.py` runs `pipeline.initial_messages_capture` through the default (old `MockSquadRunner`) path. It is NOT itself an under-flag verification test. The under-flag verification target for the CorrectnessTesting flow is `test_correctness_via_event_source.py` (runs `CorrectnessTesting()` with the flag set); the planner-submit under-flag target is `test_scenario_loop_runner_planner_submit.py`.
- **Only ONE assertion block in the target file depends on lifecycle EventTypes: lines 89-94** (the `counts = Counter(event.type for event in report.events)` block). Lines 76-87 already assert on `report.graph_summary` and `report.task_center_status` — no change needed; they survive the event deletion as-is. The `EventType` import at line 35 becomes unused once 89-94 are migrated (remove it; `Counter` import at line 28 also becomes unused — remove it).
- **"no ask_advisor in transcript" assertion is NOT in this file.** It lives in `test_correctness.py:168-198` (`_assert_message_jsonl_contains_sandbox_tools`, the `leaked_tool_uses`/`leaked_advisor_results` asserts). See the inversion note at the end.

## Enum/shape ground truth (verified against source)

- Task row (`_serialize_task`, `db/stores/task_center_store.py:44-60`) exposes BOTH `role` and `agent_name`. Canonical `role` values (`TaskCenterTaskRole`): `planner` / `generator` / `evaluator` only. There is **no** `executor`/`verifier` role at the task layer — those are mock-only EventType names. `EXECUTOR_INVOKED` → role `generator`; `agent_name` carries the specific agent (e.g. `executor`, `verifier`). The existing helper `_has_multi_dependency_verifier` keys off `task.get("agent_name") == "verifier"`.
- Task `status` (`TaskCenterTaskStatus`): `done` / `failed` / `pending` / `running` / `waiting_goal` / `blocked`. → `EXECUTOR_SUCCESS`=`done`, `EXECUTOR_FAILURE`=`failed`.
- `AttemptStatus`: `running`/`passed`/`failed` (NOT "succeeded"). `AttemptFailReason`: `planner_failed`/`generator_failed`/**`evaluator_failed`**/`startup_failed`. NOTE the §4.1 doc loosely writes "evaluation_failed" at line 144 — the real value is **`evaluator_failed`**. `EVALUATOR_SUCCESS` ⇒ `attempt.status=="passed"`; `EVALUATOR_FAILURE` ⇒ `attempt.status=="failed"` AND `fail_reason=="evaluator_failed"`.
- `deferred_goal_for_next_iteration`: `None` ⇒ PLANNER_COMPLETES_GOAL_PLAN (closed); set (truthy str) ⇒ PLANNER_DEFERS_GOAL_PLAN.
- Goal: `origin_kind` (`entry`/`task`), `requested_by_task_id`, `status` (`open`/`succeeded`/`failed`/`cancelled`). RECURSIVE_GOAL_* ⇒ child goal `origin_kind=="task"`.

## Per-assertion before/after table

| # | Current (line) | Maps to event | graph_summary replacement |
|---|---|---|---|
| A | `counts = Counter(event.type for event in report.events)` (89) | — (setup) | DELETE. Build `attempts` list (already present at 82-86) is reused. |
| B | `assert counts[EventType.PLANNER_INVOKED] >= 3, counts` (90) | PLANNER_INVOKED count | `assert count_role_tasks(goal, "planner") >= 3` (one planner task per attempt; 3 attempts) |
| C | `assert counts[EventType.PLANNER_DEFERS_GOAL_PLAN] == 1, counts` (91) | PLANNER_DEFERS_GOAL_PLAN count | `assert sum(1 for a in attempts if a["deferred_goal_for_next_iteration"]) == 1` |
| D | `assert counts[EventType.PLANNER_COMPLETES_GOAL_PLAN] == 2, counts` (92) | PLANNER_COMPLETES_GOAL_PLAN count | `assert sum(1 for a in attempts if a["deferred_goal_for_next_iteration"] is None) == 2` |
| E | `assert counts[EventType.EVALUATOR_FAILURE] == 1, counts` (93) | EVALUATOR_FAILURE count | `assert sum(1 for a in attempts if attempt_outcome(a) == "failed") == 1` (the eval-failed iter1/attempt1) |
| F | `assert counts[EventType.EVALUATOR_SUCCESS] == 2, counts` (94) | EVALUATOR_SUCCESS count | `assert sum(1 for a in attempts if attempt_outcome(a) == "passed") == 2` |

### Exact replacement code (replaces lines 89-94 verbatim)

```python
    # Workflow shape is asserted via graph_summary (real TaskCenter store),
    # not deleted lifecycle events. ``attempts`` is the flattened list built
    # above (lines 82-86).
    # PLANNER_INVOKED >= 3  -> one planner task per attempt (3 attempts).
    assert count_role_tasks(goal, "planner") >= 3, goal
    # PLANNER_DEFERS_GOAL_PLAN == 1 -> exactly one attempt deferred a goal.
    assert (
        sum(1 for a in attempts if a["deferred_goal_for_next_iteration"]) == 1
    ), attempts
    # PLANNER_COMPLETES_GOAL_PLAN == 2 -> two attempts closed without defer.
    assert (
        sum(
            1
            for a in attempts
            if a["deferred_goal_for_next_iteration"] is None
        )
        == 2
    ), attempts
    # EVALUATOR_FAILURE == 1 -> one attempt failed evaluation.
    assert (
        sum(1 for a in attempts if attempt_outcome(a) == "failed") == 1
    ), attempts
    # EVALUATOR_SUCCESS == 2 -> two attempts passed evaluation.
    assert (
        sum(1 for a in attempts if attempt_outcome(a) == "passed") == 2
    ), attempts
```

Then at module top, remove the now-unused imports:
- Line 28 `from collections import Counter` — delete (still used? No — only used at 89; safe to remove).
- Line 35 `from task_center_runner.audit.events import EventType` — delete.
- Add: `from task_center_runner.tests.mock._focused_scenario_contracts import attempt_outcome, count_role_tasks`.

**Caveat on E/F count (3 attempts, expecting 1 fail + 2 pass):** the docstring (lines 1-13) says iter1 attempt1 eval-fails, iter1 attempt2 + iter2 attempt1 pass. `attempt_outcome` returns the attempt's terminal status. If the deferred attempt (iter1/attempt2) closes with `status=="passed"` and a non-null `deferred_goal_for_next_iteration` (defer is orthogonal to pass/fail), then 2 passed + 1 failed holds and E/F are correct. If the deferred attempt is recorded with `status=="running"`/non-terminal at defer time, prefer the more robust fail-only assertion: `assert sum(1 for a in attempts if attempt_outcome(a)=="failed") == 1` plus `assert all(attempt_outcome(a) != "failed" or ... )`. Recommend the implementer confirm the deferred attempt's persisted `status` on a real run; the EVALUATOR_FAILURE==1 (assert E) is the load-bearing one and is unambiguous.

## Shared helpers needed (add to `_focused_scenario_contracts.py`)

The §4.1 doc (lines 148-149) names three: `count_role_tasks`, `attempt_outcome`, `recursive_goals`. Only the first two are used by THIS file; `recursive_goals` is used by other migrating tests (`test_full_case_user_input.py`, `test_full_stack_adversarial.py` currently have a local `_recursive_goal_count`).

```python
def count_role_tasks(goal: Mapping[str, Any], role: str) -> int:
    """Count tasks of ``role`` ("planner"/"generator"/"evaluator") across all
    attempts of ``goal``. Replaces *_INVOKED event counts (§4.1).

    Note: the task row carries both ``role`` (canonical: planner/generator/
    evaluator) and ``agent_name`` (specific agent, e.g. "executor"/"verifier").
    EXECUTOR_INVOKED -> role=="generator"; to count a specific agent
    (e.g. verifier) filter on ``agent_name`` instead.
    """
    return sum(
        1
        for iteration in goal["iterations"]
        for attempt in iteration["attempts"]
        for task in attempt["tasks"]
        if task.get("role") == role
    )


def attempt_outcome(attempt: Mapping[str, Any]) -> str:
    """Attempt terminal status ("passed"/"failed"/"running"). Replaces
    EVALUATOR_SUCCESS (=="passed") / EVALUATOR_FAILURE (=="failed") (§4.1).
    For the precise eval-failure signal also check
    ``attempt["fail_reason"] == "evaluator_failed"``.
    """
    return str(attempt["status"])


def recursive_goals(graph_summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Child goals spawned by a task (RECURSIVE_GOAL_REQUESTED/COMPLETED, §4.1).
    A recursive goal has origin_kind=="task" and a requested_by_task_id; its
    status/final_outcome give the COMPLETED signal. Generalizes the local
    ``_recursive_goal_count`` (origin_kind=="task") pattern.
    """
    return [
        goal
        for goal in graph_summary["goals"]
        if goal.get("origin_kind") == "task"
    ]
```

Required import additions to `_focused_scenario_contracts.py`: `from typing import Any` (the file currently imports `Mapping`/`Sequence` from `collections.abc` but not `Any`).

## The INVERTED ask_advisor assertion (in `test_correctness.py`, NOT this file)

`test_correctness.py:168-198` asserts the synthetic `ask_advisor` tool_use and the advisor approval `tool_result` (`helper_role=="advisor"`) **must NOT** appear in `message.jsonl` — because the old `MockSquadRunner._approve_terminal` injects a synthetic ask_advisor/approval pair onto per-call `ExecutionMetadata` that is never streamed. **Under the event-source runner this inverts:** the ScenarioEventSource drives real `ask_advisor` turns through the actual query loop, so genuine `ask_advisor` tool_use blocks (and their results) DO appear in the transcript. When `test_correctness.py` is migrated to run under `EOS_MOCK_EVENT_SOURCE_RUNNER` (or replaced by `test_correctness_via_event_source.py`):
- `leaked_tool_uses` (lines 174-185): the assertion `assert not leaked_tool_uses` must be inverted/dropped — real `ask_advisor` tool_use blocks are now expected. Replace with a positive assertion if advisor coverage is wanted (e.g. `assert leaked_tool_uses`), or simply delete the synthetic-leak guard.
- `leaked_advisor_results` (lines 186-198): same — `helper_role=="advisor"` results from real advisor turns now legitimately appear; invert or drop.
- Note: `test_correctness_via_event_source.py` (the already-written under-flag replacement) deliberately omits any ask_advisor leak assertion, sidestepping the inversion. The comment at `test_correctness.py:168-173` referencing `runner.py:_approve_terminal` becomes stale once the imperative runner is deleted (Phase 3).

## Files referenced (absolute paths)

- Target test: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_initial_messages_capture.py` (assertions to migrate: lines 89-94; lines 76-87 already graph_summary-based, unchanged)
- Shared helpers home: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/_focused_scenario_contracts.py`
- `graph_summary` shape: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/core/runner.py:90-142`
- Task row keys (`role`+`agent_name`+`status`): `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/db/stores/task_center_store.py:44-60`, `:213-225`
- Enums: attempt `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center/attempt/state.py:17-27`; iteration `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center/iteration/state.py:13-22`; goal `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center/goal/state.py:11-48`; task role/status `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center/_core/task_state.py:13-33`
- §4.1 mapping: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/docs/plans/mock_event_source_IMPL_PLAN.md:139-149`
- INVERTED ask_advisor assertion: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_correctness.py:168-198`
- Under-flag verification targets: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/contracts/test_correctness_via_event_source.py` (CorrectnessTesting), `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/contracts/test_scenario_loop_runner_planner_submit.py` (planner submit)
- Existing pattern to generalize into `recursive_goals`: `_recursive_goal_count` at `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_full_case_user_input.py:151-156`


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_deferred_parent_planner_terminal_routing.py

API Error: Internal server error


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py

`FULL_STACK_SCRIPT_COMPLETED` is emitted ONLY by `MockSquadRunner` (runner.py:1990), the imperative runner being deleted. It is grouped under "agent invocations" (lines 60–76), the deleted-lifecycle block. It is NOT a sandbox event. So it migrates: its replacement is the graph-summary check on the `:capacity_metrics_summary` task (already present in `_assert_graph_shape` at lines 124-125) reaching `status=="done"` — that task IS the full-stack script's completion. The SANDBOX_* events (lines 83–101) are a separate block and survive.

I have everything needed. Final answer below.

---

## SUMMARY ANSWER

### Flag / scenario (correction to the task premise)

`test_full_system_capacity_matrix.py` does **NOT** set `EOS_MOCK_EVENT_SOURCE_RUNNER`. It runs `capacity.full_system_capacity_matrix` through the **legacy MockSquadRunner path**. It has **no under-flag verification target**. The flag + single-scenario pattern lives in two OTHER files: `test_correctness_via_event_source.py` (runs `CorrectnessTesting`, sets flag at line 62) and `test_scenario_loop_runner_planner_submit.py` (sets flag at line 92). When Phase 2 ports `capacity.full_system_capacity_matrix` onto the loop, this test's fixture call to `run_scenario_on_sweevo_image` would gain the flag and the assertions below.

### ask_advisor "no ask_advisor in transcript" assertion — NOT PRESENT

There is no such assertion in `test_full_system_capacity_matrix.py`. The closest mechanism, `_FORBIDDEN_RUN_SIGNATURES` (lines 37–43), does not list `ask_advisor`. There is no `test_correctness.py` (only `test_correctness_via_event_source.py`, which also has no ask_advisor transcript check). The inversion the task describes (real `ask_advisor` turns now DO appear in the transcript) does not affect any assertion in this file. Confirmed by grep, not skipped.

### FULL_STACK_SCRIPT_COMPLETED resolution

Emitted ONLY at `runner.py:1990` (the imperative MockSquadRunner being deleted); grouped with the deleted "agent invocations" block in `events.py:60-76`, NOT with `SANDBOX_*` (lines 83–101). → **It is deleted-lifecycle; migrate to graph_summary.** Its faithful replacement is the existing `:capacity_metrics_summary` task check reaching `status=="done"` — that task is literally the full-stack script terminal node.

---

### Per-assertion BEFORE / AFTER table

| # | Location | Lifecycle-dependent? | BEFORE (current) | AFTER (graph_summary replacement) |
|---|---|---|---|---|
| 1 | lines 69–70, `extra_hooks` | YES (deleted `VERIFIER_FAILURE`) | `count_events(EventType.VERIFIER_FAILURE, name="verifier_failures"),` | **DROP** the hook. The flag `count_verifier_failures` is never asserted in-file. Verifier-failure presence is covered by row 9 (`count_failed_tasks(gs,"verifier")>=1`). |
| 2 | lines 71 (call) / hook in builtins.py | YES (ordering on deleted `VERIFIER_SUCCESS` + `RECURSIVE_GOAL_COMPLETED`) | `assert_recursive_goal_closed_before_parent_guard(),` | **DROP** (do NOT migrate the ordering check). Per IMPL plan §4 the real TaskCenter enforces close-before-parent-return. Residual "child goal reached succeeded" is already covered by `_assert_graph_shape` line 111 (`all(goal["status"]=="succeeded" for goal in recursive)`). |
| 3 | line 87, `_assert_graph_shape(report.graph_summary)` | NO | (already graph-based) | **No change.** |
| 4 | lines 96–130, body of `_assert_graph_shape` | NO | uses `goals`/`origin_kind`/`iterations`/`attempts`/`tasks`/`agent_name`/`needs`/`status` | **No change** — already the canonical graph-shape assertion. Note line 124-125 (`:capacity_metrics_summary` task present) is the natural home for the FULL_STACK_SCRIPT_COMPLETED replacement; optionally strengthen to also assert that task's `status=="done"` (see row 10). |
| 5 | lines 134–142, `_assert_tool_and_event_capacity` tool_counts | NO (tool_calls, not events) | `Counter(call.tool_name …)` over `report.tool_calls` | **No change** — `report.tool_calls` is re-homed via `MOCK_TOOL_CALL_RECORDED` bridged from real loop dispatch (IMPL §1.D step 3). |
| 6 | lines 144–158, `required_events` set + `seen`/`missing` | MIXED — SPLIT | the 10-event `required_events` set, `seen = {event.type …}`, `missing = …`, `assert not missing` | **Split** (see expanded code below): keep a 5-member `required_sandbox_events` set checked against `seen`; replace the 5 lifecycle members with 4 graph_summary lines (rows 7–10) + drop FULL_STACK_SCRIPT_COMPLETED into row 10. |
| 7 | line 146 `RECURSIVE_GOAL_REQUESTED` (inside set) | YES | member of `required_events` | `assert recursive_goals(gs), gs` — child goal(s) with `origin_kind=="task"` exist ⇒ a recursive goal was requested. |
| 8 | line 147 `RECURSIVE_GOAL_COMPLETED` (inside set) | YES | member of `required_events` | `assert all(g["status"]=="succeeded" for g in recursive_goals(gs)), gs` — recursive child goal reached terminal success ⇒ it completed and returned. |
| 9 | line 145 `PLANNER_DEFERS_GOAL_PLAN` + line 145 `VERIFIER_FAILURE` (inside set) | YES | members of `required_events` | DEFERS → `assert attempt_deferred(gs), gs` (some attempt has `deferred_goal_for_next_iteration` set). VERIFIER_FAILURE → `assert count_failed_tasks(gs, "verifier") >= 1, gs` (some verifier task `status=="failed"`). |
| 10 | line 154 `FULL_STACK_SCRIPT_COMPLETED` (inside set) | YES | member of `required_events` | `assert any(t.get("id","").endswith(":capacity_metrics_summary") and t["status"]=="done" for t in _all_tasks(gs)), gs` — the full-stack-script terminal task closed successfully. |
| 11 | line 159, `tool_errors_total` | NO (metric) | `assert int(report.metrics.get("tool_errors_total") or 0) >= 1` | **No change** — metric from real tool dispatch. |
| 12 | lines 162–177, `_assert_audit_artifacts` | NO | reads `run.json`/`metrics.json`/`task.json`/`message.jsonl` + `sandbox_events.jsonl` checking `SANDBOX_LAYER_STACK_LAYERS_SQUASHED` / `SANDBOX_CONFLICT_DETECTED` | **No change** — both are `SANDBOX_*` events (events.py:88,91), survive; `tool_calls_total` metric and audit files are produced identically by the real loop + same sandbox RPCs. |
| 13 | lines 180–225, `_assert_no_forbidden_signatures` / `_assert_capacity_workspace_artifacts` | NO | string/JSON scans of run artifacts | **No change** — not lifecycle-event dependent. |

### Expanded replacement for `_assert_tool_and_event_capacity` (the only body that materially changes)

```python
def _assert_tool_and_event_capacity(report: Any) -> None:
    tool_counts = Counter(call.tool_name for call in report.tool_calls)
    assert tool_counts["write_file"] >= 30
    assert tool_counts["edit_file"] >= 5
    assert tool_counts["read_file"] >= 20
    assert tool_counts["shell"] >= 10
    assert (
        sum(count for name, count in tool_counts.items() if name.startswith("lsp."))
        >= 5
    )

    # Sandbox-derived events survive (real tool dispatch still emits them).
    required_sandbox_events = {
        EventType.SANDBOX_LAYER_STACK_LAYERS_SQUASHED,
        EventType.SANDBOX_OVERLAY_EXECUTED,
        EventType.SANDBOX_OCC_CHANGESET_RECEIVED,
        EventType.SANDBOX_OCC_CHANGES_COMMITTED,
        EventType.SANDBOX_CONFLICT_DETECTED,
    }
    seen = {event.type for event in report.events}
    missing = sorted(event.value for event in required_sandbox_events - seen)
    assert not missing, f"missing required sandbox events: {missing}"

    # Lifecycle events deleted (IMPL §0a/§4.1) -> assert via real store state.
    gs = report.graph_summary
    # RECURSIVE_GOAL_REQUESTED: a child goal originated from a task.
    assert recursive_goals(gs), gs
    # RECURSIVE_GOAL_COMPLETED: that child goal returned successfully.
    assert all(g["status"] == "succeeded" for g in recursive_goals(gs)), gs
    # PLANNER_DEFERS_GOAL_PLAN: an attempt deferred a goal to the next iteration.
    assert attempt_deferred(gs), gs
    # VERIFIER_FAILURE: at least one verifier task failed.
    assert count_failed_tasks(gs, "verifier") >= 1, gs
    # FULL_STACK_SCRIPT_COMPLETED: the full-stack script terminal task closed.
    assert any(
        t.get("id", "").endswith(":capacity_metrics_summary") and t.get("status") == "done"
        for t in _all_tasks(gs)
    ), gs

    assert int(report.metrics.get("tool_errors_total") or 0) >= 1
```

### Shared helpers needed (add to `_focused_scenario_contracts.py`)

Task-row shape (from `db/stores/task_center_store.py:_serialize_task` lines 44–60): each task dict has `id`, `role` (`planner`/`generator`/`evaluator`), `agent_name` (`executor`/`verifier`/`planner`/`evaluator`), `status` (`done`/`failed`/…), `needs` (list). CRITICAL: executor AND verifier are both `role=="generator"`, distinguished only by `agent_name` — so role-task counts key on `agent_name`, not `role`.

```python
def _all_tasks(graph_summary: dict) -> list[dict]:
    return [
        task
        for goal in graph_summary["goals"]
        for iteration in goal["iterations"]
        for attempt in iteration["attempts"]
        for task in attempt["tasks"]
    ]

def count_role_tasks(graph_summary: dict, agent_name: str) -> int:
    # *_INVOKED counts. Flatten ALL attempts (retries live in earlier attempts);
    # key on agent_name because executor/verifier share role=="generator".
    return sum(1 for t in _all_tasks(graph_summary) if t.get("agent_name") == agent_name)

def count_failed_tasks(graph_summary: dict, agent_name: str) -> int:
    # EXECUTOR_FAILURE / VERIFIER_FAILURE -> per-task status.
    return sum(
        1
        for t in _all_tasks(graph_summary)
        if t.get("agent_name") == agent_name and t.get("status") == "failed"
    )

def recursive_goals(graph_summary: dict) -> list[dict]:
    # RECURSIVE_GOAL_REQUESTED/_COMPLETED -> child goals with origin_kind=="task".
    # Callers check presence (requested) and each g["status"] (completed/final_outcome).
    return [g for g in graph_summary["goals"] if g.get("origin_kind") == "task"]

def attempt_outcome(attempt: dict) -> str:
    # EVALUATOR_SUCCESS/_FAILURE + COMPLETES vs DEFERS, from one attempt row.
    #  - "deferred" if deferred_goal_for_next_iteration is set (PLANNER_DEFERS)
    #  - "eval_failed" if fail_reason == "evaluator_failed" (EVALUATOR_FAILURE)
    #  - else attempt["status"] (e.g. "succeeded" ~ EVALUATOR_SUCCESS / COMPLETES)
    if attempt.get("deferred_goal_for_next_iteration"):
        return "deferred"
    if attempt.get("fail_reason") == "evaluator_failed":
        return "eval_failed"
    return attempt["status"]

def attempt_deferred(graph_summary: dict) -> bool:
    # PLANNER_DEFERS_GOAL_PLAN presence across the run.
    return any(
        a.get("deferred_goal_for_next_iteration")
        for goal in graph_summary["goals"]
        for it in goal["iterations"]
        for a in it["attempts"]
    )
```

Notes on the §4.1 mapping as it lands HERE:
- `attempt_outcome` is defined for completeness (EVALUATOR_*/COMPLETES-vs-DEFERS), but this file's only outcome needs are DEFERS (covered by `attempt_deferred`) and the recursive-goal status. The `fail_reason=="evaluator_failed"` branch (string from `AttemptFailReason.EVALUATOR_FAILED`, `attempt/state.py:26`) is the EVALUATOR_FAILURE replacement and is exercised by other Phase-2 focused scenarios, not by this capacity test.
- `count_role_tasks` (the *_INVOKED replacement) is added for the shared helper set but is not strictly required by THIS file's assertions — `_assert_graph_shape` already counts tasks inline (lines 119–130).

Relevant file paths:
- Target test: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox/capacity/test_full_system_capacity_matrix.py`
- Shared helpers home: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/_focused_scenario_contracts.py`
- graph_summary builder: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/core/runner.py` (`_graph_summary`, lines 90–142)
- task-row shape: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/db/stores/task_center_store.py` (`_serialize_task`, lines 44–60)
- fail_reason enum: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center/attempt/state.py` (`AttemptFailReason`, lines 23–27; `EVALUATOR_FAILED="evaluator_failed"`)
- existing graph-helper precedent: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_full_case_user_input.py` (`_recursive_goal_count` L151, `_has_multi_dependency_verifier` L141, `_continuation_iterations_follow_partial_attempts` L125)
- FULL_STACK_SCRIPT_COMPLETED sole emit site: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py:1990`


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox/full_stack/test_full_stack_adversarial.py

I have all the evidence I need. The recursive child goal's `final_outcome` is `{"outcome": "success"|"failed", ...}`, and the goal `status` is "succeeded". Now I'll produce the complete analysis.

Note on this file specifically: it does NOT use `FocusedScenarioCase`/`assert_focused_scenario_report`/`min_event_counts`/`expected_event_sequence`/`absent_events`/`seen_event_types`. It is a bespoke full-stack test. Its lifecycle-event dependence is concentrated in `_assert_task_center_shape` (the `seen` set checks + two `_assert_event_order` calls), plus the `count_events(VERIFIER_FAILURE)` and `assert_recursive_goal_closed_before_parent_guard()` extra_hooks, and `EventType` references in `_assert_sandbox_monitor_events` (those are SANDBOX_* events, which are NOT in the 14-entry lifecycle-deletion set and therefore stay).

Here is the complete deliverable.

---

# Migration analysis: `test_full_stack_adversarial.py`

## Under-flag setup answer

This file does **NOT** set `EOS_MOCK_EVENT_SOURCE_RUNNER`. It currently runs against the imperative `MockSquadRunner` (default seam). It is the canonical Phase-2 migration target (the heavy full-stack tool-script matrix that runs only through the old runner). When ported, the single scenario it runs is `FullStackAdversarial()` (constructed at line 113, executed via `run_scenario_on_sweevo_image` at line 114), and the test body must add `monkeypatch.setenv("EOS_MOCK_EVENT_SOURCE_RUNNER", "1")` (matching the pattern in `test_correctness_via_event_source.py:62` — which means adding `monkeypatch: pytest.MonkeyPatch` to the test signature). Its under-flag verification target = the full-stack tool-script matrix + recursive-goal + multi-dependency-verifier workflow driven through the real query loop.

## ask_advisor note

There is **no** "no ask_advisor in transcript" assertion in *this* file. (`grep` for `ask_advisor` in this file returns nothing; the inverted-assertion warning applies to `test_advisor_gate_negative_path.py` / advisor-gate tests per IMPL_PLAN §3, not here.) The closest transcript assertion here is `_assert_message_logs` (lines 198-237), which asserts a positive set of tool_uses — `ask_advisor` is not in its allow/deny set, so it is unaffected by the advisor inversion.

## Lifecycle-EventType dependence inventory

Three loci depend on the 14 deleted lifecycle EventTypes (events.py:61-75):
1. `_assert_task_center_shape` — the `seen` set membership checks (lines 156-163) + two `_assert_event_order` calls (lines 166-177).
2. `extra_hooks` (lines 120-123): `count_events(EventType.VERIFIER_FAILURE, ...)` + `assert_recursive_goal_closed_before_parent_guard()`.
3. The `_assert_event_order` / `_event_index` helpers (lines 445-478) become dead code.

The SANDBOX_* EventTypes in `_assert_sandbox_monitor_events` (lines 240-263) are **NOT** in the lifecycle-deletion set (events.py:83-101 stay) — that whole function is **unchanged**. `FULL_STACK_SCRIPT_COMPLETED` (line 76) is in the deletion range (61-76) per §3, so its `seen` check (line 163) must migrate too.

---

## Per-assertion before/after table

### Assertion 1 — `seen` set lifecycle checks (lines 156-163)

**Before** (`_assert_task_center_shape`, lines 156-163):
```python
seen = {event.type for event in events}
assert EventType.PLANNER_COMPLETES_GOAL_PLAN in seen
assert EventType.PLANNER_DEFERS_GOAL_PLAN in seen
assert EventType.VERIFIER_FAILURE in seen
assert EventType.RECURSIVE_GOAL_REQUESTED in seen
assert EventType.RECURSIVE_GOAL_COMPLETED in seen
assert EventType.EVALUATOR_SUCCESS in seen
assert EventType.FULL_STACK_SCRIPT_COMPLETED in seen
```

**After** (drop the `seen` set; assert each via graph_summary per §4.1). Signature changes from `(graph_summary, events)` to `(graph_summary)` since `events` is no longer needed for these:

```python
# PLANNER_COMPLETES_GOAL_PLAN + PLANNER_DEFERS_GOAL_PLAN  → §4.1 row "COMPLETES vs DEFERS":
#   at least one attempt closed its goal (deferred_goal_for_next_iteration is None)
#   AND at least one attempt deferred (deferred_goal_for_next_iteration is set).
all_attempts = [
    attempt
    for goal in graph_summary["goals"]
    for iteration in goal["iterations"]
    for attempt in iteration["attempts"]
]
assert any(
    a["deferred_goal_for_next_iteration"] is None for a in all_attempts
), all_attempts  # was PLANNER_COMPLETES_GOAL_PLAN
assert any(
    a["deferred_goal_for_next_iteration"] for a in all_attempts
), all_attempts  # was PLANNER_DEFERS_GOAL_PLAN

# VERIFIER_FAILURE  → §4.1 "EXECUTOR/VERIFIER SUCCESS/FAILURE = per-task status":
#   at least one verifier-agent task ended status=="failed".
assert any(
    task.get("agent_name") == "verifier" and task["status"] == "failed"
    for a in all_attempts
    for task in a["tasks"]
), all_attempts  # was VERIFIER_FAILURE

# RECURSIVE_GOAL_REQUESTED + RECURSIVE_GOAL_COMPLETED  → §4.1 "RECURSIVE_GOAL_*":
#   a child goal exists with origin_kind=="task" (requested) AND status=="succeeded"
#   with final_outcome["outcome"]=="success" (completed).
recursive = recursive_goals(graph_summary)
assert recursive, graph_summary  # was RECURSIVE_GOAL_REQUESTED
assert all(g["requested_by_task_id"] for g in recursive), recursive
assert any(
    g["status"] == "succeeded"
    and (g.get("final_outcome") or {}).get("outcome") == "success"
    for g in recursive
), recursive  # was RECURSIVE_GOAL_COMPLETED

# EVALUATOR_SUCCESS  → §4.1 "EVALUATOR_* = attempt status + fail_reason":
#   at least one attempt passed (status=="passed", fail_reason is None).
assert any(
    a["status"] == "passed" and a["fail_reason"] is None for a in all_attempts
), all_attempts  # was EVALUATOR_SUCCESS

# FULL_STACK_SCRIPT_COMPLETED  → no store equivalent (runner-internal side-channel).
#   Migrate to the existing sandbox-effect assertion in _assert_final_sandbox_state
#   (final-reconciliation.json failed_cells==0 / recursive_goals==1, already asserted
#   at lines 391-394) + the summary row passed_cells>=32 (line 422). No replacement
#   in _assert_task_center_shape — delete the line; its signal is already covered.
```

### Assertion 2 — `_has_multi_dependency_verifier` (lines 164, 180-187)

**Before** (line 164 call + helper 180-187):
```python
assert _has_multi_dependency_verifier(graph_summary)
...
def _has_multi_dependency_verifier(graph_summary: dict[str, Any]) -> bool:
    for goal in graph_summary["goals"]:
        for iteration in goal["iterations"]:
            for attempt in iteration["attempts"]:
                for task in attempt["tasks"]:
                    if task.get("agent_name") == "verifier" and len(task["needs"]) > 1:
                        return True
    return False
```

**After** — **NO CHANGE**. This already reads graph_summary (`task["agent_name"]` + `task["needs"]`), confirmed against `_serialize_task` keys (`agent_name`, `needs` at task_center_store.py:49,53). Keep verbatim. It is one of the §4.1 "pattern already exists" cases.

### Assertion 3 — `_recursive_goal_count >= 1` (lines 165, 190-195)

**Before** (line 165 + helper 190-195):
```python
assert _recursive_goal_count(graph_summary) >= 1
...
def _recursive_goal_count(graph_summary: dict[str, Any]) -> int:
    return sum(1 for goal in graph_summary["goals"] if goal.get("origin_kind") == "task")
```

**After** — **NO CHANGE in logic**, but fold into the shared `recursive_goals` helper to avoid two near-identical local helpers:
```python
assert len(recursive_goals(graph_summary)) >= 1
# delete local _recursive_goal_count; recursive_goals() returns the same goals.
```
(`_recursive_goal_count` is the §4.1-named "pattern already exists" — promote it to the shared `recursive_goals` helper.)

### Assertion 4 — first `_assert_event_order` (lines 166-171)

**Before**:
```python
_assert_event_order(
    events,
    first=EventType.RECURSIVE_GOAL_COMPLETED,
    second=EventType.VERIFIER_SUCCESS,
    second_checkpoint="recursive_return",
)
```

**After** — **DELETE.** Per IMPL_PLAN §4 ("The `_assert_ordered_subsequence` order-check is dropped — the real TaskCenter enforces role ordering") this temporal ordering is no longer assertable from store snapshots (graph_summary is terminal state, not a timeline; `checkpoint` payloads live on events, which are gone). The invariant it guarded — recursive child goal closes before the parent's recursive-return verifier succeeds — is enforced structurally by TaskCenter: the parent verifier task that `needs` the recursive handoff cannot reach `status=="done"` until the child goal is `succeeded`. Replace the temporal check with the structural one:
```python
# Structural replacement: the recursive child goal reached a terminal succeeded
# state, and the parent attempt that requested it also passed — TaskCenter cannot
# close the parent before the child closes (recursive-return guard is enforced
# by the dependency/closure-report machinery, not test-side ordering).
recursive = recursive_goals(graph_summary)
assert recursive and all(g["status"] == "succeeded" for g in recursive), recursive
```

### Assertion 5 — second `_assert_event_order` (lines 172-177)

**Before**:
```python
_assert_event_order(
    events,
    first=EventType.VERIFIER_SUCCESS,
    second=EventType.EVALUATOR_INVOKED,
    first_checkpoint="final_release",
)
```

**After** — **DELETE.** Same rationale. The "final_release verifier succeeds before evaluator is invoked" ordering is the per-Attempt generator-DAG → evaluator contract enforced by `AttemptOrchestrator` (an evaluator task only runs after its generator DAG completes). Terminal-state replacement: the attempt has both a succeeded final-release verifier task and a passed evaluator outcome.
```python
# Structural replacement: per-Attempt the evaluator runs only after the generator
# DAG (incl. the final_release_guard verifier) completes; assert both terminal facts.
final_attempt = all_attempts[-1]  # or select by stage; final attempt of root goal
assert any(
    t.get("agent_name") == "verifier" and t["status"] == "done"
    for t in final_attempt["tasks"]
), final_attempt
assert final_attempt["status"] == "passed", final_attempt
```

### Assertion 6 — `count_events(EventType.VERIFIER_FAILURE, ...)` extra_hook (line 121)

**Before**:
```python
extra_hooks=(
    count_events(EventType.VERIFIER_FAILURE, name="verifier_failures"),
    assert_recursive_goal_closed_before_parent_guard(),
),
```

**After** — **DELETE the `count_events` hook** (VERIFIER_FAILURE is a deleted EventType; `hooks/builtins.py:30` `_ROLE_TO_INVOKED` and the verifier emit sites at builtins.py:135,162 are removed per §3). Its signal (a verifier failed at least once) is already covered by Assertion 1's new `VERIFIER_FAILURE` graph check. Nothing in this file actually reads the `count_verifier_failures` flag, so no downstream assertion needs replacing — just drop it from the tuple. **DELETE `assert_recursive_goal_closed_before_parent_guard()`** too — it fires on `EventType.VERIFIER_SUCCESS` and inspects `state.seen_events` for `RECURSIVE_GOAL_COMPLETED` (builtins.py:178-189), both deleted; its guarantee is now Assertion 4's structural check.

### Assertion 7 — `_assert_event_order` / `_event_index` helpers (lines 445-478)

**Before**: helper functions consuming `events` + `EventType` + `event.payload["checkpoint"]`.

**After** — **DELETE both functions** (dead after Assertions 4 & 5 are removed). Also drop the now-unused `Event` import (line 16) if no other use remains — but note `_assert_sandbox_monitor_events` still takes `events: list[Event]`, so the `Event`/`EventType` imports STAY (SANDBOX_* events are live).

### Assertion 8 — `_assert_message_logs` executor-error check (lines 230-237)

**Before**: asserts an `edit_file` `tool_result` with `is_error` exists in message logs.

**After** — **NO CHANGE.** This reads `message.jsonl` (re-homed `report.tool_calls`/message logs, §4 "re-homed step 3"), not lifecycle events. Under the real loop the expected `edit_file` error still flows through dispatch and is recorded. Keep verbatim. (Listed only to confirm it does NOT migrate.)

### Assertions in `_assert_final_sandbox_state` (lines 375-442) and `_assert_full_stack_performance_report_complete` (lines 266-359)

**No change.** These read sandbox state + `performance_report.json` + `sandbox_events.jsonl`, all produced by the REAL tool execution (§4 "unchanged — built from daemon sandbox_events.jsonl produced by the REAL tool execution"). They are the durable replacement target for `FULL_STACK_SCRIPT_COMPLETED`.

---

## Shared helpers needed (add to `_focused_scenario_contracts.py`)

Per IMPL_PLAN §4.1 line 148-149, add three module-level helpers. This file imports them from `_focused_scenario_contracts`:

```python
def count_role_tasks(
    graph_summary: Mapping[str, Any],
    *,
    role: str | None = None,
    agent_name: str | None = None,
) -> int:
    """Count tasks across all goals/iterations/attempts matching role and/or agent_name.

    role  -> structural role: "planner" | "generator" | "evaluator"  (task["role"])
    agent_name -> specific agent: "executor"/"executor_*"/"verifier"  (task["agent_name"])
    Use agent_name to split EXECUTOR vs VERIFIER (both have role=="generator").
    Use role for PLANNER_INVOKED / EVALUATOR_INVOKED counts.
    """
    return sum(
        1
        for goal in graph_summary["goals"]
        for iteration in goal["iterations"]
        for attempt in iteration["attempts"]
        for task in attempt["tasks"]
        if (role is None or task.get("role") == role)
        and (agent_name is None or task.get("agent_name") == agent_name)
    )


def attempt_outcome(attempt: Mapping[str, Any]) -> tuple[str, str | None]:
    """Return (status, fail_reason) for an attempt.

    status: "running" | "passed" | "failed"
    fail_reason: None | "planner_failed" | "generator_failed"
                 | "evaluator_failed" | "startup_failed"
    EVALUATOR_SUCCESS  -> status=="passed" and fail_reason is None
    EVALUATOR_FAILURE  -> status=="failed" and fail_reason=="evaluator_failed"
    """
    return attempt["status"], attempt["fail_reason"]


def recursive_goals(graph_summary: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return child goals delegated by a task (origin_kind=='task').

    Replaces RECURSIVE_GOAL_REQUESTED/COMPLETED:
      requested -> len(recursive_goals(...)) >= 1 and g["requested_by_task_id"]
      completed -> g["status"]=="succeeded"
                   and (g.get("final_outcome") or {}).get("outcome")=="success"
    Subsumes the existing local _recursive_goal_count helper.
    """
    return [
        goal
        for goal in graph_summary["goals"]
        if goal.get("origin_kind") == "task"
    ]
```

### Key field-shape facts (verified against source)

- Task row keys (`_serialize_task`, `task_center_store.py:44-60`): `id`, `role` (planner/generator/evaluator), `agent_name` (executor/executor_*/verifier), `status` (the loop sets done/failed; serialized raw from `record.status`), `needs` (list, may be empty), `fix_target_id`, `spawn_reason`.
- `attempt["status"]` ∈ {`running`, `passed`, `failed`} (`AttemptStatus`); `attempt["fail_reason"]` ∈ {`None`, `planner_failed`, `generator_failed`, `evaluator_failed`, `startup_failed`} (`AttemptFailReason`). Note the §4.1 example string `evaluation_failed` is approximate — the real enum value is **`evaluator_failed`**.
- `attempt["deferred_goal_for_next_iteration"]`: `None` ⇒ goal closed (was PLANNER_COMPLETES_GOAL_PLAN); truthy str ⇒ deferred (was PLANNER_DEFERS_GOAL_PLAN). (runner.py:115)
- Goal keys (`_graph_summary`, runner.py:132-141): `status` (`open`/`succeeded`/`failed`/`cancelled`), `origin_kind` (`entry`/`task`), `requested_by_task_id`, `final_outcome` (`{"outcome": "success"|"failed", "final_iteration_id":..., "final_attempt_id":...}` per `GoalClosureReport.to_final_outcome`, goal/state.py:96-101).

### Caveat to flag for the implementer
Two assertions (4 and 5) lose temporal precision in the migration — they were ordering checks gated on `event.payload["checkpoint"]` strings (`recursive_return`, `final_release`). graph_summary is terminal state, so the *ordering* cannot be reconstructed; the structural replacements assert the *outcome* the ordering protected (child-goal-closed-before-parent-pass; verifier-done-before-evaluator-pass), relying on TaskCenter's own dependency/closure enforcement. This matches IMPL_PLAN §4 ("order-check is dropped — the real TaskCenter enforces role ordering") but is a genuine (intended) reduction in test assertion strength worth a code comment.


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_focused_sandbox_scenarios.py

Advisor is rate-limited. I'll proceed; my interpretation is well-grounded in primary sources. Let me reconcile the file-naming tension honestly in my output and answer for both files. I have everything needed. Let me write up the complete deliverable.

---

# Migration analysis: lifecycle-event assertions → `graph_summary`

## Critical scoping note (file identity)

The task header names `test_focused_sandbox_scenarios.py`, but the assertion fingerprint it describes — `count_events(PLANNER_INVOKED/EXECUTOR_INVOKED/...)`, the `seen_event_types` ordered-subsequence check, EXECUTOR/VERIFIER SUCCESS/FAILURE, and especially the "no `ask_advisor` in transcript" assertion — does **not** live in `test_focused_sandbox_scenarios.py`. It lives in **`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/task_center/test_correctness.py`**. This is confirmed verbatim by `docs/plans/mock_event_source_HANDOFF.md:195-197`: *"Migrate `test_correctness.py` (its 'no `ask_advisor` in transcript' assertion at lines 168-198 is now INVERTED ... and its `count_events(PLANNER_INVOKED/EVALUATOR_INVOKED)` hooks key off deleted events)."*

I cover **both** files below: `test_focused_sandbox_scenarios.py` (the named file — its event dependence is entirely indirect, via the `FocusedScenarioCase` machinery in `_focused_scenario_contracts.py`) and `test_correctness.py` (the file matching every assertion the task itemizes, including the inverted `ask_advisor` one).

## Under-flag verification status

- **`EOS_MOCK_EVENT_SOURCE_RUNNER`** is **NOT set** by either `test_focused_sandbox_scenarios.py` or `test_correctness.py`. Both still run through the old `MockSquadRunner` today. The two files that DO set the flag are the already-ported Phase-1 proofs: `test_correctness_via_event_source.py:62` (runs `CorrectnessTesting()`) and `test_scenario_loop_runner_planner_submit.py:92` (runs `_PlannerSubmitProof`).
- The **under-flag verification target for `test_correctness.py` is `CorrectnessTesting`** — the single scenario it runs (`test_correctness.py:43`). Its post-migration green-under-flag analogue already exists as `test_correctness_via_event_source.py` (same scenario, asserts via `graph_summary`, `EOS_MOCK_EVENT_SOURCE_RUNNER=1`). When `test_correctness.py` is migrated it must add `monkeypatch.setenv("EOS_MOCK_EVENT_SOURCE_RUNNER", "1")` + the `_active_mock_model` fixture (the new path goes through `spawn_agent`, which needs an active model row — HANDOFF "How to run", lines 34-38).
- `test_focused_sandbox_scenarios.py` runs the single scenario **`sandbox.occ_concurrent_conflicts`** (its one `FocusedScenarioCase`, line 24).

---

## File A — `test_focused_sandbox_scenarios.py` (+ its shared machinery `_focused_scenario_contracts.py`)

This file has **no inline EventType assertions**. All event dependence is data passed into `FocusedScenarioCase` and consumed by `assert_focused_scenario_report`. There are three event-dependent surfaces to migrate.

### A1. `min_event_counts` for the sandbox scenario

The only event-typed counts here are `SANDBOX_*` + `EXECUTOR_SUCCESS`. Per §3/HANDOFF:213-216, `SANDBOX_BATCH_EDIT_APPLIED` and `SANDBOX_CONFLICT_DETECTED` are **KEPT** (still emitted by `ProbeContext`). Only `EXECUTOR_SUCCESS` is a deleted lifecycle event.

| | Before (`test_focused_sandbox_scenarios.py:26-31`) | After |
|---|---|---|
| Sandbox counts | `min_event_counts={`<br>`  EventType.SANDBOX_BATCH_EDIT_APPLIED: 1,`<br>`  EventType.SANDBOX_CONFLICT_DETECTED: 1,`<br>`  EventType.EXECUTOR_SUCCESS: 1,`<br>`}` | Keep the two `SANDBOX_*` keys (re-homed, not deleted). Drop the `EXECUTOR_SUCCESS` key; replace its intent with a graph-shape field: the executor task reached `done`. `min_event_counts={`<br>`  EventType.SANDBOX_BATCH_EDIT_APPLIED: 1,`<br>`  EventType.SANDBOX_CONFLICT_DETECTED: 1,`<br>`}` and add to `FocusedScenarioCase`: `min_role_task_status={("executor", "done"): 1}` (new field, asserted by the helper below). |

### A2. `expected_event_sequence` ordered-subsequence check (in the shared helper)

`_focused_scenario_contracts.py:39-42` calls `_assert_ordered_subsequence(scenario.expected_event_sequence, report.seen_event_types)`. Both `Scenario.expected_event_sequence` and `RunReport.seen_event_types` are deleted (§3, HANDOFF:218-220), and the real TaskCenter enforces role ordering (IMPL_PLAN §4 "the real TaskCenter enforces role ordering").

| | Before (`_focused_scenario_contracts.py:39-42`, `47-59`) | After |
|---|---|---|
| Ordered subsequence | `_assert_ordered_subsequence(`<br>`  scenario.expected_event_sequence,`<br>`  report.seen_event_types,`<br>`)` plus the whole `_assert_ordered_subsequence` function | **Delete** the call and the function. Ordering is enforced by the real loop/TaskCenter, not asserted via a mock event echo (IMPL_PLAN §4). |

### A3. `_assert_event_counts` (in the shared helper)

`_focused_scenario_contracts.py:43,62-73` iterates `case.min_event_counts` and `case.absent_events` over `Counter(event.type for event in report.events)`.

| | Before (`_focused_scenario_contracts.py:62-73`) | After |
|---|---|---|
| Count/absent over `report.events` | The `_assert_event_counts` loop over `case.min_event_counts` + `case.absent_events` | Keep this loop **only for the surviving `SANDBOX_*`/`MOCK_*` event types** (still in `report.events`). Add a new `_assert_role_task_shape(report, case)` that consumes the new graph-shape fields (`min_role_task_status`, etc.) using the shared helpers below. `absent_events` that named deleted lifecycle types (none in this file's single case) become graph-shape negatives (e.g. assert no recursive child goal exists). |

So for `test_focused_sandbox_scenarios.py` specifically the net change is small: drop `EXECUTOR_SUCCESS` from `min_event_counts`, add a `min_role_task_status` assertion, and the shared helper loses the ordered-subsequence machinery.

---

## File B — `test_correctness.py` (the file matching the task's full assertion fingerprint)

Note: `test_correctness.py` calls `assert_focused_scenario_report` **indirectly is NOT used here** — it asserts inline. The scenario it runs, `CorrectnessTesting`, has an `expected_event_sequence` (correctness_testing.py:102-123) consumed only by the focused-contract path; for this inline test it's relevant only because the migration deletes that attribute (it must be removed from the scenario too, §3 — but that's Phase 3, not this test).

### Per-assertion before/after table

| # | Assertion (current, with line numbers) | Depends on | Replacement (graph_summary-based) |
|---|---|---|---|
| B1 | `extra_hooks = (` `count_events(EventType.PLANNER_INVOKED, name="planner_invocations"),` `count_events(EventType.EVALUATOR_INVOKED, name="evaluator_invocations"),` `)` — **lines 44-47** | `PLANNER_INVOKED`, `EVALUATOR_INVOKED` (deleted) | **Delete the `extra_hooks` block** and stop passing `extra_hooks=` to `run_scenario_on_sweevo_image` (line 54). These hooks key off deleted events. Their *intent* (planner ran ≥1, evaluator ran ≥1) is asserted by B7/B8 below via `count_role_tasks`. |
| B2 | `assert report.task_center_status == "done", ...` — **lines 58-60** | none (real store) | **Keep unchanged.** |
| B3 | `assert report.passed_prompt_inspections, ...` — **lines 61-63** | none, but requires `_inspect_prompt` to be ported (HANDOFF:158-165: this PRESERVE helper is pending). | **Keep unchanged**, but gated on porting `_inspect_prompt`→`MOCK_PROMPT_INSPECTED` into `ScenarioLoopRunner` (Phase-1 remainder). Under the flag this is empty until that lands. |
| B4 | `assert report.passed_sandbox_checks, ...` — **lines 64-66** | none (re-homed via `ProbeContext`) | **Keep unchanged.** |
| B5 | `delegated = [goal for goal in report.graph_summary["goals"] if len(goal["iterations"]) >= 1 and any(ep["attempts"] for ep in goal["iterations"])]` — **lines 69-74** | already `graph_summary` | **Keep unchanged.** |
| B6 | `assert final_goal["status"] == "succeeded"` — **line 77** | already `graph_summary` | **Keep unchanged.** |
| B7 | (intent of B1 planner counter) `assert report.mutable_state_flags.get("count_planner_invocations", 0) >= 1` — **line 121** | deleted event hook | **Replace** with: `assert count_role_tasks(report.graph_summary, "planner") >= 1, report.graph_summary` |
| B8 | (intent of B1 evaluator counter) `assert report.mutable_state_flags.get("count_evaluator_invocations", 0) >= 1` — **line 122** | deleted event hook | **Replace** with: `assert count_role_tasks(report.graph_summary, "evaluator") >= 1, report.graph_summary` |
| B9 | Hook insertion-ordering block: `hook_names = [r.name for r in report.hook_results]` ... `planner_idx < evaluator_idx` — **lines 123-136** | deleted-event hook firing order | **Delete entirely.** This asserts that the `count_events` hooks (B1) fire in registration order. Those hooks are gone; there is no event-firing order to assert. Role *execution* order (planner before evaluator) is enforced by the real TaskCenter DAG (IMPL_PLAN §4) — not re-asserted, or asserted structurally via the deferral shape (B-extra below). |
| B10 | The inline scenario implicitly carries `CorrectnessTesting.expected_event_sequence` (correctness_testing.py:102-123) | deleted attribute | Not referenced inline in this test, but the attribute itself is deleted in Phase 3. No change needed *in this test file*. |

### B11 — the INVERTED `ask_advisor` transcript assertions (lines 168-198)

This is the assertion the task flags. Under the old `MockSquadRunner`, the synthetic `ask_advisor`/advisor-approval pair lived only on per-call `ExecutionMetadata` (runner.py `_approve_terminal`) and never reached the transcript, so the test asserts it does **not** leak. Under the event-source runner the advisor gate is cleared by a **real** scripted `ask_advisor` turn through the loop (HANDOFF:140-149, IMPL_PLAN §2 row "_approve_terminal"), so `ask_advisor` tool_use blocks and advisor `tool_result`s **DO** appear in `message.jsonl`. The polarity flips.

| | Before (`test_correctness.py:174-185`, the `ask_advisor` tool_use leak check) | After |
|---|---|---|
| ask_advisor tool_use | `leaked_tool_uses = [block for ... if block.get("type")=="tool_use" and str(block.get("name") or "")=="ask_advisor"]` then `assert not leaked_tool_uses, ...` | **Invert:** `advisor_tool_uses = [block for message in messages for block in message.get("content", []) if isinstance(block, dict) and block.get("type")=="tool_use" and str(block.get("name") or "")=="ask_advisor"]` then `assert advisor_tool_uses, "expected real ask_advisor turns in transcript"` |

| | Before (`test_correctness.py:186-198`, the advisor approval-result leak check) | After |
|---|---|---|
| advisor tool_result | `leaked_advisor_results = [block for ... if block.get("type")=="tool_result" and ...metadata.get("helper_role")=="advisor"]` then `assert not leaked_advisor_results, ...` | **Invert:** rename to `advisor_results` and `assert advisor_results, "expected advisor approval results in transcript"`. (The `helper_role=="advisor"` metadata predicate is unchanged — only the polarity flips from `not X` to `X`.) |

Also update the stale code comments at lines 168-173: they describe the deleted `_approve_terminal` synthetic-injection mechanism, which no longer exists.

### B12 — `_assert_message_jsonl_contains_sandbox_tools` body (lines 145-167)

| | Before | After |
|---|---|---|
| tool_use names subset | `assert {"write_file","read_file","edit_file","shell"}.issubset(tool_calls)` — **line 167** | **Keep unchanged.** These are real sandbox tool_uses now dispatched through the real loop (proven by `test_correctness_via_event_source.py:90-93` asserting the identical subset over `report.tool_calls`). Requires `_record_initial_messages`→`message.jsonl` to be ported (HANDOFF:160-165, PRESERVE-pending). |

### B-extra — structural replacement for the deleted EVALUATOR_FAILURE / PLANNER_DEFERS retry shape

`test_correctness.py` itself does not assert the retry/defer shape inline (it only counts invocations). But the scenario's whole point is eval-fail-retry → partial-defer → continuation. To preserve that coverage when the invocation hooks (B1/B7-9) are removed, mirror what the already-green `test_correctness_via_event_source.py:95-101` does:

```python
root = delegated[-1]
assert len(root["iterations"]) >= 2, root            # continuation iteration exists
iter1 = root["iterations"][0]
assert len(iter1["attempts"]) >= 2, iter1            # attempt 1 eval-failed, attempt 2 deferred
assert attempt_outcome(iter1["attempts"][0]) == ("failed", "evaluation_failed")   # EVALUATOR_FAILURE → attempt status+fail_reason
assert iter1["attempts"][-1]["deferred_goal_for_next_iteration"], iter1           # PLANNER_DEFERS_GOAL_PLAN
```

This is the §4.1 row mapping (`EVALUATOR_FAILURE` → attempt `status`+`fail_reason`; `PLANNER_DEFERS` vs `COMPLETES` → `deferred_goal_for_next_iteration`). Verify the exact `fail_reason` enum string (`"evaluation_failed"`) against `backend/src/task_center/attempt/state.py` before pinning it — `core/runner.py:108` emits `attempt.fail_reason.value`.

---

## Shared helpers needed (add to `tests/mock/_focused_scenario_contracts.py`)

Per IMPL_PLAN §4.1 / HANDOFF:189-191. Field shapes confirmed against `core/runner.py:_graph_summary` (90-142) and `db/stores/task_center_store.py:_serialize_task` (44-60): a task row has both `role` (canonical: planner/executor/verifier/evaluator/generator) and `agent_name` (may carry a suffix like `executor_1`) plus `status`; attempt has `status`, `fail_reason`, `deferred_goal_for_next_iteration`, `task_ids`, `tasks`; goal has `origin_kind`, `requested_by_task_id`, `status`, `final_outcome`. Use `role` (not `agent_name`) for role matching since `agent_name` may be suffixed (`_full_case_user_input.py:382-383` uses `startswith("executor_")` precisely because of this).

```python
def count_role_tasks(graph_summary: dict, role: str) -> int:
    """§4.1: *_INVOKED counts → count tasks of that role across all attempts."""
    return sum(
        1
        for goal in graph_summary["goals"]
        for iteration in goal["iterations"]
        for attempt in iteration["attempts"]
        for task in attempt["tasks"]
        if task.get("role") == role
    )


def attempt_outcome(attempt: dict) -> tuple[str, str | None]:
    """§4.1: EVALUATOR_SUCCESS/FAILURE → attempt status + fail_reason.
    Returns (status, fail_reason); fail_reason is None on success."""
    return (attempt["status"], attempt.get("fail_reason"))


def recursive_goals(graph_summary: dict, *, requested_by_task_id: str | None = None) -> list[dict]:
    """§4.1: RECURSIVE_GOAL_REQUESTED/COMPLETED → child goals with
    origin_kind=='task'. Optionally filter by the requesting task id.
    (Generalizes the existing _recursive_goal_count pattern in
    test_full_case_user_input.py:151 / test_full_stack_adversarial.py:190.)"""
    out = [
        goal
        for goal in graph_summary["goals"]
        if goal.get("origin_kind") == "task"
    ]
    if requested_by_task_id is not None:
        out = [g for g in out if g.get("requested_by_task_id") == requested_by_task_id]
    return out
```

Helper-usage summary:
- `count_role_tasks` → replaces every `count_events(*_INVOKED)`/`mutable_state_flags["count_*_invocations"]` assertion (B7, B8; A1's executor presence).
- `attempt_outcome` → replaces `EVALUATOR_SUCCESS`/`EVALUATOR_FAILURE` (B-extra). For per-task EXECUTOR/VERIFIER `SUCCESS`/`FAILURE` (§4.1 row 2), read `task["status"]` directly (done/failed) — add `min_role_task_status` to `FocusedScenarioCase` and a `_assert_role_task_shape` consumer (A1/A3); no separate helper required, or wrap as a thin `role_task_statuses(graph_summary, role) -> list[str]` if multiple files need it.
- `recursive_goals` → replaces `RECURSIVE_GOAL_REQUESTED`/`RECURSIVE_GOAL_COMPLETED` (not used by these two files, but required by the workflow fan-out for `test_full_case_user_input.py`/`test_full_stack_adversarial.py`, whose local `_recursive_goal_count` it subsumes).

## Cross-references / load-bearing facts
- `graph_summary` shape: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/core/runner.py:90-142`.
- Task-row fields: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/db/stores/task_center_store.py:44-60` (`_serialize_task`).
- §4.1 mapping + PRESERVE-pending helpers + inversion note: `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/docs/plans/mock_event_source_IMPL_PLAN.md:139-149` and `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/docs/plans/mock_event_source_HANDOFF.md:158-197`.
- Already-green ported analogue (the post-migration model for `test_correctness.py`): `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/contracts/test_correctness_via_event_source.py` (sets the flag, asserts via `graph_summary` + `attempt_outcome`-style shape at lines 95-101).
- `SANDBOX_BATCH_EDIT_APPLIED`/`SANDBOX_CONFLICT_DETECTED` are KEPT events (HANDOFF:213-216) — do NOT migrate those `min_event_counts` keys away; only `EXECUTOR_SUCCESS` is deleted.


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_high_concurrency_layerstack_overlay_occ.py

API Error: 500 Internal server error. This is a server-side issue, usually temporary — try again in a moment. If it persists, check https://status.claude.com.


### /Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox/project_build/test_project_build_shell_edit_lsp_three_parallel_agents.py

API Error: 500 Internal server error. This is a server-side issue, usually temporary — try again in a moment. If it persists, check https://status.claude.com.


## Test inventory (structured)


### contracts

- `contracts/test_advisor_gate_negative_path.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Instantiates MockSquadRunner only to call the _approve_terminal helper; exercises terminal gate via execute_tool_once dispatch directly, NOT the squad loop. No scenario run, no lifecycle events. Breaks under migration only if _approve_terminal helper is removed from MockSquadRunner (uses # noqa SLF001 on it).
- `contracts/test_advisor_gate_wiring.py` — **imports_only** | runs_scenario=False | refs_events=False | Pure structural check on make_submission_tools() pre_hooks (AdvisorApprovalPreHook presence/target). No scenario, no events, no runner. Fully unaffected by runner migration.
- `contracts/test_context_message_scenarios.py` — **graph_summary_or_store_state** | runs_scenario=False | refs_events=False | Imports Scenario subclasses (FullCaseUserInput/FullStackAdversarial/etc.) and calls executor_actions/planner_response/verifier_response directly on instances with hand-built ScenarioContext. Does NOT drive any runner. Asserts on returned tool names/args/deps, not events. Unaffected: scenario response methods survive the migration.
- `contracts/test_correctness_via_event_source.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs CorrectnessTesting via run_scenario_on_sweevo_image with EOS_MOCK_EVENT_SOURCE_RUNNER=1 (the NEW event-source path). Asserts via graph_summary / report.sandbox_checks / tool_calls / iteration shape, explicitly NOT lifecycle events (docstring: those migrate Phase 2). This IS the Phase 1 migration proof; depends on ported probe coroutines + ProbeContext.
- `contracts/test_runner_imports.py` — **event_dependent** | runs_scenario=True | refs_events=True | Mixed. Instantiates MockSquadRunner and tests its internals: _repo_dir, _probe_path, _inspect_prompt (all # noqa SLF001), absence of _instance. Also calls run_scenario (top-level export sig check) and does hasattr(scenario,'expected_event_sequence') (presence-only, not value assertion). The _inspect_prompt/_probe_path/_approve-style internals are exactly the imperative MockSquadRunner surface the migration ports off — these assertions break when the old runner is retired.
- `contracts/test_scenario_event_source_spike.py` — **event_dependent** | runs_scenario=False | refs_events=False | Phase 0 spike. Drives ScenarioEventSource (ToolCall/Turn/TurnScript) through real run_ephemeral_agent. Not a Scenario subclass (uses bare AgentDefinition), so runs_scenario=false. Asserts on StreamEvent / ToolExecutionCompletedEvent + query_context.tool_calls_used budget counts — engine StreamEvents, NOT the 14 lifecycle EventTypes. Validates the migration seam itself; should stay green (it is the target path).
- `contracts/test_scenario_loop_runner_planner_submit.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Defines a ScenarioBase subclass (_PlannerSubmitProof) and drives it via run_scenario with EOS_MOCK_EVENT_SOURCE_RUNNER=1 through run_pipeline -> real loop. Asserts task_center_status=='done' + graph_summary task statuses (real store), explicitly not lifecycle events. Phase 1 staged proof of the new ScenarioLoopRunner + extras['runtime_config'] injection; migration-stable.
- `contracts/test_scenario_suite_imports.py` — **event_dependent** | runs_scenario=False | refs_events=True | Imports EventType and asserts every SCENARIO_REGISTRY scenario declares a non-empty expected_event_sequence of EventType members. Pure structural/declaration check on scenario classes (no run, no MockSquadRunner). Matches the event_dependent literal criterion (imports EventType + asserts on expected_event_sequence) but tests static declarations, not emitted events, so the runner migration alone does not break it unless expected_event_sequence is removed from the scenario protocol in Phase 2.

### task_center

- `task_center/test_correctness.py` — **event_dependent** | runs_scenario=True | refs_events=True | Runs CorrectnessTesting via run_scenario_on_sweevo_image; asserts hook results from count_events(PLANNER_INVOKED) and count_events(EVALUATOR_INVOKED) plus their ordering. Migration to the real engine loop must keep emitting PLANNER_INVOKED/EVALUATOR_INVOKED lifecycle events and the on-disk audit/message.jsonl tree (no leaked synthetic ask_advisor tool_use), or counts/ordering assertions break.
- `task_center/test_correctness_via_live_e2e.py` — **event_dependent** | runs_scenario=True | refs_events=True | Same CorrectnessTesting scenario but through the generic run_scenario entry point with count_events(PLANNER_INVOKED/EVALUATOR_INVOKED) extra hooks. Migration must preserve PLANNER_INVOKED/EVALUATOR_INVOKED emission for the hooks to register; otherwise event counts go stale. (Skips without DB/Daytona.)
- `task_center/test_deferred_parent_planner_terminal_routing.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs pipeline.deferred_parent_planner_terminal_routing scenario but asserts purely on report.launches (planner roles), tool_call counts (submit_plan_defers_goal/closes_goal), graph_summary deferral flags, and active_terminals/terminal-catalog from message.jsonl. No EventType import, no min_event_counts/expected_event_sequence/FocusedScenarioCase. State-based, not event-based; would only break if the migration changed planner terminal routing or message.jsonl metadata, not from lifecycle-event changes.
- `task_center/test_focused_scenarios.py` — **event_dependent** | runs_scenario=True | refs_events=True | Parametrizes 19 FocusedScenarioCase entries with min_event_counts over PLANNER_INVOKED/COMPLETES/DEFERS, EXECUTOR_INVOKED/SUCCESS/FAILURE, EVALUATOR_INVOKED/SUCCESS/FAILURE (plus non-lifecycle TOOL_CALL_ERROR); runs each via run_scenario_on_sweevo_image and validates through assert_focused_scenario_report. Most event-sensitive file in the dir; migration must reproduce exact min event counts per scenario.
- `task_center/test_full_case_user_input.py` — **event_dependent** | runs_scenario=True | refs_events=True | Runs FullCaseUserInput via run_scenario_on_sweevo_image; asserts PLANNER_DEFERS_GOAL_PLAN, VERIFIER_FAILURE/SUCCESS, EVALUATOR_INVOKED, RECURSIVE_GOAL_REQUESTED/COMPLETED plus strict event ORDERING (RECURSIVE_GOAL_COMPLETED before VERIFIER_SUCCESS, etc.). Doubly exposed: also asserts SANDBOX_* monitor events (LayerStack/OCC/overlay/conflict) and live Daytona /testbed workspace tool state. Exercises the heavy PreparedToolScriptEngine path that Phase 2 must port.
- `task_center/test_initial_messages_capture.py` — **event_dependent** | runs_scenario=True | refs_events=True | Runs pipeline.initial_messages_capture via run_scenario_on_sweevo_image; uses Counter(event.type) over PLANNER_INVOKED/DEFERS/COMPLETES and EVALUATOR_FAILURE/SUCCESS, so event_dependent. But its core purpose is initial-message row SHAPE (record_initial_messages: system + <context> + <Task Guidance> + skill rows, AC#15 byte-for-byte terminal-catalog match). Migration changes how the runner records launch-time initial messages, so this is exposed both via event counts and via the initial-message recording seam.
- `task_center/test_stores.py` — **graph_summary_or_store_state** | runs_scenario=False | refs_events=False | Pure TaskCenterStoreBundle / create_per_test_task_center_stores DB-isolation integration test (per-schema isolation, cross-bundle non-collision, engine pool ownership). No scenario, no MockSquadRunner, no EventType. Runner-migration agnostic at the store layer (not sandbox/IWS, so not pure_sandbox_runner_agnostic). Only skips without a DB URL.

### sandbox/background_tool

- `sandbox/background_tool/_background_shell_invariants.py` — **other** | runs_scenario=True | refs_events=False | Shared include (not a test) that drives scenarios: run_background_shell_scenario() resolves SCENARIO_REGISTRY[name] and calls run_scenario_on_sweevo_image, then asserts report.task_center_status=='done', prompt/sandbox checks, and OCC/perf artifacts. No lifecycle events. Behavior-preserving-port sensitive: every scenario test below routes through this helper, so a Phase 2 heavy-probe port (background_shell_probe) that drops emitted tool calls / summary breaks all of them at once.
- `sandbox/background_tool/test_background_engine_restart_no_lease_leak.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_engine_restart_no_lease_leak' via run_background_shell_scenario; asserts on ENGINE_RESTART_SUMMARY probe JSON (inflight/abandoned/recovery) + perf artifacts. No events. Migration-sensitive only if the heavy background_shell_probe port fails to preserve emitted tool calls; not structurally broken.
- `sandbox/background_tool/test_background_exit_iws_drains_agent_tasks.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_exit_iws_drains_agent_tasks'; configures IWS then asserts EXIT_IWS_DRAIN_SUMMARY probe JSON (blocked enter/exit, cancel_bg, eviction phases). No lifecycle events. Exercises IWS lifecycle but only through the scenario probe, so it is port-sensitive, not pure-sandbox-agnostic.
- `sandbox/background_tool/test_background_heartbeat_loss_reaps_only_stale_bg.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_heartbeat_loss_reaps_only_stale_bg'; asserts HEARTBEAT_LOSS_SUMMARY probe JSON (heartbeat counts, stale reap, protected published) + perf artifacts. No events. Behavior-preserving-port sensitive on the heavy probe.
- `sandbox/background_tool/test_background_many_small_writes_do_not_starve_dispatcher.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_many_small_writes_do_not_starve_dispatcher'; asserts MANY_SMALL_WRITES_SUMMARY probe JSON (bg success counts, fg p95<5s) + read_file/write_file p95 from perf report. No events. Heavy-probe port sensitive.
- `sandbox/background_tool/test_background_mixed_fg_bg_same_path_conflict.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_mixed_fg_bg_same_path_conflict'; asserts MIXED_CONFLICT_SUMMARY probe JSON (foreground_won, background OCC abort status, mount/write timings). No events. OCC behavior asserted via probe summary, not store state; port-sensitive on the heavy probe.
- `sandbox/background_tool/test_background_mixed_op_concurrent.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_mixed_op_concurrent'; asserts MIXED_OP_CONCURRENT_SUMMARY probe JSON (heterogeneous ops terminal, OCC overlap winner/losers, disjoint writers land). No lifecycle events. Explicitly notes it reuses run_background_shell_scenario for the real OCC publish path behind BackgroundTaskSupervisor; behavior-preserving-port sensitive.
- `sandbox/background_tool/test_background_shell_cancel_during_maintenance.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | No scenario. Instantiates InFlightInvocationRegistry directly with raw asyncio tasks and asserts count_by_agent ignores the foreground maintenance invocation. Touches no runner/squad path; unaffected by the MockSquadRunner->event-source migration.
- `sandbox/background_tool/test_background_shell_cancel.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | No scenario. Drives daemon builtin_operations.cancel against an InFlightInvocationRegistry (monkeypatched get_in_flight_registry) and asserts cancel waits for cleanup. Pure sandbox-daemon internals; unaffected by the migration.
- `sandbox/background_tool/test_background_shell_engine_kill.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | No scenario. Exercises InFlightInvocationRegistry TTL reaper (reap_stale, ttl_reaped_total metric) on a raw asyncio task. Pure sandbox internals, no runner path; unaffected by the migration.
- `sandbox/background_tool/test_background_shell_executor_exhaustion.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_shell_exhaustion' via run_scenario_on_sweevo_image directly (not the helper); asserts report.task_center_status=='done' then EXHAUSTION_SUMMARY probe JSON (error/cancel counts, post-exhaustion read<1s). No events. Heavy-probe port sensitive.
- `sandbox/background_tool/test_background_shell_golden.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_shell_golden' via run_scenario_on_sweevo_image; asserts report status done then GOLDEN_SUMMARY probe JSON (3 background shell launches exit 0). No lifecycle events. This is exactly the heavy probe the Phase 2 port targets; breaks only if the port drops the probe's emitted shell ToolCalls/summary.
- `sandbox/background_tool/test_background_shell_interleave.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_shell_interleave' via run_scenario_on_sweevo_image; asserts INTERLEAVE_SUMMARY probe JSON (foreground p95 mount<5s with background lease held). No events. Heavy-probe port sensitive.
- `sandbox/background_tool/test_background_shell_late_cancel_race.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_shell_late_cancel_race' via run_scenario_on_sweevo_image; asserts LATE_CANCEL_SUMMARY probe JSON (shell exit 0, status ok, stdout marker). No lifecycle events. Heavy-probe port sensitive.
- `sandbox/background_tool/test_background_shell_partial_write_cancel.py` — **other** | runs_scenario=True | refs_events=False | Drives scenario 'sandbox.background_shell_partial_write_cancel' via run_scenario_on_sweevo_image; asserts PARTIAL_WRITE_SUMMARY probe JSON (dd not completed before cancel, file not tracked in workspace OCC after cancel). No events; OCC asserted via probe summary not store state. Heavy-probe port sensitive.

### sandbox/ephemeral_workspace + sandbox/plugin + sandbox/capacity + sandbox/full_stack + environments

- `environments/test_sweevo_image_environment_lock.py` — **other** | runs_scenario=False | refs_events=False | Pure unit test of sweevo_image.fixtures._lock_slug / _acquire/_release_sweevo_session_lock filesystem locking. No Scenario, no runner, no EventTypes. Fully runner-agnostic; unaffected by the MockSquadRunner -> ScenarioEventSource migration.
- `sandbox/capacity/test_capacity_scenario_packs.py` — **other** | runs_scenario=False | refs_events=False | Offline catalog conformance. Builds ScenarioContext manually and calls SCENARIO_REGISTRY[name]().planner_response(ctx) directly to inspect planner-tool args/DAG shape; never invokes run_scenario_on_sweevo_image/build_scenario_config/MockSquadRunner. No lifecycle EventTypes. Runner-agnostic; unaffected by migration.
- `sandbox/capacity/test_full_system_capacity_matrix.py` — **event_dependent** | runs_scenario=True | refs_events=True | Runs capacity.full_system_capacity_matrix via run_scenario_on_sweevo_image (-> core.runner.run_scenario -> MockSquadRunner runner_factory). Asserts on report.events/seen including PLANNER_DEFERS_GOAL_PLAN, VERIFIER_FAILURE, RECURSIVE_GOAL_REQUESTED/COMPLETED, FULL_STACK_SCRIPT_COMPLETED, plus count_events(VERIFIER_FAILURE) and assert_recursive_goal_closed_before_parent_guard hooks. Depends on heavy probes + PreparedToolScript engine still running through the old runner; Phase 2 must preserve event emission + tool/event capacity counts under the event-source loop.
- `sandbox/ephemeral_workspace/_ephemeral_workspace_invariants.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Shared include that DRIVES scenarios: run_ephemeral_scenario() calls run_scenario_on_sweevo_image for the 5 ephemeral test modules. Asserts report.task_center_status=='done', prompt/sandbox checks, and sandbox_events.jsonl + performance_report.json overlay/timing artifacts (no lifecycle EventTypes). Migration risk is indirect: the ephemeral_workspace_probe heavy probe + its emitted sandbox events/timings must survive the event-source loop for these assertions to hold.
- `sandbox/ephemeral_workspace/test_ephemeral_all_verbs_publish_and_cleanup.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.ephemeral_workspace_all_verbs through run_ephemeral_scenario -> runner. Asserts ephemeral_workspace_probe ALL_VERBS_SUMMARY JSON + mutation_source sandbox events + overlay timings. No lifecycle EventTypes. Breaks if the heavy ephemeral_workspace_probe (write/read/edit/grep/glob/shell verbs) is not ported faithfully (byte-identical call_tool bridge or async-gen rewrite) in Phase 2.
- `sandbox/ephemeral_workspace/test_ephemeral_cancellation_drops_partial_upperdir.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.ephemeral_workspace_cancellation through runner. Asserts CANCELLATION_SUMMARY (cancelled, partial_read_is_error) + sandbox_tool_cancelled events keyed by background_task_id/invocation_id. No lifecycle EventTypes. Exercises background-task cancellation inside the probe; Phase 2 bridge must preserve background cancellation semantics and the invocation_id correlation.
- `sandbox/ephemeral_workspace/test_ephemeral_concurrent_disjoint_writes_coalesce.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.ephemeral_workspace_concurrent_writes through runner. Asserts CONCURRENT_WRITES_SUMMARY (8 typed api_write + 2 shell overlay_capture writes coalesce) + mutation_source events + overlay timings. No lifecycle EventTypes. Depends on probe issuing concurrent tool calls; the asyncio.Queue+Future call_tool bridge must support concurrent in-flight ToolCalls or the rewrite must keep concurrency.
- `sandbox/ephemeral_workspace/test_ephemeral_lowerdir_disk_is_o1_under_100_calls.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.ephemeral_workspace_o1_disk through runner. Asserts O1_DISK_SUMMARY (100 operations, manifest delta, warm p95 tool budgets read/write/edit) + warm tool budgets from performance_report. No lifecycle EventTypes. Performance-sensitive: Phase 2 bridge must not add per-call latency that pushes warm p95 over the 500/1000ms budgets.
- `sandbox/ephemeral_workspace/test_ephemeral_outside_workspace_policy.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.ephemeral_workspace_policy through runner. Asserts POLICY_SUMMARY denied-host-path results (forbidden_host_path error_kind, has_mount_timing) + 'forbidden_host_path' in sandbox_events. No lifecycle EventTypes. Depends on probe calling tools with allow_error to capture forbidden-path errors; the call_tool bridge must preserve allow_error/error-result propagation.
- `sandbox/ephemeral_workspace/test_ephemeral_same_path_conflict_and_retry.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.ephemeral_workspace_same_path_conflict through runner. Asserts OCC conflict/retry summary (aborted_overlap/aborted_version/failed/rejected statuses, retry_records, final content). No lifecycle EventTypes. Exercises OCC conflict handling inside probe; bridge must surface tool error results (conflict reasons) back to the probe body for the retry logic.
- `sandbox/full_stack/test_full_stack_adversarial.py` — **event_dependent** | runs_scenario=True | refs_events=True | Heaviest event-dependent case. Runs FullStackAdversarial via run_scenario_on_sweevo_image with count_events(VERIFIER_FAILURE) + assert_recursive_goal_closed_before_parent_guard hooks. Asserts PLANNER_COMPLETES_GOAL_PLAN, PLANNER_DEFERS_GOAL_PLAN, VERIFIER_FAILURE, VERIFIER_SUCCESS, RECURSIVE_GOAL_REQUESTED/COMPLETED, EVALUATOR_INVOKED/SUCCESS, FULL_STACK_SCRIPT_COMPLETED AND strict event-ordering (RECURSIVE_GOAL_COMPLETED before VERIFIER_SUCCESS@recursive_return before EVALUATOR_INVOKED). Also asserts full lsp.* tool matrix in message.jsonl, expected edit_file is_error tool_result, and final sandbox-state JSON. This is the primary Phase 2 acceptance gate: the PreparedToolScript engine + heavy full-stack probe must emit identical lifecycle events in identical order through the event-source loop, and the call_tool bridge must preserve tool_use deltas + intentional error results.
- `sandbox/plugin/_plugin_invariants.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Shared include that DRIVES scenarios: run_plugin_scenario() calls run_scenario_on_sweevo_image for the 6 plugin test modules. Asserts task_center_status=='done', prompt/sandbox checks, plugin_summary JSON, and O(1) workspace resource snapshots from sandbox_events/performance_report (no lifecycle EventTypes). Migration risk is indirect via the plugin_workspace_probe heavy probe + emitted sandbox events/timings.
- `sandbox/plugin/test_plugin_blocked_in_open_isolated_workspace.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.plugin_iws_policy through run_plugin_scenario -> runner, after configure_isolated_workspace_for_background. Asserts IWS_POLICY_SUMMARY (enter/exit ok, plugin status+lsp blocked with forbidden_in_isolated_workspace). No lifecycle EventTypes. Depends on plugin probe driving enter/exit IWS + blocked plugin dispatch; bridge must preserve IWS lifecycle calls and blocked-error results.
- `sandbox/plugin/test_plugin_intent_mislabel_fails_fast.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.plugin_intent_contract through runner. Asserts INTENT_CONTRACT_SUMMARY (missing-intent TypeError, PluginOpRegistrationError, read_only->service vs write_allowed->overlay, overlay_calls). No lifecycle EventTypes. Probe asserts plugin intent/registration error contracts; bridge must propagate raised exceptions / error results from call_tool back to the probe body.
- `sandbox/plugin/test_plugin_read_only_lsp_refresh_without_publish.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.plugin_read_only_lsp_refresh through runner. Asserts READ_ONLY_LSP_REFRESH_SUMMARY (0 read-only publishes, diagnostics after edit, warm_lsp_p95<=500ms) + plugin O(1) artifacts. No lifecycle EventTypes. LSP-refresh probe; performance-sensitive (warm lsp p95) so bridge per-call overhead matters.
- `sandbox/plugin/test_plugin_service_survives_peer_publish_and_evict.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.plugin_service_evict through runner. Asserts SERVICE_EVICT_SUMMARY (5 peer publishes, refresh/remount, eviction+restart, post-evict warm lsp, warm_lsp_p95<=500ms) + plugin O(1) artifacts. No lifecycle EventTypes. Stateful long-lived LSP-service probe with peer-publish interleaving; bridge must preserve ordering/state across many call_tool round-trips.
- `sandbox/plugin/test_plugin_setup_network_failure_is_actionable.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.plugin_setup_failure through runner. Asserts SETUP_FAILURE_SUMMARY (install-step plugin_setup_network_failure with details + retry success, dispatch_calls order). No lifecycle EventTypes. Probe asserts actionable setup-failure error metadata + retry; bridge must propagate structured tool error metadata back to the probe.
- `sandbox/plugin/test_plugin_write_allowed_apply_workspace_edit_publishes.py` — **other** | runs_scenario=True | refs_events=False | Runs sandbox.plugin_write_allowed_publish through runner. Asserts WRITE_ALLOWED_PUBLISH_SUMMARY (apply_workspace_edit success, manifest version, changed_paths target.py, overlay timing keys, command_overlay_run_dir_delta<=0) + plugin O(1) artifacts. No lifecycle EventTypes. WorkspaceEdit publish path through overlay/OCC; bridge must preserve write-publish tool result fields the probe reads.

### sandbox/layer_stack_occ_overlay + sandbox/project_build

- `sandbox/layer_stack_occ_overlay/test_auto_squash_commit_resume.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.auto_squash_commit_resume via run_scenario_on_sweevo_image; imports EventType but asserts ONLY SANDBOX_* events (LAYERS_SQUASHED/OCC_CHANGESET_RECEIVED/OCC_CHANGES_COMMITTED) plus task_center_status=='done' and perf timing. No lifecycle events. Migration-exposed only via the heavy probe port reaching status==done, not via the event-source seam.
- `sandbox/layer_stack_occ_overlay/test_commit_to_workspace_materializes_git.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.auto_squash_commit_resume with commit_to_workspace=True via run_scenario_on_sweevo_image; imports no EventType; asserts task_center_status=='done' and git materialization through shell. Breaks only if the auto_squash heavy probe port stops reaching done.
- `sandbox/layer_stack_occ_overlay/test_focused_sandbox_scenarios.py` — **event_dependent** | runs_scenario=True | refs_events=True | Uses FocusedScenarioCase with min_event_counts including EventType.EXECUTOR_SUCCESS (a lifecycle event) over a scenario run via run_scenario_on_sweevo_image. Directly asserts lifecycle event counts the runner migration must keep emitting.
- `sandbox/layer_stack_occ_overlay/test_heavy_io_zoned_concurrent.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.heavy_io_zoned_concurrent via run_scenario_on_sweevo_image; imports EventType but only uses SANDBOX_RESOURCE_SNAPSHOT; asserts status=='done', tool_calls, sandbox_checks, perf and resource snapshots. Exposed via heavy probe port + reaching done, not lifecycle-event assertions.
- `sandbox/layer_stack_occ_overlay/test_high_concurrency_layerstack_overlay_occ.py` — **event_dependent** | runs_scenario=True | refs_events=True | Asserts counts[EventType.EXECUTOR_SUCCESS] and walks SCENARIO_REGISTRY[...].expected_event_sequence against report.seen_event_types (plus SANDBOX_* counts). Lifecycle event ordering/counts must survive the runner migration; the heavy concurrency probe must also port.
- `sandbox/layer_stack_occ_overlay/test_shell_concurrency_latency_matrix_diagnostic.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Drives sandbox_api.shell directly against the `workspace` fixture (no scenario, no MockSquadRunner); env-gated diagnostic (EOS_RUN_SHELL_LATENCY_MATRIX). Unaffected by the runner migration.
- `sandbox/project_build/test_complex_project_build_fixtures.py` — **other** | runs_scenario=False | refs_events=False | Host-side fixture/AST/anchor validation plus direct unit tests of heavy-probe internals via monkeypatch (complex_probe._shell, _lsp_semantic_call) and direct calls to _shell_cat_with_retry / _assert_lsp_diagnostics using SimpleNamespace/_ProbeCtx fakes. No sandbox_api/daemon/_iws_rpc fixtures and no scenario run, so NOT pure_sandbox. MOST Phase-2-exposed file: breaks if the probe port renames/relocates _shell, _lsp_semantic_call, _shell_cat_with_retry, _assert_lsp_diagnostics, ShellEditLspStats, or _compute_*_amp_pairs.
- `sandbox/project_build/test_complex_project_build_full.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build via run_scenario_on_sweevo_image; delegates assertions to _project_build_contracts which asserts only SANDBOX_* events + status. Depends on Phase 2 complex_project_build heavy probe port completing the loop to status==done.
- `sandbox/project_build/test_complex_project_build_grep_glob_full.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build_grep_glob via run_scenario_on_sweevo_image; assertions via _project_build_contracts (SANDBOX_* events + status only). Depends on the grep_glob heavy probe port, not on lifecycle-event assertions.
- `sandbox/project_build/test_complex_project_build_grep_glob_smoke.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build_grep_glob_smoke via run_scenario_on_sweevo_image; smoke contract (SANDBOX_* events + status). Depends on the grep_glob probe port reaching done.
- `sandbox/project_build/test_complex_project_build_shell_edit_lsp_full.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build_shell_edit_lsp via run_scenario_on_sweevo_image; assertions via _project_build_contracts (SANDBOX_* events + status only). Depends on the shell_edit_lsp heavy probe port reaching done.
- `sandbox/project_build/test_complex_project_build_shell_edit_lsp_smoke.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build_shell_edit_lsp_smoke via run_scenario_on_sweevo_image; smoke contract (SANDBOX_* events + status). Depends on the shell_edit_lsp probe port reaching done.
- `sandbox/project_build/test_complex_project_build_smoke.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build_smoke via run_scenario_on_sweevo_image; smoke contract via _project_build_contracts (SANDBOX_* events + status). Depends on the complex_project_build probe port reaching done.
- `sandbox/project_build/test_project_build_full_o1_disk_budget.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build via run_scenario_on_sweevo_image then asserts O(1) disk-budget behavior from the report. Goes THROUGH the scenario runner (not pure_sandbox). Depends on the complex_project_build heavy probe port reaching done.
- `sandbox/project_build/test_project_build_grep_glob_low_latency_after_many_edits.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build_grep_glob via run_scenario_on_sweevo_image then asserts grep/glob latency after many edits. Through the scenario runner (not pure_sandbox). Depends on the grep_glob heavy probe port reaching done.
- `sandbox/project_build/test_project_build_shell_edit_lsp_remount_not_restart.py` — **graph_summary_or_store_state** | runs_scenario=True | refs_events=False | Runs sandbox.complex_project_build_shell_edit_lsp via run_scenario_on_sweevo_image then asserts remount-not-restart behavior. Through the scenario runner (not pure_sandbox). Depends on the shell_edit_lsp heavy probe port reaching done.
- `sandbox/project_build/test_project_build_shell_edit_lsp_three_parallel_agents.py` — **event_dependent** | runs_scenario=True | refs_events=True | Defines a ScenarioBase subclass with expected_event_sequence over PLANNER_INVOKED/PLANNER_COMPLETES_GOAL_PLAN/EXECUTOR_INVOKED/EXECUTOR_SUCCESS/EVALUATOR_INVOKED/EVALUATOR_SUCCESS and asserts counts[EventType.EXECUTOR_SUCCESS]==2, runs via run_scenario_on_sweevo_image. Lifecycle event sequence/counts must survive the runner migration; also exercises the shell_edit_lsp shared-bootstrap heavy probe (Phase 2).

### sandbox/isolated_workspace (all subdirs — expect most are runner-agnostic)

- `sandbox/isolated_workspace/behavior_upgrade/test_iws_all_typed_verbs_same_session.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Drives daemon RPCs via _iws_rpc (enter/shell/read/write/edit/grep/glob/exit); no squad runner or lifecycle EventTypes.
- `sandbox/isolated_workspace/concurrency/test_3_workspaces_same_port_discarded_on_teardown.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Concurrent IWS handles via daemon RPC; sandbox internals only.
- `sandbox/isolated_workspace/concurrency/test_5_concurrent_audit_events_complete.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Asserts on sandbox-side IWS audit events (sandbox_isolated_workspace_*), not the 14 squad EventTypes; fixture-driven.
- `sandbox/isolated_workspace/concurrency/test_5_concurrent_cgroup_memory_isolated.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | cgroup memory isolation via daemon RPC; sandbox internals only.
- `sandbox/isolated_workspace/concurrency/test_5_concurrent_fs_no_interference.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Concurrent filesystem isolation via _iws_rpc; runner-agnostic.
- `sandbox/isolated_workspace/concurrency/test_5_concurrent_network_no_interference.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Concurrent network isolation via daemon RPC; sandbox internals only.
- `sandbox/isolated_workspace/concurrency/test_concurrent_default_and_isolated_in_same_agent.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Default+isolated mode coexistence via daemon RPC; runner-agnostic.
- `sandbox/isolated_workspace/concurrency/test_concurrent_enter_no_ip_double_allocation.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | IP-pool allocation under concurrent enter via daemon RPC; sandbox internals only.
- `sandbox/isolated_workspace/concurrency/test_init_complete_blocks_enter_during_startup_gc.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Startup-GC gating of enter via daemon RPC; runner-agnostic.
- `sandbox/isolated_workspace/concurrency/test_iws_parallel_conflicting_upperdir_writes.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Parallel upperdir writes via daemon RPC; overlay internals only.
- `sandbox/isolated_workspace/concurrency/test_map_lock_serializes_enter_exit_only.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Lifecycle-map lock semantics via daemon RPC; sandbox internals only.
- `sandbox/isolated_workspace/concurrency/test_re_enter_after_exit_gets_fresh_handle.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Handle freshness across re-enter via daemon RPC; runner-agnostic.
- `sandbox/isolated_workspace/concurrency/test_same_agent_tool_calls_can_overlap.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Overlapping tool calls per agent via daemon RPC; sandbox internals only.
- `sandbox/isolated_workspace/concurrency/test_two_agents_same_port.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Two agents binding same port via daemon RPC; network isolation, runner-agnostic.
- `sandbox/isolated_workspace/failure_modes/test_dns_helper_fails_does_not_strand_handle.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | DNS-helper failure injection via daemon env + RPC; sandbox internals only.
- `sandbox/isolated_workspace/failure_modes/test_holder_refuses_sigterm_sigkill_fallback.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | ns-holder kill fallback via daemon RPC; sandbox internals only.
- `sandbox/isolated_workspace/failure_modes/test_ns_holder_dies_before_ready.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Holder-crash injection via daemon RPC; runner-agnostic.
- `sandbox/isolated_workspace/failure_modes/test_overlay_mount_fails.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Overlay-mount failure injection via daemon RPC; overlay internals only.
- `sandbox/isolated_workspace/failure_modes/test_setup_timeout_wedge.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Setup-timeout injection via daemon env + RPC; sandbox internals only.
- `sandbox/isolated_workspace/failure_modes/test_veth_install_fails_releases_lease.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | veth-install failure + lease release via daemon RPC; runner-agnostic.
- `sandbox/isolated_workspace/failure_modes/test_write_file_streams_large_body_without_argv_e2big.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Large write_file streaming via daemon RPC (argv E2BIG guard); sandbox internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_daemon_restart_reaps_orphan_cgroup.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Daemon-restart GC of orphan cgroup via raw_exec + RPC; sandbox internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_daemon_restart_reaps_orphan_netns.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Daemon-restart GC of orphan netns; sandbox internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_daemon_restart_reaps_orphan_scratch.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Daemon-restart GC of orphan scratch dir; runner-agnostic.
- `sandbox/isolated_workspace/gc_and_persistence/test_daemon_restart_reaps_orphan_veth.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Daemon-restart GC of orphan veth; sandbox internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_daemon_restart_reconciles_ip_pool.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Daemon-restart IP-pool reconciliation via RPC; sandbox internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_daemon_restart_releases_orphan_lease.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Daemon-restart snapshot-lease release; runner-agnostic.
- `sandbox/isolated_workspace/gc_and_persistence/test_iws_daemon_restart_mid_parallel_calls.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Daemon restart mid parallel tool calls via RPC; sandbox internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_lowerdir_bytes_and_inodes_constant_as_n_grows.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | lowerdir O(1) bytes/inodes via RPC + raw_exec; overlay internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_lowerdir_disk_usage_is_o1.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | lowerdir O(1) disk usage via RPC; overlay internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_lowerdir_layer_paths_shared_across_concurrent_handles.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Shared lowerdir layer paths across handles via RPC; overlay internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_manager_json_roundtrip.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | IWS manager-state JSON persistence roundtrip; sandbox internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_manager_json_schema_mismatch_treated_as_empty.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Manager-state schema-mismatch handling; sandbox internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_upperdir_discarded_on_abnormal_exit_daemon_kill.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | upperdir discard on daemon-kill via RPC + raw_exec; overlay internals only.
- `sandbox/isolated_workspace/gc_and_persistence/test_upperdir_fully_discarded_on_normal_exit.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | upperdir discard on normal exit via RPC; overlay internals only.
- `sandbox/isolated_workspace/happy_path/test_enter_then_shell_then_exit.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Golden enter/shell/exit via _iws_rpc; asserts sandbox-side IWS audit sequence (not squad EventTypes).
- `sandbox/isolated_workspace/happy_path/test_lowerdir_visible_inside_mntns.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | lowerdir visibility inside mount-ns via RPC; sandbox internals only.
- `sandbox/isolated_workspace/happy_path/test_mount_overlay_backstop.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Overlay-mount backstop via RPC; overlay internals only.
- `sandbox/isolated_workspace/happy_path/test_server_survives_tool_call_boundary.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | ns-holder server survives tool-call boundary via RPC; sandbox internals only.
- `sandbox/isolated_workspace/happy_path/test_status_reports_open_handle.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | status RPC reports open handle; sandbox internals only.
- `sandbox/isolated_workspace/isolation/test_cross_agent_unreachable.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Cross-agent network unreachability via RPC (network reachability probes, not squad probes); runner-agnostic.
- `sandbox/isolated_workspace/isolation/test_default_mode_unaffected_during_pinned.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Default-mode behavior during pinned isolated cycle via RPC; sandbox internals only.
- `sandbox/isolated_workspace/isolation/test_full_cycle_never_calls_occ.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Verifies layer_metrics manifest_version unchanged across isolated cycle via call_daemon_api; asserts IWS audit sequence not squad EventTypes.
- `sandbox/isolated_workspace/isolation/test_iws_peer_publish_pin_and_refresh_boundary.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Peer-publish pin/refresh boundary via daemon RPC; OCC/overlay internals only.
- `sandbox/isolated_workspace/isolation/test_lowerdir_pinned_against_peer_publish.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | lowerdir snapshot pin vs peer publish via RPC; overlay internals only.
- `sandbox/isolated_workspace/isolation/test_upperdir_discarded_on_exit.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | upperdir discard on isolated exit via RPC; overlay internals only.
- `sandbox/isolated_workspace/network/test_arbitrary_egress_via_masquerade.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | MASQUERADE egress via RPC shell; network internals only.
- `sandbox/isolated_workspace/network/test_daemon_host_introspection_allowed.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Daemon-host introspection reachability via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_dns_fallback_survives_tool_call_boundary.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | DNS fallback persistence across tool calls via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_dns_routable_resolver.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Routable DNS resolver via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_dns_symlinked_resolv_conf.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | symlinked resolv.conf DNS handling via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_dns_systemd_resolved_fallback.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | systemd-resolved DNS fallback via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_external_inbound_icmp_rejected.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | External inbound ICMP rejection via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_external_inbound_tcp_rejected.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | External inbound TCP rejection via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_external_inbound_udp_rejected.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | External inbound UDP rejection via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_imds_dropped.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | IMDS endpoint drop rule via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_imds_rule_reinstalled_on_boot.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | IMDS drop rule reinstall on boot via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_masquerade_rule_reinstalled_on_boot.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | MASQUERADE rule reinstall on boot via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_no_ipv6_default_route.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | No IPv6 default route inside ns via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_port_isolation_flag_present.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Port-isolation flag presence via RPC; network internals only.
- `sandbox/isolated_workspace/network/test_rfc1918_egress_drop_opt_in.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | RFC1918 egress drop opt-in via daemon env + RPC; network internals only.
- `sandbox/isolated_workspace/performance/test_baseline_collection_invariant.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Tier 9 latency-baseline invariant via iws_latency_baseline fixture + LatencyBudget; IWS audit total_ms/phases_ms, not squad EventTypes.
- `sandbox/isolated_workspace/performance/test_enter_phase_breakdown_complete.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | enter phase-breakdown completeness via RPC + IWS audit phases_ms; sandbox internals only.
- `sandbox/isolated_workspace/performance/test_exit_phase_breakdown_complete.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | exit phase-breakdown completeness via RPC + IWS audit phases_ms; sandbox internals only.
- `sandbox/isolated_workspace/performance/test_iws_parallelism_and_phase_budget.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Parallelism + phase-budget via RPC + latency helpers; sandbox internals only.
- `sandbox/isolated_workspace/performance/test_latency_regression_band.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Latency ratio-band regression via baseline fixture + IWS audit total_ms; runner-agnostic.
- `sandbox/isolated_workspace/performance/test_per_op_latency_within_baseline.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Per-op latency vs baseline via RPC + LatencyBudget; sandbox internals only.
- `sandbox/isolated_workspace/performance/test_phases_ms_subset_cover_invariant.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | phases_ms subset-cover invariant via IWS audit payloads; sandbox internals only.
- `sandbox/isolated_workspace/performance/test_tool_call_phase_breakdown_complete.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | tool_call phase-breakdown completeness via RPC + IWS audit phases_ms; sandbox internals only.
- `sandbox/isolated_workspace/policy/test_plugin_and_lsp_blocked_or_routed_in_iws.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | plugin/LSP block-or-route policy in IWS via daemon RPC; sandbox internals only.
- `sandbox/isolated_workspace/pre_flight/test_exit_path_no_occ.py` — **other** | runs_scenario=False | refs_events=False | Static AST source-scan of pipeline/lifecycle modules for OCC/commit_queue tokens; no fixtures, no daemon. Unaffected by migration.
- `sandbox/isolated_workspace/pre_flight/test_handle_shape_no_publish.py` — **other** | runs_scenario=False | refs_events=False | Class-attribute/subclass introspection of the IWS handle type (no publish/overlay parent); no fixtures. Unaffected by migration.
- `sandbox/isolated_workspace/pre_flight/test_import_graph_fence.py` — **other** | runs_scenario=False | refs_events=False | Static fence: filesystem existence checks + dispatcher.OP_TABLE source inspection; only file not using daemon RPC. Unaffected by migration.
- `sandbox/isolated_workspace/pre_flight/test_phase_timer_invariants.py` — **other** | runs_scenario=False | refs_events=False | Pure unit test of a PhaseTimer helper object (subset-cover, defensive-copy); no fixtures, no daemon. Unaffected by migration.
- `sandbox/isolated_workspace/pre_flight/test_setns_exec_discipline.py` — **imports_only** | runs_scenario=False | refs_events=False | AST import-allowlist fence pinning module-level imports of setns_exec/_setns_libc/etc.; pure source scan. Unaffected by migration.
- `sandbox/isolated_workspace/resource_controls/test_host_ram_gate_refuses_over_budget.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Host-RAM gate refusal via daemon env + RPC; sandbox internals only.
- `sandbox/isolated_workspace/resource_controls/test_quota_one_per_agent.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | One-handle-per-agent quota via RPC; sandbox internals only.
- `sandbox/isolated_workspace/resource_controls/test_total_cap_blocks_new_agent.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Total-cap blocks new agent via daemon env + RPC; sandbox internals only.
- `sandbox/isolated_workspace/resource_controls/test_ttl_does_not_evict_active.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | TTL does not evict active handle via daemon env + RPC; sandbox internals only.
- `sandbox/isolated_workspace/resource_controls/test_ttl_evict_and_audit.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | TTL eviction + IWS audit event via RPC; asserts sandbox-side audit, not squad EventTypes.
- `sandbox/isolated_workspace/resource_controls/test_upperdir_tmpfs_enospc_natural_backpressure.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | upperdir tmpfs ENOSPC backpressure via RPC; overlay internals only.
- `sandbox/isolated_workspace/stress/test_5_concurrent_isolated_workspaces.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | 5 concurrent IWS handles stress via RPC; sandbox internals only.
- `sandbox/isolated_workspace/stress/test_disk_at_rest_bounded.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Disk-at-rest bounded via RPC + raw_exec; overlay internals only.
- `sandbox/isolated_workspace/stress/test_pip_install_then_run_e2e.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | pip install + run e2e inside IWS via RPC shell; sandbox internals only.
- `sandbox/isolated_workspace/stress/test_rapid_create_destroy_cycle.py` — **pure_sandbox_runner_agnostic** | runs_scenario=False | refs_events=False | Rapid enter/exit churn via RPC; sandbox internals only.

## Phase-3 deletion checklist

Confirmed. Both benchmark references are resolved:

- `scripts/bench_sandbox_e2e.py`: `MockSquadRunner` appears ONLY in the module docstring (line 7) — no import, no instantiation. The benchmark does NOT actually use the class. (The docstring is stale prose; not load-bearing.) Does not block deletion.
- `tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py:393`: uses `EventType.EXECUTOR_SUCCESS` to fabricate a synthetic `Event` testing the audit recorder's `sandbox_events.jsonl` filter — it asserts the SANDBOX event is written and the EXECUTOR_SUCCESS event is filtered OUT (`len(rows) == 1`). This is a genuine out-of-scope consumer of the enum member. It must be re-pointed to a surviving EventType (e.g. another non-sandbox lifecycle event) or use one of the KEEP members before the enum member is removed.

I have everything. Writing the final checklist now.

---

# Phase-3 Deletion Checklist: Retire MockSquadRunner + lifecycle EventTypes

All paths absolute. Steps are ordered so that **EventType-enum removal is strictly LAST** (step 6). Two true prerequisites (steps 0a/0b) must land before any deletion, or `RunReport.prompt_inspections` goes empty and the negative-path test loses its harness.

Discrepancy flagged up front: the task says "14 lifecycle EventType members (61-76)". Lines 61-76 of `audit/events.py` actually hold **16** members. I list all 16. `PLANNER_REPLAN` (line 64) is self-referenced only (dead already).

---

## STEP 0 — PREREQUISITES (must land before any deletion)

### 0a. Port observability into `ScenarioLoopRunner` — REQUIRED, currently MISSING
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/scenario_loop_runner.py`

`ScenarioLifecycle` (`scenarios/lifecycle.py:85-86`) consumes `MOCK_PROMPT_INSPECTED` → `RunReport.prompt_inspections`, and `assert_focused_scenario_report` asserts `report.passed_prompt_inspections`. `ScenarioLoopRunner` today publishes only `MOCK_LAUNCH_RECORDED` + `MOCK_TOOL_CALL_RECORDED` (lines 140-169). Before runner.py is deleted, move these into `ScenarioLoopRunner` (or its `__call__`):
- `_inspect_prompt` (runner.py **1748-1840**) → produce a `PromptInspection`, publish `EventType.MOCK_PROMPT_INSPECTED`. It depends on `_current_attempt_and_iteration` (1865-1879); the equivalent already exists as `scenario_adapter._attempt_and_iteration` (54-65) — reuse it, do not duplicate.
- `_record_initial_messages` (runner.py **1842-1863**) → only fires when `audit_recorder` is set; `ScenarioLoopRunner` already carries `_audit_recorder`. Port it so message recording survives.
- `_stream_run_id` (1910-1917) and `_initial_message_metadata` (154-158) are helper dependencies of `_record_initial_messages`; port what's needed.

### 0b. Add `consume_advisor_verdict` to `MutableMockState` — REQUIRED, currently MISSING
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/hooks/registry.py` (class at lines 27-84)
- `scenario_adapter._advisor_script` (scenario_adapter.py:160-165) reads `getattr(mutable_state, "consume_advisor_verdict", None)`; that method does NOT exist today (`__slots__` line 36: `("seen_events","flags","_failures","_next_planner_response")`), so it always falls back to "approve". Add:
  - a `_next_advisor_verdict` slot to `__slots__` (line 36),
  - an injector setter (mirror `replace_next_planner_response`, line 54-55),
  - `consume_advisor_verdict()` (pop-once, mirror `consume_next_planner_response`, 81-84).

---

## STEP 1 — Migrate tests off runner internals + lifecycle-event assertions

### 1a. `_focused_scenario_contracts.py` assertion machinery (item 6, part)
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/_focused_scenario_contracts.py`
- Remove `_assert_ordered_subsequence` (**47-59**) and its call site (**39-42**) — it reads `scenario.expected_event_sequence` + `report.seen_event_types`, both being deleted.
- `_assert_event_counts` (**62-73**): the `min_event_counts`/`absent_events` entries consumers pass are lifecycle types being removed; rework so it no longer keys on deleted members (or drop the lifecycle keys from each `FocusedScenarioCase`).
- Consumers to update in lockstep: `tests/mock/task_center/test_focused_scenarios.py`, `tests/mock/sandbox/layer_stack_occ_overlay/test_focused_sandbox_scenarios.py`.

### 1b. `seen_event_types` direct consumer
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/sandbox/layer_stack_occ_overlay/test_high_concurrency_layerstack_overlay_occ.py:128-134` reads `SCENARIO_REGISTRY[...]().expected_event_sequence` and iterates `report.seen_event_types`. Rewrite to assert via `graph_summary` instead.

### 1c. Tests asserting lifecycle EventTypes / lifecycle-keyed hooks
All emit no lifecycle events under the new runner, so every lifecycle-keyed hook (`capture_prompt`, `count_events` on lifecycle types, `fail_verifier_at`, `assert_guard_after_wave`, `assert_recursive_goal_closed_before_parent_guard`) goes inert. Migrate these (all under `.../src/task_center_runner/tests/mock/`):
- `task_center/test_correctness.py` (45-46), `task_center/test_correctness_via_live_e2e.py` (45-46) — `count_events(PLANNER_INVOKED/EVALUATOR_INVOKED)`.
- `task_center/test_full_case_user_input.py` (59-60), `sandbox/capacity/test_full_system_capacity_matrix.py` (70-71), `sandbox/full_stack/test_full_stack_adversarial.py` (121-122) — `count_events(VERIFIER_FAILURE)` + `assert_recursive_goal_closed_before_parent_guard()`.
- `task_center/test_focused_scenarios.py`, `task_center/test_initial_messages_capture.py`, `sandbox/layer_stack_occ_overlay/test_focused_sandbox_scenarios.py`, `sandbox/project_build/test_project_build_shell_edit_lsp_three_parallel_agents.py` (line 49 declares its own `expected_event_sequence`).

### 1d. `test_runner_imports.py` — runner-internal calls
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/contracts/test_runner_imports.py`
- Calls `runner._inspect_prompt(...)` at **86, 126, 160** and asserts `hasattr(scenario, "expected_event_sequence")` at **229**. Re-point at the ported `ScenarioLoopRunner` prompt-inspection (step 0a) and drop the `expected_event_sequence` assertion.

### 1e. Negative-path test rewrite (item 7)
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/contracts/test_advisor_gate_negative_path.py`
- Today it instantiates `MockSquadRunner` (line 28 import, 65-71 `_runner()`) and calls `runner._approve_terminal(base_metadata, submit_execution_success)` (85-87) to synthesize a wrong-tool advisor approval, then drives `execute_tool_once(submit_execution_blocker, …)` and asserts `result.is_error`, `"BLOCKED" in result.output`, and `hook_trace` reason `wrong_tool`/`missing`.
- Rewrite off `MockSquadRunner`/`_approve_terminal`: build the wrong-tool gated metadata directly via `build_advisor_approval_messages(tool_name=submit_execution_success.name)` (its new home — see step 5) or drive the negative path through the loop using `MutableMockState.consume_advisor_verdict()` returning `"reject"` (the seam the adapter already reads at scenario_adapter.py:160-165).

### 1f. Out-of-scope enum consumer (the one external reference)
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py:393` fabricates a synthetic `Event(type=EventType.EXECUTOR_SUCCESS, …)` to prove the audit recorder filters non-sandbox events out of `sandbox_events.jsonl` (asserts `len(rows)==1`). This is independent of the mock runner. Re-point to a surviving non-sandbox EventType (e.g. `TOOL_CALL_STARTED` or a KEEP lifecycle member) before step 6.

Non-blocking references confirmed NOT to require action:
- `scripts/bench_sandbox_e2e.py:7` — `MockSquadRunner` only in module docstring; no import/instantiation.
- `tests/unit_test/test_task_center_runner/test_no_core_imports.py` — architectural guard that forbids `core/*` importing the runner by grepping source text; deleting runner.py leaves it valid.
- `tests/unit_test/test_task_center_runner/test_mock_event_types.py` — only tests the 4 `MOCK_*` members (KEEP); unaffected.

---

## STEP 2 — Strip `expected_event_sequence` (item 4)

### 2a. Protocol/base
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/base.py`
- Remove `expected_event_sequence: tuple[EventType, ...]` from the `Scenario` Protocol (**line 51**) and the `ScenarioBase` default (**line 74**).
- The `EventType` import (**line 14**) becomes unused after these two lines go — remove it.

### 2b. Every scenario file that declares it (remove the line; drop now-unused `EventType` import where it was only used for this)
Under `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/scenarios/`:
- `correctness_testing.py:102`, `full_case_user_input.py:42`, `full_stack_adversarial.py:56`
- `pipeline/initial_messages_capture.py:104`, `pipeline/attempt_budget_exhausted.py:55`, `pipeline/iterative_deferral.py:40`, `pipeline/dependency_blocked_descendants.py:43`, `pipeline/initial_goal.py:29`, `pipeline/dependency_dag_mixed.py:68`, `pipeline/dependency_dag_diamond.py:40`, `pipeline/attempt_retry_generator_failure.py:37`, `pipeline/attempt_retry_evaluator_failure.py:32`, `pipeline/deferred_parent_planner_terminal_routing.py:93`, `pipeline/nested_goal.py:88` and `:138`, `pipeline/dependency_dag_parallel.py:40`, `pipeline/dependency_dag_serial.py:49`, `pipeline/attempt_retry_planner_failure.py:31`, `pipeline/generator_failure_quiescence.py:82`
- `sandbox/high_concurrency_layerstack_overlay_occ.py:91`, `sandbox/background_shell.py:61`, `sandbox/occ_concurrent_conflicts.py:56`, `sandbox/ephemeral_workspace.py:37`, `sandbox/heavy_io_zoned_concurrent.py:91`, `sandbox/auto_squash_commit_resume.py:67`, `sandbox/plugin.py:37`, `sandbox/complex_project_build.py:106` and `:132` (+ its `_EXPECTED_EVENT_SEQUENCE` constant), `sandbox/complex_project_build_grep_glob.py:94` and `:119` (+ constant), `sandbox/complex_project_build_shell_edit_lsp.py:86` and `:111` (+ constant)
- `planner_validation/empty_tasks.py:28`, `planner_validation/defers_without_deferred_goal.py:28`, `planner_validation/unknown_dep.py:34`, `planner_validation/duplicate_local_id.py:49` (note: `:64` is a comment), `planner_validation/unknown_agent_name.py:30`, `planner_validation/cycle_in_deps.py:34`
- `pipeline/initial_messages_capture.py` is also a scenario file (above).
- Test-side declaration: `tests/mock/sandbox/project_build/test_project_build_shell_edit_lsp_three_parallel_agents.py:49` (covered in step 1c).

### 2c. Contract test for the field
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/tests/mock/contracts/test_scenario_suite_imports.py:72-76` — `test_every_scenario_declares_expected_event_sequence` asserts non-empty `expected_event_sequence`. Delete this test (and the docstring mention at line 4).

---

## STEP 3 — Remove `RunReport.seen_event_types` + hook emit sites

### 3a. `RunReport` field (item 6)
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/core/runner.py`
- Remove `seen_event_types: list[EventType]` field (**line 65**) and its assignment `seen_event_types=list(mutable_state.seen_events)` (**line 199**). After step 2/1 there are no readers left (`_focused_scenario_contracts.py:41` and the high-concurrency test both migrated).
- Check whether `EventType` import (**line 31**, imported with `Event`) is still needed; `Event` is still used (line 64), so keep the line but it imports both — leave as-is unless `EventType` is otherwise unused (it's used by the `list[EventType]` annotation being removed; the `Event` annotation stays, so trim to `from ... import Event` if `EventType` becomes unused).

### 3b. Hook emit sites (item 5)
`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/hooks/builtins.py`
- VERIFIER emit sites to drop: `fail_verifier_at` `event=EventType.VERIFIER_INVOKED` (**line 135**); `assert_guard_after_wave` `event=EventType.VERIFIER_SUCCESS` (**line 162**); `assert_recursive_goal_closed_before_parent_guard` `event=EventType.VERIFIER_SUCCESS` (**line 199** — the task missed this one).
- The `_ROLE_TO_INVOKED` map (**27-32**) keys on `PLANNER/EXECUTOR/VERIFIER/EVALUATOR_INVOKED`; it backs `capture_prompt` (53-69). With no lifecycle events emitted, `capture_prompt` never fires. Remove `capture_prompt` + `_ROLE_TO_INVOKED`, or keep them as inert no-ops only if a test still imports the symbol (none does after step 1c).
- `assert_event_sequence` (72-102) keys on `RUN_COMPLETED` (KEEP member) but reads `state.seen_events`; with lifecycle events gone its subsequence checks are vacuous — remove it and the `__all__` entry (213).
- These hooks have NO dedicated hook unit test; their only consumers are the scenario tests migrated in step 1c. Dropping the emit sites does not break a hook-specific test, but `__all__` (212-221) and importing tests must be updated to not reference removed names.

---

## STEP 4 — Delete `runner.py` MockSquadRunner (item 1)

`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/runner.py` — delete whole file (entire `MockSquadRunner`). Confirm `builder.py:80-89` (the `_event_source_runner_enabled()==False` fallback branch importing `MockSquadRunner`) is removed too, leaving only the `ScenarioLoopRunner` path; the `_EVENT_SOURCE_RUNNER_ENV` flag (builder.py:27-37, 68) becomes dead and should be removed so the new runner is unconditional.

DELETE (lifecycle/imperative-execution machinery):
- Tool-execution + gating: `_call_tool` (**1583-1714**), `_approve_terminal` (**317-338**), `_record_tool_check` (**1716-1725**), `_assert_read_contains` (**1727-1746**), `_script_engine` attr (init **190**).
- Role drivers: `_run_planner` (**340-370**), `_run_executor` (**372-829**), `_run_verifier` (**831-870**), `_run_evaluator` (**872-886**), `_scenario_context` (**888-914**).
- All probes (re-homed to `probes.py` or still old-only): `_run_preflight_probe` (916-928), `_run_sandbox_integrity_probe` (929-1003), `_run_batch_edit` (1005-1043), `_run_expected_conflict` (1044-1082), `_run_auto_squash_commit_resume_probe` (1083-1256), `_run_complex_project_build_probe` (1258-1280), `_run_high_concurrency_*` (1282-1334), `_run_heavy_io_zoned_*` (1335-1387), `_run_background_shell_probe` (1388-1438), `_run_ephemeral_workspace_probe` (1440-1473), `_run_plugin_workspace_probe` (1475-1508), `_run_complex_project_build_shell_edit_lsp_probe` (1510-1534), `_run_complex_project_build_grep_glob_probe` (1536-1558), `_run_final_probe` (1560-1581).
  - NOTE Phase-2 gap: the heavy probes above are still old-runner-only (`probes.py` ports just preflight/sandbox_integrity/final_probe). They must be ported into the event-source path (per the CallTool-shim/bridge plan) BEFORE this file is deleted, or those scenarios lose coverage. This is the bulk of Phase 2 and a hard precondition for Step 4.
- Event-type maps: `_PLANNER_EVENT_BY_TOOL` / `_EVALUATOR_EVENT_BY_TOOL` / `_VERIFIER_EVENT_BY_TOOL` (**109-122**).
- Lifecycle publishers: `_publish(EventType.<lifecycle>)` call sites — invocation publish (**264-271**), planner (**360-369**), executor success/failure (**397-402, 823-828**), recursive requested (**417-424, 439-446**), verifier (**841-845, 864-868**), evaluator (**885**), recursive completed/`_recursive_close_payload` (**1957-1982**), `_publish_full_stack_script`/`FULL_STACK_SCRIPT_COMPLETED` (**1984-1993**), and `SANDBOX_*` publishes inside the probes. The generic `_publish` method (**1995-2026**) goes with the file.

KEEP — already ported / superseded (do NOT need separate relocation, they live in the new modules):
- `_inspect_prompt` (1748-1840), `_record_initial_messages` (1842-1863) → ported in **Step 0a** into `ScenarioLoopRunner`.
- `_current_attempt_and_iteration` (1865-1879) → equivalent is `scenario_adapter._attempt_and_iteration` (54-65); no new port needed.
- `_publish_mock_record` / `MOCK_*` publishing (2028-2040) → superseded by `ScenarioLoopRunner._publish_record` (scenario_loop_runner.py:171-176) + `ProbeContext._publish_check` (probes.py:86-97).
- `_invocation_payload` (1919-1940) + `_verifier_payload` (1942-1955): task lists `_invocation_payload` as KEEP, but its ONLY callers are the lifecycle `_publish(...payload=...)` sites being deleted (264-271, 864-868 via `_verifier_payload`). **Reconcile: both are dead-on-removal — delete them, do not port.** (`context_message_field` import they use is only consumed here.)

---

## STEP 5 — Relocate-then-delete `_advisor_approval.py` (item 2)

`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/agent/mock/_advisor_approval.py`
- NOT cleanly deletable: `build_advisor_approval_messages` is re-exported by `tests/unit_test/test_tools/test_submission/_advisor_approval_fixtures.py:13-17`, which feeds real submission-prehook tests unrelated to the mock runner:
  - `tests/unit_test/test_tools/test_submission/test_advisor_approval_prehook.py` (73, 81, 93, 109, 115, 127, 132, 146, 157)
  - `tests/unit_test/test_tools/submission_test_utils.py` (21-22, 113)
  - `tests/unit_test/test_task_center/test_lifecycle/test_phase03_submission_integration.py` (23-24, 51)
- The src file exists ONLY to avoid a src→test layering inversion (its own docstring, lines 10-14). Once runner.py (the sole src consumer, lines 78-79, 336) is gone, that reason evaporates. Action: **move `build_advisor_approval_messages` into `_advisor_approval_fixtures.py`** (collapse the re-export into the definition), then delete the src file. Update `_advisor_approval_fixtures.py` to define rather than import. The three test modules keep working unchanged (they import from the fixtures module).

---

## STEP 6 — REMOVE THE ENUM MEMBERS (STRICTLY LAST)

`/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/task_center_runner/audit/events.py`

REMOVE — the "agent invocations" block, lines **61-76** (16 members, not 14):
`PLANNER_INVOKED` (61), `PLANNER_COMPLETES_GOAL_PLAN` (62), `PLANNER_DEFERS_GOAL_PLAN` (63), `PLANNER_REPLAN` (64, already dead/self-ref only), `EXECUTOR_INVOKED` (65), `EXECUTOR_SUCCESS` (66), `EXECUTOR_FAILURE` (67), `VERIFIER_INVOKED` (68), `VERIFIER_SUCCESS` (69), `VERIFIER_FAILURE` (70), `EVALUATOR_INVOKED` (71), `EVALUATOR_SUCCESS` (72), `EVALUATOR_FAILURE` (73), `RECURSIVE_GOAL_REQUESTED` (74), `RECURSIVE_GOAL_COMPLETED` (75), `FULL_STACK_SCRIPT_COMPLETED` (76).

KEEP — everything else:
- task-center lifecycle (47-58: `RUN_STARTED`…`ATTEMPT_FAILED`),
- tools (78-81: `TOOL_CALL_*`),
- `SANDBOX_*` (83-101) — many still emitted by `ProbeContext._publish` (probes.py) and consumed by metrics/recorder tests,
- hook synthetic (103-105: `HOOK_INJECTED_FAILURE`, `HOOK_ASSERTED`),
- `MOCK_*` (111-114) — still published by `ScenarioLoopRunner` + `ProbeContext`, consumed by `ScenarioLifecycle` + `test_mock_event_types.py`.

Final-step gate: removing these 16 only compiles after Steps 1-5 land (every reference — scenario `expected_event_sequence` tuples, `hooks/builtins.py` emit sites + `_ROLE_TO_INVOKED`, the migrated tests, runner.py, and the external `test_sweevo_audit_recorder.py:393` consumer) has been migrated or deleted. Re-run the per-member grep (`PLANNER_INVOKED` … `FULL_STACK_SCRIPT_COMPLETED`) and confirm the only remaining hits are `audit/events.py` itself before deleting.
