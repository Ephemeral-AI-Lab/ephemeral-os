---
title: "SWE-EVO Real-Agent Live E2E — Plan"
created: 2026-05-12
status: draft
related:
  - docs/wiki/live-e2e-testing-framework-design.md
  - docs/wiki/task-center-pipeline.md
  - docs/wiki/engine-query-loop-llm-seam.md
  - .omc/plans/sweevo-live-e2e-test-framework-plan-20260508.md
---

# SWE-EVO Real-Agent Live E2E — Plan

## Goal

Enable the **real LLM agent** to run a SWE-EVO benchmark instance end-to-end through the existing `task_center` pipeline, while reusing the `live_e2e` audit infrastructure (AuditEventBus, AuditRecorder, per-task `message.jsonl`, sandbox event stream). Run F2P/P2P evaluation after the task center pipeline reports `done`. Land both a CLI driver (`python -m benchmarks.sweevo --real-agent --instance-id=<id>`) and a gated pytest test.

## Requirements Summary

- **No changes** to `task_center`, `engine.api`, `MockSquadRunner`, or any production agent code. The seam already exists: `start_task_center_entry_run(runner=None)` triggers the real LLM loop via `engine.api.run_ephemeral_agent` (see `backend/src/task_center/agent_launch/launcher.py:101-103` and wiki [[engine-query-loop-llm-seam]]).
- **No scenario, no hooks, no Scenario protocol involvement.** The real-agent flow is a single shot: build the entry prompt from the SWE-EVO instance, hand it to the task center, await `wait_for_idle()`, evaluate. Nothing decides "what the planner should submit at attempt 2" — the real LLM does. The `live_e2e.scenarios`, `live_e2e.hooks`, and `live_e2e.squad` packages are **never imported** on this code path.
- **The entry prompt is the PR description** as built by `benchmarks.sweevo.prompt.build_sweevo_user_prompt(instance)`. The prompt source-of-truth chain (already implemented at `backend/src/benchmarks/sweevo/prompt.py:53-99`):
  1. CSV override loaded from `backend/config/benchmarks/sweevo_gpt5_2025_08_07_pr_descriptions.csv` (default path resolved via `_PR_DESCRIPTION_CSV_PATH`, overridable through env `SWEEVO_PR_DESCRIPTIONS_CSV`). The CSV is keyed by `test_folder == instance.instance_id` (or `instance.instance_id_swe`) and exposes a single `pr_description` column.
  2. Fallback to `instance.pr_description` from the dataset row.
  3. Fallback to `instance.problem_statement` (the raw changelog).
  No new prompt logic is written; we call the existing builder.
- The `live_e2e` framework is reused **only** for the audit/persistence side: event bus, recorder, per-task message persistence, sandbox event stream, run-directory layout, store bundle. The mock runner is *not* used.
- Audit artifacts live under `.sweevo_runs/real_agent/<instance_id>/<UTCstamp>_<run_id>/` so each of the 48 SWE-EVO instances has a stable parent directory.
- F2P/P2P run **only** when `task_center_status == "done"`. When the pipeline fails or is cancelled, write a `sweevo_result.json` with `resolved=False`, `fix_rate=0.0`, and `error=<failure reason>`; do **not** run the test suite (saves sandbox time).

## Workflow

The benchmark for one SWE-EVO instance proceeds through four sequential phases. The CLI entry runs them inline; the pytest entry runs them inside a single `await` against fixtures. Nothing is concurrent within a single instance run.

### Phase 1 — Setup (`Δt ≈ 30–180s` cold, `≈ 5–30s` warm)

**What it does:** prepare a Daytona sandbox holding the target repo at `instance.base_commit`, and prepare the per-test PostgreSQL schema that holds task-center state.

**Steps (each invokes existing code — no new logic):**

1. `_bootstrap_sandbox_provider()` — calls `sandbox.provider.daytona.bootstrap.bootstrap_daytona_provider()` (once per process). Side-effect: registers the Daytona client + DSN against the global sandbox API. Mock-runner CLI already does this at `backend/src/benchmarks/sweevo/__main__.py:44-47`.
2. `instance = select_sweevo_instance(source=args.source, instance_id=args.instance_id)` — `backend/src/benchmarks/sweevo/dataset.py:197`. Loads the 48-instance HuggingFace dataset row by `instance_id` (e.g. `dask__dask_2023.3.2_2023.4.0`) and returns a `SWEEvoInstance` dataclass.
3. `sandbox_result = await create_sweevo_test_sandbox(instance, register_snapshot=True, repo_dir=args.repo_dir)` — `backend/src/benchmarks/sweevo/sandbox.py:655`. Executes the canonical 10-step bring-up from [[live-e2e-testing-framework-design]] §"Sandbox setup steps":
   - resolve/register Daytona snapshot from `instance.docker_image`
   - create or reuse sandbox with labels `{purpose=sweevo-test, sweevo_instance, sweevo_repo, project_dir}`
   - wait for exec readiness (bounded retry on transient errors)
   - `git reset --hard HEAD && git clean -fd && git checkout -f {base_commit} && git checkout -B sweevo-work {base_commit}`
   - `pip install -e . -q` (best-effort, 6-minute timeout)
   - `api.build_workspace_base {workspace_root: /testbed, reset: true}` then `api.runtime.ready` probe — **required**, else `read_file`/`edit_file`/`write_file` tools fail with `workspace_not_ready`
   - returns `{sandbox_id, sandbox, snapshot_name, repo_dir, reused_existing, fallback_reason}`
4. `bundle = create_per_test_task_center_stores()` — `backend/src/live_e2e/stores.py`. Carves a fresh PostgreSQL schema `live_e2e_<uuid12>`, runs `Base.metadata.create_all` against it, wires `TaskCenterStore`/`MissionStore`/`EpisodeStore`/`AttemptStore`/`ContextPacketStore` with `search_path = <schema>, public`. Cleaned up via `DROP SCHEMA CASCADE` in phase 4.
5. `audit_dir = Path(args.audit_dir or os.getenv("EOS_SWEEVO_AUDIT_DIR") or ".sweevo_runs").resolve()` and `run_dir = audit_dir / "real_agent" / instance.instance_id / f"{utc_stamp}_{self_run_id}"` where `self_run_id = uuid4().hex[:12]`. The directory is **not** created here — the recorder creates it on `start()`.

**Inputs available at phase end:** `instance`, `sandbox_id`, `bundle`, `run_dir`.

### Phase 2 — Task center run (`Δt ≈ minutes`, LLM-bound)

**What it does:** hand the SWE-EVO PR description to the task center and let the real planner/generator/evaluator pipeline drive itself to a terminal status. No mocks, no scenarios, no hooks.

**Steps:**

1. `entry_prompt = build_sweevo_user_prompt(instance, repo_dir="/testbed")` — `backend/src/benchmarks/sweevo/prompt.py:75`. Reads the CSV override at `backend/config/benchmarks/sweevo_gpt5_2025_08_07_pr_descriptions.csv` (or env-override), keyed by `test_folder == instance.instance_id`. Falls back to `instance.pr_description` → `instance.problem_statement`. Returns the SWE-agent-style message: `<Workspace Root>\n/testbed\n…<pr_description>…\n` plus the "minimal changes to non-tests files" instruction footer.
2. Build the audit harness:
   - `bus = AuditEventBus()`
   - `recorder = AuditRecorder(run_dir, task_center_run_id="", bus=bus, scenario_name="real_agent", instance_id=instance.instance_id, sandbox_id=sandbox_id)`
   - `recorder.start()` — creates `run_dir`, registers 5 SQLAlchemy `after_insert`/`after_update` listeners (mission/episode/attempt/task/agent_run), subscribes `MetricsAggregator.observe` and `_record_sandbox_event` to the bus, writes initial `run.json` with `status="running"`.
3. Define `_on_agent_event` (verbatim shape from `live_e2e/runner.py:179-192`): translates `StreamEvent`s into bus events via `stream_bridge` AND routes per-agent-run stream events into the task's `AgentMessageJsonlRecorder` via `recorder.message_recorder_for_agent_run(agent_run_id)`. **Double duty — both writes happen for every event.**
4. Invoke the seam:
   ```python
   handle = start_task_center_entry_run(
       config=SimpleNamespace(cwd="/testbed"),
       prompt=entry_prompt,
       sandbox_id=sandbox_id,
       on_agent_event=_on_agent_event,
       task_store=bundle.task_store, mission_store=bundle.mission_store,
       episode_store=bundle.episode_store, attempt_store=bundle.attempt_store,
       context_packet_store=bundle.context_packet_store,
       runner=None,                                              # ← real LLM path
       sandbox_bridge=TaskCenterSandboxBridge(start_fn=lambda existing_id: {"id": existing_id}),
   )
   ```
   When `runner=None`, `EphemeralAttemptAgentLauncher` falls back to `engine.api.run_ephemeral_agent` per `task_center/agent_launch/launcher.py:101-103`. Every planner/generator/evaluator agent run goes through the **real query loop** (`engine/query/loop.py:_run_query_loop`) with the real `AnthropicClient` constructed by `make_api_client` per [[engine-query-loop-llm-seam]] §"run_ephemeral_agent lifecycle".
