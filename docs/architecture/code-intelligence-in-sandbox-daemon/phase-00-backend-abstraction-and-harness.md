# Phase 0 — Backend abstraction, feature flag, and timing harness

**Estimated effort:** 5-6 days (2 days engineering + 3-4 days harness/baseline E2E)
**Risk profile:** LOW (no behavior change with flag off) but MEDIUM blast radius (touches the public service facade)
**Status:** Not started

## Goal

Introduce the `CiBackend` Protocol and route every existing `CodeIntelligenceService` call through `InProcessCiBackend` (today's logic). Add the `EOS_CI_IN_SANDBOX` feature flag. Land the live-E2E `TimingHarness` so every subsequent phase reuses the same timing/comparison infrastructure. **Add `msgpack` as a runtime dep in `pyproject.toml`** so Phase 1 can ship it in the bundle.

## Why first

Three reasons:

1. **The seam unblocks every later phase.** Once `CodeIntelligenceService` delegates to a swappable backend, Phases 1-5 only ever change the backend selection or add to `RpcCiBackend` — they never touch the public facade or callers.
2. **The harness must exist before the first measurement.** Phase 0 produces the canonical baseline JSON (`phase_0_baseline_<ts>.json`) that every later phase's `compare_to(baseline)` references. If the harness lands in Phase 1 instead, Phase 1's deltas have nothing to compare against.
3. **It proves "flag off = byte-identical" mechanically.** With the seam in place but no daemon code shipped, the regression suite running flag-off is a hard contract: every later phase's `flag-off` path must continue to pass it.

## What ships

| Artifact | File | Purpose |
|---|---|---|
| Protocol | `backend/src/sandbox/code_intelligence/backend.py` (`CiBackend`) | Single shape that every backend implements |
| In-process backend | `backend/src/sandbox/code_intelligence/backend.py` (`InProcessCiBackend`) | Wraps today's logic, default selection |
| Stub RPC backend | `backend/src/sandbox/code_intelligence/backend.py` (`RpcCiBackend`) | Raises `NotImplementedError`; placeholder for Phase 1+ |
| Service delegation | `backend/src/sandbox/code_intelligence/service.py` (modified) | Constructor selects backend; methods forward |
| Registry passthrough | `backend/src/sandbox/code_intelligence/registry.py` (modified) | Threads flag/transport through `code_intelligence_for(...)` |
| Timing harness | `backend/tests/test_e2e/_timing_harness.py` | `TimingHarness` + `step()` + JSON dump + `compare_to()` |
| Timings dir | `backend/tests/test_e2e/_timings/.gitkeep` | Holds per-run JSON reports |
| Phase 0 live E2E | `backend/tests/test_e2e/test_live_ci_phase0_baseline.py` | Establishes canonical baseline timings |
| Unit tests | `backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py` | Proves Protocol shape + delegation |
| Harness unit tests | `backend/tests/test_e2e/test_timing_harness_unit.py` | `TimingHarness` records, prints, dumps JSON correctly |
| Runtime deps update | `pyproject.toml` | Add `msgpack` to `[project.dependencies]`. Verified by `python -c "import msgpack"` in CI |

No `in_sandbox/` package yet, no daemon code, no socket, no storage layer.

## Detailed task list

### Task 0.1 — Catalog the public API surface of `CodeIntelligenceService`

**File to read:** `backend/src/sandbox/code_intelligence/service.py`

**Action:** Enumerate every public method with its full signature and return type. The list (from current code) is:

```
ensure_initialized(wait: bool = True) -> bool
warmup() -> None
rebind_sandbox(sandbox: Any) -> None
cmd(sandbox, command, **kwargs) -> Any                # async
find_definitions(file_path, symbol, line=0, character=0) -> list[SymbolInfo]
find_references(file_path, symbol, line=0, character=0) -> list[ReferenceInfo]
hover(file_path, line, character) -> HoverResult | None
diagnostics(file_path) -> list[Diagnostic]
query_symbols(query) -> list[SymbolInfo]
apply_edit(request: EditRequest) -> EditResult
commit_operation_against_base(changes, *, agent_id="", edit_type, description="") -> OperationResult
commit_specs_many(requests) -> list[OperationResult]
list_folder_files(folder) -> list[str]
write_file(specs, *, agent_id="", description="") -> OperationResult
edit_file(specs, *, agent_id="", description="") -> OperationResult
delete_file(paths, *, agent_id="", description="") -> OperationResult
move_file(specs, *, agent_id="", description="") -> OperationResult
undo_last_edit(file_path) -> EditResult
status() -> dict[str, Any]
get_telemetry() -> CITelemetry
dispose() -> None
```

Also expose: `is_initialized`, `sandbox_id`, `workspace_root` (today these are attributes; preserve them as properties on the facade).

**Verify:** `grep -n "def " backend/src/sandbox/code_intelligence/service.py` matches the list (modulo private `_` methods).

### Task 0.2 — Define `CiBackend` Protocol

**File to create:** `backend/src/sandbox/code_intelligence/backend.py`

**Action:** Use `typing.Protocol` (runtime-checkable not required). One method per public op from Task 0.1. Key constraints:

- Return-type stability: every method's return type is identical to today's `CodeIntelligenceService`. Phase 4 verifies this for `svc.cmd`'s `SimpleNamespace` shape.
- `cmd` stays `async`; everything else stays sync (today's mutation pipeline is sync; the daemon's async loop is internal).
- No new params, no removed params.

Skeleton:

```python
from typing import Protocol, Any, Sequence
from collections.abc import Sequence as _Seq
# ... import existing dataclasses from sandbox.code_intelligence.core.types

class CiBackend(Protocol):
    sandbox_id: str
    workspace_root: str
    is_initialized: bool

    def ensure_initialized(self, wait: bool = True) -> bool: ...
    def warmup(self) -> None: ...
    def rebind_sandbox(self, sandbox: Any) -> None: ...
    async def cmd(self, sandbox: Any, command: str, **kwargs: Any) -> Any: ...
    def find_definitions(self, file_path: str, symbol: str, line: int = 0, character: int = 0) -> list[SymbolInfo]: ...
    # ... (one per public method)
    def dispose(self) -> None: ...
```

**Verify:** `mypy --strict backend/src/sandbox/code_intelligence/backend.py` passes.

### Task 0.3 — Implement `InProcessCiBackend`

**File:** same `backend.py`

**Action:** Construct today's components (`SymbolIndex`, `Arbiter`, `WriteCoordinator`, `MutationService`, `LspClient`, `AuditedCommandExecutor`, `ContentManager`, `TimeMachine`, `Patcher`) inside the backend's `__init__`. Every method is a one-line forward to the corresponding component method. **No logic changes** — this is a pure re-home.

Skeleton:

```python
class InProcessCiBackend:
    def __init__(self, sandbox_id: str, workspace_root: str, sandbox: Any = None,
                 *, transport: SandboxTransport | None = None) -> None:
        self.sandbox_id = sandbox_id
        self.workspace_root = workspace_root
        # Construct: symbol_index, arbiter, time_machine, patcher, lsp_client,
        # content_manager, write_coordinator, mutation_service, command_executor
        # (verbatim copy from today's CodeIntelligenceService.__init__)
        ...

    def ensure_initialized(self, wait: bool = True) -> bool:
        # verbatim from today
        ...
```

**Verify:** every existing test under `backend/tests/test_sandbox/test_code_intelligence/` that today constructs `CodeIntelligenceService` directly can be reparametrized to construct `InProcessCiBackend` and still pass.

### Task 0.4 — Implement `RpcCiBackend` stub

**File:** same `backend.py`

**Action:** Same Protocol shape, every method raises `NotImplementedError("RpcCiBackend lands in Phase 1+")`. Constructor takes `transport` and `sandbox_id` and stashes them.

**Why now:** locks in the selection logic (Task 0.5) so Phase 1 only has to flesh out method bodies, not also wire the constructor.

### Task 0.5 — Backend selection in `CodeIntelligenceService`

**File to modify:** `backend/src/sandbox/code_intelligence/service.py`

**Action:**
- Constructor delegates to `_select_backend(...)` which returns `InProcessCiBackend` unless **all** of: `os.environ.get("EOS_CI_IN_SANDBOX") == "1"` AND `transport is not None` AND `sandbox_id != ""`. In that case returns `RpcCiBackend` (which will still raise on every method until Phase 1 ships).
- Every public method becomes a one-line delegation: `return self._impl.method(...)`.
- Properties `sandbox_id`, `workspace_root`, `is_initialized` forward to `_impl`.
- `dispose()` forwards to `_impl.dispose()`.

**Critical preservation:** the threading/locking semantics of `__init__` (the `_init_lock`) and `ensure_initialized` (the `wait` semantics) move INTO `InProcessCiBackend`. The facade has none of its own state.

**Verify:** `git diff backend/src/sandbox/code_intelligence/service.py` shows: imports added, every method body replaced with one-line forward, `__init__` shrunk to ~10 lines.

### Task 0.6 — Build `_timing_harness.py`

**File to create:** `backend/tests/test_e2e/_timing_harness.py`

**API:**

```python
class TimingHarness:
    def __init__(self, phase: int, test_name: str) -> None: ...

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        """Time a block. Records elapsed seconds under `name`."""

    def record(self, name: str, *, count: int | None = None, bytes_: int | None = None) -> None:
        """Attach a count/byte-size to a previously-stepped name (or a new bare key)."""

    def report(self) -> str:
        """Return the structured human-readable report."""

    def dump_json(self) -> Path:
        """Write JSON to backend/tests/test_e2e/_timings/phase_<N>_<test>_<ts>.json. Return the path."""

    def compare_to(self, baseline_path: Path) -> str:
        """Load baseline JSON, return per-step delta report (signed, percent change, NEW for missing baseline keys)."""
```

**Report format (exact):**

```
=== Phase N E2E timing breakdown for <test_name> ===
sandbox_create:           1.234s
ci_runtime_upload:        0.456s   (12.3 KB, 5 files)
daemon_spawn:             0.789s
daemon_first_ping:        0.012s
index_build_in_sandbox:   3.456s   (1024 files)
snapshot_pickle_read:     0.234s   (847.2 KB)
query_symbols_first:      0.045s
query_symbols_warm:       0.003s
svc_cmd_baseline:         1.420s
svc_cmd_via_daemon:       0.635s
sandbox_dispose:          0.890s
--- TOTAL: 8.614s ---
```

**Compare-to format (exact):**

```
--- vs Phase 0 baseline (phase_0_baseline_2026-05-03T10:14:22.json) ---
query_symbols_first:      0.045s  (-0.155s, 77% faster)
svc_cmd:                  0.635s  (-0.785s, 55% faster)
sandbox_create:           +0.0s   (no change, expected)
daemon_spawn:             +0.789s (NEW cost, must be amortized)
```

**Implementation notes:**
- Use `time.perf_counter()` exclusively.
- `dump_json` writes atomically: write to `<path>.tmp`, then `os.replace(...)`.
- JSON shape: `{"phase": int, "test_name": str, "timestamp": ISO8601, "steps": [{"name": str, "elapsed_s": float, "count": int|None, "bytes": int|None}], "total_s": float}`.
- `compare_to` is order-preserving for the new test's steps; baseline-only steps appear at end as `(REMOVED)`.

### Task 0.7 — Phase 0 live E2E baseline

**File to create:** `backend/tests/test_e2e/test_live_ci_phase0_baseline.py`

**Mirror these conventions from `test_live_ci_diagnostics.py`:**
- `pytestmark = [pytest.mark.e2e, pytest.mark.live]`
- Skip with `if not EvalAgent.has_daytona(): pytest.skip("Daytona credentials not configured")`
- Use `sandbox.testing.create_test_sandbox` / `delete_test_sandbox` in a fixture
- Constants: `_DASK_SWEEVO_INSTANCE_ID = "dask__dask_2023.3.2_2023.4.0"`, `_DASK_SWEEVO_REPO_DIR = "/testbed"`

**Test flow:**

```python
def test_phase0_baseline_timings(live_sweevo_env):
    h = TimingHarness(phase=0, test_name="baseline_timings")

    with h.step("sandbox_create"):
        env = live_sweevo_env  # already provisioned by fixture; this measures fixture+1st access

    with h.step("sweevo_setup"):
        # ensure /testbed exists, dask repo cloned (handled by sweevo fixture)
        ...

    with h.step("ci_service_construct"):
        svc = env.make_ci_service()  # InProcessCiBackend under the hood

    with h.step("index_build_in_process"):
        svc.ensure_initialized(wait=True)
    h.record("index_build_in_process", count=svc.symbol_index.indexed_files, bytes_=svc.symbol_index.size)

    with h.step("query_symbols_first"):
        results = svc.query_symbols("Bag")  # known dask symbol
    h.record("query_symbols_first", count=len(results))

    with h.step("query_symbols_warm"):
        results = svc.query_symbols("Bag")

    with h.step("svc_cmd_baseline"):
        result = await svc.cmd(env.raw_sandbox, "find /testbed -name '*.py' | wc -l")
    h.record("svc_cmd_baseline", bytes_=len(str(result.result)))

    # Mutation hot-path baseline
    with h.step("write_file_baseline"):
        svc.write_file([WriteSpec(file_path="/testbed/_phase0_probe.txt", content="hello", overwrite=True)])

    with h.step("edit_file_baseline"):
        svc.edit_file([EditSpec(file_path="/testbed/_phase0_probe.txt", edits=[Edit(...)])])

    with h.step("delete_file_baseline"):
        svc.delete_file(["/testbed/_phase0_probe.txt"])

    with h.step("ci_service_dispose"):
        svc.dispose()

    with h.step("sandbox_dispose"):
        # handled by fixture teardown; measure separately if needed
        pass

    print(h.report())
    baseline_path = h.dump_json()
    print(f"Baseline saved at: {baseline_path}")
```

**Run command:** `uv run pytest backend/tests/test_e2e/test_live_ci_phase0_baseline.py -m live -v -s`

**Acceptance:**
- Test passes against a real `dask__dask_2023.3.2_2023.4.0` sandbox.
- JSON report written to `backend/tests/test_e2e/_timings/phase_0_baseline_<ts>.json`.
- `print(h.report())` produces output matching the exact format from Task 0.6.

### Task 0.8 — Unit tests

**Files:**
- `backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py`
- `backend/tests/test_e2e/test_timing_harness_unit.py`

**`test_backend_inprocess.py`:**
- Construct `InProcessCiBackend` with no sandbox bound; assert `query_symbols("foo")` returns `[]`.
- Construct `CodeIntelligenceService(...)`; assert `type(svc._impl) is InProcessCiBackend` when env unset.
- Set `EOS_CI_IN_SANDBOX=1` and pass a fake transport + non-empty `sandbox_id`; assert `type(svc._impl) is RpcCiBackend`.
- Set `EOS_CI_IN_SANDBOX=1` but no transport; assert backend falls back to `InProcessCiBackend`.
- Call every public method on `RpcCiBackend` and assert each raises `NotImplementedError`.

**`test_timing_harness_unit.py`:**
- `TimingHarness.step()` records elapsed time within ±10ms of a known sleep.
- `record(name, count=10, bytes_=2048)` attaches metadata.
- `report()` matches the documented format byte-for-byte (using a fixed monkey-patched `perf_counter`).
- `dump_json()` writes valid JSON with the documented shape.
- `compare_to(baseline_path)` produces signed deltas, marks NEW keys, marks REMOVED keys.
- `dump_json` is atomic — kill mid-write doesn't corrupt prior file.

### Task 0.9 — Regression check

**Command:** `.venv/bin/pytest backend/tests/test_sandbox/ backend/tests/test_tools/ -q`

**Expectation:** Same baseline pass count as today (per `docs/architecture/code-intelligence-merged-into-sandbox.md`: 436 tests passing).

If any test fails: it's a bug in the delegation (Task 0.5), not in any backend logic — fix the facade.

## Definition of done

- [ ] `CiBackend` Protocol exists in `backend.py` and is `mypy --strict` clean.
- [ ] `InProcessCiBackend` wraps today's logic with no behavior change.
- [ ] `RpcCiBackend` stub raises `NotImplementedError` on every method.
- [ ] `CodeIntelligenceService.__init__` selects backend via `_select_backend(...)`; every public method is a one-line forward.
- [ ] `EOS_CI_IN_SANDBOX` flag selection works as documented (4-truth-table tested in `test_backend_inprocess.py`).
- [ ] `TimingHarness` API matches the spec (Task 0.6); unit tests pass.
- [ ] Phase 0 live E2E (`test_live_ci_phase0_baseline.py`) passes against a real `dask__dask_2023.3.2_2023.4.0` sandbox.
- [ ] Baseline JSON `phase_0_baseline_<ts>.json` is committed to `_timings/` (or its existence is documented; team policy on whether to commit run artifacts goes here).
- [ ] Full existing test suite (`backend/tests/test_sandbox/`, `backend/tests/test_tools/`) passes with flag off.
- [ ] PR description includes the baseline timing report (paste of `h.report()`) so reviewers can see the starting numbers.
- [ ] `msgpack` added to `[project.dependencies]` in `pyproject.toml`; `uv sync` succeeds; `python -c "import msgpack"` works in venv.

## Risk callouts (Phase 0 specific)

| Severity | Risk | Mitigation |
|---|---|---|
| MEDIUM | Delegation drops a kwarg or changes default → silent behavior change | Task 0.5 verification: line-by-line diff; existing tests are the contract |
| LOW | Threading semantics broken when `_init_lock` moves into backend | Keep `ensure_initialized` body verbatim; the lock and event objects move with it |
| LOW | `TimingHarness` JSON dump races on concurrent test runs | Each test writes a distinct `<ts>` filename; atomic rename prevents partial writes |
| LOW | Baseline E2E flaky on Daytona resource limits | Skip-on-no-creds gate + `pytest --maxfail=1`; document `EvalAgent.has_daytona()` requirement |

## Hand-off to Phase 1

Phase 1 picks up with:
- A working `RpcCiBackend` stub at `backend.py` ready to have its first method (`build_index`) implemented.
- A `TimingHarness` + baseline JSON ready to compare against.
- The `EOS_CI_IN_SANDBOX=1` selection path proven (just raises `NotImplementedError` until Phase 1 fleshes it out).
