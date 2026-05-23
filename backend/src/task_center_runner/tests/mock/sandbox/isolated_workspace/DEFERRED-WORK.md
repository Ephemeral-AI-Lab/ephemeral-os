# Deferred work — isolated_workspace live e2e suite

**Status as of 2026-05-23:** after 11 infrastructure + correctness fixes, the
live suite reaches the actual iws lifecycle on macOS Docker Desktop. Tests
pass **in isolation** but fail when run together. The remaining work is
test-isolation engineering, not config or single-bug fixes.

This doc tracks what landed, what was deferred, and the concrete next steps.

---

## What works

- **Static surface (Tier 0):** 152 tests pass on any host — `pre_flight/`
  + `unit_test/test_sandbox/test_daemon/` + import-fence + audit unit
  tests. Always-on baseline.
- **Happy-path (Tier 1):** 4/5 tests pass in isolation:
  - `test_enter_then_shell_then_exit` ✓
  - `test_lowerdir_visible_inside_mntns` ✓
  - `test_server_survives_tool_call_boundary` ✓
  - `test_status_reports_open_handle` ✓
  - `test_mount_overlay_backstop` ✗ — see "Known remaining" below.
- **Single-test runs across other tiers:** anecdotally pass (e.g.
  `concurrency/test_handle_lock_serializes_tool_calls` passes alone but
  takes ~2.5 min including fixture teardown). Not systematically validated.

The full set of infra and correctness fixes (audit/ bundle, daemon
`/etc/environment` sourcing, unshare `--map-root-user`, pipe FD direction,
veth IFNAMSIZ, `pid_for_children`, heredoc-safe cwd wrapping, etc.) is
captured in commits on this PR.

---

## What is deferred

### 1. Test isolation under combined run (PRIMARY BLOCKER)

**Symptom:** running the full Tier 1-7 + 9 suite
(`pytest backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/
-m "not live_e2e_soak"`) produces:
- ~36+ defunct `python3` processes in the sweevo container after ~30 tests
  — every `iws.enter()` spawns `unshare --fork python3 -m sandbox.isolated_workspace.scripts.ns_holder`
  which is never reaped on test exit.
- Daemon eventually dies with `ConnectionRefusedError` once PID pressure
  or socket state degrades.
- Tests that pass in isolation start failing partway through the run.

**Hypothesis:** `iws_clean_sandbox` fixture only drives `api.isolated_workspace.exit`
for a fixed list of agent IDs (`agent-A..E`) but does NOT:
- reap the `ns_holder` child processes spawned by `_LinuxRuntime.spawn_ns_holder`
  (these become defunct once their PID 1 dies but parent never waits);
- clean up `/tmp/eos-iws/*` scratch dirs left by aborted enters;
- reset the IP-address pool between tests;
- free veth interfaces that survived a failed exit.

**Recommended next steps:**
1. Audit `_iws_rpc.exit_` cleanup contract. Confirm what it should do for
   "agent never entered" vs "agent entered + crashed" vs "agent entered +
   exited normally."
2. In `_LinuxRuntime.kill_holder`, add a `waitpid` after the SIGKILL so
   the ns_holder doesn't become defunct. Currently `subprocess.Popen` keeps
   the process descriptor in the daemon, which never gets a `.wait()` from
   the manager's exit path.
3. Make `iws_clean_sandbox` fixture more aggressive: enumerate all open
   handles via `api.isolated_workspace.list_open` (would need to add), then
   exit each. Today it only knows about hardcoded `agent-A..E`.
4. Add a per-test container-side janitor RPC (e.g.
   `api.isolated_workspace.test_reset`) that nukes all iws state — only
   exposed when `EOS_ISOLATED_WORKSPACE_TEST_HARNESS=1`. Call it from
   `iws_clean_sandbox`.

### 2. `test_mount_overlay_backstop` (Tier 1) — diagnostic test, deeper issue

**Symptom:** the backstop test reads a Python script (already PYTHONPATH-
and asyncio-fixed in this PR) that invokes `_LinuxRuntime.mount_overlay`
DIRECTLY (bypassing the daemon). Result:
```
sandbox.isolated_workspace.manager.IsolatedWorkspaceError: mount_overlay helper failed
```

**Note:** the OTHER 4 happy_path tests prove `mount_overlay` works through
the normal `enter()` path. So this is a diagnostic-only test that breaks
when invoked outside the daemon's context. Likely missing:
- The `setns_overlay_mount.py` helper expects to be called from a context
  with the daemon's PYTHONPATH and access to the runtime bundle;
- The test runs the helper as a fresh `python3 -m` invocation which may
  have different env / capability inheritance.

**Recommended next step:** debug by running the failing helper with
`EOS_ISOLATED_WORKSPACE_TEST_HANG_AT=overlay_mount` and inspecting the
container's `dmesg` for the mount syscall errno. Or just remove this
backstop test since the 4 daemon-path tests already cover the surface.

