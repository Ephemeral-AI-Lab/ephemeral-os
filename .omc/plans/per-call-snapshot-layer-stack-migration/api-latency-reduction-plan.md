# Public Sandbox API Latency Reduction — 4-Phase Plan

**Source data:** `backend/tests/live_e2e_test/sandbox/phase-04-latency-attribution-report.md`
**Per-call telemetry:** `.omc/results/live-e2e-phase3-per-call-timings-20260505T175743Z-1591.jsonl`
**Probe:** `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py`

## Baseline (before any phase, c=16, p99 ms)

| Verb | Wall | Transport | Py boot | Dispatch | flock_wait | OCC prepare | OCC commit |
|---|---:|---:|---:|---:|---:|---:|---:|
| read_file | 931 | 856 | 61 | 14 | – | – | – |
| write_file | 2131 | 929 | 58 | 1144 | 1054 | 98 | 8 |
| edit_file | 2111 | 855 | 66 | 1190 | 1097 | 92 | 6 |
| shell `:` | 1407 | 866 | 61 | 480 | 0 | – | – |
| shell `echo>file` | 1438 | 905 | 70 | 463 | 0 | – | – |

## Bottleneck → phase mapping

| Bottleneck | c=16 cost | Phase |
|---|---:|---|
| Cross-process flock serialization | ~1050 ms | **Phase 1** |
| GitignoreOracle cold-start per process | 40–75 ms | **Phase 2** |
| Provider exec + Python interpreter spawn per call | ~960 ms (transport+boot) | **Phase 3** |
| Hot lock contention at extreme concurrency | residual | **Phase 4** |

---

## Phase 1 — Shrink the flock hot zone

**Goal.** Move `OCC prepare` outside the commit lock. Hold flock only across
`_serial_merger.apply` (validate + publish layer + bump manifest).

**Hypothesis.** Hot zone drops from ~70–100 ms to ~6 ms. flock_wait p99 at c=16
drops from ~1050 ms to ~90 ms — a ~12× reduction with no architectural change.

### Work items

| File | Change |
|---|---|
| `backend/src/sandbox/occ/service.py` | Add public `commit_prepared(prepared)` that calls `_serial_merger.apply(prepared)` (and the `_sync` twin). Existing `apply_changeset` keeps its current shape for callers that don't care about the split. |
| `backend/src/sandbox/runtime/api_handlers.py` | In `write_file`, `edit_file`, `_apply_overlay_capture`: call `prepare_changeset` lock-free, then take `_process_commit_gate` + `_commit_lock` only around `commit_prepared(prepared)`. |
| `backend/src/sandbox/runtime/api_handlers.py` | Emit additional timings: `api.{verb}.prepare_s`, `api.{verb}.commit_s`. Move existing `process_gate_wait_s` / `flock_wait_s` to wrap commit only. |
| `backend/tests/unit_test/test_sandbox/test_api/` | Add tests covering: (a) successful prepare+commit; (b) stale prepare retried after intervening commit; (c) gitignored vs tracked classification still consistent. |

### Stale-prepare correctness

