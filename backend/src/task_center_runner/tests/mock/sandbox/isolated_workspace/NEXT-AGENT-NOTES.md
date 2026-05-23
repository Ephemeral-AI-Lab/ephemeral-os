# Next-agent notes — iws live e2e session 2026-05-23 (post-NEXT-FIXES)

This session implemented `NEXT-FIXES.md` items #1, #3, #5 and unblocked
all of tier 2 (isolation) and tier 3 (network), plus half of tier 4
(failure_modes). The remaining work is per-tier debugging — code is
green where landed; the remaining failures are isolated and documented
below with reproducers.

## Score card

| Tier | Tests | Status | Notes |
|---|---:|---|---|
| 0 — pre_flight | 152 | PASS (unchanged from session 2) | static AST checks |
| 1 — happy_path | 5/5 PASS | 5 passed in ~45s | backstop xfail REMOVED (was diagnostic-only) |
| 2 — isolation | 5/5 PASS | 5 passed in ~66s | flush during pinned iws now works |
| 3 — network | 15/15 PASS | 15 passed in ~158s | DNS bind-mount + veth-side IP fix + ping/host/iputils |
| 4 — failure_modes | 4/8 PASS | partial | 2 deferred (R11), 2 need investigation — §Remaining tier 4 |
| 5 — resource_controls | NOT RUN | unknown | next agent: run after tier 4 closes |
| 6 — concurrency | NOT RUN | unknown | next agent: run after tier 5 closes |
| 7 — gc_and_persistence | NOT RUN | unknown | next agent: run after tier 6 closes |
| 8 — soak | OPT-IN | -- | per DEFERRED-WORK.md, out of scope |
| 9 — performance | NOT RUN | unknown | needs latency_budget.json refresh |

## NEXT-FIXES.md status

- **§1 apt cache** — DONE. `backend/scripts/cache_iws_apt_debs.sh`
  builds a 27-deb closure (iproute2 + nftables + iputils-ping +
  bind9-host + transitive deps); fixture docker-cps + dpkg -i's the
  closure when in-container `ip`/`nft`/`ping`/`host` are missing. Cache
  is committed at `backend/tests/_assets/iws_apt_cache/jammy-amd64/`.
  Re-run the script when the sweevo base ubuntu version bumps.
- **§2 validate tiers 2-7** — IN PROGRESS. Tier 2 + 3 closed. Tier 4 at
  4/8. Tiers 5-7 not yet run.
- **§3 backstop root-cause** — DONE. The actual bug was (a) scratch on
  container overlayfs (overlay refuses to be its own upperdir) and
  (b) the new fsopen/fsmount API renders the mountinfo source as "none"
  not "overlay". The previous session's "PYTHONPATH/cap inheritance"
  hypothesis was wrong. Test now passes strictly.
- **§4 latency_budget.json** — NOT STARTED. Tier 9 performance not run.
- **§5 upstream LinuxKit issue** — DRAFT WRITTEN at
  `UPSTREAM-LINUXKIT-PROCFS-EPERM.md`. Ready to file when next we hit
  the procfs-in-userns EPERM on a Docker Desktop bump.

## Production-code changes landed this session

Three real product bugs were uncovered and fixed:

1. **`launch_daemon.sh` zombie detection** (commit `81b127e96`). The
   fast-path used `kill -0 $PID` which returns success for zombie
   processes — but a zombie daemon can't serve requests, so respawn
   was being skipped. Sweevo container PID 1 is `sleep infinity`, not
   a reaper, so a daemon that dies stays zombie indefinitely. Added
   `daemon_pid_alive()` helper that reads `/proc/<pid>/status` State
   and rejects Z/X. Production deployments using `--init` or k8s aren't
   affected (real init reaps).

2. **iws veth-ns-end was never configured** (commit `81b127e96`).
   `install_veth()` created the veth pair, attached the host-end to the
   bridge, brought host-end up — but the ns-side end inside the iws
   had no IP, no link up, no route. The daemon allocated `ns_ip
   10.244.0.2` but never assigned it anywhere. Any ping/curl/DNS from
   inside the iws hit "Network unreachable". Added `_ip_ns()` helper
   (nsenter into the iws net ns) that runs `ip link set ns up + ip addr
   add ns_ip/24 + ip route add default via gateway`.

3. **`configure_dns_in_ns` couldn't unlink bind-mounted resolv.conf**
   (commit `81b127e96`). Docker bind-mounts `/etc/resolv.conf` from the
   container runtime; `os.unlink` returned EBUSY. Switched to
   "write fresh file in /tmp + mount --bind it OVER /etc/resolv.conf".
   The iws's mountns has private propagation so this shadowing stays
   inside the iws.

