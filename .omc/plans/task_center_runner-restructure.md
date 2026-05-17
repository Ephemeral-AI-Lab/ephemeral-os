# Plan — Restructure `live_e2e/` → `task_center_runner/` with Unified Workflow

Mode: SHORT (refactor; no auth/security/migration/destructive risk).
Audience: Architect + Critic (next ralplan stages).
Iteration: 2 (post-Architect REVISE + Critic CRITIC-ITERATE).

---

## 1. RALPLAN-DR Summary

### Principles (non-negotiable)
1. **Unified workflow.** Mock scenarios, real-agent runs, and benchmark runs share one `run_pipeline(config)` coroutine. Mode differs only via `config.runner_factory` and `config.lifecycle`.
2. **Core knows nothing about runner type.** No imports of `MockSquadRunner`, `MutableMockState`, Daytona, or SWE-EVO from `task_center_runner/core/`. Mock-only side-channels reach observers only via the existing audit bus — core never calls `getattr`/`hasattr`/`collect_extras` on the runner.
3. **Provisioning is configurable.** `SandboxProvisioner` Protocol so core never imports a sandbox provider; SWE-EVO sandbox creation is a benchmark concern.
4. **Audit + perf reports for every mode.** The recorder runs identically; the perf report is generated **asynchronously** (fire-and-forget) so the pipeline returns immediately.
5. **Backward compatibility for one release.** `live_e2e/` survives as a deprecation shim that re-exports from `task_center_runner/`. The legacy rich `RunReport` survives as a derived view assembled by the shim from `PipelineReport` + bus events, not as a parallel return type.

### Decision Drivers (top 3)
1. **Hard constraints from user** (rename to `task_center_runner`; `SandboxProvisioner` protocol; unified workflow; `BenchmarkInstance` Protocol; async perf report).
2. **Independently mergeable phases** so a long refactor stays bisectable and any one phase can ship.
3. **Test cost** — keep `live_e2e/tests/` and `tests/unit_test/test_benchmarks/` green throughout. ~40 test files import `live_e2e.*` today.

### Viable Options

| | Option A: full restructure in one milestone | Option C (chosen): phased rename → restructure |
|---|---|---|
| Phase 1 ships | rename + restructured tree | rename only + shim, no internals moved |
| Reviewability | one big diff, hard to review | small green diffs, each independently mergeable |
| Bisect ergonomics | regression bisects land in one giant commit | regressions land in the phase that introduced them |
| Risk if a phase breaks | revert is large | revert is local |
| Total LOC moved | same | same |
| Time on main with mixed naming | none | ~1 milestone |

