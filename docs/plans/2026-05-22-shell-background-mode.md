# Shell `background=True` — ralplan consensus plan (round 2)

Status: ITERATE complete after Architect + Critic review. Ready for execution.
Authoritative source for implementation; supersedes any earlier conversation draft.

## What changed vs round 1

- Chosen option flipped from A (engine-only wrap) → B (daemon-native job control). A was rejected with file:line evidence that asyncio cancel on the host cannot reach the in-sandbox bash through the current daemon RPC + thread-executor + synchronous-subprocess stack.
- Pre-mortem grew 3 → 6 scenarios.
- Test plan grew 3 → 8 scenarios (added the 5 the Critic required).
- New v1-scope subsystems (no longer "optional hardening"): daemon `ShellJobRegistry`, cancel-flag-polled subprocess wait, idempotent `release_lease`, dedicated `ShellExecutor` pool, single-terminal-status latch in `BackgroundTaskManager`.

## Principles

1. **Daemon owns the lease lifecycle.** Cancel routes through the daemon, not around it.
2. **Backgrounding is a control-plane change.** Lease → mount → run → capture → publish → release stays the same lifecycle as foreground shell.
3. **Exactly one terminal status per task** (`finished`, `failed`, `cancelled`).
4. **Concurrent shells bounded by existing daemon concurrency** (`sandbox_quota` + per-sandbox queue).
5. **Progress is best-effort, results are authoritative.** `check_background_task_result` on a running shell may return stale stdout; on a terminal shell it returns the full daemon `ShellResult`.

## Decision drivers

1. Lease-orphan safety on cancel — only B satisfies this natively.
2. Per-call workspace consistency: `workspace_tree_bytes==0`, no leaked `run_dir`/`upperdir`, single lease release.
3. No regression on synchronous shell.

## Options

### B — Daemon-native job control (chosen)

New daemon RPC verbs:
- `shell.launch(req) → {job_id}` — returns once the child is spawned and the lease is acquired.
- `shell.poll(job_id) → snapshot` — `{status, exit_code?, stdout_tail, stderr_tail, pid_alive}`.
- `shell.cancel(job_id) → ack` — sets cancel flag; SIGTERM pgrp; SIGKILL after 2 s grace.
- `shell.reap(job_id) → ShellResult` — waits for thread join, captures upperdir only if NOT cancelled, OCC publishes only if capture happened, releases lease idempotently, removes `run_dir`.

Lease is owned by the daemon-side `ShellJob`, indexed by `job_id`. The engine's `BackgroundTaskManager` wraps a thin polling asyncio task that owns the host-side lifecycle; cancel on the engine fires `shell.cancel` then awaits `shell.reap`. Daemon's cleanup is fully decoupled from the host RPC connection.

- Pros: lease release is daemon-driven; survives host RPC disconnect; survives engine process kill via TTL reaper; idempotent and observable.
- Cons: ~300 LOC daemon-side `ShellJobRegistry` + RPC verbs; one new audit-event family.

### A — Engine-only wrap (rejected)

The plan's cancel mechanism does not exist in the binary. Three load-bearing hops break:

- `backend/src/sandbox/daemon/rpc/server.py:82-160` `_handle_connection` issues no concurrent socket read while the handler runs → client disconnect cannot raise `CancelledError` into the handler.
- `backend/src/sandbox/daemon/rpc/dispatcher.py:71-92` wraps the handler in `try/except Exception` that swallows any cancellation that did propagate.
- `backend/src/sandbox/execution/service.py:110` runs `run_sync_in_executor(command_runner, ...)`; the strategies (`backend/src/sandbox/execution/strategies/namespace.py:95`, `backend/src/sandbox/execution/subprocess_runner.py:82`) use synchronous `subprocess.run` / `Popen.wait`. Python has no thread interrupt — even with a propagated cancel, bash keeps running, the lease stays held, the upperdir keeps growing.

To salvage A we would need:
- A.1 daemon-side disconnect listener at `server.py`
- A.2 polling subprocess wait at `subprocess_runner.py`
- A.3 idempotent cleanup at `sandbox_overlay.py:647-649`

That is the same surface as B minus the explicit `job_id`, and still leaves the `done_callback ↔ cancel()` race in `backend/src/engine/background/manager.py:125-141, 232-252` unresolved (violates Principle 3). **B is the smallest correct design, not the larger one.**

### C — Defer (rejected)

Does not answer the user's need (long shells while the agent does other work).