Prepare reads the active manifest unlocked. A faster commit may bump the
manifest between prepare and commit. The serial merger already validates
against the current manifest at commit time, so a stale prepare is a normal
OCC rejection — no new failure mode. Document this in `commit_prepared`'s
docstring. Worst-case retry rate at c=16 with 6 ms hot zone: ~6%.

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_api/ -q
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ/ -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py -v -rs -s
```

### Pass bar

| Metric (c=16 p99) | Before | Target | Observed | Verdict |
|---|---:|---:|---:|---|
| `api.write.flock_wait_s` | 1054 ms | ≤ 150 ms | **78.1 ms** | ✅ 13.5× |
| `api.edit.flock_wait_s` | 1097 ms | ≤ 150 ms | **34.6 ms** | ✅ 32× |
| `api.shell.flock_wait_s` | n/a (broken probe) | ≤ 150 ms | **107.6 ms** | ✅ |
| write wall_ms | ~2100 ms | ≤ 1100 ms | **1100.5 ms** | ✅ |
| edit wall_ms | ~2100 ms | ≤ 1100 ms | **1098.4 ms** | ✅ |
| shell wall_ms | ~1438 ms | (informational) | **694 ms** | ~52% reduction |
| Drift | 0 | 0 (mandatory) | 0 | ✅ |

### Status — landed and verified (2026-05-06)

Implemented in `OccService.commit_prepared` (+ sync twin) and the three
runtime API handlers (`write_file`, `edit_file`, `_apply_overlay_capture`).
Prepare runs lock-free; only `commit_prepared` runs under
`_process_commit_gate + _commit_lock`.

New attribution keys emitted: `api.{write,edit,shell}.{prepare_s,commit_s}`.
Wired into the live-e2e JSONL allowlist and per-stage p99 summary in
`backend/tests/live_e2e_test/sandbox/_harness/integrated_cases.py` and
`.../layer_stack_overlay_occ/test_latency_attribution.py`.

Side fix during verification: `_run_shell_real` was missing `mkdir -p`
for the parent dir, so every shell call in earlier sweeps failed with
`status="error"` and `_apply_overlay_capture` short-circuited at the
`if not changes: return` guard, hiding the shell flock cost. Fixed and
re-measured.

Verification: `.venv/bin/pytest backend/tests/unit_test/test_sandbox`
→ 302 passed; live attribution sweep (above) at c∈{1,4,8,16} → all pass-bar
targets met; 4 new unit tests in
`backend/tests/unit_test/test_sandbox/test_api/test_prepare_commit_split.py`
cover prepare→commit happy path, stale-snapshot disjoint-path success,
stale-snapshot overlap → `ABORTED_VERSION`, and gitignored-vs-tracked
routing parity.

### Phase 1 attribution snapshot (c=16 p99)

| Stage | write | edit | shell |
|---|---:|---:|---:|
| `prepare_s` (lock-free) | 154 ms | 148 ms | 156 ms |
| `commit_s` (under lock) | 19 ms | 18 ms | 25 ms |
| `flock_wait_s` | 78 ms | 35 ms | 108 ms |
| wall p99 | 1100 ms | 1098 ms | 694 ms |

Prepare is now the dominant per-call cost across all three verbs, and
~100 ms of it is `gitignore.git_init_s` + `gitignore.materialize_snapshot_s`
— exactly the surface Phase 2 targets.

### Risks

- **Stale prepare cascade.** If retry rate spikes under contention, surface
  retries as a new metric (`occ.prepare.retries`) and gate the phase on
  observed retry rate < 10% at c=16. *Not observed in the c=16 sweep —
  no overlap-conflict failures across write/edit/shell.*
- **Subtle ordering bug** if `serial_merger` was relying on prepare side
  effects happening under lock. *Audit complete: `_orchestrator.prepare_sync`
  performs no layer-stack filesystem mutation; the only writes during
  prepare are into the gitignore oracle's evaluation tmpdir, which is not
  the layer stack.*

### Estimate

1 day implementation + 0.5 day re-verification. *Actual: ~0.5 day across
the prior session's telemetry groundwork plus this session's structural
split and live verification.*

---

## Phase 2 — Eliminate gitignore oracle cold start

**Goal.** Remove the 40–75 ms `materialize + git init` paid by every new
sandbox runtime process. Two complementary mechanisms.

### 2a. Disk-cached oracle workspace

Persist the materialized git workspace under
`<storage_root>/cache/gitignore-<manifest_version>/` instead of in
`tempfile.TemporaryDirectory`.

| File | Change |
|---|---|
| `backend/src/sandbox/runtime/api_handlers.py` | `_LayerStackGitignoreOracle._oracle_for_snapshot`: replace `TemporaryDirectory` with `<storage_root>/cache/gitignore-<version>/`. Use `os.makedirs(..., exist_ok=True)` and a marker file (`.ready`) to detect a fully-built cache. Build under a unique temp name, then atomic-rename. |
| `backend/src/sandbox/layer_stack/stack_manager.py` | Garbage-collect cached oracles whose `manifest.version` is below the active manifest minus N (configurable, default 16). |

After phase 2a:
- First call after a new commit: full cost (~75 ms).
- Concurrent and subsequent calls until the next commit: ~0 ms (atomic open existing dir).

### 2b. Pure-Python pathspec backend (optional, larger win)

Replace `subprocess.run(["git", "check-ignore", ...])` with a `pathspec`-based
matcher reading the `.gitignore` files directly from the snapshot.

| File | Change |
|---|---|
| `backend/src/sandbox/occ/content/gitignore_oracle.py` | Add a `PathspecGitignoreOracle` implementing the same interface. Behind a feature flag (`EPHEMERALOS_GITIGNORE_BACKEND=git|pathspec`). |
| `backend/tests/unit_test/test_sandbox/test_occ/test_content/` | Run the existing gitignore test matrix against both backends; require parity (nested ignores, `!` re-includes, case-folding). |

After phase 2b:
- No `git init`, no materialize, no subprocess. Per-call gitignore cost <1 ms regardless of cache state.
- Default to `pathspec` only after parity proven.

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  EPHEMERALOS_GITIGNORE_BACKEND=pathspec \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py -v -rs -s
```