5. `tcrid = str(handle.task_center_run_id); recorder.bind_task_center_run_id(tcrid)` — late-binds the run id so the recorder's `run.json` and per-task filters use the correct id. Publish `RUN_STARTED` to the bus.
6. `await handle.launcher.wait_for_idle()` — blocks until **all** recursively spawned asyncio tasks drain: every Mission, every retry Attempt, every continuation Episode, every nested child Mission. The standard task center state machine terminates each agent run with one of {`submit_full_plan`, `submit_partial_plan`, `submit_execution_success`, `submit_execution_failure`, `request_mission_solution`, `submit_verification_success`, `submit_verification_failure`, `submit_evaluation_success`, `submit_evaluation_failure`} or a launcher-synthesised exhaustion submission.
7. Publish `RUN_COMPLETED`. Read `run_row = bundle.task_store.get_run(tcrid)`; capture `task_center_status = run_row.get("status")` (expected values: `"done"`, `"failed"`, `"cancelled"`).

**Outputs available at phase end:** `tcrid`, `task_center_status`, `duration_s = time.perf_counter() - started_at`, recorder has written every Mission/Episode/Attempt/Task snapshot to disk.

### Phase 3 — Post-run evaluation (only when `task_center_status == "done"`; `Δt ≈ test runtime`)

**What it does:** apply the SWE-EVO test patch, run F2P (must flip to pass), run P2P (must stay passing), compute `fix_rate` and `resolved`.

**Steps (all invoke `benchmarks.sweevo.evaluation.evaluate_sweevo_result`):**

1. Initialise `result = SWEEvoResult(plan_id=tcrid, instance_id=instance.instance_id, status="completed", duration_s=duration_s)`. Populate `task_count`, `tasks_completed`, `tasks_failed` from `bundle.task_store.list_tasks_for_run(tcrid)` (verified to exist at `db/stores/task_center_store.py:204`).
2. **Branch:**
   - If `task_center_status == "done"`:
     - `result = await evaluate_sweevo_result(instance, result, sandbox_id, repo_dir)` — `backend/src/benchmarks/sweevo/evaluation.py:31`. Internally:
       a. `await ensure_sweevo_test_patch(instance, sandbox_id, repo_dir)` — uploads `instance.test_patch` via base64-chunked exec, runs `git apply --check` to detect APPLYABLE / ALREADY_APPLIED / conflict, applies if APPLYABLE.
       b. `await _run_test_set(sandbox_id, repo_dir, instance.fail_to_pass, instance.test_cmds)` — invokes `instance.test_cmds` (typically `pytest --continue-on-collection-errors -rA`) inside the conda `testbed` env on each F2P test ID, parses `N passed` from the summary line. Returns count passed.
       c. Same for `instance.pass_to_pass`; computes `p2p_broken = p2p_total - p2p_passed`.
       d. Also extracts `agent_patch` via `git add -A && git diff HEAD`.
       e. Sets `result.fix_rate = f2p_passed / max(f2p_total, 1)` and `result.resolved = (f2p_passed == f2p_total) and (p2p_broken == 0)`.
   - Else (`task_center_status != "done"`):
     - `result.status = "failed"`, `result.error = task_center_status or "unknown"`, leave `resolved=False`, `fix_rate=0.0`, F2P/P2P counts at `0`. **Do not** call `evaluate_sweevo_result` — saves 1–10 minutes of sandbox test time.

**Output at phase end:** populated `SWEEvoResult`.

### Phase 4 — Persistence & cleanup

1. `_atomic_write_json(run_dir / "sweevo_result.json", dataclasses.asdict(result))` — atomic tmp-and-replace, same as `run.json`.
2. `recorder.dispose()` — unregisters the 5 SQLAlchemy listeners, flushes every per-task `AgentMessageJsonlRecorder`, sets `finished_ts` + `status="finished"`, rewrites `run.json` final, writes `metrics.json` (`MetricsAggregator.snapshot()`).
3. If `bundle` was owned (not passed in by a pytest fixture): `bundle.close()` → `DROP SCHEMA "live_e2e_<id>" CASCADE`.
4. Return `RealAgentRunReport(...)` so the CLI can print the one-line summary and exit code.

## Data Storage

### Run directory location

```
<EOS_SWEEVO_AUDIT_DIR or repo>/.sweevo_runs/real_agent/<instance_id>/<UTCstamp>_<short_run_id>/
```

- `<instance_id>` is the verbatim SWE-EVO dataset key (e.g. `dask__dask_2023.3.2_2023.4.0`). One parent dir per of the 48 instances.
- `<UTCstamp>` is `datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")`.
- `<short_run_id>` is `uuid4().hex[:12]` — guarantees uniqueness on concurrent invocations against the same instance.
- The `real_agent/<instance_id>/` segment is the **only** difference from the existing mock-runner layout (which uses `scenario_logs/<scenario_name>/`). Same `<UTCstamp>_<short_run_id>` leaf convention.

### File layout (concrete)

```
.sweevo_runs/real_agent/dask__dask_2023.3.2_2023.4.0/20260512T173800Z_a1b2c3d4e5f6/
├── run.json                                     # run-level metadata; rewritten at start, bind, and dispose
├── metrics.json                                 # MetricsAggregator snapshot (tool latencies); written on dispose
├── sandbox_events.jsonl                         # sandbox-derived events; appended live
├── sweevo_result.json                           # SWEEvoResult (NEW for this plan); written in phase 4
├── entry_executor_<entry_task_id>:entry/        # the top-level entry task; not nested under any mission
│   ├── task.json                                # TaskCenterTaskRecord snapshot; rewritten on every update
│   └── message.jsonl                            # per-agent-run stream events; one JSON message per line
└── mission_01_<mission_id>/
    ├── mission.json                             # MissionRecord snapshot
    └── episode_01_<episode_id>/
        ├── episode.json                         # EpisodeRecord snapshot
        └── attempt_01_<attempt_id>/
            ├── attempt.json                     # AttemptRecord snapshot
            ├── 01_planner_<attempt_id>:planner/
            │   ├── task.json
            │   └── message.jsonl
            ├── 02_executor_<attempt_id>:gen:<local_id>/
            │   ├── task.json
            │   └── message.jsonl
            ├── 03_verifier_<attempt_id>:gen:<local_id>/
            │   ├── task.json
            │   └── message.jsonl
            └── 04_evaluator_<attempt_id>:evaluator/
                ├── task.json
                └── message.jsonl
```

Numeric prefixes are 1-based, 2-digit zero-padded, per-parent monotonic (see `audit/recorder.py:181-184` counters). Counter scopes: mission seq is per-recorder, episode seq resets per mission, attempt seq resets per episode, role seq resets per attempt.

### File formats

All JSON files are written via `_atomic_write_json` (`audit/recorder.py:134-140`) — `tmp + os.replace`, `default=str`, `ensure_ascii=False`. JSONL files append one JSON object per line, no trailing comma.

#### `run.json` (rewritten on start / bind / dispose)
```json
{
  "task_center_run_id": "5da2f268-...",
  "scenario_name": "real_agent",
  "instance_id": "dask__dask_2023.3.2_2023.4.0",
  "sandbox_id": "2e654ee1-...",
  "started_ts": 1778321751.26,
  "finished_ts": 1778321989.71,
  "status": "finished"
}
```
Status values during a run: `"running"` (after `start()`), `"finished"` (after `dispose()`). The `task_center_status` from the run row is **not** stored here — it lands in `sweevo_result.json`.

#### `metrics.json` (written on dispose)
```json
{
  "per_tool": {
    "shell": {"count": 14, "errors": 0, "latencies_ms": [...], "p50_ms": 113.5, "p95_ms": 891.2, "total_ms": 2114.3},
    "read_file": {"count": 22, "errors": 1, "latencies_ms": [...], "p50_ms": 27.1, "p95_ms": 142.0, "total_ms": 671.4}
  },
  "tool_calls_total": 73,
  "tool_errors_total": 1
}
```

