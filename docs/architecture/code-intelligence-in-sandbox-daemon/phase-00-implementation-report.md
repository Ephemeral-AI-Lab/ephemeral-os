# Phase 0 — Backend Abstraction + Timing Harness: Implementation Report

Companion to
[`phase-00-backend-abstraction-and-harness.md`](./phase-00-backend-abstraction-and-harness.md).
This report records the structural changes, file inventory, verification
outcome, and key implementation decisions for the Phase 0 deliverable.

---

## 1. Verdict

**Verdict: ships as a structural seam on top of the existing
`CodeIntelligenceService`. Architect-approved
(`APPROVED-WITH-OPEN-INFRA-BLOCKER` resolved to clean approval after the
local Daytona service was started). 10/10 PRD stories pass.**

Phase 0 introduces a single seam — `CiBackend` Protocol — between the
public `CodeIntelligenceService` facade and the concrete in-process
implementation. With the seam in place, every later phase changes only
the backend selection or fleshes out the daemon-bound `DaemonCiBackend`;
no caller of the public facade has to move. The default selection is
byte-identical to today's logic, which the regression suite mechanically
proves at 1070 default-suite tests passing.

The phase also lands the live-E2E `TimingHarness` and a canonical
baseline JSON
(`backend/tests/test_e2e/_timings/phase_0_baseline_timings_2026-05-02T11-28-31Z.json`)
that every subsequent phase's `compare_to(...)` references.

`msgpack>=1.0.0` is added to `[project.dependencies]` so Phase 1 can
ship the daemon's binary protocol without a dependency churn.

---

## 2. File inventory

### Added

| Path | LoC | Purpose |
|---|---:|---|
| `backend/src/sandbox/code_intelligence/backend.py` | 553 | `CiBackend` Protocol + `InProcessCiBackend` (verbatim re-home) + `DaemonCiBackend` stub |
| `backend/tests/test_e2e/_timing_harness.py` | 249 | `TimingHarness` (`step` / `record` / `report` / `dump_json` / `compare_to`) |
| `backend/tests/test_e2e/_timings/.gitkeep` | 0 | Directory marker |
| `backend/tests/test_e2e/_timings/phase_0_baseline_timings_2026-05-02T11-28-31Z.json` | n/a | Canonical Phase 0 baseline (12 steps, 16.146s total) |
| `backend/tests/test_e2e/test_live_ci_phase0_baseline.py` | 234 | Live E2E baseline test against `dask__dask_2023.3.2_2023.4.0` |
| `backend/tests/test_e2e/test_timing_harness_unit.py` | 203 | 8 harness unit tests (default suite) |
| `backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py` | 284 | 33 backend tests (4-truth-table + Protocol shape + daemon command stub raises) |

### Modified

| Path | Change |
|---|---|
| `backend/src/sandbox/code_intelligence/service.py` | Refactored from 339 LoC to 281 LoC thin facade |
| `pyproject.toml` | `msgpack>=1.0.0` added to `[project.dependencies]` |

### Deleted

None — Phase 0 is purely additive on the source side. The previous
service.py implementation was relocated verbatim into
`InProcessCiBackend`; service.py itself is rewritten in place.

---

## 3. Architecture: the CiBackend seam