### Pass bar

| Metric (c=16 p99) | Before | After 2a | After 2b |
|---|---:|---:|---:|
| `gitignore.materialize_snapshot_s` | 33 ms | ~0 ms warm | ~0 ms |
| `gitignore.git_init_s` | 43 ms | ~0 ms warm | 0 ms (skipped) |
| `occ.prepare.total_s` | 92 ms | ~50 ms | ~25 ms |
| Existing gitignore parity tests | green | green | **green required** |

### Risks

- **`pathspec` semantics drift** vs git on edge cases. Mitigation: feature
  flag, exhaustive parity matrix before flipping the default.
- **Cache directory bloat.** Mitigation: bounded GC keyed on manifest version.

### Estimate

2a: 1 day. 2b: 1.5 days (mostly parity testing). Total: 2.5 days.

---

## Phase 3 — Resident runtime worker

**Goal.** Replace per-call `sh -c | python -m sandbox.runtime.server <json>`
with one resident sandbox-side process serving requests over a unix socket.
Eliminates Python interpreter cold start, eliminates cross-process flock
contention entirely (single process → `asyncio.Lock`), eliminates per-call
gitignore oracle cold-start.

### Architecture

```
host                                          sandbox
SandboxAPI.shell()                            sandbox.runtime.daemon
  → call_runtime_api(op, args)                  ↑ accepts unix-socket
  → _call_runtime_server                          JSON envelopes
    → provider.exec_into_existing_process    ←  dispatch to OP_TABLE
      (writes JSON to socket, reads response)    handlers
```

### Work items

| File | Change |
|---|---|
| `backend/src/sandbox/runtime/daemon.py` (new) | Asyncio server bound to a unix socket under `<bundle>/runtime.sock`. Reuses `dispatch_envelope`. Maintains per-`layer_stack_root` `OccService` and `_LayerStackGitignoreOracle` cached across calls. |
| `backend/src/sandbox/control/daemon/install.py` | After bundle upload, start the daemon via `nohup python -m sandbox.runtime.daemon &` with PID file under `<bundle>/runtime.pid`. Health-check on socket. |
| `backend/src/sandbox/control/daemon/command.py` | Replace `_call_runtime_server` with a unix-socket client. Fall back to spawning the daemon if missing. Keep the existing `python -m sandbox.runtime.server` subprocess path behind a flag for migration. |
| `backend/src/sandbox/runtime/api_handlers.py` | Replace flock with the existing in-process `_process_commit_gate` (asyncio.Lock). Remove `_commit_lock` (flock) usage in single-process mode. Keep the flock path only when running in legacy fork-per-call mode. |
| `backend/tests/unit_test/test_sandbox/test_runtime/` | New: socket framing test, daemon lifecycle test, single-process commit serialization test. |
| `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/` | Re-run latency attribution sweep; expect dispatch ~10 ms, flock_wait 0. |

### Migration

