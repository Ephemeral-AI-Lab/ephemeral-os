# Phase 6 — Fold daemon-side overlay stages into a single in-namespace process

**Estimated effort:** 2-3 days (1 day engineering + 1-2 days E2E)
**Risk profile:** MEDIUM — narrows the daemon-side `svc.cmd` hot path; correctness is well-bounded by existing parity tests, but the result envelope changes shape between auditor and committer
**Status:** Proposed
**Blocks on:** Phase 5 default-on lands and remains stable; Phase 4 svc_cmd dispatch remains the daemon entry point

> **Background.** Phase 4 moved `OverlayAuditor.execute` from the orchestrator
> into the daemon. One `ci_rpc` call per `svc.cmd`. But the auditor itself
> still has six stages — `git_snapshot`, `upload_runtime`, `run_overlay`,
> `read_stdout`, `read_diff`, `cleanup` — and each one spawns its own
> sandbox-local subprocess via `_exec_process` (`subprocess.run` shell=True
> with `wrap_bash_command`). That made sense when the auditor lived on the
> orchestrator and stages had to round-trip through `transport.exec`. Inside
> the daemon, those subprocess fork/exec costs are now the dominant per-op
> wall time.

## Goal

Reduce daemon-side `svc.cmd` per-op latency from ~3.5s of subprocess
overhead to ~0.8-1.0s by collapsing the multi-stage auditor into **one**
in-namespace subprocess invocation plus pure-Python file I/O for the
result. Project the modeled p50 from 4.1s @ 10× to ~1.3-1.5s @ 10×.

This is a **perf phase**, not a feature phase. Result-shape parity is the
gate; latency reduction is the win.

## Why now

Phase 5 perf framing carried Phase 4's "8.047 s → 0.450 s ≈ 18×" headline
forward. That number is for `write_file` (a pure daemon RPC, no overlay).
The actual `svc.cmd` overlay path measured in
`phase_3.5_svc_cmd_overlay_concurrency_1_5_10_2026-05-02T18-57-05Z.json`
shows a different story:

| concurrency | p50 wall | observed daemon-side stage cost (sum) | unaccounted |
|---:|---:|---:|---:|
| 1 | 8.71s | ~3.5s (5 subprocess hops) + 0.5s `upload_runtime` (one-time) | ~4.7s (cold daemon imports + first-op tax) |
| 5 | 3.39s | ~3.5s + ci_rpc | matches |
| 10 | 4.09s | ~3.5s + ci_rpc | matches |

The 1× → 10× concurrency improvement (8.7s → 4.1s) confirms transport
amortization is the dominant amortizable cost, leaving daemon-internal
subprocess multiplexing as the per-op floor we cannot pipeline within a
single op. Phase 6 attacks that floor.

## What is and isn't in scope

**In scope.**
- Daemon-side `OverlayAuditor.execute` and `AuditedCommandExecutor` collapse.
- Inlining `git_snapshot` into `overlay_run.py` (the runtime already
  supports it — `overlay/runtime/runner.py:66-71` builds an in-namespace
  snapshot when `--snap` is empty).
- Replacing `read_stdout` / `read_diff` / `cleanup` with pure-Python file
  I/O after the unshare subprocess returns.
- Stripping `wrap_bash_command` (conda activation overhead) from the
  internal hops that survive.

**Out of scope.**
- Replacing overlayfs with a userland CoW. The CoW layer is not the
  bottleneck (`walk_upperdir` is 1ms in the JSON; `setup_mounts` is 100ms).
- Streaming `on_progress_line`. Phase 4 already documented this as a
  separate transport-level enhancement; Phase 6 keeps the existing
  final-stdout replay behavior.
- The orchestrator-side fallback path (in-process backend used when
  `EOS_CI_IN_SANDBOX=0`). It's already in cleanup-pending status per
  Phase 5 §7.1 and should be deleted before Phase 6 ships, not modified.

## What ships