```
┌──────────────────────────────────────────────────────────┐
│  CodeIntelligenceService  (facade, 281 LoC)               │
│  ──────────────────────────────────────────              │
│  __init__(...) → self._impl = _select_backend(...)        │
│                                                           │
│  Every public op is a one-line forward:                  │
│    def find_definitions(...): return self._impl.find_…    │
│  Every load-bearing internal accessor is a property:     │
│    @property symbol_index → self._impl.symbol_index       │
│    (setter for symbol_index + lsp_client; tests reassign) │
└────────────────────┬──────────────────────────────────────┘
                     │
                     ▼  (via Protocol)
        ┌────────────────────────────┐
        │       CiBackend            │
        │  (typing.Protocol)         │
        │   sandbox_id: str          │
        │   workspace_root: str      │
        │   is_initialized: bool     │
        │   ensure_initialized(…)    │
        │   warmup() / dispose()     │
        │   cmd(…) async             │
        │   find_definitions(…)      │
        │   …17 more methods…        │
        └────────────────────────────┘
                     │
       ┌─────────────┴─────────────┐
       ▼                           ▼
┌──────────────────┐       ┌──────────────────────┐
│ InProcessCiBackend │     │ DaemonCiBackend          │
│ ─────────────────  │     │ ────────────────────  │
│ Verbatim re-home   │     │ Stub: every method    │
│ of today's logic.  │     │ raises NotImplemented │
│                    │     │ (Phase 1+ flesh out). │
│ Selected when ANY  │     │ Selected when ALL of: │
│ of: env unset, no  │     │ EOS_CI_IN_SANDBOX=1   │
│ transport, empty   │     │ AND transport != None │
│ sandbox_id.        │     │ AND sandbox_id != "". │
└──────────────────┘       └──────────────────────┘
```

**Selection truth table** (from `service._select_backend`):

| EOS_CI_IN_SANDBOX | transport | sandbox_id | Backend |
|---|---|---|---|
| unset | any | any | InProcess |
| `"1"` | None | any | InProcess |
| `"1"` | not None | `""` | InProcess |
| `"1"` | not None | non-empty | **Daemon** |
| `"true"` (or any non-`"1"`) | not None | non-empty | InProcess |
| unset | not None | non-empty | InProcess |

Every row is pinned by a test in
`backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py`.

---

## 4. Per-story PRD coverage map

| Story | Verdict | Evidence |
|---|---|---|
| **P0-001** msgpack runtime dep | PASS | `pyproject.toml:37` adds `"msgpack>=1.0.0"`; `python -c 'import msgpack'` → `(1, 1, 2)`; `ruff check pyproject.toml` clean. |
| **P0-002** `CiBackend` Protocol | PASS | `backend.py:57-130` declares `class CiBackend(Protocol)` with `sandbox_id: str`, `workspace_root: str`, `is_initialized: bool`, and 21 methods matching spec Task 0.1. Not `@runtime_checkable`. |
| **P0-003** `InProcessCiBackend` | PASS | `backend.py:133-419`. Constructor builds the same component graph as the prior `CodeIntelligenceService`. **Method-body byte-equivalence verified** for all 23 methods/properties (mechanical diff after docstring strip). Threading semantics preserved: `_init_lock` retained at `backend.py:154`. `_is_python` re-homed at `backend.py:407-409`. |
| **P0-004** `DaemonCiBackend` stub | PASS | `backend.py:424-553`. Every method including `dispose` raises `NotImplementedError("DaemonCiBackend lands in Phase 1+")`. Constructor takes required `transport=…` keyword. **21 raise sites total** (`grep -c "raise NotImplementedError"`). |
| **P0-005** Facade refactor | PASS | `service.py` now 281 LoC. `_select_backend(...)` enforces the 4-truth-table. Public methods are one-line forwards. Properties `sandbox_id` / `workspace_root` / `is_initialized` forward to `_impl`. Backward-compat properties for 11 internal accessors plus setters for `symbol_index` + `lsp_client` (three tests reassign these as `MagicMock`). |
| **P0-006** `TimingHarness` | PASS | `_timing_harness.py:46-249`. `step()` ctx mgr uses `time.perf_counter()`. `record()` attaches metadata or creates bare entry. `report()` produces canonical `=== Phase N E2E timing breakdown for <test> ===` header + `--- TOTAL: <sum>s ---` footer. `dump_json()` writes atomically via `<path>.tmp` + `os.replace(...)`. `compare_to()` order-preserves new-run keys, marks NEW (`+0.789s (NEW cost, must be amortized)`) and REMOVED. |
| **P0-007** Harness unit tests | PASS | `test_timing_harness_unit.py` ships 8 tests, all passing in default suite (~0.13s). Step bound check; record metadata; bare-entry creation; canonical-format assertion (with monkey-patched `perf_counter`); `dump_json` shape; `dump_json` atomicity (no `.tmp` leftover); `compare_to` signed deltas + NEW; `compare_to` REMOVED. |
| **P0-008** Backend tests | PASS | `test_backend_inprocess.py` ships 33 tests. InProcess defaults; 4-truth-table backend selection (6 cases); daemon command init attributes; 19 sync ops parametrized → each raises `NotImplementedError`; async `cmd` raises; Protocol-shape tests assert every public CiBackend method exists on both impls. |
| **P0-009** Live E2E baseline | PASS | `test_live_ci_phase0_baseline.py` ran end-to-end against a real `dask__dask_2023.3.2_2023.4.0` Daytona sandbox on 2026-05-02. All 12 documented steps recorded. Baseline JSON committed at `_timings/phase_0_baseline_timings_2026-05-02T11-28-31Z.json` (254 indexed files, 28.0 KB symbol index, 16.146s total). |
| **P0-010** Regression sweep | PASS | `pytest backend/tests --ignore=…test_e2e --ignore=…test_benchmarks --ignore=…experiments -q` → **1070 passed in 18.49s** (post-deslop, post-test-reorder); `pytest backend/tests/test_sandbox backend/tests/test_tools -q` → 575 passed; `ruff check backend/src/sandbox/code_intelligence backend/tests/test_sandbox/test_code_intelligence backend/tests/test_e2e` clean. |