4. **`run_in_handle` stdin contract mismatch** (commit `1a4fe1fd7`).
   The manager was sending `<json>\n<raw_stdin>` to the setns_exec
   helper, but the helper's design expects a single JSON object with
   stdin bytes as `stdin_b64` inside. The helper crashed with
   `JSONDecodeError: Extra data` whenever stdin was non-empty (e.g. 5
   MB write bodies). Fixed both sides — manager encodes b64,
   helper decodes and pipes to child's fd 0.

## Test infrastructure changes landed this session

1. **`iws_clean_sandbox` env autopurge** (commit `1a4fe1fd7`). If a
   failure_modes test sets `EOS_ISOLATED_WORKSPACE_TEST_FAIL_AT=<phase>`
   via `set_daemon_env` and then crashes before the try/finally
   `clear_daemon_env`, the env var leaks into `/etc/environment` and
   poisons EVERY subsequent test in the session with the same inject.
   `iws_clean_sandbox` now greps for known TEST_* knobs and purges +
   respawns the daemon if found. Cheap no-op on the steady-state path.

2. **`_iws_rpc.enter`/`exit_`/etc. domain-error catch** (commit
   `1a4fe1fd7`). The shared `call_daemon_api` raises
   `_DaemonDispatchError` on any response with an "error" key, but the
   handlers return `{"success": False, "error": {...}}` for expected
   domain errors. The failure_modes tests assert on the dict form.
   Wrapped lifecycle calls in `_call_lifecycle` that catches the
   exception and rebuilds the envelope. Docstring already promised this
   ("lifecycle errors are surfaced inside the response envelope") —
   implementation now matches.

3. **`daemon_kill_and_respawn` bootstrap probe tolerates inject errors**
   (commit `1a4fe1fd7`). The respawn probe is just to trigger
   `_ensure_manager`/`startup_gc`; whether the bootstrap enter SUCCEEDS
   is irrelevant. Tests that set `EOS_ISOLATED_WORKSPACE_TEST_FAIL_AT`
   before respawning correctly expect their OWN enter to fail; the
   respawn probe shouldn't propagate that as a setup failure.

4. **Conftest `EOS_ISOLATED_WORKSPACE_UPPERDIR_BYTES=256MiB`** (commit
   `a88011feb`). Default production value is 1 GiB × handle; with
   `memavail_fraction=0.5` and a ~3 GiB sweevo container budget, two
   handles would be refused by the host RAM gate (2 GiB > 1.5 GiB).
   256 MiB × 5 fits. The cap is a reservation accounting unit (not an
   enforced quota) so shrinking only widens what tests can probe.

5. **`peer_publish_file` drops redundant `api.overlay.flush`** (commit
   `a88011feb`). `api.write_file` already commits via OCC and advances
   the layer-stack tip — that IS the publish. The previous fixture
   additionally called `api.overlay.flush` to collapse the tip back
   into the workspace base, but flush refuses to run while any iws
   snapshot lease is active (it resets layer storage, invalidating the
   pinned refs). Removing the flush lets the isolation/ tier exercise
   peer publishes during an open iws without changing the design
   invariant.

## Remaining tier 4 failures (4 of 8)

### test_freezer_stall_falls_back_to_sigstop — DEFER (R11 unimplemented)

The R11 SIGSTOP-fallback path is INTENTIONALLY DEFERRED per a
comment in `manager.py:IsolatedWorkspaceHandle.freezer_degraded`:

```python
# PR 0 wires the path but R11's fallback itself is deferred — the field
# stays False on healthy hosts and is forward-compatible with the
# eventual implementation.
```

The test asserts `freezer_degraded=True` after chmod 000 on
`cgroup.freeze`. With R11 unimplemented, the daemon either (a) succeeds
in writing to the chmod-000 file because it runs as root with
CAP_DAC_OVERRIDE, or (b) silently swallows the EACCES without flipping
the flag. Either way, the flag stays False.

**Recommendation:** mark `xfail(reason="R11 SIGSTOP fallback deferred,
see manager.py:freezer_degraded comment")` until R11 lands as a real
feature.

### test_holder_refuses_sigterm_sigkill_fallback — INVESTIGATE

raw_exec at line 48 (the bash `kill -STOP $pid` command) times out
after 10s. The 300s `RuntimeWarning: executor did not finish joining`
is Python's asyncio cleanup, not the original timeout window.