#### `sandbox_events.jsonl` (appended live)
One line per `sandbox_*` audit event:
```json
{"ts": "2026-05-12T17:38:14.213000+00:00", "event_type": "sandbox_batch_edit_applied", "node": {"task_center_run_id": "...", "agent_name": "executor", "agent_role": "generator", "agent_run_id": "...", "attempt_id": "...", "tool_name": null}, "payload": {"applied_edits": 2}, "correlation_id": null}
```

#### `mission.json` / `episode.json` / `attempt.json` (rewritten on every commit)
Exact dataclass-mirror shapes as per `_serialize_mission` (`audit/recorder.py:53-67`), `_serialize_episode` (`70-88`), `_serialize_attempt` (`91-110`). Notable fields:
- `mission.json`: `id`, `task_center_run_id`, `requested_by_task_id`, `goal`, `status` (`open|succeeded|failed|cancelled`), `episode_ids[]`, `final_outcome`, ISO timestamps.
- `episode.json`: `id`, `mission_id`, `sequence_no`, `creation_reason` (`initial|partial_continuation`), `goal`, `attempt_budget`, `status`, `attempt_ids[]`, `continuation_goal`, `task_specification`, `task_summary`.
- `attempt.json`: `id`, `episode_id`, `attempt_sequence_no`, `stage` (`planning|generating|evaluating|closed`), `status` (`running|passed|failed`), `planner_task_id`, `task_specification`, `evaluation_criteria[]`, `generator_task_ids[]`, `evaluator_task_id`, `continuation_goal`, `fail_reason` (`planner_failed|generator_failed|evaluator_failed|startup_failed|null`).

#### `task.json` (rewritten on every commit)
Per `_serialize_task` (`audit/recorder.py:113-131`):
```json
{
  "id": "<attempt_id>:planner",
  "task_center_run_id": "...",
  "role": "planner",
  "agent_name": "planner",
  "task_input": "<rendered prompt blob>",
  "status": "done",
  "summaries": [{"payload": {...}, "from": "submit_full_plan", ...}],
  "needs": [],
  "task_center_attempt_id": "<attempt_id>",
  "context_packet_id": "...",
  "system_prompt": "...",
  "user_prompt": "...",
  "fix_target_id": null,
  "spawn_reason": "attempt_planner",
  "created_at": "2026-05-12T17:38:01.000000+00:00",
  "updated_at": "2026-05-12T17:38:14.000000+00:00"
}
```

#### `message.jsonl` (appended live; one message per line)
Written by `AgentMessageJsonlRecorder` for tasks whose `_display_role` is one of `{entry_executor, planner, executor, verifier, evaluator}`. Each line carries a `base_event` envelope (`{task_id, task_center_run_id}`) merged with the actual `StreamEvent` payload (`AssistantTextDelta`, `AssistantMessageComplete`, `ToolExecutionStarted`, `ToolExecutionCompleted`, `ThinkingDelta`, etc.). Format is whatever `AgentMessageJsonlRecorder` already serialises — we do not change it.

#### `sweevo_result.json` (**new** in this plan; written in phase 4)
`dataclasses.asdict(SWEEvoResult)` per `backend/src/benchmarks/sweevo/models.py:57-75`:
```json
{
  "plan_id": "5da2f268-...",
  "instance_id": "dask__dask_2023.3.2_2023.4.0",
  "status": "completed",
  "agent_patch": "diff --git a/dask/...\n...",
  "resolved": true,
  "fix_rate": 1.0,
  "fail_to_pass_passed": 12,
  "fail_to_pass_total": 12,
  "pass_to_pass_broken": 0,
  "pass_to_pass_total": 87,
  "duration_s": 238.4,
  "task_count": 11,
  "tasks_completed": 11,
  "tasks_failed": 0,
  "error": "",
  "task_summaries": {}
}
```
On the failure branch (`task_center_status != "done"`): `status="failed"`, `error=<status>`, `resolved=false`, `fix_rate=0.0`, `fail_to_pass_passed=0`, `fail_to_pass_total=0`, `pass_to_pass_*=0`, `agent_patch=""`. `task_count`/`tasks_completed`/`tasks_failed` reflect whatever did execute before the failure.

### One-line CLI summary (stdout)

```
real_agent instance_id=dask__dask_2023.3.2_2023.4.0 task_center_run_id=5da2f268-... status=done resolved=true fix_rate=1.00 f2p=12/12 p2p_broken=0/87 duration_s=238.4 run_dir=/Users/.../.sweevo_runs/real_agent/dask__dask_2023.3.2_2023.4.0/20260512T173800Z_a1b2c3d4e5f6
```

Exit code: `0` if `resolved == true`, `1` otherwise (including the `task_center_status != "done"` branch).

## Acceptance Criteria

1. `python -m benchmarks.sweevo --real-agent --instance-id=dask__dask_2023.3.2_2023.4.0` runs the real agent through `start_task_center_entry_run(..., runner=None)` against a real Daytona sandbox provisioned by `create_sweevo_test_sandbox`, then exits 0 when `resolved == True`, 1 otherwise.
2. The CLI run writes the canonical live_e2e audit tree under `.sweevo_runs/real_agent/<instance_id>/<UTCstamp>_<run_id>/` containing at minimum: `run.json`, `metrics.json`, `sandbox_events.jsonl`, per-task `task.json` + `message.jsonl`, and `mission_*/episode_*/attempt_*/...` sub-tree per the layout invariants in `live-e2e-testing-framework-design.md` §"Canonical run-directory layout".
3. The run dir also contains `sweevo_result.json` (a serialized `SWEEvoResult` from `benchmarks.sweevo.models.SWEEvoResult`) with populated `fail_to_pass_passed`, `fail_to_pass_total`, `pass_to_pass_broken`, `pass_to_pass_total`, `fix_rate`, `resolved`, `agent_patch`, `duration_s`, `task_count`, `tasks_completed`, `tasks_failed` fields.
4. When `task_center_status != "done"`, `sweevo_result.json` is still written with `resolved=False`, `fix_rate=0.0`, `error=<task_center_status>`, and `evaluate_sweevo_result` is **not** invoked.
5. `backend/src/live_e2e/tests/sweevo/test_real_agent.py` provides one parameterized test (default-skipped via env gate `EOS_SWEEVO_REAL_AGENT_TESTS=1`) that runs against one canonical small instance, asserts the run dir layout, and asserts a `sweevo_result.json` exists.
6. Existing mock-runner tests under `backend/src/live_e2e/tests/sweevo/` continue to pass unchanged (no regressions in `test_correctness.py`, `test_correctness_via_live_e2e.py`, `test_focused_scenarios.py`, etc.).
7. `python -m benchmarks.sweevo --real-agent` without `--instance-id` exits 2 with a clear error message; `--list` still works as before.
8. `python -m benchmarks.sweevo --scenario <name>` (mock path) is untouched and continues to function.
9. **Phase 0 prerequisite cleanup** lands first as its own PR: a new `backend/src/runtime/app_factory.py` exposes `RuntimeConfig`, `model_store`, `agent_run_store`, `task_center_store`, `ensure_runtime_stores_ready`; all 8 production files + 1 test file that today import `server.app_factory` are rewritten to `runtime.app_factory`; `grep -rn "server\.app_factory" backend/ docs/` returns zero; all existing `pytest -k "not real_agent"` tests pass on the new import path.
10. The Phase 1 pytest test depends on the **`workspace`** fixture (function-scoped, calls `reset_sweevo_workspace`), NOT `sweevo_sandbox` (session-scoped, no reset). Future multi-instance tests must not leak sandbox state.
11. `--max-duration-s` CLI flag (default `1800`) wraps `handle.launcher.wait_for_idle()` in `asyncio.wait_for`. On timeout, `sweevo_result.json` is written with `error="timeout"`, `aborted_by_timeout=True`, `resolved=False`, no F2P call. Exit code `1`.

## Implementation Phase 0 — Prerequisite Cleanup (separate PR, lands first)

The `backend/src/server/` package was deleted on 2026-05-05 in commit `9969f891` ("Rename TaskCenter domain to mission episode attempt"). The deletion did **not** migrate five symbols that production code still imports lazily:

- `RuntimeConfig` (dataclass; runtime config object for `start_task_center_entry_run` and `spawn_agent`)
- `model_store` (module-level `ModelStore` singleton)
- `agent_run_store` (module-level `AgentRunStore` singleton)
- `task_center_store` (module-level singleton; test-only reference)
- `ensure_runtime_stores_ready()` (one-shot init that hydrates the singletons)