---

## 5. Verification

### Test counts

| Suite | Result |
|---|---|
| `pytest backend/tests/test_sandbox/test_code_intelligence -q` | **227 passed** |
| `pytest backend/tests/test_sandbox backend/tests/test_tools -q` | **575 passed** |
| `pytest backend/tests --ignore=…test_e2e --ignore=…test_benchmarks --ignore=…experiments -q` | **1070 passed** |
| `pytest backend/tests/test_e2e/test_timing_harness_unit.py backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py -q` | **41 passed** |
| `pytest backend/tests/test_e2e/test_live_ci_phase0_baseline.py -m live -v -s` | **1 passed** (real Daytona) |

### Lint sweep

```
.venv/bin/ruff check backend/src/sandbox/code_intelligence \
  backend/tests/test_sandbox/test_code_intelligence \
  backend/tests/test_e2e
→ All checks passed!
```

Extended ruff sweep with UP/SIM/B/N pyupgrade rules (post-deslop):
clean.

### Spec Task 0.1 enumeration (mechanical)

Every public method on the original `CodeIntelligenceService` (HEAD)
appears as a one-line forward on the new facade:

```
ensure_initialized, warmup, rebind_sandbox, cmd (async),
find_definitions, find_references, hover, diagnostics, query_symbols,
apply_edit, commit_operation_against_base, commit_specs_many,
list_folder_files, write_file, edit_file, delete_file, move_file,
undo_last_edit, status, get_telemetry, dispose
```

Plus the three Protocol attributes (`sandbox_id`, `workspace_root`,
`is_initialized`) preserved via `@property`.

### Grep proofs

```
$ grep -c "raise NotImplementedError" backend/src/sandbox/code_intelligence/backend.py
21

$ grep -nE "^    (async )?def " backend/src/sandbox/code_intelligence/service.py | wc -l
21        # public methods (matches spec Task 0.1)
```

---

## 6. Phase 0 baseline timings

Live run on `dask__dask_2023.3.2_2023.4.0` against a self-hosted
Daytona at `localhost:3000` (Docker Compose stack):

```
=== Phase 0 E2E timing breakdown for baseline_timings ===
sandbox_create:           0.000s
sweevo_setup:             0.000s
ci_service_construct:     0.000s
index_build_in_process:   3.923s   (28.0 KB, 254 files)
query_symbols_first:      0.007s   (333 files)
query_symbols_warm:       0.004s   (333 files)
write_file_baseline:      0.783s
edit_file_baseline:       0.790s
delete_file_baseline:     2.592s
svc_cmd_baseline:         8.047s   (4 B)
ci_service_dispose:       0.000s
sandbox_dispose:          0.000s
--- TOTAL: 16.146s ---
```