### 3. Performance: tests are 10-30× slower than the doc's "12 min" estimate

**Observed:**
- `test_handle_lock_serializes_tool_calls` alone: 151 s (doc-implied: <5 s).
- Full run estimated at 50+ min (doc said ~12 min for Tier 1-9 minus Tier 8).

**Contributors:**
- `iws_clean_sandbox` calls `exit_` 5 times per test at 10 s timeout each.
  When daemon is degraded, that's 50 s of pure cleanup-timeout per test.
- Fixture setup paths (sweevo image pull, container provision) dominate
  the first 30-60 s of a fresh session.
- No fixture caching means N×fixture-cost — but the session-scoped
  `iws_sandbox` should amortize this. It's the per-test `iws_clean_sandbox`
  that's hot.

**Recommended next step:** add a `--iws-skip-cleanup` pytest flag for dev
loops, and trim `iws_clean_sandbox` to first call a fast "any-open?" probe
before iterating exits.

### 4. Other untested tiers

The following tiers have NOT been live-validated end-to-end. Single-test
spot-checks suggest most should work given the infra fixes, but the
combined-run failure means we can't say "Tier X passes":

| Tier | Tests | Status |
|---|---:|---|
| 2 — isolation | 5 | Untested |
| 3 — network | 15 | Untested |
| 4 — failure_modes | 8 | Untested |
| 5 — resource_controls | 7 | Untested |
| 6 — concurrency | 11 | At least 1 passes in isolation (151 s) |
| 7 — gc_and_persistence | 14 | Untested |
| 8 — soak (live_e2e_soak) | 5 | Opt-in only; not attempted |
| 9 — performance | 7 | Untested; needs latency_budget.json (§6 in RUNNING-LIVE-TESTS.md) |

---

## Out-of-scope (not deferred — just not in scope)

- **Tier 8 soak suite** (`-m live_e2e_soak`): 5 stress tests, 30-90 min
  budget. Not attempted; opt-in only per the doc.
- **Tier 9 latency budget refresh** (`_data/latency_budget.json`): PR 7
  governance per PLAN §17, not part of "make tests green".
- **Real Linux host validation:** all of the above is from macOS + Docker
  Desktop. Native Linux runs should be faster but the test-isolation bug
  is kernel-independent.

---

## Cross-references

- **RUNNING-LIVE-TESTS.md** — this directory's setup + how-to-run doc.
- **PLAN.md** — original phase plan; §§5-23 cover per-test contract.
- **NEXT-AGENT-GUIDE.md** — phase-by-phase context for next session.

## Commits in this PR

The 11 fixes that landed (in commit order):

1. `fix(sandbox/host): runtime bundle ships top-level audit/ package` — daemon was crashing at import because iws handlers depend on `audit.jsonl`.
2. `fix(sandbox/host): daemon spawn sources /etc/environment` — env vars written to `/etc/environment` by the conftest never reached the respawned daemon.
3. `fix(sandbox/iws): unshare needs --map-root-user for --mount-proc` — without root-mapping, `--mount-proc` fails EPERM on Docker Desktop and on kernels that reject unprivileged /proc mounts.
4. `fix(sandbox/iws): spawn_ns_holder pipe FD direction was reversed` — parent was reading from the write end, child was writing to the read end.
5. `fix(sandbox/iws): open_ns_fds uses pid_for_children, not pid` — `unshare --fork` keeps the unshare PROCESS in the outer PID ns; the new ns is reachable only via `pid_for_children`.
6. `fix(sandbox/iws): veth interface name fits IFNAMSIZ (15)` — was 17 chars (`eos-iws-` + 8-char handle + suffix), Linux rejected with "not a valid ifname".
7. `fix(sandbox/provider/docker): heredoc-safe cwd subshell wrapping` — `(cmd)` on one line glued `)` to the last `<<EOF`-terminator line and broke bash heredocs.
8. `test(sandbox/iws): conftest binds workspace, installs iproute2/nftables, uses IWS_LAYER_STACK_ROOT` — bundles three test-fixture fixes.
9. `test(sandbox/iws): mass-rename layer_stack_root=_REPO_DIR → layer_stack_root=_iws_rpc.IWS_LAYER_STACK_ROOT` — 131 call-sites; tests were passing the workspace path as `layer_stack_root` which violates the `validate_workspace_binding_paths` constraint.
10. `test(sandbox/iws): backstop helper script PYTHONPATH + asyncio.run` — diagnostic test needed runtime bundle on path and to await the async `mount_overlay`.
11. `docs(sandbox/iws): clarify Linux-means-container in RUNNING-LIVE-TESTS, write DEFERRED-WORK` — this doc.