Eight production files + one test file still reference `server.app_factory`:

| File:line | Import | Lazy / TYPE_CHECKING |
|---|---|---|
| `backend/src/task_center/entry/coordinator.py:49` | `RuntimeConfig` | TYPE_CHECKING |
| `backend/src/task_center/agent_launch/launcher.py:28` | `RuntimeConfig` | TYPE_CHECKING |
| `backend/src/config/model_config.py:16` | `ensure_runtime_stores_ready, model_store` | lazy (inside fn) |
| `backend/src/providers/provider.py:31` | `model_store` | lazy (inside fn) |
| `backend/src/engine/agent/run_tracker.py:41` | `agent_run_store` | lazy (inside fn) |
| `backend/src/engine/agent/lifecycle.py:28` | `RuntimeConfig` | TYPE_CHECKING |
| `backend/src/engine/agent/lifecycle.py:102` | `agent_run_store as _ars` | lazy (inside fn) |
| `backend/src/engine/agent/factory.py:16` | `RuntimeConfig` | TYPE_CHECKING |
| `backend/tests/unit_test/test_engine/eval_agent_support.py:366,421` | `RuntimeConfig, agent_run_store, model_store, task_center_store` | lazy |

Mock-runner tests still pass because the lazy imports never trigger: `MockSquadRunner.__call__` bypasses `engine.agent.factory.spawn_agent` (the gateway to `make_api_client` and `config.resolve_settings()`).

### Step 0.1 — Choose successor location

**Recommendation: `backend/src/runtime/app_factory.py`** (a new top-level `runtime/` package).

- Preserves the `app_factory` filename so import diffs stay one line (`from server.app_factory import X` → `from runtime.app_factory import X`).
- `server/` was deleted because the HTTP routers under `server/routers/*` are gone for good. The `app_factory` symbols are about runtime bootstrap, not HTTP. `runtime/` matches what's actually left.
- Aligns with existing `runtime` naming (`task_center.attempt.runtime`, etc.).
- Alternative considered: split `RuntimeConfig` → `config/runtime.py` and stores → `db/stores/__init__.py`. Rejected because it would force the 9 import sites into ~16 import lines.

### Step 0.2 — Define resurrected `RuntimeConfig` (3-field shape; no system-prompt override)

The pre-deletion `RuntimeConfig` (`9969f891^:backend/src/server/app_factory.py:55-72`) had a `system_prompt_override: str | None` field plumbed through `resolve_settings → Settings.merge_cli_overrides(system_prompt=...)`. **Empirical scope check**: grep across HEAD finds **zero callers** that pass a non-None `system_prompt_override` to `RuntimeConfig` — the only constructor call site, `tests/unit_test/test_engine/eval_agent_support.py:368`, uses `RuntimeConfig(cwd=".")`. The override is dead code.

**Design decision (per user direction)**: the agent profile markdown frontmatter (`agents/profile/main/*.md` → `system_prompt:` field) is the **sole source** of an agent's system prompt. Runtime-level override is intentionally removed.

```python
# backend/src/runtime/app_factory.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from config.settings import Settings, load_settings

if TYPE_CHECKING:
    from providers.types import SupportsStreamingMessages


@dataclass
class RuntimeConfig:
    """Durable runtime configuration shared by request-scoped agents."""

    cwd: str
    external_api_client: "SupportsStreamingMessages | None" = None
    # Messages to restore on first spawn when bootstrapping from saved state.
    _initial_messages: list[dict] | None = field(default=None, repr=False)

    def resolve_settings(self) -> Settings:
        """Load Settings as-is. System prompts come from agent profile frontmatter,
        not from runtime overrides — see agents/profile/main/*.md `system_prompt:` field."""
        return load_settings()
```

All three fields are load-bearing:
- `cwd` — read at `factory.py:197,284,323,359`.
- `external_api_client` — read at `factory.py:174` to short-circuit `make_api_client`.
- `_initial_messages` — read at `live_e2e/squad/runner.py:1239,1252` (and any other replay-from-state caller).

**Removed (intentional):**
- `system_prompt_override` field → dead in HEAD; agent profile is canonical.
- `Settings.merge_cli_overrides(system_prompt=...)` call inside `resolve_settings` → no callers rely on it; `Settings.merge_cli_overrides` itself remains as the generic kwargs merger used by other CLI flags (e.g. `verbose=True`, tested at `test_settings.py:23-31`).

**Phase 0 verification** (added to the smoke gate):
```bash
# No new callers re-introduce the override.
! grep -rn "system_prompt_override\|merge_cli_overrides.*system_prompt=" backend/src/ docs/
```

`Settings` and `load_settings` live at `backend/src/config/settings.py`.

### Step 0.3 — Define resurrected store singletons + bootstrap (3 singletons, not 7)

The pre-deletion `app_factory.py` (`git show 9969f891^:backend/src/server/app_factory.py` lines 191-232) initialised seven module-level store singletons. **Empirical scope check**: of those seven, only three are referenced by the stale lazy imports that production code still triggers; the other four were consumed by the now-deleted HTTP routers (`server/routers/*`) and have **no remaining lazy-import consumers** in the codebase. Furthermore, 3 of the original 7 store *modules* were renamed in the same commit (`complex_task_request_store.py` → `mission_store.py`, `task_segment_store.py` → `episode_store.py`, `harness_graph_store.py` → `attempt_store.py`); importing them under the old names will now raise `ModuleNotFoundError`. The 4 stores that today have no lazy-import consumers are routed via `TaskCenterStoreBundle` (constructed in `live_e2e/stores.py:create_per_test_task_center_stores`) and passed as keyword args directly into `start_task_center_entry_run` — no singleton needed.

**Lazy-import consumers in HEAD that we must keep working** (verified via grep):

| Lazy import site | Symbol needed |
|---|---|
| `config/model_config.py:16` | `ensure_runtime_stores_ready, model_store` |
| `providers/provider.py:31` | `model_store` |
| `engine/agent/run_tracker.py:41` | `agent_run_store` |
| `engine/agent/lifecycle.py:102` | `agent_run_store` |
| `tests/unit_test/test_engine/eval_agent_support.py:421` | `agent_run_store, model_store, task_center_store` |

Minimum required singletons: **`model_store`, `agent_run_store`, `task_center_store`**.

```python
# backend/src/runtime/app_factory.py (continued)
from pathlib import Path

from db.stores.agent_run_store import AgentRunStore
from db.stores.model_store import ModelStore
from db.stores.task_center_store import TaskCenterStore

task_center_store = TaskCenterStore()
agent_run_store = AgentRunStore()
model_store = ModelStore()


def _model_registry_path() -> Path:
    """Path to the JSON model registry seed.

    Recovered verbatim from pre-deletion app_factory.py:200:
        return Path(__file__).resolve().parent.parent.parent.parent / "models" / "registry.json"

    Resolves to <repo>/models/registry.json. With __file__ at
    backend/src/runtime/app_factory.py, four .parent hops walk:
    backend/src/runtime/ → backend/src/ → backend/ → <repo>/.
    Verified file exists at <repo>/models/registry.json.
    """
    return Path(__file__).resolve().parent.parent.parent.parent / "models" / "registry.json"


def ensure_runtime_stores_ready(settings: "Settings | None" = None):
    """Initialise the three module-level store singletons against db.engine + seed
    the model registry. Idempotent: each store has an is_ready / is_available guard.
    """
    from db.engine import get_session_factory, initialize_db
    if get_session_factory() is None:
        initialize_db()
    sf = get_session_factory()

    if not task_center_store.is_ready:
        task_center_store.initialize(sf)
    if not agent_run_store.is_ready:
        agent_run_store.initialize(sf)
    if not model_store.is_available:
        model_store.initialize(sf)

    # Seed model registrations from JSON — required for make_api_client to
    # resolve an Anthropic model id via get_active_resolved().
    # Fail-fast: if the registry file is missing, seed_from_json silently
    # logs and returns 0 (db/stores/model_store.py:222-226). Assert first so
    # the executor gets a clear path-not-found error instead of a downstream
    # "no active model" AssertionError.
    registry_path = _model_registry_path()
    assert registry_path.is_file(), (
        f"Model registry JSON not found at {registry_path}. "
        f"Recover via git show 9969f891^:backend/src/server/app_factory.py "
        f"for the original path resolution."
    )
    model_store.seed_from_json(str(registry_path))
    return sf
```

Required postcondition after a single `ensure_runtime_stores_ready()` call:

```python
assert task_center_store.is_ready
assert agent_run_store.is_ready
assert model_store.is_available
assert model_store.get_active_resolved() is not None, "model registry seeding failed"
```

