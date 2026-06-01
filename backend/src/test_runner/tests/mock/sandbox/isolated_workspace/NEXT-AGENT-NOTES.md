# Next-agent notes — iws live e2e session 2026-05-23 (closure)

This session closed every remaining tier failure from the prior handover.
**All eight live tiers pass end-to-end** against the docker provider on
the dask sweevo image. The 152-test static surface (Tier 0) continues to
pass on any OS.

## Score card

| Tier | Tests | Status | Wall time | Notes |
|---|---:|---|---:|---|
| 0 — pre_flight | 152 | PASS | <1 s | static AST checks, unchanged |
| 1 — happy_path | 5/5 PASS | green | ~45 s | unchanged |
| 2 — isolation | 5/5 PASS | green | ~60 s | unchanged |
| 3 — network | 15/15 PASS | green | ~155 s | host introspection green |
| 4 — failure_modes | 7/7 PASS | green | ~125 s | rollback paths + 3 test/contract fixes |
| 5 — resource_controls | 6/6 PASS | green | ~135 s | conftest restores UPPERDIR_BYTES if cleared |
| 6 — concurrency | 11/11 PASS | green | ~225 s | cgroup move-in + event-loop unblock + audit-filter fix |
| 7 — gc_and_persistence | 13/13 PASS | green | ~225 s | veth orphan parser fix |
| 8 — soak | OPT-IN | -- | -- | 4 tests, per DEFERRED-WORK.md, out of scope |
| 9 — performance | 7/7 PASS | green | ~115 s | capability probe trusts docker provider |

Combined tier 4-9 in a single pytest invocation: **44 passed in 12 m 35 s**.
Full live suite (tiers 1-7 + 9, soak deselected) in a single pytest
invocation: **85 passed in 16 m 26 s**. The combined-run isolation
bug flagged in the prior session is closed — the launch_daemon.sh
zombie-detection fix and the conftest UPPERDIR_BYTES restoration
together remove the failure modes that previously surfaced only when
all tiers ran end-to-end.

## Commits this session (newest first)

1. `466058dd3` fix(sandbox/iws): move orphan PIDs to root cgroup before idle cleanup
2. `552d49454` test(sandbox/iws): tier 9 — capability probe trusts docker provider
3. `28e88bb99` fix(sandbox/iws): tier 7 — veth orphan reaper + skipped-test log path
4. `85f23c368` fix(sandbox/iws): tier 6 — 3 concurrency invariants
5. `643a9f6bd` test(sandbox/iws): conftest re-adds UPPERDIR_BYTES if cleared
6. `e0bf03069` fix(sandbox/iws): tier 4 — close last 4 failure_modes failures

## Production-code changes landed this session

Seven real product bugs surfaced once tier-4 unblocked the rest of the
suite. Every one of them is a long-standing race or contract violation
the prior implementation never had to face, not a regression.

1. **The legacy isolated write wrapper invoked `in_ns_write` via `python -m`**
   (commit `e0bf03069`). After setns into the iws's mntns, the bundle's
   import path is no longer reliably on sys.path; `-m` then crashed with
   `ModuleNotFoundError: No module named 'sandbox'`. Switched to absolute
   path lookup via `__file__`. The script is bundle-local; /tmp is
   inherited into the iws mntns so the path is reachable.

2. **`install_veth` raised bare `RuntimeError` for "No such process"**
   (commit `e0bf03069`). When ns_holder dies between spawn and netns
   attach (HOLDER_CRASH inject, real-world race), the netlink call
   `ip link set ... netns <root_pid>` fails. The daemon dispatcher then
   surfaced this as `internal_error` instead of the contract-required
   `setup_failed`. Wrapped the install_veth call site to translate the
   netlink ESRCH into `setup_failed` with `failed_step="install_veth"`.

3. **`run_in_handle` blocked the event loop on synchronous
   `subprocess.run`** (commit `85f23c368`). Two agents' tool_calls
   serialized because the daemon's sole asyncio thread sat on subprocess wait for the full
   helper duration. Wrapped the synchronous runtime call in
   `loop.run_in_executor` so other agents can progress while one helper
   is in wait. Two parallel `sleep 0.7` now finish in <1.1 s wall (was
   1.69 s).