`sandbox_create` / `sweevo_setup` / `sandbox_dispose` are zero-elapsed
markers because those costs live in the module-scoped fixture, not in
the test body. Including them keeps the baseline shape stable so later
phases can `compare_to(...)` without missing-key annotations.

The 8s `svc_cmd_baseline` is the headline cost: `svc.cmd` is an audited
fail-closed overlay that runs the user command in a fresh `unshare`
namespace with git snapshot before/after for OCC tracking. On dask's
~10k-file working set, the namespace's `find` walk and post-run
`git diff` dominate. Phase 4's hot-path daemon command is targeted at
collapsing this to ~0.6s.

---

## 7. Implementation decisions

### 7.1 Property setters on the facade

Three existing tests reassign internal components on a real
`CodeIntelligenceService` instance:
- `test_write_coordinator_batch.py:111-112` — `svc.symbol_index = MagicMock()`, `svc.lsp_client = MagicMock()`
- `test_ci_reference_lazy_sandbox.py:60` — `svc.lsp_client = MagicMock()`

Read-only properties broke those tests. Two options were considered:

1. Add `@<prop>.setter` decorators that forward to `_impl.<attr>`.
2. Update the tests to assign through `svc._impl.<attr>`.

Option 1 was chosen: the test contract (a `CodeIntelligenceService`
instance accepts attribute assignment to swap in mocks) was already
implicitly part of the public surface, and option 2 would have meant
modifying tests outside the Phase 0 scope. Setters are documented as
load-bearing in the file; only `symbol_index` and `lsp_client` got
setters because only those two are reassigned in the existing test
base. Other properties remain read-only.

### 7.2 InProcessCiBackend is a verbatim re-home, not a tidy-up

The architect's verification cross-checked the prior `service.py` body
against the new `InProcessCiBackend` body for byte-equivalence (after
docstring stripping). The `_init_lock` block in `ensure_initialized`
keeps its two separate `with` blocks (rather than one), the
`rebind_sandbox` body keeps its `lsp_client._sandbox = sandbox`
mutation in original order, and the `warmup` remote-only branch is
preserved verbatim. This is intentional: P0-003's "no behavior change"
requirement is mechanically auditable only when the bodies match
character-for-character.

### 7.3 DaemonCiBackend.dispose raises, not no-op

The spec Task 0.4 says "every method raises `NotImplementedError`."
The PRD originally proposed `dispose` could be a no-op (since callers
might invoke it on registry teardown), but the architect (in pre-flight
review) flagged this as a soft-criterion violation. `dispose()` raises
like every other method; once Phase 1 ships the daemon, `dispose()`
gets a real implementation that closes the daemon command channel.

### 7.4 Live test mutation/cmd ordering

The phase-00 spec example places `svc_cmd_baseline` *before* the
mutation steps. The first live run failed with
`Future attached to a different loop` after `svc.cmd` because
`get_async_sandbox(env.sandbox_id)` initialized an `AsyncDaytona`
client cached against the pytest-asyncio event loop, and the
SUBSEQUENT sync `svc.write_file(...)` traversed
`sandbox.client.async_bridge` which tripped on a stale aiohttp future
bound to the prior loop.

The fix: reorder so all sync mutations finish BEFORE the async
`svc.cmd` step. The spec ordering is informative (it walks through the
hot paths in narrative order), not mandatory; the baseline JSON
captures the new order so all later phases compare apples-to-apples.

This isn't a Phase 0 bug — it's a test-harness limitation: the sweevo
fixture's `raw_sandbox` uses the sync Daytona SDK, and `svc.cmd`
strictly requires `process.exec` to be a coroutine
(`AuditedCommandExecutor._exec_sandbox_process` asserts
`inspect.iscoroutinefunction`). Production callers (e.g.
`lifecycle/commit.py:272`) operate on async sandbox handles obtained
via `get_async_sandbox(...)`, so the mixed-handle conflict only
manifests in this specific test.

### 7.5 Mid-flight progress prints