**Stores NOT restored as module-level singletons (intentional)**: `mission_store`, `episode_store`, `attempt_store`, `context_packet_store`. These are constructed per-test/per-run inside `TaskCenterStoreBundle` (`live_e2e/stores.py:create_per_test_task_center_stores`) and passed directly into `start_task_center_entry_run` keyword args. The deleted HTTP routers (`server/routers/*`) were the only callers of their pre-deletion module-level singletons.

If a future requirement reintroduces global singletons for any of these four, add them here using the same `is_ready`-guarded initialisation pattern — but do not pre-emptively restore them, and import them under their **current** module names (`db.stores.mission_store`, `db.stores.episode_store`, `db.stores.attempt_store`, `db.stores.context_packet_store`), not the pre-rename names which no longer exist.

### Step 0.4 — Update all 9 stale import sites

Mechanical replace `from server.app_factory import X` → `from runtime.app_factory import X` in:

- `backend/src/task_center/entry/coordinator.py:49`
- `backend/src/task_center/agent_launch/launcher.py:28`
- `backend/src/config/model_config.py:16`
- `backend/src/providers/provider.py:31`
- `backend/src/engine/agent/run_tracker.py:41`
- `backend/src/engine/agent/lifecycle.py:28, 102`
- `backend/src/engine/agent/factory.py:16`
- `backend/tests/unit_test/test_engine/eval_agent_support.py:366, 421`

No other changes to those files.

### Step 0.5 — Phase 0 smoke + regression checks

```bash
# 1. No stale references remain.
grep -rn "server\.app_factory\|from server import\|^import server$" backend/ docs/ || echo "clean"

# 2. New module is importable.
python -c "from runtime.app_factory import RuntimeConfig, model_store, agent_run_store, task_center_store, ensure_runtime_stores_ready; print('ok')"

# 3. Bootstrap initialises stores.
EPHEMERALOS_DATABASE_URL=$EPHEMERALOS_DATABASE_URL python -c "from runtime.app_factory import ensure_runtime_stores_ready, model_store; ensure_runtime_stores_ready(); assert model_store.is_available"

# 4. Mock-runner regression suite passes on the new import path.
.venv/bin/pytest backend/src/live_e2e/tests/sweevo/ -k "not real_agent" -x

# 5. Engine unit tests pass.
.venv/bin/pytest backend/tests/unit_test/test_engine/ -x
```

Phase 0 ships as its own PR. Phase 1 depends on it.

---

## Implementation Phase 1 — Real-Agent Live E2E

Pre-condition: Phase 0 has shipped. `RuntimeConfig`, `model_store`, `agent_run_store`, `ensure_runtime_stores_ready` import cleanly from `runtime.app_factory`.

### Step 1.1 — Add `live_e2e/real_agent_bootstrap.py`

```python
# backend/src/live_e2e/real_agent_bootstrap.py
"""One-shot runtime bootstrap for the real-agent live-e2e path.

Ensures the production agent registry + runtime stores are populated before
start_task_center_entry_run runs. Idempotent.
"""
from __future__ import annotations
from pathlib import Path

_BOOTSTRAPPED = False
# Agent definition root. Resolves to backend/src/agents/profile/.
# Tree layout (load_agents_tree walks recursively, so one call picks all three):
#   main/      — planner.md, evaluator.md, entry_executor.md,
#                generator_executor.md (name=executor), generator_verifier.md (name=verifier),
#                planner_full_only.md
#   helper/    — helper agents (sub-planners, etc.)
#   subagent/  — programmatic-only subagents (advisor, resolver, explorer)
# CRITICAL: parents[1] (verified). __file__ = backend/src/live_e2e/real_agent_bootstrap.py,
# parents[1] = backend/src/, parents[1]/agents/profile = backend/src/agents/profile/ (exists).
# parents[2] would resolve to backend/agents/profile which does NOT exist and would
# silently load zero definitions.
_PROFILE_ROOT = Path(__file__).resolve().parents[1] / "agents" / "profile"

# Production agent definitions expected by EphemeralAttemptAgentLauncher._resolve_agent_definition.
# Markdown files at _PROFILE_ROOT/main/ register these names (per agents/definition/loader.py
# frontmatter `name:` field): planner, executor (from generator_executor.md),
# verifier (from generator_verifier.md), evaluator, entry_executor.
_REQUIRED_AGENT_NAMES = frozenset({"planner", "executor", "verifier", "evaluator", "entry_executor"})

def bootstrap_real_agent_runtime() -> None:
    global _BOOTSTRAPPED
    if _BOOTSTRAPPED:
        return

    # 1. Sandbox provider.
    from sandbox.provider.daytona.bootstrap import bootstrap_daytona_provider
    bootstrap_daytona_provider()

    # 2. Runtime stores (3 singletons + model registry seeding — see Phase 0 Step 0.3).
    from runtime.app_factory import ensure_runtime_stores_ready
    ensure_runtime_stores_ready()

    # 3. Production agent definitions.
    assert _PROFILE_ROOT.is_dir(), f"Agent profile root missing: {_PROFILE_ROOT}"
    from agents.definition.loader import load_agents_tree
    from agents.definition.registry import list_definitions, register_definition
    if not _REQUIRED_AGENT_NAMES.issubset({d.name for d in list_definitions()}):
        loaded = list(load_agents_tree(_PROFILE_ROOT))
        assert loaded, f"load_agents_tree({_PROFILE_ROOT}) returned no definitions"
        for defn in loaded:
            register_definition(defn)
    missing = _REQUIRED_AGENT_NAMES - {d.name for d in list_definitions()}
    assert not missing, f"Agent registry missing required definitions: {sorted(missing)}"

    # NOTE on recipes/predicates: task_center/entry/coordinator.py:238-239 ALREADY calls
    # register_builtin_recipes() and register_builtin_predicates() inside _build_composer
    # on every start_task_center_entry_run. Re-registering them here is dead defensive
    # scaffolding for a "what if a future caller bypasses the coordinator" scenario that
    # does not exist in HEAD. Add only when that bypass appears.

    # NOTE on tool registration: there is NO global "register built-in tools" step.
    # Tool registries are built per-agent at spawn time by
    # backend/src/engine/agent/factory.py:_build_agent_tool_registry, gated by each
    # agent's `allowed_tools` frontmatter. The pre-deletion app_factory's
    # `create_default_tool_registry()` (tools/__init__.py:58) returns an empty
    # ToolRegistry — there was nothing to bootstrap globally. Do not add a
    # `register_built_in_tools_against` call here; it does not exist.

    # NOTE on agents.builtins: the pre-deletion app_factory also called
    # `agents.builtins.register_builtin_agents`, but `backend/src/agents/builtins.py`
    # was intentionally removed in commit a17373f2 ("Remove dead agents surfaces").
    # All production agent registration now lives in the markdown profile tree
    # (_PROFILE_ROOT). No restoration needed.

    _BOOTSTRAPPED = True
```

Critical verifications confirmed before this bootstrap can be written:

1. `_PROFILE_ROOT` resolves to `backend/src/agents/profile/` (verified: `parents[1]` is correct; `parents[2]` resolves to a non-existent `backend/agents/profile/`).
2. `register_builtin_recipes` lives at `task_center/context_engine/recipes/__init__.py:38` (verified by grep; the misnamed `task_center/context_engine/recipes_registry.py` only contains the `RecipeRegistry` class).
3. `register_builtin_predicates` lives at `task_center/agent_launch/predicates.py:70` (verified).
4. The 5 required agent names match what the launcher resolves: `agents/profile/main/{planner,evaluator,entry_executor}.md` declare `name: planner|evaluator|entry_executor`; `generator_executor.md` declares `name: executor`; `generator_verifier.md` declares `name: verifier` (filename ≠ frontmatter name).
5. Recipes/predicates are already auto-registered inside `task_center/entry/coordinator.py:_build_composer` (lines 238-239), so the explicit calls in step 4 are redundant but idempotent.
6. There is NO global tool registry to populate — tools are per-agent at spawn time. The first-draft "Built-in tool registry" TODO was a false alarm.
7. `agents.builtins.register_builtin_agents` is intentionally gone (commit `a17373f2` "Remove dead agents surfaces"). The markdown profile tree is the sole source of agent definitions.

### Step 1.2 — Add `live_e2e/real_agent_run.py`