**Option B (rejected — violates hard constraint #1):** Keep `live_e2e/` as the canonical name and add `task_center_runner` as a thin facade. The user explicitly asked to **rename** to `task_center_runner/`; a facade in the wrong direction does not satisfy "rename + back-compat shim for one release." Invalidated.

**Decision: Option C.** A pure rename ships in Phase 1 with a shim; protocol extraction, async perf report, workflow unification, and internal layout split each ship as their own phase. Same end state as Option A; far cheaper to land safely.

---

## 2. Target Module Decomposition

```
task_center_runner/                              # canonical name; live_e2e/ becomes shim
├── core/
│   ├── engine.py                       # run_pipeline(config) — sole entrypoint
│   ├── config.py                       # RunConfig dataclass
│   ├── lifecycle.py                    # LifecycleHooks Protocol + NoopLifecycle
│   ├── report.py                       # PipelineReport (carries perf-report task)
│   ├── sandbox.py                      # SandboxProvisioner Protocol + AttachExisting helper
│   ├── stores.py                       # ← live_e2e/stores.py
│   ├── fixtures.py                     # ← live_e2e/fixtures.py + NEW pipeline_run fixture
│   └── bootstrap.py                    # ← live_e2e/real_agent_bootstrap.py
├── audit/
│   ├── bus.py  node_id.py  metrics.py  sandbox_events.py
│   ├── stream_bridge.py  legacy.py
│   ├── events.py                       # +4 MOCK_* event types
│   ├── recorder.py                     # dispose() no longer writes perf report
│   ├── performance_report.py           # build/render/write — schema string unchanged
│   └── io.py                           # NEW — public atomic_write_json/atomic_write_text
├── agent/
│   ├── real.py                         # real_agent_runner_factory() → returns None (LLM path)
│   └── mock/                           # ← live_e2e/squad/  (1:1 file move)
│       ├── runner.py                   # MockSquadRunner publishes MOCK_* events instead of mutating lists
│       ├── definitions.py  prompt_inspector.py  sandbox_probe.py
│       ├── tool_scripts.py  full_stack_tool_scripts.py
│       ├── complex_project_build_probe.py
│       ├── complex_project_build_shell_edit_lsp_probe.py
│       └── capacity_actions/
├── scenarios/                          # ← live_e2e/scenarios/  (1:1 file move)
│   ├── base.py                         # Scenario protocol (unchanged)
│   ├── lifecycle.py                    # NEW — ScenarioLifecycle (HookSet + state)
│   ├── builder.py                      # NEW — build_scenario_config(scenario, ...) → RunConfig
│   ├── correctness_testing.py  full_case_user_input.py  full_stack_adversarial.py  user_input.py
│   ├── pipeline/  capacity/  sandbox/  context/  tools/  planner_validation/
│   └── _utils/
├── hooks/                              # ← live_e2e/hooks/  (mock-only state holder)
│   ├── registry.py                     # Hook + HookSet + MutableMockState
│   └── builtins.py
├── benchmarks/
│   ├── base.py                         # BenchmarkInstance Protocol + BenchmarkAdapter Protocol
│   └── sweevo/
│       ├── adapter.py                  # SWEEvoBenchmark — implements BenchmarkAdapter
│       ├── lifecycle.py                # SweevoLifecycle.after_run = evaluate_sweevo_result
│       ├── provisioner.py              # SandboxProvisioner wrapping benchmarks.sweevo.sandbox
│       ├── prompt.py                   # ← thin re-export of benchmarks.sweevo.prompt
│       └── fixtures.py                 # ← live_e2e/sweevo_adapter.py pytest pieces
├── entrypoints/
│   ├── user_run.py                     # convenience: real LLM + freeform prompt
│   ├── benchmark_run.py                # convenience: real LLM + adapter + instance loop
│   └── __main__.py                     # awaits performance_report_task before exit
└── tests/                              # ← live_e2e/tests/  (1:1 file move; imports updated)

live_e2e/                               # SHIM — one-release back compat
├── __init__.py                         # silent re-exports (no DeprecationWarning — per user)
├── runner.py                           # run_scenario shim → run_pipeline + RunReport view
│                                       #   subscribes to MOCK_* events to rebuild legacy lists
├── real_agent_run.py                   # run_sweevo_real_agent shim
├── sweevo_adapter.py  stores.py  fixtures.py  real_agent_bootstrap.py
└── hooks/  scenarios/  squad/  audit/  (re-export targets)

backend/src/benchmarks/sweevo/          # UNCHANGED — data layer
└── models.py  dataset.py  prompt.py  sandbox.py  evaluation.py  __main__.py
```

**One-line responsibilities:**

- `core/engine.py` — assemble bus + recorder + sandbox provisioning + runner; drive `start_task_center_entry_run`; spawn async perf-report task; return `PipelineReport`. **Contains zero references to `MockSquadRunner`, `collect_extras`, `runner_extras`, or `hasattr` against runner attributes.**
- `core/config.py` — frozen `RunConfig` dataclass.
- `core/lifecycle.py` — `LifecycleHooks` Protocol with `before_run`, `after_run`, `on_aborted`, `on_event`; `NoopLifecycle` default.
- `core/report.py` — `PipelineReport` dataclass carrying `performance_report_task: asyncio.Task[Path] | None` + `lifecycle_extras: Mapping[str, Any]`.
- `core/sandbox.py` — `SandboxProvisioner` Protocol with `provision()` / `release()`; `AttachExisting(sandbox_id)` adapter for tests that pre-create a sandbox.
- `core/bootstrap.py` — `bootstrap_real_agent_runtime` (idempotent, real-LLM only).
- `core/fixtures.py` — `db_engine`, `stores`, `audit_dir` (existing) + new `pipeline_run` fixture that auto-awaits `performance_report_task` on teardown.
- `audit/io.py` — public `atomic_write_json` / `atomic_write_text` (was `_atomic_write_json` in `recorder.py`).
- `audit/events.py` — adds `MOCK_LAUNCH_RECORDED`, `MOCK_TOOL_CALL_RECORDED`, `MOCK_PROMPT_INSPECTED`, `MOCK_SANDBOX_CHECK_RECORDED`.
- `audit/recorder.py` — unchanged behavior except `dispose()` no longer writes perf report.
- `agent/mock/runner.py` — `MockSquadRunner` publishes 4 `MOCK_*` events at the existing append sites; the `self.launches`/`tool_calls`/`prompt_inspections`/`sandbox_checks` lists are removed (or kept internal-only for assertions inside the runner; Phase 4 deletes them).
- `scenarios/lifecycle.py` — `ScenarioLifecycle(hook_set, mutable_state)` implementing `LifecycleHooks.on_event` to fire post-hooks.
- `scenarios/builder.py` — `build_scenario_config(scenario, *, sandbox, audit_dir, repo_dir, entry_prompt, ...)` constructs the `MutableMockState`, the `MockSquadRunner` factory, and the `ScenarioLifecycle` so they share state.
- `benchmarks/base.py` — `BenchmarkInstance` (`instance_id: str`) and `BenchmarkAdapter` (`build_prompt`, `provisioner_for(instance)`, `evaluate(...)`).
- `benchmarks/sweevo/adapter.py` — `SWEEvoBenchmark` implementing `BenchmarkAdapter` over `benchmarks.sweevo.{prompt,sandbox,evaluation}`.
- `benchmarks/sweevo/lifecycle.py` — `SweevoLifecycle.after_run` calls `adapter.evaluate(...)` and writes `sweevo_result.json`.
- `entrypoints/__main__.py` — awaits `report.performance_report_task` before exit so the perf-report file is durable on CLI runs.

---

## 3. Public API Contracts

```python
# task_center_runner/core/config.py
@dataclass(frozen=True, slots=True)
class RunConfig:
    entry_prompt: str
    repo_dir: str
    sandbox: SandboxProvisioner                               # how to get/release a sandbox
    runner_factory: Callable[["RunContext"], AttemptAgentRunner | None]
                                                              # None → real LLM path
    lifecycle: LifecycleHooks = NoopLifecycle()
    bootstrap: Callable[[], None] | None = None               # real-agent only; mock leaves None
    stores: TaskCenterStoreBundle | None = None               # None → owned-per-run schema
    audit_dir: Path = Path(".sweevo_runs")
    run_label: str = "task_center_runner"                              # path segment under audit_dir
    run_dir_factory: Callable[[Path, "RunContext"], Path] | None = None
                                                              # default (unified per user): audit_dir/<run_label>/<utc>_<self_id>
                                                              # — same scheme for ALL modes; no per-mode override in the default.
    bridge_factory: Callable[[], TaskCenterSandboxBridge] | None = None
    instance_id: str = ""                                     # opaque tag for run.json
    max_duration_s: float | None = None                       # real-agent timeout cap
    extras: Mapping[str, Any] = field(default_factory=dict)   # adapter-specific opaque payload

# task_center_runner/core/sandbox.py
class SandboxProvisioner(Protocol):
    async def provision(self, ctx: "RunContext") -> SandboxLease: ...
    async def release(self, lease: SandboxLease) -> None: ...
    # Default semantic per user decision: release() DESTROYS the sandbox best-effort.
    # AttachExisting(sandbox_id) overrides release() to no-op so pre-provisioned test
    # sandboxes survive the run. Benchmark adapters keep destroy semantics by default.

@dataclass(frozen=True, slots=True)
class SandboxLease:
    sandbox_id: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

# task_center_runner/core/lifecycle.py
class LifecycleHooks(Protocol):
    async def before_run(self, ctx: "RunContext") -> None: ...
    def on_event(self, event: Event) -> None: ...             # fires on every audit Event
    async def after_run(self, ctx: "RunContext", report: "PipelineReport") -> None: ...
    async def on_aborted(self, ctx: "RunContext", reason: str) -> None: ...

class NoopLifecycle:                                          # default, satisfies Protocol
    async def before_run(self, ctx): pass
    def on_event(self, event): pass
    async def after_run(self, ctx, report): pass
    async def on_aborted(self, ctx, reason): pass

# task_center_runner/benchmarks/base.py
class BenchmarkInstance(Protocol):
    @property
    def instance_id(self) -> str: ...

class BenchmarkAdapter(Protocol):
    def build_prompt(self, instance: BenchmarkInstance, *, repo_dir: str) -> str: ...
    def provisioner_for(self, instance: BenchmarkInstance) -> SandboxProvisioner: ...
    async def evaluate(
        self,
        instance: BenchmarkInstance,
        *,
        sandbox_id: str,
        run_dir: Path,
        task_center_status: str | None,
        duration_s: float,
        task_count: int,
        tasks_completed: int,
        tasks_failed: int,
    ) -> Mapping[str, Any]: ...                               # written to run_dir/<benchmark>_result.json by lifecycle

# task_center_runner/audit/events.py — additions
class EventType(StrEnum):
    # ... (existing 50+ values unchanged) ...
    # mock-only side-channel events (consumed only by legacy live_e2e/ shim;
    # removed in next milestone with the shim).
    MOCK_LAUNCH_RECORDED = "mock_launch_recorded"             # payload: LaunchRecord fields
    MOCK_TOOL_CALL_RECORDED = "mock_tool_call_recorded"       # payload: ToolCallRecord fields
    MOCK_PROMPT_INSPECTED = "mock_prompt_inspected"           # payload: PromptInspection fields
    MOCK_SANDBOX_CHECK_RECORDED = "mock_sandbox_check_recorded"  # payload: SandboxCheck fields

# task_center_runner/core/report.py
@dataclass(slots=True)
class PipelineReport:
    status: Literal["completed", "aborted"]
    task_center_run_id: str
    request_id: str
    sandbox_id: str
    instance_id: str
    run_dir: Path
    task_center_status: str | None
    duration_s: float
    task_count: int
    tasks_completed: int
    tasks_failed: int
    metrics: Mapping[str, Any]                                # MetricsAggregator.snapshot()
    aborted_by_timeout: bool
    lifecycle_extras: Mapping[str, Any] = field(default_factory=dict)
                                                              # benchmark adapters stuff sweevo_result/...
                                                              # mock scenarios stuff hook_results/...
    performance_report_task: asyncio.Task[Path] | None = None # await for perf-report path

# task_center_runner/core/engine.py
async def run_pipeline(config: RunConfig) -> PipelineReport: ...
```

**Note:** `PipelineReport` has NO `runner_extras` field. The mock runner's side-channels (launches, tool calls, prompt inspections, sandbox checks) are emitted as `MOCK_*` audit events on the bus. Only the legacy `live_e2e/runner.py` shim subscribes to those events to reconstruct the rich `RunReport` view. Core has no name `MockSquadRunner`, no `hasattr(runner, ...)`, and no string `collect_extras` or `runner_extras` anywhere.

---

## 4. The Unified-Workflow Proof

`run_pipeline(config)` body (pseudocode, byte-identical for all three modes):

```python
async def run_pipeline(config: RunConfig) -> PipelineReport:
    if config.bootstrap is not None:
        config.bootstrap()                                    # real-agent only

    bundle = config.stores or create_per_test_task_center_stores()
    owns_stores = config.stores is None

    bus = AuditEventBus()
    bus.subscribe(config.lifecycle.on_event)                  # ← ScenarioLifecycle / SweevoLifecycle / Noop

    ctx = RunContext(config=config, bundle=bundle, bus=bus)
    await config.lifecycle.before_run(ctx)

    lease = await config.sandbox.provision(ctx)
    run_dir = (config.run_dir_factory or _default_run_dir)(config.audit_dir, ctx)
    recorder = AuditRecorder(run_dir, task_center_run_id="", bus=bus,
                             scenario_name=config.run_label,
                             instance_id=config.instance_id,
                             sandbox_id=lease.sandbox_id)
    recorder.start()

    runner = config.runner_factory(ctx)                       # None for real LLM path
    bridge = (config.bridge_factory or _default_bridge)()
    on_event = _build_stream_bridge(bus, recorder)            # same stream_bridge() as today

    started = time.perf_counter()
    aborted_by_timeout = False
    try:
        handle = start_task_center_entry_run(
            config=_runtime_config(config), prompt=config.entry_prompt,
            sandbox_id=lease.sandbox_id, on_agent_event=on_event,
            task_store=bundle.task_store, mission_store=bundle.mission_store,
            episode_store=bundle.episode_store, attempt_store=bundle.attempt_store,
            context_packet_store=bundle.context_packet_store,
            runner=runner, sandbox_bridge=bridge,
        )
        tcrid = str(handle.task_center_run_id)
        recorder.bind_task_center_run_id(tcrid)
        bus.publish(Event(EventType.RUN_STARTED, NodeId(task_center_run_id=tcrid)))
        try:
            if config.max_duration_s is not None:
                await asyncio.wait_for(handle.launcher.wait_for_idle(), timeout=config.max_duration_s)
            else:
                await handle.launcher.wait_for_idle()
        except asyncio.TimeoutError:
            aborted_by_timeout = True
            for task in tuple(handle.launcher._pending):
                task.cancel()
            await asyncio.gather(*handle.launcher._pending, return_exceptions=True)
            await config.lifecycle.on_aborted(ctx, "timeout")
        bus.publish(Event(EventType.RUN_COMPLETED, NodeId(task_center_run_id=tcrid)))

        run_row = bundle.task_store.get_run(tcrid) or {}
        task_rows = bundle.task_store.list_tasks_for_run(tcrid)
        snapshot = recorder.metrics.performance_snapshot()    # ← BEFORE dispose
        metrics = recorder.metrics.snapshot()
    finally:
        await config.sandbox.release(lease)
        recorder.dispose()                                    # sync; writes run.json + metrics.json
        if owns_stores:
            bundle.close()

    perf_task = asyncio.create_task(
        _write_perf_report_safe(run_dir, snapshot),
        name=f"perf_report:{tcrid}",
    )

    report = PipelineReport(
        status="aborted" if aborted_by_timeout else "completed",
        task_center_run_id=tcrid, request_id=str(handle.request_id),
        sandbox_id=lease.sandbox_id, instance_id=config.instance_id,
        run_dir=run_dir, task_center_status=run_row.get("status"),
        duration_s=time.perf_counter() - started,
        task_count=len(task_rows),
        tasks_completed=sum(1 for r in task_rows if r.get("status") == "done"),
        tasks_failed=sum(1 for r in task_rows if r.get("status") == "failed"),
        metrics=metrics, aborted_by_timeout=aborted_by_timeout,
        performance_report_task=perf_task,
    )
    await config.lifecycle.after_run(ctx, report)             # benchmark.evaluate fires here;
                                                              # mutates report.lifecycle_extras
    return report
```

**Mode-specific differences (the ONLY differences — exactly 5 rows):**

| Concern | Mock scenario | Real-agent freeform | SWE-EVO benchmark |
|---|---|---|---|
| `config.runner_factory` | `lambda ctx: MockSquadRunner(scenario=s, mutable_state=state, bus=ctx.bus, ...)` | `lambda ctx: None` | `lambda ctx: None` |
| `config.bootstrap` | `None` | `bootstrap_real_agent_runtime` | `bootstrap_real_agent_runtime` |
| `config.lifecycle` | `ScenarioLifecycle(hook_set, state)` | `NoopLifecycle()` | `SweevoLifecycle(adapter, instance)` |
| `config.sandbox` | `AttachExisting(sandbox_id)` (test pre-provisioned) | user-supplied `SandboxProvisioner` | `SWEEvoBenchmark.provisioner_for(instance)` |
| `config.run_label` | `"scenario_logs/<name>"` | `"user_run"` | `"benchmark/sweevo/<instance_id>"` |

**Where scenario hook firing happens.** `bus.subscribe(config.lifecycle.on_event)` once at engine startup. `ScenarioLifecycle.on_event(event)` appends to `state.seen_events`, then `hook_set.fire(event, "post", state)`, then stashes `hook_results` in itself. After the run, `ScenarioLifecycle.after_run(ctx, report)` writes `report.lifecycle_extras["hook_results"]`.

**Where mock-only instrumentation goes.** `MockSquadRunner` publishes the 4 new `MOCK_*` events to its `self._bus` (already wired via constructor) at the call sites that today mutate `self.launches` / `self.tool_calls` / `self.prompt_inspections` / `self.sandbox_checks`:

| Event | Source line (current `live_e2e/squad/runner.py`) |
|---|---|
| `MOCK_LAUNCH_RECORDED` | line 185 — replaces `self.launches.append(LaunchRecord(...))` |
| `MOCK_PROMPT_INSPECTED` | line 194 — replaces `self.prompt_inspections.append(self._inspect_prompt(...))` |
| `MOCK_TOOL_CALL_RECORDED` | lines 1124–1131 inside `_call_tool` — replaces `self.tool_calls.append(ToolCallRecord(...))` |
| `MOCK_SANDBOX_CHECK_RECORDED` | every `self.sandbox_checks.append(SandboxCheck(...))` site (sprinkled throughout `_run_*_probe` methods, `_record_tool_check`, `_assert_read_contains`, `_run_batch_edit`, `_run_expected_conflict`, `_run_auto_squash_commit_resume_probe`). Note: `self.sandbox_checks` is also passed by reference into helper closures at `squad/runner.py:1005` and `:1029` adjacent to `publish=self._publish` kwargs; Phase 4 must thread bus publication through those helpers as well, not just replace top-level `.append` sites. |

The legacy `live_e2e/runner.py` shim subscribes to those four event types when assembling its `RunReport` view, accumulating into the same lists the old `RunReport` carried. **This is the only place that knows about the mock-only event shapes; core/engine remains literally runner-agnostic.**

**Shared `MutableMockState` rule.** `build_scenario_config(scenario)` constructs *one* `MutableMockState`, hands it to both the `MockSquadRunner` (via factory closure) and the `ScenarioLifecycle`. This is the only place where `consume_next_planner_response()` and `inject_failure()` are wired together. Outside this builder, no module imports `MutableMockState`. **Core has no name `MutableMockState` anywhere.**

---

## 5. Async Performance-Report Design

**Where the task is created.** Inside `run_pipeline`, *after* the recorder is disposed but *before* the `PipelineReport` is returned. NOT inside `recorder.dispose()` (keeps recorder sync + idempotent + unit-testable in isolation).

**Snapshot timing.** `recorder.metrics.performance_snapshot()` is captured **before** `recorder.dispose()`. The recorder's listeners are still attached at snapshot time, but `MetricsAggregator` state is in-memory and the snapshot is a deep render. `sandbox_events.jsonl` is flushed to disk by `append_jsonl_event` per row, so `dispose()` only finalizes counters; the perf-report writer reads the JSONL safely after dispose.

**The wrapper** (lives in `task_center_runner/audit/performance_report.py`):

```python
async def _write_perf_report_safe(
    run_dir: Path, snapshot: Mapping[str, Any]
) -> Path:
    try:
        await asyncio.to_thread(write_performance_reports, run_dir, snapshot)
    except BaseException as exc:                              # noqa: BLE001 — never crash the run
        logger.warning(
            "Async perf-report failed for %s: %s", run_dir, exc, exc_info=True,
        )
    return run_dir / "performance_report.json"                # caller checks existence
```

**Caller contract.** CLI/`__main__` MUST `await report.performance_report_task` before exit; this is enforced by `test_cli_awaits_perf_report.py` (see §8). Test code MUST go through the `pipeline_run` pytest fixture (added in `task_center_runner/core/fixtures.py`), which auto-awaits `report.performance_report_task` at teardown if non-None and logs the resulting path. Application code that drives `run_pipeline` directly is responsible for awaiting; the `pipeline_run` fixture exists so tests cannot forget.

**Cancellation.** If the awaiting caller cancels its task tree before perf-report finishes, the asyncio task is cancelled; `to_thread` work continues briefly until the next checkpoint. We accept that — perf reports are observability, not correctness.

**Failure semantics.** Perf-report failures are logged with `exc_info=True` and never propagated. The pipeline always returns its `PipelineReport` even if perf-report writes fail. Downstream consumers must tolerate `performance_report.json` missing.

**Test for failure isolation:** Phase 3 includes `test_perf_report_failure_isolated.py` (see §7 Risk #2 + §8).

---

## 6. Migration Phases (5; each independently mergeable)

### Phase 1 — Pure rename + back-compat shim
- **Scope:** `git mv backend/src/live_e2e backend/src/task_center_runner`. Recreate `backend/src/live_e2e/` containing thin re-export modules (one per public file) that **silently** re-export from `task_center_runner.*` — **no `DeprecationWarning` per user decision; shim removal is the migration signal, not import-time noise.** Update internal `task_center_runner` imports `from live_e2e.X` → `from task_center_runner.X`. Update `tests/conftest.py` `pytest_plugins` to canonical `task_center_runner.core.fixtures` (NOT `live_e2e.fixtures` — pytest cannot tolerate a fixture name reachable through two import paths).
- **Files touched:** every `*.py` under `backend/src/live_e2e/` (move). New shim files at `backend/src/live_e2e/`. ~40 test files that import `from live_e2e.*` are NOT touched here — the shim makes them green.
- **Tests proving green:** existing `live_e2e/tests/test_runner_imports.py`, `test_scenario_suite_imports.py`, `test_stores.py`, `test_sweevo_adapter_lock.py`; `tests/unit_test/test_benchmarks/test_sweevo_*` (still importing from `live_e2e` paths). ALL must pass with no source changes outside `live_e2e/` shim and `task_center_runner/` rename.
- **Rollback:** revert one commit; the shim is purely additive.

### Phase 2 — Extract protocols + promote `_atomic_write_json` + add MOCK_* event types
- **Scope:** introduce `task_center_runner/core/{config,lifecycle,report,sandbox}.py`, `task_center_runner/audit/io.py`, `task_center_runner/benchmarks/base.py`. Promote `_atomic_write_json`/`_atomic_write_text` from `audit/recorder.py` and `audit/performance_report.py` into `audit/io.py` (keep underscore aliases as re-exports for one phase). Add the 4 new `MOCK_*` `EventType` values to `audit/events.py`. Old `run_scenario` and `run_sweevo_real_agent` continue working unchanged — the new types are not yet wired.
- **Files touched:** `task_center_runner/audit/recorder.py`, `task_center_runner/audit/performance_report.py`, `task_center_runner/audit/events.py`, `task_center_runner/real_agent_run.py` (replace private import). New: 5 protocol/dataclass files.
- **Tests proving green:** all Phase 1 tests stay green. New unit test in `tests/unit_test/test_task_center_runner/test_protocols.py` that instantiates `RunConfig`/`SandboxProvisioner`/`BenchmarkAdapter` stubs and asserts shapes. New `test_mock_event_types.py` asserts the 4 enum values exist with their string names.
- **Rollback:** revert; protocols and event types are unused by runtime code.

### Phase 3 — Async performance report + `pipeline_run` fixture
- **Scope:** remove `write_performance_reports` call from `AuditRecorder.dispose()`. Add `_write_perf_report_safe` wrapper to `task_center_runner/audit/performance_report.py`. Update `task_center_runner/runner.py` (the still-existing `run_scenario`) and `task_center_runner/real_agent_run.py` to: snapshot pre-dispose, dispose, spawn `asyncio.create_task(_write_perf_report_safe(...))`, return the task on the report. Update `RunReport` and `RealAgentRunReport` to expose `performance_report_task: asyncio.Task | None`. Add `pipeline_run` fixture to `task_center_runner/core/fixtures.py` (yields a callable returning `PipelineReport`; on teardown, `await report.performance_report_task` if non-None and log the resulting path).
- **Files touched:** `task_center_runner/audit/recorder.py`, `task_center_runner/audit/performance_report.py`, `task_center_runner/runner.py`, `task_center_runner/real_agent_run.py`, `task_center_runner/core/fixtures.py`, plus the legacy shim's `RunReport` view. Update `tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py:565`-area: split into "recorder dispose writes metrics.json + run.json" (sync) and "perf report is produced via `write_performance_reports` directly" (the schema string assertion moves there).
- **Tests proving green:** the recorder test split passes. `test_async_perf_report.py` asserts `await report.performance_report_task` returns a path that exists. `test_perf_report_failure_isolated.py` (see §7 Risk #2) asserts failure isolation. `test_cli_awaits_perf_report.py` (see §8) asserts CLI awaits before exit.
- **Rollback:** revert; the scenario/real-agent paths fall back to sync writes inside dispose.

### Phase 4 — Unify the workflow under `run_pipeline` (mock runner publishes MOCK_* events)
- **Scope:** implement `task_center_runner/core/engine.py:run_pipeline`. Add `task_center_runner/scenarios/lifecycle.py:ScenarioLifecycle` and `task_center_runner/scenarios/builder.py:build_scenario_config`. Add `task_center_runner/benchmarks/sweevo/{adapter,lifecycle,provisioner,fixtures,prompt}.py`. **Modify `MockSquadRunner` to publish the 4 `MOCK_*` events at the call sites listed in §4** — remove `self.launches` / `self.tool_calls` / `self.prompt_inspections` / `self.sandbox_checks` attributes. Reduce `task_center_runner/runner.py:run_scenario` to a thin shim: build a `RunConfig` via `build_scenario_config`, subscribe to `MOCK_*` events, call `run_pipeline`, return a `RunReport` view assembled from `PipelineReport` + accumulated event payloads + `lifecycle_extras["hook_results"]`. Reduce `task_center_runner/real_agent_run.py:run_sweevo_real_agent` to a shim: build a `RunConfig` via `SWEEvoBenchmark` + `SweevoLifecycle`, call `run_pipeline`, return a `RealAgentRunReport` view.
- **Files touched:** `task_center_runner/agent/mock/runner.py` (publish events; remove list attributes), `task_center_runner/runner.py` (slim to ~60 lines including event accumulator), `task_center_runner/real_agent_run.py` (slim to ~40 lines), 5 new files under benchmarks/sweevo, 2 new files under scenarios/.
- **Tests proving green:** `live_e2e/tests/sweevo/test_real_agent.py`, `test_correctness_via_live_e2e.py`, `test_focused_scenarios.py` all pass unchanged. New invariant test `test_unified_workflow_invariant.py` (see §8). New `test_no_core_imports.py` asserts core has no `hasattr`/`collect_extras`/`runner_extras`/`MockSquadRunner` strings.
- **Rollback:** revert; the previous bespoke `run_scenario` / `run_sweevo_real_agent` implementations resurface.

### Phase 5 — Internal restructure (file moves only)
- **Scope:** `mv` `task_center_runner/squad/` → `task_center_runner/agent/mock/`; create `task_center_runner/agent/real.py`; create `task_center_runner/entrypoints/`; move `task_center_runner/{stores,fixtures,real_agent_bootstrap,runner,real_agent_run,sweevo_adapter}.py` → `task_center_runner/core/` (and `benchmarks/sweevo/fixtures.py`). Update `live_e2e/` shim to reflect new internal paths (its public surface stays identical). NO behavioral change.
- **Files touched:** every `task_center_runner/` file moves; `live_e2e/` shim re-export targets updated.
- **Tests proving green:** every test from prior phases. Shim integration test asserts `from live_e2e.runner import run_scenario` still works.
- **Rollback:** revert the move commit.

**Shim removal** is explicitly out of scope for this milestone. Tracked as ADR follow-up: "remove `backend/src/live_e2e/` after one release; update all `from live_e2e.*` imports to `from task_center_runner.*`; remove the 4 `MOCK_*` event types from `audit/events.py` along with the shim that consumed them."

---

## 7. Risks and Mitigations

| # | Risk | Mitigation |
|---|---|---|
| 1 | **`pytest_plugins = ["live_e2e.fixtures"]`** in two conftests is fragile under rename — pytest can collect the same fixture from both shim + canonical paths and refuse with "duplicate fixture name." | Phase 1 updates conftests to canonical `task_center_runner.core.fixtures` only. The shim's `live_e2e.fixtures` re-exports from `task_center_runner` but is NOT registered as a `pytest_plugins`. Add a regression test that pytest can collect tests under `live_e2e/tests/` after rename. |
| 2 | **Async perf-report swallowing failures silently.** A real bug in `write_performance_reports` could go unnoticed. | Phase 3 adds `test_perf_report_failure_isolated.py` that monkeypatches `write_performance_reports` to raise `RuntimeError`; asserts the run returns a `PipelineReport` normally AND `caplog.records` contains a WARNING with substring `Async perf-report failed`. |
| 3 | **Latent runner↔scenarios cycle** (pre-existing `MockSquadRunner.__init__` does a deferred `from live_e2e.scenarios.correctness_testing import CorrectnessTesting`). | Preserved as-is; document at the top of `task_center_runner/agent/mock/runner.py` that the late import is intentional. Add an import-cycle smoke test that imports `task_center_runner.core.engine`, `task_center_runner.scenarios`, `task_center_runner.agent.mock.runner` in all six orderings. |
| 4 | **`RunReport` rich fields** (`launches`, `tool_calls`, `prompt_inspections`, `sandbox_checks`, `graph_summary`, `requirement_ledger`, `package_plan`, `matrix_plan`) disappearing from real-agent mode would silently change downstream consumers. | Before Phase 4 lands, capture a `RunReport` instance via `dataclasses.asdict` from a `CorrectnessTesting` scenario run on `main` and check in as `tests/golden/run_report_correctness_testing.json`. Phase 4 contract test asserts the shim-produced `RunReport`, after `dataclasses.asdict`, equals the golden dict (modulo non-deterministic fields: `task_center_run_id`, `request_id`, `run_dir`, `duration_s`, `entry_prompt_sha256`). Real-agent path returns `RealAgentRunReport` (no rich mock fields). |
| 5 | **Perf-report schema string `"live_e2e.performance_report.v1"`** is asserted in a unit test and likely consumed by downstream graders/dashboards. | Decision per user: **bump the schema string to `task_center_runner.performance_report.v2` in Phase 3** alongside the async perf-report split. Same commit updates the test assertion in `tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py` and any downstream consumers. **Pre-Phase-3 audit:** run `grep -rn "live_e2e.performance_report.v1" backend/ docs/ scripts/` and surface any non-repo consumer to the user before merging Phase 3. |
| 6 | **`run_dir` path scheme** is mode-specific today (`scenario_logs/<name>/<utc>` vs `real_agent/<instance_id>/<utc>`). Unifying breaks `EOS_TIER_RUN_ID`-based resume in `run_tiered.py`. | Per user decision: **unify all modes on the canonical scheme `audit_dir/<run_label>/<utc>_<self_id>`** — no per-mode customization in the default factory. Mode identity comes from `run_label` (`scenario_logs/<name>` for mock; `user_run` for real freeform; `benchmark/sweevo/<instance_id>` for SWE-EVO). **Known impact:** `backend/tests/live_e2e_test/_tools/run_tiered.py` resume path-mapper must be updated in Phase 4 to discover runs under the new layout (one-time migration; document in commit message). Phase 4 adds `test_run_dir_canonical_scheme.py` asserting all three modes produce paths matching `audit_dir/<run_label>/<utc>_<12hex>`. |
| 7 | **`bootstrap_real_agent_runtime` invoked in mock mode** would corrupt the agent registry (mock uses `registered_mock_agents` context manager that unregisters all definitions on entry). | `RunConfig.bootstrap` defaults to `None`; `build_scenario_config` never sets it. A unit test asserts `bootstrap is None` in the config produced by `build_scenario_config`. |

---

## 8. Test Strategy

**Goal:** every existing test stays green at every phase boundary; new invariant tests prove the unified workflow + the runner-agnostic core.

### Tests that must remain green at all 5 phases
- `backend/src/live_e2e/tests/test_runner_imports.py` — public re-exports.
- `backend/src/live_e2e/tests/test_scenario_suite_imports.py` — `SCENARIO_REGISTRY` shape.
- `backend/src/live_e2e/tests/test_stores.py` — per-test PG schema isolation.
- `backend/src/live_e2e/tests/test_sweevo_adapter_lock.py` — flock semantics.
- `backend/tests/unit_test/test_live_e2e/test_sweevo_adapter.py` — adapter pytest fixtures.
- `backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py` — recorder + perf report (Phase 3 splits the perf-report assertion into its own test).
- `backend/tests/unit_test/test_benchmarks/test_sweevo_sandbox_event_monitor.py` — stream bridge → bus.
- `backend/tests/unit_test/test_benchmarks/test_sweevo_mock_agent_execution.py` — `run_sweevo_scenario` end-to-end.
- `backend/tests/unit_test/test_live_e2e_tools/test_shell_edit_lsp_probe.py` — tool probes.

### NEW tests proving the unified workflow + runner-agnostic core (Phase 3 + Phase 4)

The **`pipeline_run` fixture** (added in Phase 3, lives in `task_center_runner/core/fixtures.py`) is the canonical test surface — it auto-awaits `report.performance_report_task` on teardown so individual tests cannot leak unfinished perf-report tasks.

- `tests/unit_test/test_task_center_runner/test_unified_workflow_invariant.py`:
  - Uses `pipeline_run` fixture.
  - Build a mock `RunConfig` via `build_scenario_config(CorrectnessTesting())`.
  - Build a real-agent `RunConfig` for a stub instance.
  - Build a SWE-EVO `RunConfig` for a stub instance.
  - Patch `task_center_runner.core.engine.start_task_center_entry_run` and assert all three configs reach it with byte-identical kwargs *except* `runner` and the lifecycle subscription.
  - Assert `runner is None` for real + SWE-EVO; `isinstance(runner, MockSquadRunner)` for mock.
  - Assert all three set up exactly one `bus.subscribe(lifecycle.on_event)` (introspect bus handler list length).
- `tests/unit_test/test_task_center_runner/test_async_perf_report.py`:
  - Uses `pipeline_run` fixture.
  - Run a stub pipeline; assert `report.performance_report_task` is `asyncio.Task`; await it; assert `performance_report.json` exists.
- `tests/unit_test/test_task_center_runner/test_perf_report_failure_isolated.py`:
  - Monkeypatch `write_performance_reports` to raise `RuntimeError`.
  - Run a stub pipeline; assert run returns a `PipelineReport` normally.
  - Assert `caplog.records` contains a WARNING with substring `Async perf-report failed`.
- `tests/unit_test/test_task_center_runner/test_cli_awaits_perf_report.py`:
  - Run `python -m task_center_runner` (or whichever `__main__` entry exists post-Phase 4) against a stub config in a tmp `audit_dir`.
  - Assert process exit code 0.
  - Assert `performance_report.json` exists in the run_dir at process exit.
- `tests/unit_test/test_task_center_runner/test_no_core_imports.py`:
  - Static import-graph test: `task_center_runner.core.*` modules must not import `MockSquadRunner`, `MutableMockState`, Daytona, or any `benchmarks.sweevo.*` symbol.
  - Source-string test: every `task_center_runner/core/*.py` source contains none of the following substrings: `hasattr(`, `getattr(runner`, `isinstance(runner`, `collect_extras`, `runner_extras`. The import-graph test above already blocks symbol-level reach to `MockSquadRunner`, so an `isinstance` check would fail at lookup; this source-string belt-and-suspenders catches the canonical Python escape valves (`getattr`, `isinstance`) at review time before they reach a reviewer.
- `tests/unit_test/test_task_center_runner/test_mock_events_published.py` (Phase 4):
  - Build a `MockSquadRunner` with a captured-events bus subscriber.
  - Run a `CorrectnessTesting` scenario through `run_pipeline`.
  - Assert at least one of each `MOCK_LAUNCH_RECORDED` / `MOCK_TOOL_CALL_RECORDED` / `MOCK_PROMPT_INSPECTED` / `MOCK_SANDBOX_CHECK_RECORDED` was published with the expected payload shape.
- `tests/golden/run_report_correctness_testing.json` (captured pre-Phase-4):
  - Consumed by `test_run_report_golden.py` (Phase 4): asserts shim-produced `RunReport.asdict()` equals golden modulo non-deterministic fields.

### Real-LLM/Daytona tests (gated by env vars, run on demand)
- `live_e2e/tests/sweevo/test_real_agent.py` — full real-agent path, exercised manually post-Phase-4 to confirm no regressions.

### Test-cost guardrail
Run order during each phase merge (per project memory `feedback_use_venv_pytest.md` — global pytest reports ~88 spurious failures because pytest-asyncio isn't loaded; the uv venv has it):

1. `cd backend && .venv/bin/pytest tests/unit_test -x` (all unit tests, no Daytona/PG).
2. `cd backend && .venv/bin/pytest src/live_e2e/tests -x` (PG required, no Daytona).
3. **Merge gate per phase + final migration pass condition (per user):** all mocked scenario tests pass. Run:
   ```
   cd backend && .venv/bin/pytest src/live_e2e/tests/sweevo -x -k "not test_real_agent" --no-header
   ```
   This covers the entire `live_e2e/tests/sweevo/` mock-scenario suite — `test_correctness.py`, `test_correctness_via_live_e2e.py`, `test_focused_scenarios.py`, `test_full_case_user_input.py`, `test_full_stack_adversarial.py`, `test_partial_parent_planner_full_only.py`, `test_complex_project_build.py`, `test_complex_project_build_fixtures.py`, `test_complex_project_build_shell_edit_lsp.py`, `test_auto_squash_commit_resume.py` — exercising every scenario family (pipeline / sandbox / capacity / planner_validation / context) through the unified `run_pipeline` (post-Phase 4). `-k "not test_real_agent"` excludes the gated real-LLM smoke. **Final migration pass condition: this command exits 0 at the end of Phase 5 with the `live_e2e/` shim still active.**

4. (Optional, gated, not part of the merge gate) Manual real-agent smoke after Phase 4:
   ```
   python -m benchmarks.sweevo --real-agent --instance-id=dask__dask_2023.3.2_2023.4.0
   ```
   - **Required env vars** (verified from `<repo>/models/registry.json` — `active="minimax"`, model `MiniMax-M2.7` via `providers.clients.anthropic_native.AnthropicClient`): `MINIMAX_API_KEY`, `MINIMAX_BASE_URL`, `DAYTONA_API_KEY` (sandbox provisioning), `EPHEMERALOS_DATABASE_URL` (PG stores).
   - **LLM wiring detail:** Each `task_center` invocation consumes the *currently-active* model via `db.stores.model_store.ModelStore.get_active()`. `runtime.app_factory.ensure_runtime_stores_ready()` (called by `bootstrap_real_agent_runtime()`) seeds the `ModelRegistrationRecord` table from `<repo>/models/registry.json` on first call. Single active model at a time, project-wide. To swap LLMs: edit `models/registry.json` `active` field, OR call `model_store.set_active("<key>")` directly against the DB. The `task_center_runner` plan does not change this wiring.
   - **Success assertion:** exit code 0 AND `sweevo_result.json` exists in `run_dir` AND `performance_report.json` exists in `run_dir`.

---

## 9. ADR

**Decision.** Adopt `task_center_runner/` as the canonical name with a unified `run_pipeline(config)` entrypoint. Mock scenarios, real-agent runs, and benchmark runs share the pipeline and differ only in `RunConfig.runner_factory` and `RunConfig.lifecycle`. Mock-only side-channels (launches, tool calls, prompt inspections, sandbox checks) are emitted as 4 new `MOCK_*` audit events on the bus and consumed only by the legacy shim — core is literally runner-agnostic. Performance reports are emitted asynchronously after the TaskCenter loop drains. `live_e2e/` survives as a deprecation shim for one release.

**Drivers.**
1. Existing duplication: `live_e2e/runner.py:run_scenario` and `live_e2e/real_agent_run.py:run_sweevo_real_agent` reimplement bus + recorder + bridge wiring with diverging variants.
2. Sandbox provisioning leaks into core (real-agent path imports `bootstrap_daytona_provider`; tests pre-create a sandbox out-of-band).
3. Synchronous perf-report inside `recorder.dispose()` (~hundreds of ms wall time + JSONL re-read) blocks the caller for no functional reason.
4. Hard constraints from the user: rename direction, `SandboxProvisioner` protocol, single workflow, async perf report, `BenchmarkInstance` Protocol.

**Alternatives considered.**
- **Option A — full restructure in one milestone.** Same end state. Rejected for this milestone in favor of phased landing because of bisect/review cost; Option C reaches the same code through 5 small mergeable diffs.
- **Option B — keep `live_e2e/` as canonical, add `task_center_runner` facade.** Rejected: violates user hard constraint #1 (the rename direction is `live_e2e → task_center_runner`, not the reverse).
- **(rejected mid-iteration) `runner.collect_extras() → runner_extras` on PipelineReport.** Rejected: violates principle #2 — core would be calling `getattr(runner, "collect_extras", ...)` which encodes a runner-shape assumption. Replaced with bus-event publication so the runner's instrumentation flows through the same pipe as every other audit event.

**Why chosen.** Option C minimizes per-merge risk (each phase is independently green and revertable) while reaching the exact end state requested by the user. The async perf-report design keeps `AuditRecorder` synchronous and idempotent, which is the safer interface for the recorder to keep. Mock-only side-channels emitted as `MOCK_*` audit events keep core literally runner-agnostic — the engine never names `MockSquadRunner` and never introspects runner attributes. The `LifecycleHooks.on_event` seam reuses the existing bus subscription pattern, without giving core any knowledge of `MutableMockState`.

**Consequences.**
- One release of double-naming (`live_e2e/` shim + `task_center_runner/` canonical). The shim is **silent** (no `DeprecationWarning` per user decision) — shim removal is the migration trigger, not import-time noise.
- New required protocol in `RunConfig.sandbox` — every caller (CLI, pytest fixtures, ad-hoc scripts) must either pass a real `SandboxProvisioner` or use `AttachExisting(sandbox_id)`. The benchmark/sweevo `__main__` is updated; ad-hoc scripts may break.
- `PipelineReport` carries an unfinished `asyncio.Task`; CLI callers must explicitly `await` it before exit (enforced by `test_cli_awaits_perf_report.py`); test code must go through the `pipeline_run` fixture.
- The recorder is no longer responsible for perf-report production — anyone who reads `metrics.json` synchronously after dispose is fine; anyone who relied on perf-report files being present at dispose-return must now await the task.
- Existing rich `RunReport` fields stay intact via the legacy shim, which subscribes to the 4 `MOCK_*` events; the new `PipelineReport` is intentionally narrower (no `runner_extras`).
- The 4 `MOCK_*` `EventType` values are emitted by the mock runner and consumed only by the legacy `live_e2e/` shim — they will be removed in the next milestone alongside the shim. Until then, real-agent runs simply never emit them, so downstream subscribers tolerate their absence.

**Follow-ups (next milestone).**
1. Remove `backend/src/live_e2e/` shim after one release; rewrite all `from live_e2e.*` imports to `from task_center_runner.*`. Remove the 4 `MOCK_*` event types from `audit/events.py` together with the shim's subscriber.
2. ~~Bump perf-report schema string~~ — **landed in Phase 3** per user decision (see Risk #5 for the consumer-audit checklist).
3. Promote `BenchmarkInstance` Protocol to a project-wide module (currently SWE-EVO is the only consumer; add HumanEval/SWE-bench adapters here).
4. Investigate hoisting `bridge_factory` into `SandboxProvisioner` so sandbox + bridge are owned by one Protocol.
5. (Stretch) Replace `MutableMockState`'s implicit shared-mutation channel between `MockSquadRunner` and `ScenarioLifecycle` with an explicit request/response pattern over the bus, so `consume_next_planner_response()` and `inject_failure()` become observable in audit events. Distinct from follow-up #1; this would let scenarios run without sharing in-process state.

---

## Open Questions (RESOLVED — handoff)

All 5 questions resolved by user 2026-05-15. See §10 Handoff Brief for the locked-in answers.

1. ~~Perf-report schema rename~~ → **RESOLVED:** bump to `task_center_runner.performance_report.v2` in Phase 3 (with pre-merge consumer audit per Risk #5).
2. ~~`RunConfig.run_dir_factory` default~~ → **RESOLVED:** unify on `audit_dir/<run_label>/<utc>_<self_id>` for all modes; Phase 4 updates `run_tiered.py` resume path-mapper.
3. ~~`SandboxProvisioner.release` semantics~~ → **RESOLVED:** default destroys (best-effort); `AttachExisting` overrides to no-op.
4. ~~Shim DeprecationWarning frequency~~ → **RESOLVED:** drop entirely; shim is silent.
5. ~~LLM provider key~~ → **RESOLVED:** active model is `minimax` (MiniMax-M2.7) per `<repo>/models/registry.json`; env vars `MINIMAX_API_KEY` + `MINIMAX_BASE_URL`. See §8 step 4.

---

## 10. Handoff Brief

This section is the executor's quick-reference. All decisions in §1-9 are locked through 3 ralplan iterations (Planner ↔ Architect ↔ Critic) plus one user-decision pass; re-opening any closed item costs time, not quality.

### 10.1 Locked decisions (user 2026-05-15)

| # | Decision | Source |
|---|---|---|
| 1 | Package rename `live_e2e/` → `task_center_runner/` (silent shim, one release) | User constraint #1; §1 principle #5 |
| 2 | Unified workflow — mock / real-agent / benchmark share `run_pipeline(config)`; differ only in `runner_factory` + `lifecycle` + `bootstrap` + `sandbox` + `run_label` | User constraint #3; §4 5-row mode-delta table |
| 3 | `SandboxProvisioner` Protocol owns provisioning; `release()` **destroys** by default; `AttachExisting` overrides to no-op | User 2026-05-15; §3 contract |
| 4 | `BenchmarkInstance`/`BenchmarkAdapter` Protocols in `benchmarks/base.py`; SWE-EVO data layer at `backend/src/benchmarks/sweevo/` UNCHANGED | User constraint #4; §2 tree |
| 5 | Async perf-report via `asyncio.create_task` after `recorder.dispose()`; `pipeline_run` fixture auto-awaits at teardown; CLI test asserts `__main__` awaits before exit | User constraint #5; §5 |
| 6 | 4 `MOCK_*` event types live in production `EventType` enum; core has zero `hasattr`/`getattr(runner`/`isinstance(runner`/`collect_extras`/`runner_extras` | Critic iter-2 (rejected `MockEventType` separation as bikeshedding); §3 events + §8 source-string + import-graph tests |
| 7 | Perf-report schema string bumps to `task_center_runner.performance_report.v2` in Phase 3 (with `grep -rn live_e2e.performance_report.v1` consumer audit first) | User 2026-05-15; §7 Risk #5 |
| 8 | `run_dir` unified on canonical `audit_dir/<run_label>/<utc>_<self_id>` for all modes; Phase 4 updates `run_tiered.py` resume path-mapper | User 2026-05-15; §7 Risk #6 |
| 9 | `live_e2e/` shim is **silent** — no `DeprecationWarning` | User 2026-05-15; §6 Phase 1 |
| 10 | LLM wiring: active model `minimax` (MiniMax-M2.7) via `<repo>/models/registry.json` → `db.stores.model_store.ModelStore` (`get_active()` returns the row, `runtime.app_factory.ensure_runtime_stores_ready()` seeds it on first call). Env vars: `MINIMAX_API_KEY`, `MINIMAX_BASE_URL`. Plan does **not** change this wiring. | User 2026-05-15; §8 step 4 |
| 11 | Migration pass condition: all mocked scenario tests pass at end of Phase 5 | User 2026-05-15; §8 step 3 |

### 10.2 The merge gate (binding for every phase + final migration pass)

```bash
cd backend && .venv/bin/pytest src/live_e2e/tests/sweevo -x -k "not test_real_agent" --no-header
```

Exit code 0 = green. The real-agent smoke (§8 step 4) is OPTIONAL and not part of the gate.

Pre-merge order for every phase:
1. `cd backend && .venv/bin/pytest tests/unit_test -x` — unit tests, no Daytona/PG.
2. `cd backend && .venv/bin/pytest src/live_e2e/tests -x` — PG required, no Daytona.
3. The merge gate above.

Always `.venv/bin/pytest` — never global `pytest` (project memory `feedback_use_venv_pytest.md`: ~88 spurious failures from missing pytest-asyncio).

### 10.3 Phase execution order (5 phases, each independently mergeable)

| Phase | Scope | Merge command (in addition to §10.2 gate) |
|---|---|---|
| 1 | Pure rename `git mv` + silent shim modules | nothing extra |
| 2 | Extract Protocols (`config`/`lifecycle`/`report`/`sandbox`/`base`) + add 4 `MOCK_*` `EventType` values + promote `_atomic_write_json` → `audit/io.py` | `test_protocols.py`, `test_mock_event_types.py` |
| 3 | Async perf-report + `pipeline_run` fixture + bump schema → v2 + consumer audit | recorder split + `test_async_perf_report.py` + `test_perf_report_failure_isolated.py` + `test_cli_awaits_perf_report.py` |
| 4 | Unify under `run_pipeline`; `MockSquadRunner` publishes `MOCK_*` events; slim `run_scenario`/`run_sweevo_real_agent` shims; update `run_tiered.py` resume path-mapper | `test_unified_workflow_invariant.py`, `test_no_core_imports.py`, `test_mock_events_published.py`, `test_run_dir_canonical_scheme.py`, golden `run_report_correctness_testing.json` |
| 5 | Internal restructure (file moves only — `squad/` → `agent/mock/`, top-level → `core/`, etc.) | merge gate must remain green |

### 10.4 Files the executor MUST read before starting Phase 1

- `backend/src/live_e2e/runner.py` — current `run_scenario` (becomes the shim)
- `backend/src/live_e2e/real_agent_run.py` — current `run_sweevo_real_agent` (becomes the shim)
- `backend/src/live_e2e/squad/runner.py` — `MockSquadRunner` (Phase 4 publishes `MOCK_*` at the call sites in §4 lines 338-341, including the pass-by-ref helper closures at `:1005`/`:1029`)
- `backend/src/live_e2e/audit/recorder.py` lines 316-321 — sync perf-report write to remove in Phase 3
- `backend/src/live_e2e/audit/performance_report.py` — `write_performance_reports` (wrapped async + schema bump in Phase 3)
- `backend/src/live_e2e/audit/events.py` — add 4 `MOCK_*` `EventType` values in Phase 2
- `backend/src/benchmarks/sweevo/{models,prompt,sandbox,evaluation}.py` — UNCHANGED data layer
- `<repo>/models/registry.json` — LLM wiring; **do not modify**
- `backend/tests/live_e2e_test/_tools/run_tiered.py` — Phase 4 resume path-mapper update target

### 10.5 Risks already accepted (do not re-litigate)

- Async perf-report can swallow failures silently → mitigated by `test_perf_report_failure_isolated.py` (§7 Risk #2).
- `RunReport` rich-fields shape preserved by golden file at `tests/golden/run_report_correctness_testing.json` (§7 Risk #4) — **capture this on `main` BEFORE Phase 4 lands**.
- 4 `MOCK_*` enum values live in production `EventType` until shim removal — Critic adjudicated Architect's `MockEventType` separation as bikeshedding; **do not re-raise without escalating to user**.
- Schema string bump in Phase 3 may break external (non-repo) consumers — audit via `grep -rn "live_e2e.performance_report.v1" backend/ docs/ scripts/` and surface non-repo hits to user before merging Phase 3.
- `run_tiered.py` resume path-mapper update in Phase 4 is a one-shot migration — document in commit message; back-compat path-mapper not needed (no in-flight resumes during migration window).

### 10.6 Branch state + commit hygiene at handoff

- Branch at handoff: `codex/fix-dot-path-normalization-tests` (clean per `git status` at session start).
- Suggest creating `feature/task-center-runner-restructure-phase-N` per phase; PR to main after Phase 5.
- Per project memory `feedback_parallel_user_commits.md`: user runs codex in parallel during sessions. Stage with **explicit file paths only**; never `git add live_e2e/` or `git add task_center_runner/`; verify HEAD between phases.

### 10.7 Next action for the executor

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS
git checkout -b feature/task-center-runner-restructure-phase-1

# Phase 1: pure rename + silent shim
git mv backend/src/live_e2e backend/src/task_center_runner

# Then per §6 Phase 1 scope:
#   - create silent shim modules at backend/src/live_e2e/ that re-export from task_center_runner.*
#     (NO DeprecationWarning per locked decision #9)
#   - update internal imports `from live_e2e.X` → `from task_center_runner.X`
#   - update tests/conftest.py pytest_plugins to canonical task_center_runner.core.fixtures only

# Run the merge gate:
cd backend && .venv/bin/pytest src/live_e2e/tests/sweevo -x -k "not test_real_agent" --no-header
```

Green = commit Phase 1, proceed to Phase 2. Red = diagnose, do not advance.

### 10.8 Open items NOT closed

None. All 5 original open questions resolved by user 2026-05-15. Plan is consensus-approved (CRITIC-APPROVE iter-3) and handoff-ready.