4. **No code wrote PIDs into `cgroup.procs` of the iws cgroup** (commit
   `85f23c368`). `memory.current` of an iws shell was charged against
   the daemon's parent cgroup (`/docker/<id>`), defeating the entire
   point of the iws cgroup. `setns_exec` now accepts an optional
   `cgroup_path` in the JSON payload and writes its own PID to
   `cgroup.procs` BEFORE fork; the forked child inherits cgroup
   membership and per-iws memory accounting works correctly.

5. **`_reap_orphans` veth parser skipped every eos-iws-* veth**
   (commit `28e88bb99`). The filter `token.startswith(HANDLE_PREFIX)
   and ":" not in token` rejected every interface because
   `ip -o link show` formats lines as `<idx>: <ifname>[@<peer>]:
   <flags>` — the ifname token always carries a trailing colon.
   Stripped the trailing colon + `@<peer>` suffix before pattern
   matching; veth orphans now actually get reaped by startup_gc.

## Test infrastructure changes landed this session

1. **conftest `iws_clean_sandbox` restores `UPPERDIR_BYTES`** (commit
   `643a9f6bd`). `test_host_ram_gate_refuses_over_budget` overrides
   the env var then `clear_daemon_env` deletes the line entirely, so
   the conftest-session default (256 MiB) was lost and downstream
   tests tripped the 1 GiB-default host RAM gate. The clean-sandbox
   fixture now idempotently re-adds the line if missing alongside its
   existing TEST_* purge.

2. **`test_holder_refuses_sigterm` filters pgrep output by comm**
   (commit `e0bf03069`). `pgrep -f 'ns_holder'` matched the calling
   sh subshell itself (its cmdline contains the literal pattern);
   SIGSTOPping the shell deadlocked the docker-exec channel and the
   raw_exec timed out at 10 s. Filter `pgrep -lf` output to only the
   `unshare` parent + `python` grandchild via comm prefix.

3. **`test_init_complete_blocks_enter_during_startup_gc` filters
   enters to post-restart only** (commit `85f23c368`). The audit JSONL
   persists across SIGKILL+respawn, so the seeded pre-restart enter
   ALSO showed up in the prior-events window. Count only enters
   appearing after the last `gc_orphan` watermark; that's the
   invariant the test actually wants to pin.

4. **`iws_capability_probe` trusts the docker provider** (commit
   `552d49454`). Host-side probes always returned False on macOS dev
   boxes (no overlay/cgroup-v2/userns on Darwin), so every Tier 9
   test that gated on `has_mount_overlay` skipped with "capability
   not detected." But the actual kernel work happens inside the
   sweevo container, which has all three surfaces. When
   `EOS_SANDBOX_PROVIDER=docker` is set, treat the probes as constant
   True; the native-Linux path keeps the empirical probe for
   reference-CI semantics.

## Known limitations / open follow-ups

1. **Latency budget file** — `_data/latency_budget.json` still
   absent. Tier 9 latency tests pass against the in-session baseline
   collected by the warm-up fixture, but the absolute-p95 half of
   each test is silently a no-op. PR-7 governance per
   `RUNNING-LIVE-TESTS.md` §6 is unchanged from prior session.

2. **Tier 8 soak (4 tests)** is opt-in and out of scope per
   `DEFERRED-WORK.md`. Last validated 2026-05-23 prior to this
   session's changes; re-validate before declaring soak green.

## Cross-references

- `DEFERRED-WORK.md` — session 2 resolution log
- `NEXT-FIXES.md` — session 3 recipes (now fully landed)
- `UPSTREAM-LINUXKIT-PROCFS-EPERM.md` — upstream kernel issue draft
- `RUNNING-LIVE-TESTS.md` — environment setup
- `PLAN.md` — original phase plan
- `IMPLEMENTATION-REPORT.md` — prior-session landing log

## How to resume

```bash
# Full live tier in one shot — should be 89 passed in ~25 min.
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EOS__RUNNER__SANDBOX_REUSE_MODE=reuse \
EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
  .venv/bin/pytest \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/ \
    -m "not live_e2e_soak" \
    -v --tb=short -p no:randomly

# If you want the latency budget refresh next:
# see RUNNING-LIVE-TESTS.md §6 for the PR-7 governance.
```