```python
# backend/src/live_e2e/real_agent_run.py
@dataclass(slots=True)
class RealAgentRunReport:
    instance_id: str
    task_center_run_id: str
    sandbox_id: str
    run_dir: Path
    task_center_status: str | None
    sweevo_result: SWEEvoResult     # always present; resolved=False on failure paths
    aborted_by_timeout: bool = False

async def run_sweevo_real_agent(
    *,
    instance: SWEEvoInstance,
    sandbox_id: str,
    audit_dir: Path,
    repo_dir: str = _REPO_DIR,
    stores: TaskCenterStoreBundle | None = None,
    max_duration_s: float = 1800.0,
) -> RealAgentRunReport: ...
```

`duration_s`, `request_id`, `entry_prompt_*`, `metrics`, and `graph_summary` are **not** on `RealAgentRunReport` — their consumers either don't exist (no caller reads `request_id`/`entry_prompt_*`/`metrics`/`graph_summary`) or the data is already available elsewhere (`duration_s` is on `sweevo_result`; `metrics` is in `run_dir/metrics.json`; the per-task graph can be reconstructed from `bundle.task_store.list_tasks_for_run(tcrid)`). Add fields only when a real caller needs them.

Function body (mirrors `live_e2e.runner.run_scenario` minus mock-runner wiring):

1. `bootstrap_real_agent_runtime()` (idempotent).
2. `bundle = stores or create_per_test_task_center_stores()`.
3. `bus = AuditEventBus()`; `mutable_state` is NOT needed (no scenario hooks).
4. `run_dir = audit_dir / "real_agent" / instance.instance_id / f"{utc_stamp}_{self_run_id}"`.
5. `recorder = AuditRecorder(run_dir, task_center_run_id="", bus=bus, scenario_name="real_agent", instance_id=instance.instance_id, sandbox_id=sandbox_id)`; `recorder.start()`. **Verified at `audit/recorder.py:155-172, 228`**: `AuditRecorder` writes verbatim into `run_dir`; no `scenario_logs/` prefix is hard-coded anywhere — the prefix in scenario runs is computed by the caller (`live_e2e/runner.py:195-200`).
6. `entry_prompt = build_sweevo_user_prompt(instance, repo_dir=repo_dir)`.
7. Define `_on_agent_event` (verbatim shape from `live_e2e/runner.py:179-192`): both `stream_bridge` to bus AND `recorder.message_recorder_for_agent_run(agent_run_id)` per-task `message.jsonl` write. Known minor hazard: the per-task message recorder lookup at `audit/recorder.py:218-224` returns `None` if the `agent_runs` row hasn't committed yet — events emitted before that commit are dropped from `message.jsonl`. Same hazard exists on the scenario path; accept it.
8. Build the runtime config:
   ```python
   from runtime.app_factory import RuntimeConfig
   config = RuntimeConfig(cwd=repo_dir, external_api_client=None)
   ```
9. Invoke the seam and wrap in `asyncio.wait_for`:
   ```python
   started_at = time.perf_counter()
   aborted_by_timeout = False
   handle = start_task_center_entry_run(
       config=config,
       prompt=entry_prompt,
       sandbox_id=sandbox_id,
       on_agent_event=_on_agent_event,
       task_store=bundle.task_store,
       mission_store=bundle.mission_store,
       episode_store=bundle.episode_store,
       attempt_store=bundle.attempt_store,
       context_packet_store=bundle.context_packet_store,
       runner=None,
       sandbox_bridge=TaskCenterSandboxBridge(start_fn=lambda existing_id: {"id": existing_id}),
   )
   tcrid = str(handle.task_center_run_id)
   recorder.bind_task_center_run_id(tcrid)
   bus.publish(Event(type=EventType.RUN_STARTED, node=NodeId(task_center_run_id=tcrid)))
   try:
       await asyncio.wait_for(handle.launcher.wait_for_idle(), timeout=max_duration_s)
   except asyncio.TimeoutError:
       aborted_by_timeout = True
       # Cancel pending launcher tasks so LLM API calls actually stop. Without
       # this, asyncio.wait_for only cancels the wait_for_idle awaiter while
       # the tasks inside launcher._pending keep running and continue spending
       # tokens. _pending is private (set[asyncio.Task] at agent_launch/launcher.py:61) —
       # access is intentional; revisit if/when a public cancel_all() is added.
       pending = tuple(handle.launcher._pending)
       for task in pending:
           task.cancel()
       if pending:
           await asyncio.gather(*pending, return_exceptions=True)
   bus.publish(Event(type=EventType.RUN_COMPLETED, node=NodeId(task_center_run_id=tcrid)))
   ```
10. `run = bundle.task_store.get_run(tcrid)`; `task_center_status = run.get("status")`.
11. Compute `task_count`/`tasks_completed`/`tasks_failed` from `bundle.task_store.list_tasks_for_run(tcrid)` (**verified exists** at `db/stores/task_center_store.py:204`).
12. Build `SWEEvoResult(plan_id=tcrid, instance_id=instance.instance_id, status="completed" if task_center_status == "done" and not aborted_by_timeout else "failed", duration_s=time.perf_counter() - started_at, task_count=..., tasks_completed=..., tasks_failed=...)`.
13. Evaluation branch:
    - If `task_center_status == "done"` **and** not `aborted_by_timeout`: `result = await evaluate_sweevo_result(instance, result, sandbox_id, repo_dir)` — populates F2P/P2P + `agent_patch`.
    - Else: `result.error = "timeout" if aborted_by_timeout else (task_center_status or "unknown")`. Leave `resolved=False`, `fix_rate=0.0`. Do **not** invoke `evaluate_sweevo_result`.
14. `_atomic_write_json(run_dir / "sweevo_result.json", dataclasses.asdict(result))`.
15. Build and return `RealAgentRunReport(..., aborted_by_timeout=aborted_by_timeout)`.
16. Cleanup (unsubscribe, `recorder.dispose()`, `bundle.close()` if owned).

Critical: import audit primitives from `live_e2e.audit.bus`, `live_e2e.audit.recorder`, `live_e2e.audit.events`, `live_e2e.audit.node_id`, `live_e2e.audit.stream_bridge` — do not duplicate.

### Step 1.3 — Replace the deferred `--real-agent` stub in `benchmarks/sweevo/__main__.py`

(`live_e2e/sweevo_adapter.py` is **not** edited on the real-agent path. The CLI and pytest both import `run_sweevo_real_agent` directly from `live_e2e.real_agent_run` — there is no per-dataset adapter layer because the function is already dataset-agnostic from its signature.)

1. Add to `_build_parser()` (`__main__.py:50-84`):
   ```python
   parser.add_argument("--max-duration-s", type=float, default=1800.0,
       help="Wall-clock cap for the real-agent task_center run (default 30min).")
   ```
2. Replace `_cmd_real_agent` (~`__main__.py:149-156`) with an async implementation. **Library bootstraps what its own body needs (`runtime` stores + agent definitions). The CLI still bootstraps the Daytona provider before `create_sweevo_test_sandbox` — the sandbox-creation call is a CLI prerequisite, not a `run_sweevo_real_agent` argument.**
   ```python
   async def _cmd_real_agent(args: argparse.Namespace) -> int:
       if not args.instance_id:
           print("--real-agent requires --instance-id=<id>", file=sys.stderr)
           return 2
       from live_e2e.real_agent_run import run_sweevo_real_agent
       from benchmarks.sweevo.dataset import select_sweevo_instance
       from benchmarks.sweevo.sandbox import create_sweevo_test_sandbox

       _bootstrap_sandbox_provider()   # required before create_sweevo_test_sandbox; matches __main__.py:116
       instance = select_sweevo_instance(source=args.source, instance_id=args.instance_id)
       sandbox = await create_sweevo_test_sandbox(instance, register_snapshot=True, repo_dir=args.repo_dir)
       audit_dir = Path(args.audit_dir or os.getenv("EOS_SWEEVO_AUDIT_DIR", ".sweevo_runs")).resolve()

       report = await run_sweevo_real_agent(
           instance=instance,
           sandbox_id=str(sandbox["sandbox_id"]),
           audit_dir=audit_dir,
           repo_dir=args.repo_dir,
           max_duration_s=args.max_duration_s,
       )

       r = report.sweevo_result
       print(
           f"real_agent instance_id={instance.instance_id} "
           f"task_center_run_id={report.task_center_run_id} "
           f"status={report.task_center_status} "
           f"resolved={r.resolved} fix_rate={r.fix_rate:.2f} "
           f"f2p={r.fail_to_pass_passed}/{r.fail_to_pass_total} "
           f"p2p_broken={r.pass_to_pass_broken}/{r.pass_to_pass_total} "
           f"duration_s={r.duration_s:.1f} "
           f"aborted_by_timeout={report.aborted_by_timeout} "
           f"run_dir={report.run_dir}"
       )
       return 0 if r.resolved else 1
   ```