| Artifact | File | Purpose |
|---|---|---|
| Single-shot daemon auditor | `backend/src/sandbox/code_intelligence/overlay/auditor.py` (modified) | New `execute_single_shot` codepath: one `subprocess.run` for the unshare invocation, in-process file reads for stdout/diff, in-process `shutil.rmtree` for cleanup. |
| Snapshot inlining | `backend/src/sandbox/code_intelligence/overlay/command_executor.py` (modified) | Daemon-resident `OverlayAuditor` is constructed with `inline_snapshot=True`; orchestrator-side stays unchanged for the in-process fallback path. |
| Bash-wrap stripping | same auditor file | `_do_exec` learns a `bypass_wrap_bash=True` mode for daemon-internal hops; not applied to the user command. |
| Result envelope contract | `backend/src/sandbox/code_intelligence/overlay/runtime/runner.py` (modified) | `overlay_run.py` writes its result envelope to a single file (`result.json`) atomically before exit; the auditor reads it via `pathlib.Path.read_text()`. |
| Single-shot parity test | `backend/tests/test_sandbox/test_code_intelligence/test_overlay_single_shot_parity.py` (new) | Asserts the single-shot path produces a `SimpleNamespace` byte-identical to the multi-stage path on a fixed corpus of `gitinclude` / `gitignore` / `mixed` / `aborted` / `rejected` cases. |
| Daemon perf E2E | `backend/tests/test_e2e/test_live_ci_phase6_svc_cmd_fold.py` (new) | Live `svc.cmd` at 1×/5×/10× against the same dask sweevo fixture as Phase 3.5 §F; asserts p50 @ 10× < 1.8s. Headline gate. |

## Detailed task list

### Task 6.1 — `overlay_run.py` writes one result envelope

**File:** `backend/src/sandbox/code_intelligence/overlay/runtime/runner.py`

Today the runtime writes:
- `<run_dir>/stdout.bin` — captured user stdout
- `<run_dir>/diff.ndjson` — meta line + per-change lines