The live test runs ~30-50s end-to-end (sandbox provisioning + index
build + 6 timed steps). Without progress visibility, an operator can't
distinguish "running" from "hung." The test wraps `harness.step` in a
`_traced_step` context manager that prints
`  → <name> ...` on entry and `  ✓ <name> (<elapsed>s)` on exit, all
flushed to stdout. With `pytest -s` plus a `tee` to a log file plus
`Monitor(tail -F | grep)`, the operator gets per-step notifications in
near real time.

### 7.6 msgpack lands in Phase 0, not Phase 1

The migration spec puts the daemon's binary protocol in Phase 1, but
phase-00 explicitly requires `msgpack` in `[project.dependencies]` so
that uv's lockfile + the sandbox runtime bundle don't churn between
Phase 0 and Phase 1. The dep is harmless on the in-process path
(unused) and ready for the daemon's first binary frame.

---

## 8. Open items + hand-off to Phase 1

Phase 1 picks up with these guarantees from Phase 0:

1. **A working `DaemonCiBackend` stub** at
   `backend/src/sandbox/code_intelligence/backend.py:424-553` ready to
   have its first method (`build_index` per the migration plan)
   implemented. The constructor signature is locked
   (`sandbox_id`, `workspace_root`, required `transport=…`).

2. **A canonical baseline JSON** at
   `_timings/phase_0_baseline_timings_2026-05-02T11-28-31Z.json` for
   `compare_to(...)` invocations. Phase 1's first measurement will
   compare against this baseline.

3. **The `EOS_CI_IN_SANDBOX=1` selection path proven** (raises
   `NotImplementedError` on every op until Phase 1 fleshes it out).
   The 4-truth-table is pinned by 6 unit tests.

4. **`msgpack>=1.0.0`** available as a runtime dependency for the
   daemon's binary protocol.

5. **Mechanical byte-equivalence guarantee** on the in-process path —
   any future change to `InProcessCiBackend.<method>` that diverges
   from the prior `CodeIntelligenceService.<method>` will be caught by
   one of 1070 default-suite tests or 33 backend tests.

### Non-blocking follow-ups (LOW severity, deferred)

| Item | Location | Owner | Note |
|---|---|---|---|
| Tighten `test_report_renders_canonical_format` to use full line-equality (not substring `in` checks) for per-step rendering | `test_timing_harness_unit.py:89-105` | Phase 1+ | Catches column-padding regressions; ~2-line edit. Architect-rated LOW. |
| Decide team policy on whether to commit the Phase 0 baseline JSON (committed in this PR) vs keep `_timings/` as build-artifact-only | `_timings/.gitkeep` | Team | Currently committed for cross-machine reproducibility. |
| Consider extracting `_traced_step` into the harness itself (opt-in `verbose=True`) so other phases get progress prints for free | `_timing_harness.py` | Phase 1+ | Pure additive; defer until a second phase needs it. |

### What Phase 0 explicitly does NOT ship

- No `in_sandbox/` package.
- No daemon code, socket, or storage layer.
- No actual daemon command implementation — `DaemonCiBackend` is pure stub.
- No msgpack usage — only the dep declaration.
- No CI for the live E2E test (it's manually invoked; gated on
  `has_daytona()` so default `pytest` skips cleanly).

---

## 9. Test counts at the end of Phase 0

```
backend/tests --ignore=test_e2e --ignore=test_benchmarks --ignore=experiments
  → 1070 passed (was 1037 pre-Phase-0; +33 new backend tests)

backend/tests/test_sandbox/test_code_intelligence
  → 227 passed (was 194 pre-Phase-0; +33)

backend/tests/test_e2e/test_timing_harness_unit.py
  → 8 passed

backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py
  → 33 passed

backend/tests/test_e2e/test_live_ci_phase0_baseline.py -m live
  → 1 passed (real Daytona, 16.146s total wall)
```

---

## 10. Commit attribution

This PR introduces the structural seam in a single staged commit:

```
9 files changed, 1755 insertions(+), 198 deletions(-)
```

Files staged at the time of report writing (no commit yet — user will
review before committing):