3. Update `main(...)` to `if args.real_agent: return asyncio.run(_cmd_real_agent(args))`.

### Step 1.4 — Add pytest test `live_e2e/tests/sweevo/test_real_agent.py`

```python
import os
import pytest

from live_e2e.real_agent_run import run_sweevo_real_agent

pytestmark = pytest.mark.skipif(
    os.getenv("EOS_SWEEVO_REAL_AGENT_TESTS") != "1",
    reason="Real-agent live e2e gated by EOS_SWEEVO_REAL_AGENT_TESTS=1",
)

@pytest.mark.asyncio
async def test_real_agent_resolves_canonical_instance(
    sweevo_instance, workspace, audit_dir, stores
):
    report = await run_sweevo_real_agent(
        instance=sweevo_instance,
        sandbox_id=str(workspace["sandbox_id"]),
        audit_dir=audit_dir,
        stores=stores,
        max_duration_s=float(os.getenv("EOS_SWEEVO_REAL_AGENT_MAX_DURATION_S", "1800")),
    )
    assert report.task_center_run_id
    assert report.run_dir.is_dir()
    assert (report.run_dir / "run.json").is_file()
    assert (report.run_dir / "sweevo_result.json").is_file()
    assert report.task_center_status in {"done", "failed", "cancelled"}
    if report.task_center_status == "done" and not report.aborted_by_timeout:
        assert report.sweevo_result.fail_to_pass_total > 0
```

**Critical fixture choice**: depend on `workspace` (function-scoped, calls `reset_sweevo_workspace` per `sweevo_adapter.py:79-94`), not `sweevo_sandbox` (session-scoped, no reset). Future multi-instance tests need the reset to avoid cross-instance leakage.

### Step 1.5 — Documentation

Add "Real-agent path" section to `docs/wiki/live-e2e-testing-framework-design.md`:
- Phase 0 prerequisite: `runtime.app_factory` resurrection.
- The seam (`runner=None`).
- Run-dir convention.
- `bootstrap_real_agent_runtime` semantics.
- F2P/P2P gate (`task_center_status == "done"` AND not `aborted_by_timeout`).
- `--max-duration-s` flag + override env.

## File-Touch Inventory

| File | Action | Phase | Why |
|---|---|---|---|
| `backend/src/runtime/__init__.py` | **new** | 0 | Make `runtime` package importable. |
| `backend/src/runtime/app_factory.py` | **new** | 0 | Resurrect `RuntimeConfig`, `model_store`, `agent_run_store`, `task_center_store`, `ensure_runtime_stores_ready`. |
| `backend/src/task_center/entry/coordinator.py:49` | **edit** | 0 | `server.app_factory` → `runtime.app_factory`. |
| `backend/src/task_center/agent_launch/launcher.py:28` | **edit** | 0 | Same. |
| `backend/src/config/model_config.py:16` | **edit** | 0 | Same. |
| `backend/src/providers/provider.py:31` | **edit** | 0 | Same. |
| `backend/src/engine/agent/run_tracker.py:41` | **edit** | 0 | Same. |
| `backend/src/engine/agent/lifecycle.py:28, 102` | **edit** | 0 | Same. |
| `backend/src/engine/agent/factory.py:16` | **edit** | 0 | Same. |
| `backend/tests/unit_test/test_engine/eval_agent_support.py:366, 421` | **edit** | 0 | Same. |
| `backend/src/live_e2e/real_agent_bootstrap.py` | **new** | 1 | Idempotent runtime bootstrap. |
| `backend/src/live_e2e/real_agent_run.py` | **new** | 1 | Real-agent runtime assembly. |
| `backend/src/benchmarks/sweevo/__main__.py` | **edit** | 1 | Replace `_cmd_real_agent`; add `--max-duration-s`. |
| `backend/src/live_e2e/tests/sweevo/test_real_agent.py` | **new** | 1 | Gated pytest test (uses `workspace`). |
| `docs/wiki/live-e2e-testing-framework-design.md` | **edit** | 1 | "Real-agent path" subsection. |

No edits to: `task_center/{api,attempt,episode,mission,task,domain,config,exceptions}.py`, `engine/api.py`, `live_e2e/runner.py`, `live_e2e/squad/*`, `live_e2e/scenarios/*`, `live_e2e/hooks/*`, `live_e2e/audit/*`, `benchmarks/sweevo/{evaluation,sandbox,dataset,prompt,models}.py`.

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Recovered `RuntimeConfig` signature diverges from `factory.py`'s expectations | Medium | High | Step 0.2: `git show 9969f891^:backend/src/server/app_factory.py` for verbatim shape; cross-check every `config.<attr>` access in `factory.py` (lines 174, 197, 284, 311, 323, 359). |
| `ensure_runtime_stores_ready` body cannot be exactly reproduced | Low | High | Step 0.3: recover via git history. If unrecoverable, write a fresh impl mirroring `live_e2e/stores.py:_bind_search_path` pattern against `db.engine.get_session_factory()`. |
| Phase 0 missed import sites | Low | Medium | Step 0.5 grep gate (`grep -rn "server\.app_factory"`) must return empty before declaring done. |
| Built-in tool registration path differs from pre-deletion | Medium | High | Step 1.1 has explicit TODO for this; implementor recovers via git archaeology before declaring the bootstrap complete. |
| Real LLM cost for opt-in CLI runs | High | Medium | `--max-duration-s` default 1800s wraps `wait_for_idle` in `asyncio.wait_for`. Per-tool budget remains via `tool_call_limit` on `AgentDefinition`. |
| Daytona sandbox flakes | Medium | Medium | Reuse `create_sweevo_test_sandbox` bounded-retry. Pytest uses `workspace` so each test starts from a reset state. |
| Test patch apply conflicts with agent's edits | Medium | Low | `evaluate_sweevo_result` uses `git apply --check` to detect conflicts; F2P drops to 0; `resolved=False` naturally. |
| `--max-duration-s` timeout does not actually stop LLM spend | Low | High | Timeout branch iterates `handle.launcher._pending` (private `set[asyncio.Task]` at `agent_launch/launcher.py:61`), cancels each, then awaits `asyncio.gather(*pending, return_exceptions=True)`. Real cancellation, not soft suggestion. If launcher gains a public `cancel_all()`, switch to it. |
| Agent registration collides across mock test + real-agent CLI in same process | Low | Medium | `registered_mock_agents()` context manager already unregisters on exit (`live_e2e/squad/definitions.py:34-37`). Bootstrap is idempotent via `_BOOTSTRAPPED` + `if not list_definitions()` guard. |
| `_on_agent_event` drops first stream events before `agent_runs` row commits | Low | Low | Same hazard as scenario path. Recoverable from `task.json` snapshots; first events typically `AssistantTextDelta`. |
| `dataclasses.asdict(SWEEvoResult)` serialization fails | Low | Low | `_atomic_write_json` passes `default=str`; `task_summaries` is plain dict. |
| Run-dir collisions on concurrent invocations | Low | Low | `self_run_id = uuid4().hex[:12]` + `<UTCstamp>` ensures uniqueness per instance subdir. |
| Phase 1 lands before Phase 0 (deployment ordering) | Low | High | Acceptance criterion 9 makes Phase 0 a hard gate. PR descriptions must reference the Phase 0 PR; CI fails Phase 1 PR if `runtime.app_factory` is not importable. |

## Verification Steps

### Phase 0 verification (must pass before Phase 1 lands)

1. `grep -rn "server\.app_factory\|^from server import\|^import server$" backend/ docs/` returns empty.
2. The 3 production-needed singletons + `RuntimeConfig` (4 fields) + `ensure_runtime_stores_ready` import cleanly:
   ```bash
   python -c "from runtime.app_factory import RuntimeConfig, task_center_store, agent_run_store, model_store, ensure_runtime_stores_ready; print('ok')"
   ```
3. With `EPHEMERALOS_DATABASE_URL` set, all 3 stores reach the ready state, the model registry path is valid, and seeding produces an active model:
   ```bash
   python -c "
   from runtime.app_factory import ensure_runtime_stores_ready, _model_registry_path, task_center_store, agent_run_store, model_store
   assert _model_registry_path().is_file(), f'registry missing: {_model_registry_path()}'
   ensure_runtime_stores_ready()
   assert task_center_store.is_ready and agent_run_store.is_ready and model_store.is_available
   assert model_store.get_active_resolved() is not None, 'model registry seed failed'
   "
   ```