## Pre-mortem (6 scenarios)

1. **Lease orphan on RPC disconnect.** Host engine crashes mid-shell.
   - Mitigation: daemon `ShellJobRegistry` has a TTL reaper (default 5 min idle = no `shell.poll`/`reap`) that runs SIGKILL + umount + release_lease + rmtree on its own loop.
2. **Bash refuses SIGTERM.** Child ignores signal.
   - Mitigation: escalate to SIGKILL on pgrp after 2 s grace; force-umount with `MNT_DETACH`.
3. **OCC publish on a half-killed upperdir.** Capture sees partial files.
   - Mitigation: cancel sets `job.cancelled=True` before SIGKILL; `publish_cycle` is skipped on cancelled jobs — upperdir is `rmtree`d without OCC apply. `ShellResult` on reap carries `status="cancelled"` and `changed_paths==[]`.
4. **Executor-thread leak on cancel fan-out.** `backend/src/sandbox/daemon/async_bridge.py:60` has a 200-worker default; 200 stuck shells exhaust it.
   - Mitigation: dedicated `ShellExecutor(max_workers=sandbox_quota*4)` + the polling subprocess wait (~100 ms cancel-flag check + SIGKILL) ensures threads return promptly.
5. **`_release_lease` non-idempotent.** Double release on cancel race.
   - Mitigation: add `_released` flag on `ShellJob` and idempotency guard at `backend/src/sandbox/daemon/service/sandbox_overlay.py:647-649`; second release returns silently.
6. **Cancel after `done_callback` fired.** Engine marks CANCELLED on a task that already completed.
   - Mitigation: `BackgroundTaskManager` uses a single terminal-status latch (CAS) so only the first writer wins. Precedence: completed > failed > cancelled.

## Implementation steps

1. **Daemon `ShellJobRegistry`** — new file `backend/src/sandbox/daemon/service/shell_job.py`.
   - `ShellJob` dataclass: `job_id`, `pid`, `pgrp`, `lease_id`, `upperdir`, `run_dir`, `started_at`, `cancel_event: threading.Event`, `cancelled: bool`, `result: Optional[ShellResult]`, `released: bool`.
   - Registry: `dict[job_id, ShellJob]` with TTL reaper running every 30 s.

2. **RPC verbs** — `backend/src/sandbox/daemon/rpc/server.py` + `dispatcher.py`.
   - `shell.launch`: build `ShellJob`, acquire lease, mount overlay, fork bash in `ShellExecutor`, return `{job_id}`.
   - `shell.poll`: return `{status, exit_code?, stdout_tail, stderr_tail, pid_alive}`.
   - `shell.cancel`: set `cancel_event`, SIGTERM pgrp, schedule SIGKILL+2 s; mark `cancelled=True`.
   - `shell.reap`: wait for thread join (caller-supplied timeout), capture upperdir IFF `not cancelled`, OCC publish IFF capture happened, release lease idempotently, rmtree `run_dir`, return `ShellResult`.

3. **Polling subprocess wait** — `backend/src/sandbox/execution/subprocess_runner.py:82-110`.
   - Replace `proc.wait(timeout=...)` with `while proc.poll() is None:` loop sleeping 100 ms, checking `cancel_event.is_set()` each tick. On set: `os.killpg(pgrp, SIGTERM)`, wait 2 s, `SIGKILL` if alive.

4. **Idempotent `_release_lease`** — `backend/src/sandbox/daemon/service/sandbox_overlay.py:647-649`.
   - Wrap in `if self._released: return; self._released = True`; same pattern as `OperationOverlayHandle.release` at lines 97-101.

5. **Engine shell tool** — `backend/src/sandbox/api/tool/shell.py`.
   - On `request.background == True`: call `shell.launch` daemon RPC, return placeholder `ShellResult`, register an asyncio task that periodically `shell.poll`s and on terminal `shell.reap`s. `BackgroundTaskManager` tracks that asyncio task.
   - On `background == False`: existing single-RPC path stays untouched.

6. **Single-terminal-status latch** — `backend/src/engine/background/manager.py:125-141, 232-252`.
   - Add `_terminal_latch: threading.Lock` per tracked task; `set_terminal_status` checks-and-sets. Documented precedence: completed > failed > cancelled.

7. **Executor pool cap** — `backend/src/sandbox/daemon/async_bridge.py:60`.
   - Introduce `ShellExecutor(max_workers=sandbox_quota*4)` distinct from the default pool. Shell subprocess work uses it exclusively.