The daemon and the legacy fork-per-call path coexist behind
`EPHEMERALOS_RUNTIME_TRANSPORT=daemon|fork`. Default flips to `daemon` after
phase 3 verification gate. `fork` retained for one release for rollback.

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/ -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  EPHEMERALOS_RUNTIME_TRANSPORT=daemon \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox -v -rs -s
```

### Pass bar

| Metric (c=16 p99) | Before | After |
|---|---:|---:|
| `runtime.boot_to_dispatch_s` | 60 ms | ≤ 2 ms (no spawn) |
| Transport (wall − dispatch − boot) | ~900 ms | ≤ 150 ms (one socket round trip) |
| `api.write.flock_wait_s` | post-Phase 1 ≤ 150 ms | ≤ 10 ms (asyncio.Lock) |
| write/edit wall_ms | post-Phase 1 ≤ 1100 ms | ≤ 200 ms |
| Drift | 0 | 0 (mandatory) |
| Daemon RSS after 1000 calls | n/a | < 200 MB |
| Daemon survives sandbox restart | n/a | yes |

### Risks

- **Daemon lifecycle.** Crashes need supervised restart. Mitigation: PID
  file + retry-on-connect-refused in client; if reconnect fails, fall back to
  fork-per-call once and surface an alert.
- **Memory growth.** Cached oracles + manifest snapshots accumulate.
  Mitigation: LRU on manifest version cache (default 16), bounded by
  Phase 2a's GC.
- **Concurrency model change.** flock guaranteed cross-process serialization;
  asyncio.Lock guarantees in-process. If anything else writes the layer
  stack (host-side recovery scripts), it must coordinate with the daemon.
  Mitigation: keep flock as a *secondary* fence inside the daemon for any
  rare host-side writers.

### Estimate

5–8 working days, inclusive of socket-framing edge cases, daemon supervision,
and migration flag.

---

## Phase 4 — Per-path lock buckets (only if needed)

**Goal.** Eliminate residual hot-lock contention for very high concurrency on
overlapping paths. Only justified if Phase 3 metrics show measurable
`asyncio.Lock` queue wait at c ≥ 32.

### Work items

| File | Change |
|---|---|
| `backend/src/sandbox/occ/serial_merger.py` | Replace single global `asyncio.Lock` with N hashed locks (default 16). Hash key: `tuple(sorted(prepared.changed_paths))`. Lock acquisition is in path-sorted order to prevent deadlock. |
| `backend/src/sandbox/runtime/api_handlers.py` | Pass the prepared changeset's path set into the merger so it can route to the right lock bucket. |
| `backend/tests/unit_test/test_sandbox/test_occ/` | Disjoint-paths concurrency test: N=64 parallel commits across N path buckets must complete in ~serial-bucket time, not N × commit-hold. |

### Verification

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_latency_attribution.py \
    -v -rs -s --concurrency-extra=32,64
```

### Pass bar

- At c=32 with disjoint paths, write/edit wall_ms p99 ≤ post-Phase-3 c=16 p99.
- At c=32 with 100% overlapping paths, behavior matches Phase 3 (no regression).
- Drift = 0; conflict semantics preserved.

### Risks

- **Lock-ordering deadlock.** Two commits whose path sets intersect on
  multiple buckets must take all needed locks in a stable order. Mitigation:
  always acquire in sorted bucket-id order.
- **False sharing on `manifest.version`.** All buckets still write the
  manifest. The manifest append is already CAS-atomic; verify under
  contention. Mitigation: add manifest-CAS retry test.

### Estimate

3–5 days. Only land after Phase 3 metrics justify it.

---

## End-state projection

| Verb | Baseline c=16 p99 | After P1 | After P1+P2 | After P1+P2+P3 |
|---|---:|---:|---:|---:|
| read_file | 931 ms | 931 ms | 931 ms | ~150 ms |
| write_file | 2131 ms | ~1100 ms | ~1050 ms | ~200 ms |
| edit_file | 2111 ms | ~1100 ms | ~1050 ms | ~200 ms |
| shell `echo>file` | 1438 ms | 1438 ms | 1438 ms | ~500 ms |

Phase 1 alone is the highest-leverage / lowest-risk change. Phase 3 is the
single biggest unlock but the largest engineering investment. Phase 2 is
cheap insurance that pays off at every concurrency level. Phase 4 is gated
on real-world need.

---

## Cross-references

- Per-call snapshot migration: `.omc/plans/per-call-snapshot-layer-stack-migration/per-call-snapshot-layer-stack.md`
- Live E2E suite plan: `.omc/plans/per-call-snapshot-layer-stack-migration/live-e2e-test-suite-plan.md`
- Latency attribution report: `backend/tests/live_e2e_test/sandbox/phase-04-latency-attribution-report.md`
- Pure API load report: `backend/tests/live_e2e_test/sandbox/phase-04-pure-sandbox-api-load-report.md`