Phase 6 adds:
- `<run_dir>/result.json` — single envelope:
  ```json
  {
    "snap": "<sha>",
    "exit_code": 0,
    "stdout_path": "<run_dir>/stdout.bin",
    "diff_path": "<run_dir>/diff.ndjson",
    "snapshot_timings": {...},
    "run_timings": {...},
    "rejected": null
  }
  ```
  When the script policy-rejects, `rejected` is the reject block and the
  diff/stdout paths are omitted (matches today's `_reject_result` shape).

Write order: stdout.bin → diff.ndjson → result.json. The result file is
atomic (`os.replace` from a temp file in the same run_dir) so the daemon
never reads a half-written envelope.

The existing NDJSON layout for `diff.ndjson` and the binary `stdout.bin`
do not change. Only the addition of `result.json` is new; downstream
parsers in `auditor.parse_diff_ndjson` are unchanged.

### Task 6.2 — `OverlayAuditor.execute_single_shot`

**File:** `backend/src/sandbox/code_intelligence/overlay/auditor.py`

New entry point (signature mirrors `execute`, drops `on_progress_line` —
single-shot path does not stream):

```python
async def execute_single_shot(
    self,
    command: str,
    *,
    timeout: int | None = None,
    description: str = "",
    agent_id: str = "",
    stdin: str | None = None,
    attribute_changes: bool = True,
) -> SimpleNamespace:
    ...
```

Pipeline (one subprocess for the unshare invocation + zero subprocess
calls for the rest):

1. **Acquire semaphore** (unchanged).
2. **Ensure runtime uploaded** (unchanged; one-time, cached).
3. **Spawn unshare** with `--snap=""` so `overlay_run.py` builds the
   snapshot in-namespace via `build_live_snapshot_in_namespace`
   (`overlay/runtime/runner.py:66-71`).
   `subprocess.run(["unshare", "-Urm", "python3", script, ...])` — one
   process, captures stdout for the result envelope path.
4. **Read result.json** via `pathlib.Path.read_text()`. No subprocess.
5. **Read stdout.bin** via `pathlib.Path.read_bytes()` and decode utf-8.
   No subprocess.
6. **Read diff.ndjson** via `pathlib.Path.read_text()` if present, parse
   via the existing `parse_diff_ndjson` (no behavior change).
7. **OCC commit** in-process via `OverlayCommandCommitter` (unchanged).
8. **Cleanup** via `shutil.rmtree(run_dir, ignore_errors=True)`. No
   subprocess.

Stage timings preserved: each step still records into `stage_timings` so
the JSON shape downstream callers expect (`run_timings`,
`snapshot_timings`, etc.) is unchanged.

`OverlayAuditor.execute` (the multi-stage path) stays in place for the
orchestrator-side fallback. When the in-process backend is deleted (Phase
5 §7.1 cleanup), the multi-stage path can be deleted with it.

### Task 6.3 — Daemon-side wiring

**File:** `backend/src/sandbox/code_intelligence/overlay/command_executor.py`

`AuditedCommandExecutor` learns one new flag:

```python
def __init__(
    self,
    *,
    sandbox_id: str,
    workspace_root: str,
    write_coordinator: Any,
    rebind_sandbox: Callable[[Any], None],
    transport: SandboxTransport | None = None,
    single_shot: bool = False,  # NEW
) -> None:
    ...
```

`cmd(...)` dispatches to `overlay.execute_single_shot(...)` when
`single_shot=True`, else `overlay.execute(...)`.

**File:** `backend/src/sandbox/code_intelligence/backend.py`

`InProcessCiBackend` (line 201) constructs `AuditedCommandExecutor`. When
the backend is running inside the daemon (i.e., `transport is None and
sandbox is None` — the daemon-side construction site), pass
`single_shot=True`. Detection: gated by an explicit constructor flag
threaded from the daemon's `run_daemon` setup (`ci_daemon.py`) — not by
runtime guesswork.

### Task 6.4 — Result-shape parity

**File:** `backend/tests/test_sandbox/test_code_intelligence/test_overlay_single_shot_parity.py`

Five parametrized cases run both `execute` and `execute_single_shot`
against the same fixture corpus, then assert byte-equality on the full
16-field `SimpleNamespace`:

1. **Pure gitinclude OCC commit** — tracked file edit, `git_commit_status="committed"`.
2. **Pure gitignore direct-merge** — write under a `.gitignore`'d path, `gitignore_direct_merged_count > 0`, `git_commit_status="noop"`.
3. **Mixed gitinclude + gitignore** — both routes hit, `mixed_gitinclude_gitignore=True`.
4. **Aborted-version (OCC strict-base mismatch)** — base content drift between snapshot and commit.
5. **Policy reject** — overlay script emits `_reject` (e.g., `.git/` write).

For each case the test verifies the `SimpleNamespace` produced by both
paths is field-for-field identical (only `git_snapshot_timings` and
`overlay_run_timings` are normalized to keys-only since absolute timings
differ). Includes the additive metadata: `gitinclude_changed_paths`,
`gitignore_direct_merged_paths`, `mixed_partial_apply`, `warnings`.

The corpus runs without a real Daytona sandbox by mocking
`_exec_process` against a tmpdir-rooted overlay (the existing
`test_overlay_*.py` mechanism in `test_sandbox/test_code_intelligence/`).

### Task 6.5 — Strip `wrap_bash_command` from internal hops

**File:** `backend/src/sandbox/code_intelligence/overlay/auditor.py`

In `execute_single_shot`, after Task 6.2 lands, the only subprocess hop
left is the unshare invocation. That one **does** still need
`wrap_bash_command` because the user's command runs inside it under
bash and needs the conda environment activated.

If any internal hops survive (e.g., the script-upload bootstrap on cold
daemon), they should call `subprocess.run` directly with `shell=False`
and `argv=[...]`, bypassing the conda wrapper. Audit
`_ensure_script_uploaded` for this — it currently uses a python3 -c
inline snippet via `wrap_bash_command`, which is unnecessary.

### Task 6.6 — Phase 6 live E2E

**File:** `backend/tests/test_e2e/test_live_ci_phase6_svc_cmd_fold.py`

Mirror Phase 3.5 §F's `test_svc_cmd_overlay_high_concurrency_probe` at
1×/5×/10× concurrency on the same dask sweevo fixture. New assertions:

- `svc_cmd_10x_latency.p50 < 1.8s` (down from 4.09s baseline).
- `svc_cmd_1x_latency.p50 < 2.5s` (down from 8.71s baseline).
- Per-op subprocess count from daemon log: exactly **1** unshare
  invocation per op (gated by a `daemon.log` grep for `subprocess.run`
  call sites — a structural check, not a perf check).

Includes a `compare_to(phase_3_5_svc_cmd_baseline)` summary
table printed in test teardown.

**Run command:** `.venv/bin/pytest backend/tests/test_e2e/test_live_ci_phase6_svc_cmd_fold.py -m live -v -s`

### Task 6.7 — Regression check

- `.venv/bin/pytest backend/tests/test_sandbox/ backend/tests/test_tools/ -q`
  — green with new single-shot tests.
- Phase 0–5 live E2Es — re-run only if any of `auditor.py`,
  `command_executor.py`, `runner.py`, or `backend.py` have surface-area
  changes that touch their hot path; otherwise rely on the parity test
  (Task 6.4) as the structural gate.

## Definition of done

- [ ] `overlay_run.py` writes `result.json` atomically; existing
      `stdout.bin` + `diff.ndjson` semantics unchanged.
- [ ] `OverlayAuditor.execute_single_shot` exists; runs one subprocess
      and uses pure-Python file I/O for stdout/diff/cleanup.
- [ ] Daemon-side `AuditedCommandExecutor` constructed with
      `single_shot=True`; orchestrator-side falls back to the multi-stage
      path until the Phase 5 §7.1 cleanup deletes it.
- [ ] Five-case parity test (Task 6.4) is green; the full 16-field
      `SimpleNamespace` is byte-identical between paths.
- [ ] Live E2E (Task 6.6) headline assertion: **`svc_cmd_10x p50 < 1.8s`**
      against `dask__dask_2023.3.2_2023.4.0`.
- [ ] Live E2E secondary assertion: `svc_cmd_1x p50 < 2.5s`.
- [ ] Per-op subprocess count from daemon log = 1 (Task 6.6 structural check).
- [ ] Regression: full unit suite green; relevant prior-phase live E2Es
      re-run if hot-path surface area changed.
- [ ] PR description includes: side-by-side timing JSON for Phase 3.5 §F
      vs Phase 6, the headline p50 delta in big bold letters, and the
      list of stages that disappeared (`git_snapshot`, `read_stdout`,
      `read_diff`, `cleanup` as separate hops).

## Risk callouts

| Severity | Risk | Mitigation |
|---|---|---|
| **HIGH** | Single-shot path produces a `SimpleNamespace` shape drift downstream callers in `backend/src/sandbox/lifecycle/commit.py` rely on | Task 6.4 parity test gates this for the full 16-field shape; Phase 4's existing `test_svc_cmd_shape_parity.py` carries forward as a second wall. |
| **HIGH** | Inlined `build_live_snapshot_in_namespace` runs as the namespace-mapped uid (typically root inside `unshare -Urm`); fails if git refuses to operate when `safe.directory` is set or the workspace owner mismatches | The runtime already exercises this codepath when `--snap=""` is passed (per `run.py:67-71`); add an explicit unit test for the inlined path. If `safe.directory` blocks it, set `GIT_CONFIG_GLOBAL=/dev/null` + `safe.directory=*` in the snapshot env (the live snapshot script already does similar). |
| **MEDIUM** | `result.json` write is interrupted (kill -9 mid-write) → daemon reads malformed JSON → cmd fails with `OverlayRunError` | Atomic rename via `os.replace(tmp, result.json)`; daemon falls back to `OverlayRunError` with a recognizable message and the cmd surfaces as a normal failure (not a silent hang). |
| **MEDIUM** | `shutil.rmtree(run_dir, ignore_errors=True)` swallows real errors that `rm -rf` would have logged | Switch to `ignore_errors=False` and catch `OSError` explicitly; log at warning level, do not raise (matches today's `_cleanup_run_dir` semantics). |
| **MEDIUM** | Removing `wrap_bash_command` from `_ensure_script_uploaded` breaks PATH resolution for python3 in some sandbox configs | The script bootstrap is one-time per daemon; if portability is a concern, keep `wrap_bash_command` here and only strip it from the read/cleanup hops. The win is in the per-op stages, not bootstrap. |
| **LOW** | Cold daemon first-op tax (the ~4.7s unaccounted slack at 1× in the baseline) is not addressed by Phase 6 | Out of scope. Track separately as "daemon eager-import bootstrap" if the cold path matters; the first-op tax does not reproduce on warm path which is what Phase 6 measures. |
| **LOW** | `on_progress_line` callers rely on streaming behavior that the single-shot path explicitly drops | Phase 4 §4.4 already documented the streaming contract as final-stdout replay; Phase 6 keeps that contract. If streaming is later added back, it will be a transport-level addition (Phase 4 option B), independent of Phase 6. |

## What this does *not* solve

The remaining wall-time floor after Phase 6 lands:

| Component | Floor |
|---|---:|
| Orchestrator → daemon `ci_rpc` | ~0.5s (Phase 5 verb path) |
| One unshare subprocess invocation | ~0.1-0.3s startup + ~0.5s real work (snapshot + setup_mounts + user_cmd + walk + classify) |
| In-process OCC commit | ~0.005s |
| Pure-Python file reads (stdout, diff) + rmtree | ~0.005-0.01s |
| **Modeled total per `svc.cmd` warm path** | **~1.1-1.3s** |

To go materially below ~1s, the next levers (none of which Phase 6
addresses) are:
1. **Pre-warm the unshare namespace.** Keep one `unshare -Urm bash`
   process pooled per daemon; reuse it via signal/fifo for new ops.
   Saves the ~0.3s namespace creation cost per op.
2. **Batch the OCC commit pre-step.** Hash and base-content-fetch can
   start while the user command is still running.
3. **Native binary for `overlay_run`.** Replace the python startup with
   a Go/Rust binary; saves ~0.1s per invocation.

These are individually smaller wins than Phase 6's collapse. Worth
considering only if the modeled ~1.2s p50 still leaves `svc.cmd` on the
critical path of agent latency targets.

## Hand-off

After Phase 6 lands and stabilizes:

1. **Delete the multi-stage `OverlayAuditor.execute` path.** Phase 5
   §7.1 cleanup deletion target grows by one method. Atomic separate
   commit, easily revertable.
2. **Re-baseline Phase 3.5 §F.** The svc_cmd overlay JSON becomes the
   new `phase_6_svc_cmd_overlay_concurrency_*.json`; carry it forward
   as the perf claim of record.
3. **Decide on namespace pre-warming.** If the `~1.2s` p50 is still on
   the critical path, propose a Phase 7 namespace pool. If not, declare
   the migration's perf work done and treat further reductions as
   feature-time work, not migration debt.

---

## Appendix A — Why this is not a CoW redesign

The user-facing question prompted by the perf signal — "should we
replace overlayfs with a userland CoW (CubeSandbox / joeinnes-cow
style)?" — was investigated in the analysis preceding this spec. The
finding: overlayfs's contribution to wall time is ~0.5s of real work
(`setup_mounts` 0.10s, `walk_upperdir` 0.001s, `classify` 0.06s, user
command 0.30s). The remaining ~3.5s/op is daemon-internal subprocess
multiplexing, not CoW.

A userland CoW (FUSE passthrough, hardlink trees, `LD_PRELOAD`-style
write redirection) would add cost, not remove it: FUSE pays a context
switch per syscall in the user command's hot path; hardlink trees turn
the diff walk from "scan a sparse upperdir" into "scan the entire
workspace looking for changed inodes." The only honest motivation for a
userland CoW in this codebase is **portability** to sandbox providers
that ban `unshare -Urm` or kernel overlay mounts. Daytona allows both.

If portability ever becomes a constraint, the simpler answer is to drop
overlay isolation entirely, run the user command in the live tree, and
let `inotify`/`fanotify` capture writes — the OCC commit's strict-base
contract already provides the conflict-detection guarantee that makes
the overlay's "atomic apply" property non-load-bearing.