8. **Audit events** — new family:
   - `sandbox_shell_launched(job_id, lease_id)`
   - `sandbox_shell_polled(job_id, exit_code)`
   - `sandbox_shell_cancelled(job_id, reason)`
   - `sandbox_shell_reaped(job_id, status, changed_paths_count)`

## Tests (8 scenarios)

All under `backend/src/task_center_runner/tests/mock/sandbox/` with a shared probe `backend/src/task_center_runner/agent/mock/background_shell_probe.py`. All run against the real SWE-EVO Docker sandbox with a scripted probe (same pattern as `test_heavy_io_zoned_concurrent.py`).

| # | Test | Asserts |
|---|---|---|
| T1 | `test_background_shell_golden.py` | 3 launches → wait → check → all `finished` with full stdout; `workspace_tree_bytes==0`; manifest_depth grew by exactly 3. |
| T2 | `test_background_shell_cancel.py` | Launch + cancel at 5 s. Next foreground mount < 100 ms. Cancelled job's `changed_paths==[]`. No leftover `run_dir`/`upperdir`. `/proc/<pid>` gone within 3 s. Exactly one `release_lease` audit event for that `lease_id`. |
| T3 | `test_background_shell_interleave.py` | 1 background 30 s + 10 interleaved foreground ops. `max_tool_concurrency==2`. Foreground p95 mount latency unchanged. `_foreign_watch_task` does not serialize foreground past 250 ms. |
| T4 | `test_background_shell_engine_kill.py` | Start engine subprocess, launch 1 background shell, SIGKILL the engine. Assert daemon TTL reaper releases lease + kills bash within `ttl_seconds + 30 s`. Foreground op from a fresh engine succeeds afterward. |
| T5 | `test_background_shell_executor_exhaustion.py` | Launch 210 background shells (> 200-worker default). Cancel all. A subsequent foreground read completes in < 1 s, confirming threads returned to the dedicated `ShellExecutor`. |
| T6 | `test_background_shell_partial_write_cancel.py` | Shell runs `dd of=tracked.bin bs=1M count=200`. Cancel mid-write at 5 s. `tracked.bin` does not exist in workspace OCC (no truncated publish). Upperdir was discarded. |
| T7 | `test_background_shell_cancel_during_maintenance.py` | Launch + reap normally + cancel arriving during `run_maintenance_after_publish`. OCC ends in consistent state, no orphan manifest fragment. |
| T8 | `test_background_shell_late_cancel_race.py` | Launch a 1 s shell; sleep 1.2 s; cancel. Exactly one terminal status (single-latch invariant). `check_background_task_result` returns the real result, not "cancelled overlaid on result". |

T4 – T8 are the five the Critic required.

## Acceptance criteria

| # | Criterion | Measured via |
|---|---|---|
| AC-1 | `shell(background=True)` returns within ≤ 50 ms regardless of shell duration | T1 |
| AC-2 | `wait_background_tasks` settles to `wait_completed` with full stdout per task | T1 |
| AC-3 | After cancel: next foreground `command_exec.mount_workspace_s` ≤ 100 ms | T2 |
| AC-4 | After cancel: `ps -p <pid>` empty within 3 s, `/proc/<pid>` gone | T2 |
| AC-5 | Exactly one `sandbox_shell_reaped` audit event per `sandbox_shell_launched`; lease release count == lease acquire count | T1, T2, T6 |
| AC-6 | Cancelled jobs contribute 0 layers and 0 OCC writes | T2, T6 |
| AC-7 | Engine-process SIGKILL with live background shell → daemon releases lease and kills bash within `ttl + 30 s` | T4 |
| AC-8 | 210 cancelled shells do not block a follow-up foreground op > 1 s | T5 |
| AC-9 | Cancel during `run_maintenance_after_publish` leaves OCC consistent | T7 |
| AC-10 | Late cancel after natural completion returns exactly one terminal status; precedence completed > failed > cancelled | T8 |
| AC-11 | No `internal_error`, `stale lowerdir`, `mount_failed`, `manifest references missing layer` in `sandbox_events.jsonl` across all 8 scenarios | sandbox_events scan |

## Verification