What's likely happening: `pgrep -f 'ns_holder'` finds the iws's
ns_holder which is owned by the just-respawned daemon. `kill -STOP`
succeeds. But the docker-exec channel for raw_exec may share some
resource that locks up — possibly the same containerd shim handling
SIGSTOPped processes.

**Reproducer:**
```bash
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EOS__RUNNER__SANDBOX_REUSE_MODE=reuse \
EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
  .venv/bin/pytest \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/failure_modes/test_holder_refuses_sigterm_sigkill_fallback.py \
    -v --tb=long -p no:randomly
```

**Investigation suggestions:**
1. Time the raw_exec — confirm it hangs >10s (TimeoutError is correct
   then, not a 300s wait).
2. Replace `pgrep -f 'ns_holder'` with something narrower so we don't
   risk matching the daemon itself.
3. Use `nsenter -t <holder_pid> -- kill -STOP 1` (signal the holder's
   PID 1 inside the iws's pid ns) instead of host-side SIGSTOP, so we
   don't pause a process owned by the daemon's process tree.

### test_argv_e2big_via_in_ns_write — SHOULD PASS WITH §1 FIX

The stdin_b64 contract fix (commit `1a4fe1fd7`) addresses this. NOT
re-validated — the next agent should re-run tier 4 to confirm.

### test_ns_holder_dies_before_ready — SHOULD PASS WITH §3 FIX

The daemon_kill_and_respawn tolerance fix (commit `1a4fe1fd7`)
addresses this. NOT re-validated — next agent should re-run.

## Tier 5/6/7 — likely-green forecast + risks

Based on what tier 3 exposed:
- **veth-side IP/route fix** unblocks any test that does outbound
  network from inside the iws (likely tier 6 concurrency does this for
  N=5 fan-out).
- **launch_daemon.sh zombie fix** unblocks any test that uses
  `daemon_kill_and_respawn` (likely tier 7 daemon-restart tests).
- **stdin_b64 contract** unblocks any test that writes >argv-size
  bodies to in-iws files (likely tier 5 resource_controls'
  upperdir_tmpfs_enospc test).
- **iws_clean_sandbox env purge** shields any test that previously
  contaminated /etc/environment.

Risk: cgroup write tests in tier 5 (e.g.
`test_quota_one_per_agent`, `test_total_cap_blocks_new_agent`) may hit
the same chmod-000 / root-bypass issue as freezer_stall. Watch for
similar "flag never flips" failures.

## Commits this session (newest first)

1. `1a4fe1fd7` fix(sandbox/iws): tier 4 partial — stdin_b64, env shield, error surfacing
2. `81b127e96` fix(sandbox/iws): tier 3 network — daemon respawn, veth config, DNS bind-mount
3. `a88011feb` test(sandbox/iws): apt deb cache + backstop fix + tier 2 unblock

## How to resume

```bash
# 1. Re-run tier 4 to confirm stdin_b64 + bootstrap tolerance fixes.
EOS_SWEEVO_INSTANCE=dask__dask_2023.3.2_2023.4.0 \
EOS_SANDBOX_PROVIDER=docker EOS_ISOLATED_WORKSPACE_ENABLED=true \
EOS__RUNNER__LIVE_E2E__HEAVY_ENABLED=true \
EOS__RUNNER__SANDBOX_REUSE_MODE=reuse \
EPHEMERALOS_DATABASE_URL="sqlite:///./.ephemeralos/ephemeralos.db" \
  .venv/bin/pytest \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/failure_modes/ \
    -v --tb=short -p no:randomly

# Expected: 6/8 PASS (was 4/8). The 2 remaining are #freezer_stall
# (DEFER) and #test_holder_refuses (INVESTIGATE).

# 2. Add xfail marker to test_freezer_stall (one-line change, see
#    test_mount_overlay_backstop session 2 commit for pattern).

# 3. Investigate test_holder_refuses_sigterm per §investigation suggestions.

# 4. Move to tier 5:
.venv/bin/pytest \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/resource_controls/ \
    -v --tb=short -p no:randomly

# 5-7. Tiers 6, 7, then performance / tier 9 budget refresh.
```

## Cross-references

- `DEFERRED-WORK.md` — session 2 resolution log
- `NEXT-FIXES.md` — original NEXT-FIXES recipes (this session's input)
- `UPSTREAM-LINUXKIT-PROCFS-EPERM.md` — upstream kernel issue draft
- `RUNNING-LIVE-TESTS.md` — environment setup
- `PLAN.md` — original phase plan
- `IMPLEMENTATION-REPORT.md` — prior-session landing log