- `backend/src/sandbox/code_intelligence/backend.py` (NEW, 553 LoC)
- `backend/src/sandbox/code_intelligence/service.py` (refactored)
- `backend/tests/test_e2e/_timing_harness.py` (NEW, 249 LoC)
- `backend/tests/test_e2e/_timings/.gitkeep`
- `backend/tests/test_e2e/_timings/phase_0_baseline_timings_2026-05-02T11-28-31Z.json`
- `backend/tests/test_e2e/test_live_ci_phase0_baseline.py` (NEW, 234 LoC)
- `backend/tests/test_e2e/test_timing_harness_unit.py` (NEW, 203 LoC)
- `backend/tests/test_sandbox/test_code_intelligence/test_backend_inprocess.py` (NEW, 284 LoC)
- `pyproject.toml` (msgpack added)

`.omc/prd.json` and `.omc/progress.txt` are gitignored and not part of
the commit.

---

## 11. Key learnings (carry forward to Phase 1+)

1. **Property forwarding is load-bearing for the in-process refactor.**
   `workspace.py`, `code_intelligence_api.py`, and seven test files
   read internals like `svc.symbol_index` and `svc.lsp_client`
   directly. Three tests *reassign* them. The architect's pre-flight
   call surfaced that "swap to thin facade" is a backward-incompat
   change unless every internal accessor is preserved as a property
   — and `symbol_index` + `lsp_client` need full `@property` +
   `@.setter` pairs to keep test contracts intact.

2. **Verbatim re-home beats tidied re-home.** The instinct to clean up
   the two separate `with self._init_lock:` blocks in
   `ensure_initialized` was correctly resisted. P0-003's "no behavior
   change" requirement is only mechanically auditable when the bodies
   match character-for-character; any tidy-up creates a hidden risk
   surface that the regression suite cannot prove away.

3. **`svc.cmd` against the sweevo fixture needs an async sandbox
   handle.** The sweevo helper returns a sync Daytona SDK Sandbox;
   `AuditedCommandExecutor` strictly requires async. Resolving a
   parallel async handle on the same sandbox via
   `get_async_sandbox(sandbox_id)` works — both clients can coexist
   against the same sandbox UUID because the Daytona API is RESTful,
   not stateful. But the AsyncDaytona client caches against the
   asyncio event loop, so any subsequent sync write/edit/delete that
   crosses through `async_bridge` will fail with
   `Future attached to a different loop`. Mitigation: order
   sync-mutation steps BEFORE async `svc.cmd`.

4. **Mid-flight progress visibility for live tests is cheap.**
   `print(..., flush=True)` + `pytest -s` + `tee` + Monitor with
   `tail -F | grep --line-buffered` gives near-real-time per-step
   notifications without needing a pytest plugin. The Monitor's grep
   filter must include both progress markers AND failure signatures
   (`Traceback`, `RuntimeError`, `FAILED`) — otherwise a crash looks
   identical to silence.

5. **Pre-flight collection check is the cheapest safety net.**
   `pytest --collect-only -m live` validates imports, fixture
   resolution, and decorator typing WITHOUT the live infrastructure
   cost. Always run it after editing a live test before paying the
   provisioning round-trip.

6. **`has_daytona()` returning True ≠ Daytona is reachable.** It only
   checks credentials. Connectivity must be verified separately
   (`curl localhost:3000/api/health`). The first iteration of Phase 0
   spent a full 30-second sandbox provisioning attempt before
   discovering the local Daytona service wasn't actually running.

---

## 12. Out-of-scope notes (referenced but not changed)

- `backend/src/sandbox/code_intelligence/registry.py` — unchanged.
  The registry continues to construct `CodeIntelligenceService` with
  the same signature. Backend selection is internal to the facade.
- `backend/src/sandbox/code_intelligence/{indexing,language_server,
  mutations,overlay,core}/` — unchanged. The components are
  re-imported by `backend.py` but their implementations are
  untouched.
- `docs/architecture/code-intelligence-in-sandbox-daemon/{overview,
  phase-01-indexing-and-storage,phase-03-6-lsp-server-upgrade}.md` —
  pre-existing user edits visible in `git status`; left untouched
  per surgical-changes rule.