```bash
set -a; source .env; set +a

# Stage 1: daemon unit tests
uv run pytest -q -x --tb=short \
  backend/src/sandbox/daemon/service/tests/test_shell_job.py \
  backend/src/sandbox/daemon/rpc/tests/test_shell_job_rpc.py \
  backend/src/sandbox/execution/tests/test_subprocess_runner_cancel.py

# Stage 2: engine unit tests
uv run pytest -q -x --tb=short \
  backend/src/engine/background/tests/test_manager_terminal_latch.py \
  backend/src/sandbox/api/tool/tests/test_shell_background.py

# Stage 3: integration scenarios (real sandbox, mocked agent)
uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox/test_background_shell_golden.py \
  backend/src/task_center_runner/tests/mock/sandbox/test_background_shell_cancel.py \
  backend/src/task_center_runner/tests/mock/sandbox/test_background_shell_interleave.py \
  backend/src/task_center_runner/tests/mock/sandbox/test_background_shell_engine_kill.py \
  backend/src/task_center_runner/tests/mock/sandbox/test_background_shell_executor_exhaustion.py \
  backend/src/task_center_runner/tests/mock/sandbox/test_background_shell_partial_write_cancel.py \
  backend/src/task_center_runner/tests/mock/sandbox/test_background_shell_cancel_during_maintenance.py \
  backend/src/task_center_runner/tests/mock/sandbox/test_background_shell_late_cancel_race.py

# Stage 4: full regression
uv run pytest -q -x --tb=short --durations=20 \
  backend/src/task_center_runner/tests/mock/sandbox

# Stage 5: perf evaluation
for D in $(ls -td .sweevo_runs/scenario_logs/sandbox.background_shell_* 2>/dev/null | head -8); do
  python3 .agents/skills/sandbox-performance-evaluation/scripts/summarize_sandbox_perf.py "$D"
done
```

## ADR

- **Decision**: implement Option B (daemon-native job control: `shell.launch` / `poll` / `cancel` / `reap`) for `shell(background=True)`. Engine's `BackgroundTaskManager` wraps a thin polling task that owns the asyncio lifecycle; the daemon owns the lease, child process, upperdir, and OCC publish.
- **Drivers**: lease-orphan safety, per-call workspace consistency, no regression on synchronous shell.
- **Alternatives considered**: A (engine-only wrap, rejected with file:line evidence), C (defer, rejected).
- **Why chosen**: only design where Principle 1 ("daemon owns lease lifecycle") holds without the engine guessing the daemon's state. Survives host RPC disconnect and engine process kill via TTL reaper. A would have required A.1 + A.2 + A.3, which adds the same surface as B minus the explicit `job_id`, and still leaves the `done_callback ↔ cancel` race in `backend/src/engine/background/manager.py:125-141` unresolved.
- **Consequences**: ~300 LOC daemon-side `ShellJobRegistry` + RPC; new dedicated `ShellExecutor` pool; new audit event family; existing synchronous shell path unchanged.
- **Follow-ups**:
  - stdout streaming for in-flight shells (separate phase).
  - generalize `shell.launch` / `poll` / `reap` to other long-running tools.

## Risk register

- **R1 (medium)**: TTL value too short → reaps a still-busy shell. Mitigation: configurable, default 5 min, **reset on every `shell.poll`**.
- **R2 (low)**: dedicated `ShellExecutor` sized too small → background launches queue. Mitigation: default `sandbox_quota * 4`; expose as config.
- **R3 (low)**: SIGTERM-then-SIGKILL grace blocks `shell.cancel` RPC for 2 s. Mitigation: `cancel` returns immediately after SIGTERM; SIGKILL escalation runs on a daemon-side timer thread.

## Review provenance

- Round 1 plan: Option A, 3 pre-mortem scenarios, 3 tests. Rejected.
- Round 2 (this document): Option B, 6 pre-mortem scenarios, 8 tests.
- Architect findings cited file:line:
  - `backend/src/sandbox/daemon/rpc/server.py:82-160`
  - `backend/src/sandbox/daemon/rpc/dispatcher.py:71-92`
  - `backend/src/sandbox/execution/service.py:110`
  - `backend/src/sandbox/execution/subprocess_runner.py:82-110`
  - `backend/src/sandbox/execution/strategies/namespace.py:95-108`
  - `backend/src/sandbox/daemon/async_bridge.py:60,258-292`
  - `backend/src/sandbox/host/daemon_client.py:477-504`
  - `backend/src/engine/background/manager.py:125-141, 232-252`
  - `backend/src/sandbox/daemon/service/sandbox_overlay.py:647-649, 666-689`
  - `ephemeralos.yaml:45`
- Critic verdict: REJECT round 1; iterate as above. Round 2 satisfies the seven must-fix items the Critic listed.