4. `RuntimeConfig` round-trips with the 3-field shape (no `system_prompt_override`):
   ```bash
   python -c "
   from runtime.app_factory import RuntimeConfig
   c = RuntimeConfig(cwd='/tmp', external_api_client=None, _initial_messages=None)
   s = c.resolve_settings()
   assert s is not None
   # Guard: the override field must not be reintroduced.
   import dataclasses
   names = {f.name for f in dataclasses.fields(RuntimeConfig)}
   assert 'system_prompt_override' not in names, 'system_prompt_override must stay removed'
   "
   ```
5. `.venv/bin/pytest backend/src/live_e2e/tests/sweevo/ -k "not real_agent" -x` passes.
6. `.venv/bin/pytest backend/tests/unit_test/test_engine/` passes.

### Phase 1 verification

6. **Lint** — `.venv/bin/ruff check backend/src/runtime backend/src/live_e2e backend/src/benchmarks/sweevo` clean.
7. **Mock regression on Phase 1 branch** — `.venv/bin/pytest backend/src/live_e2e/tests/sweevo/ -k "not real_agent"` passes.
8. **Smoke imports** — `python -c "from live_e2e.real_agent_bootstrap import bootstrap_real_agent_runtime; from live_e2e.real_agent_run import RealAgentRunReport, run_sweevo_real_agent; print('ok')"`.
9. **Bootstrap idempotent + correct agents registered** — passing this is the canary that the `_PROFILE_ROOT` path arithmetic and registration assertions hold:
   ```bash
   python -c "
   from live_e2e.real_agent_bootstrap import bootstrap_real_agent_runtime
   bootstrap_real_agent_runtime()
   bootstrap_real_agent_runtime()  # idempotent
   from agents.definition.registry import list_definitions
   names = {d.name for d in list_definitions()}
   required = {'planner', 'executor', 'verifier', 'evaluator', 'entry_executor'}
   missing = required - names
   assert not missing, f'Missing agents after bootstrap: {sorted(missing)}'
   print(f'ok: {sorted(names)}')
   "
   ```
   A `len() > 0` smoke is insufficient — the test must verify the **5 specific names** the launcher resolves.
10. **`--list` unchanged** — `python -m benchmarks.sweevo --list | head -5` lists 48 instances.
11. **`--real-agent` without instance-id exits 2.**
12. **Deferred path gone** — `_cmd_real_agent` no longer prints "deferred to a follow-up phase".
13. **Timeout works** — `python -m benchmarks.sweevo --real-agent --instance-id=<id> --max-duration-s=5` writes `sweevo_result.json` with `error="timeout"`, `aborted_by_timeout=true` after ~5s; exit code 1.
14. **End-to-end manual** — `python -m benchmarks.sweevo --real-agent --instance-id=<small-instance>` with valid LLM credentials + Daytona access produces the full audit tree + `sweevo_result.json`. Verify exit code matches `resolved`.
15. **End-to-end pytest** — `EOS_SWEEVO_REAL_AGENT_TESTS=1 .venv/bin/pytest backend/src/live_e2e/tests/sweevo/test_real_agent.py -v` passes against the canonical small instance.
16. **Failure-path** — inject a failing PR description (e.g. force planner_failed); confirm `sweevo_result.json` has `resolved=false`, `error="failed"`, no F2P call.

## Verification of the Goal Itself

> Enable actual agent to run with sweevo test with existing live_e2e and sweevo module, but make sure live_e2e module act as a thin wrapper for the actual sweevo test.

✓ Real agent runs via `start_task_center_entry_run(runner=None)` — existing seam, real LLM via launcher fallback.
✓ `live_e2e` reused only for audit/persistence; no mock scenarios, no mock agents, no hooks.
✓ `benchmarks.sweevo` modules (dataset, models, prompt, sandbox, evaluation) reused as-is.

> For actual test, the task center and agent workflow should make no difference from normal runs.

✓ `registered_mock_agents()` never called on real-agent path; production registry installed by `bootstrap_real_agent_runtime`.
✓ Same `RuntimeConfig` (resurrected from pre-deletion `server.app_factory`) used by the standard `run_ephemeral_agent` path.

> Enable f2p and p2p testing after the task center run is completed.

✓ After `wait_for_idle` (or timeout) returns and `task_center_status == "done"` and not `aborted_by_timeout`, call `evaluate_sweevo_result` which applies `test_patch` and runs F2P/P2P.

## Non-Goals (explicit out of scope)

- Replay / fake-LLM mode (Seam #2 / `FakeReplayApiClient`) — deferred.
- Batch orchestration across all 48 instances in one CLI call — single-instance only; loop externally.
- New scenarios under `live_e2e/scenarios/` — real-agent path is not scenario-driven.
- Renaming `EOS_SWEEVO_AUDIT_DIR`.
- Per-tool token-budget enforcement beyond what `tool_call_limit` already provides; wall-clock is bounded via `--max-duration-s`.
- Cancelling stuck Daytona sandboxes mid-run.
- Restoring `server/routers/*` — HTTP API surface is intentionally gone.
- **Support for non-SWE-EVO benchmark datasets (SWE-bench, SWE-bench-Verified, custom).** `run_sweevo_real_agent` imports `build_sweevo_user_prompt` and `evaluate_sweevo_result` by name; extending to a new dataset means either (a) introducing a parallel `run_swebench_real_agent` module, or (b) refactoring to inject `(prompt_builder, evaluator)` callables. Both are deferrable. The audit/recorder infrastructure is already dataset-agnostic, so the extension cost is bounded to the two callables.
- **Concurrent CLI invocations against the same instance id.** Audit subdirs are unique via `uuid4().hex[:12]`, but the underlying Daytona sandbox label-based reuse logic in `create_sweevo_test_sandbox` may collide. Run instances serially or with distinct `--repo-dir` values; process-level mutex is out of scope.

## Open Items the Implementor Should Verify Before Coding

1. **Other pre-deletion `ensure_runtime_stores_ready` body details** — `RuntimeConfig` (3 fields) and `_model_registry_path()` body are inlined in Steps 0.2 and 0.3 with verbatim shape recovered. Verify only the **other** pre-deletion details on first compile: the `settings.database` parameter passed to `initialize_db` (`9969f891^:backend/src/server/app_factory.py:209`), and the "Running without database — file-based persistence only" log branch at the same file's line 215.
2. **`bundle.task_store.list_tasks_for_run(tcrid)` row shape** — verified the method exists (`db/stores/task_center_store.py:204`); confirm row fields expose `status` for the `task_count`/`tasks_completed`/`tasks_failed` computation.
3. **`handle.launcher.wait_for_idle()` under `asyncio.wait_for` cancellation** — confirm whether the coroutine responds to cancellation cleanly, or whether the timeout branch needs explicit launcher-task cancellation plumbing.
4. **`TaskCenterSandboxBridge` reachable surface on the real-agent path** — the plan reuses the scenario-path stub `start_fn=lambda existing_id: {"id": existing_id}`. Confirm no other bridge method is invoked during `run_ephemeral_agent` for the SWE-EVO sandbox lifecycle. If other methods are reached, supply real implementations or document why no-op is safe.
5. **`EPHEMERALOS_DATABASE_URL` requirement** — production stores require a Postgres DSN to commit. The pre-deletion app supported file-based persistence as a fallback (logged "Running without database — file-based persistence only"). Decide whether the real-agent CLI fails fast when the env is unset, or degrades to file-only mode (in which case the audit recorder's per-task `message.jsonl` may be empty because `agent_runs` rows never commit). Recommend: fail fast in CLI; pytest fixture already skips when env is unset.

**Resolved by Phase 0/1 (no longer open items):**
- ~~Built-in tool registration entry point~~ — verified there is no global tool registration step; tools are per-agent at spawn time via `engine/agent/factory.py:_build_agent_tool_registry`.
- ~~`agents.builtins.register_builtin_agents` restoration~~ — verified intentionally removed in commit `a17373f2` "Remove dead agents surfaces"; markdown profile tree is the sole agent source.
- ~~`AuditRecorder.bind_task_center_run_id` existence~~ — verified at `audit/recorder.py:212`.
- ~~`AuditRecorder` hard-coded `scenario_logs/` prefix~~ — verified absent; recorder writes verbatim to caller-supplied `run_dir`.
- ~~`register_builtin_recipes` import path~~ — verified at `task_center/context_engine/recipes/__init__.py:38` (NOT `recipes_registry`).
- ~~`_PROFILE_ROOT` path arithmetic~~ — verified `parents[1]` is correct (`backend/src/agents/profile`).
