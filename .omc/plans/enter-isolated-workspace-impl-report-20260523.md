# Implementation Report — `enter_isolated_workspace` (v2)

**Date:** 2026-05-23
**Source plan:** `.omc/plans/enter-workspace-with-isolated-network-20260522.md`
**Branch:** `main`
**Naming choice:** Per user direction, the `pinned_*` internal prefix was
dropped — files, classes, audit events, and cgroup naming all use
`isolated_workspace` / `eos-iws-` consistently.

---

## Summary

Implemented the structural skeleton of `enter_isolated_workspace` /
`exit_isolated_workspace`: a daemon-side `IsolatedWorkspaceManager` that
acquires a layer-stack lease, allocates a per-workspace IP from a /24 pool,
emits 5 new audit events, and tears down cleanly. The kernel-touching helpers
(unshare/setns/fsmount/cgroup-freezer/ip-nft) are stubbed behind a `_Runtime`
protocol so the lifecycle is fully exercisable on macOS via a `FakeRuntime`
while the real Linux implementation is filled in iteratively against the
sweevo Docker image.

**Structural separation from OCC (the load-bearing security argument) is
complete and verified.** `IsolatedWorkspaceHandle` is not a subclass of
`OperationOverlayHandle`, the handler module's transitive imports do not
reach `sandbox.occ.*` or `sandbox.daemon.service.sandbox_overlay`, and the
setns_exec helper has the R10-required minimal import set.

### Numbers

- **Core LoC:** 1422 (plan estimate: ~950 — the gap is docstrings,
  `_LinuxRuntime` stubs, and `_ManagerConfig`; algorithm itself is ~700).
- **Test LoC:** 888 (plan estimate: ~1300; integration/e2e deferred).
- **New ops registered:** 8 (`api.isolated_workspace.{enter,exit,status,shell,
  read_file,write_file,edit_file,search_content}`).
- **New audit events:** 5
  (`sandbox_isolated_workspace_{enter,exit,tool_call,evicted,gc_orphan}`).
- **Files added:** 8 production, 6 test.
- **Files edited:** 3 (`dispatcher.py`, `events.py`, `test_routing_invariants.py`).
- **Files NOT edited (per Principle 2):** `shell.py`, `read.py`, `write.py`,
  `edit.py`, `search.py`. Default flow is untouched.
- **Tests passing:** 49 new + 17 routing/fence regressions = 66/66 on macOS.

---

## macOS-verified (66 tests pass; live on this machine)

| Area | Test file | Asserts |
|---|---|---|
| **R1 — behavioral OCC fence** | `test_no_occ_calls.py` | Mocks `CommitQueue.apply` AND `apply_sync` across a full `enter → op → exit` cycle; both call counts == 0. Demoted to smoke test (see "Known limitations"). |
| **R3 — import-graph fence** | `test_import_fence.py` | Transitive imports of `handler.isolated_workspace_ops` and `service.isolated_workspace` exclude `sandbox.occ.*`, `sandbox.daemon.service.sandbox_overlay`, and `sandbox.execution.overlay.kernel_mount`. Walks every `sandbox.*` module transitively reached. |
| **N2 — dynamic-import fence** | `test_import_fence.py` | AST-walks `handler.isolated_workspace_ops` for `importlib.import_module`, `__import__`, `spec_from_*` calls; flags any match. |
| **R10 — setns_exec import discipline** | `test_import_fence.py` | `setns_exec.py` top-level imports are within `{__future__, os, sys, ctypes, json, sandbox.daemon.scripts._setns_libc}` (plus the parent package); flags `logging`, `asyncio`, `subprocess`, `threading`, `concurrent` specifically. |
| **C1 — distinct handle type** | `test_handle_shape.py` | `IsolatedWorkspaceHandle` is not a subclass of `OperationOverlayHandle`; carries no `publish_*` / `apply_changeset` / `commit_prepared` callable; persisted shape excludes raw FDs. |
| **C2 — distinct exit path** | `test_handle_shape.py` | `IsolatedWorkspaceManager.exit` and `_teardown` source contains no reference to OCC publish symbols. |
| **State machine** | `test_manager_lifecycle.py` | `active → exiting → stopped`; idempotent exit; no-op when agent has no handle. |
| **Quota (B1)** | `test_manager_lifecycle.py` | Second `enter()` for same agent raises `isolated_workspace_already_open` with `created_at` / `last_activity` diagnostics. Different agents share the daemon. |
| **Global cap** | `test_manager_lifecycle.py` | `total_cap=1` blocks second agent with `quota_exceeded`. |
| **Host-RAM gate (R6)** | `test_manager_lifecycle.py` | `(N+1) × upperdir > 0.5 × MemAvailable` returns `host_capacity_exceeded` with budget/required deltas. |
| **TTL eviction** | `test_manager_lifecycle.py` | Stale handle is reaped; fresh handle stays; audit event `sandbox_isolated_workspace_evicted` emitted. |
| **ns FD lifecycle** | `test_manager_lifecycle.py` | All four FDs (`user`, `mnt`, `pid`, `net`) closed exactly once on `exit`; re-closing raises `OSError(EBADF)`. |
| **Cgroup freeze/thaw** | `test_manager_lifecycle.py` | Per tool call: `freeze=False` → `run_in_handle` → `freeze=True`. |
| **Partial-mount rollback** | `test_manager_lifecycle.py` | If `mount_overlay` throws, the lease is released, the agent map stays empty. |
| **manager.json persistence** | `test_manager_lifecycle.py` | Round-trip writes/reads `schema_version` + per-handle records (lease_id, ns_ip, cgroup_path, but NEVER raw FDs). |
| **Schema-mismatch fallback (N5)** | `test_manager_lifecycle.py` | Foreign `schema_version` is logged + treated as empty; pool falls back to naming-convention GC. |
| **`_init_complete` gates enter (§5 step 0)** | `test_manager_lifecycle.py` | Concurrent `enter()` blocks until startup_gc finishes; prevents IP-pool double-allocation race. |
| **`initialize()` idempotence** | `test_manager_lifecycle.py` | Re-calling `initialize` preserves active handles. |
| **Feature gate** | `test_manager_lifecycle.py` | `EOS_ISOLATED_WORKSPACE_ENABLED=false` → `enter()` raises `feature_disabled`. |
| **Invalid args** | `test_manager_lifecycle.py` | Empty `agent_id` rejected. |
| **Shutdown** | `test_manager_lifecycle.py` | `shutdown()` evicts every active handle. |
| **IPPool — alloc/free/exhaust** | `test_network_and_helpers.py` | Lowest-IP-first allocation; freed IPs reusable; `/30` exhaustion raises `IsolatedNetworkUnavailable`; reserve skips already-used; out-of-range rejected. /24 capacity = 253. |
| **IsolatedNetwork — refusal on macOS** | `test_network_and_helpers.py` | `initialize()` raises `IsolatedNetworkUnavailable` on non-Linux; `install_veth` refuses when not initialized. |
| **veth naming convention** | `test_network_and_helpers.py` | `eos-iws-{handle_short}h` / `n` suffix; matches GC reaper's prefix expectation. |
| **`in_ns_write` end-to-end** | `test_network_and_helpers.py` | Subprocess invocation: pipe base64 in, file written verbatim with `O_CREAT \| O_TRUNC`. |
| **`_setns_libc` clone-flag constants** | `test_network_and_helpers.py` | `CLONE_NEW{USER,PID,NET,NS}` match the kernel-ABI hex values. |
| **Dispatcher op-table tripwire** | `test_routing_invariants.py` (edit) | All 8 new isolated_workspace ops registered, default ops unchanged. |
| **R1 audit-bus integration** | `test_manager_lifecycle.py` | `sandbox_isolated_workspace_enter` is the first emitted event; full event log captured. |

---

## Linux-deferred (code written, exercise needs sweevo Docker image)

These items have stub or partial implementations in `_LinuxRuntime` and must be
completed against a real Linux host before the feature is end-to-end usable.

| Item | Status | Location |
|---|---|---|
| `unshare -Unpm` + `ns_holder` spawn | Skeleton present in `_LinuxRuntime.spawn_ns_holder` and `scripts/ns_holder.py`; live exercise needs the sweevo image. | `service/isolated_workspace.py:_LinuxRuntime.spawn_ns_holder`, `scripts/ns_holder.py` |
| Two-step `ns-up → net-ready → ready` handshake | `ns-up` read is wired; `net-ready` write back from the manager is not — the manager constructs but does not yet flush its setup signal to the holder. | `_LinuxRuntime.spawn_ns_holder` |
| Open ns FDs via `/proc/{pid}/ns/*` | Implemented (real `os.open(O_RDONLY \| O_CLOEXEC)`). | `_LinuxRuntime.open_ns_fds` |
| `mount_overlay` (setns into mntns + fsopen + fsconfig + fsmount) | **`NotImplementedError`** — needs a small helper subprocess that `setns(mntns)` then calls the existing `sandbox.execution.overlay.kernel_mount.mount_overlay`. Cannot import that module directly from `isolated_workspace_ops` (R3 fence). Resolution: a *separate* helper subprocess that the bounded manager spawns. | `_LinuxRuntime.mount_overlay` |
| DNS detection inside new mntns | Returns `False` stub. Real impl: read `/etc/resolv.conf` after setns, parse first nameserver, bind-mount fallback if `127.0.0.0/8`. | `_LinuxRuntime.configure_dns` |
| `cgroup v2 freezer` + `cgroup.events` poll | Creates the cgroup dir but does not write `+freezer` to parent's `cgroup.subtree_control`, does not poll `cgroup.events`. Falls back to writing `cgroup.freeze` blindly. R11 freezer-stall fallback (per-pid `SIGSTOP`) is not yet wired. | `_LinuxRuntime.{create_cgroup,freeze}` |
| `kill_holder` SIGTERM-then-SIGKILL grace | Implemented (real `os.kill`). | `_LinuxRuntime.kill_holder` |
| `setns_exec` real ns-traversal + fork+exec | `scripts/setns_exec.py` is complete (R10-compliant); not yet exercised live. | `scripts/setns_exec.py` |
| Bridge + nft static rules (`eos-shared0`, MASQUERADE, IMDS drop) | `IsolatedNetwork.initialize` shells out to `ip` + `nft`; idempotent (EEXIST ignored). Not yet exercised on real host. | `service/isolated_network.py:_install_static_rules` |
| RFC1918 deny rule (Scenario 5 opt-in) | Wired through `_ManagerConfig.rfc1918_egress`; nft rules installed when set to `deny`. | `service/isolated_network.py:_install_static_rules` |
| Bridge port isolation (`bridge_slave isolated on, mcast_flood off`) | Code present in `install_veth`; not yet exercised. | `service/isolated_network.py:install_veth` |
| `ip -6 route del default` + `accept_ra=0` (IPv6 mitigation) | NOT implemented — needs a netns helper that runs after `install_veth`. Specified in plan §4. | (deferred) |
| Daemon-restart GC reaping veths/cgroups/netns by naming convention | Veth + scratch reap implemented (`_reap_orphans`); cgroup-dir unfreeze + SIGCONT belt-and-suspenders NOT implemented (plan §5 GC step 1+2). | `service/isolated_workspace.py:_reap_orphans` |
| Daytona-launch wiring + `EOS_ISOLATED_WORKSPACE_ENABLED` toggle | Manager bootstraps lazily on first `enter()` call using `args["layer_stack_root"]`. Daemon `__main__.py` is NOT modified (the existing per-call layer_stack pattern made the plan's "construct in __main__" awkward). Documented as a deviation. | `handler/isolated_workspace.py:_ensure_manager` |

---

## Not implemented (deferred to follow-on iterations)

| Item | Plan reference | Reason |
|---|---|---|
| Daemon-host RFC1918 reachability warning at boot (§4 step 7) | Plan §4 / Scenario 5 | Implementation present in `IsolatedNetwork.reachable_rfc1918_subnets`; not yet wired to a daemon-startup log probe (no `__main__.py` integration this iteration). |
| `api.runtime.ready` `capabilities.isolated_workspace` field | Plan §8 | Out of scope for the structural iteration; the existing `health.runtime_ready` is untouched. |
| Egress flow logging (Scenario 4, `EOS_ISOLATED_WORKSPACE_AUDIT_EGRESS=true`) | Plan Scenario 4 | Opt-in only; explicit follow-up. |
| Setup-timeout N1 with per-step booleans | Plan §2 / N1 | Implemented as a simple `try/except` around `_wire_handle`; the partial-rollback `failed_step` granularity (`ns_holder_ready` / `overlay_mount` / `veth_install` / `dns_configure` / `net_ready_handshake`) is collapsed to "any step failed → roll back everything." Documented deviation. |
| Live integration tests under `@pytest.mark.requires_namespaces` (§7 integration tier — 14 tests) | Plan §7 | All tests scaffolded as deferred — live execution needs the sweevo Docker image; mock unit tests cover the cross-platform half. |
| Live E2E tests (§7 e2e tier — 4 tests, `test_pip_install_then_run` etc.) | Plan §7 | Same as above; live tier. |
| MetricsAggregator gauges (`pinned_workspace.active_count` etc.) | Plan §7 observability | Audit events already provide the underlying signal; gauge registration deferred. |
| `bridge_ip_collision` detection at `initialize()` | Plan §4 step 1 | The probe described in R13 step 4 is partial — `_install_static_rules` ignores EEXIST but doesn't compare gateway IP if an unrelated `eos-shared0` already exists. |
| Plan §3 / `LeaseRegistry.iter_leases()` accessor for orphan lease reap | Plan §5 GC step 7 | Not implemented; `LeaseRegistry` has no public iterator. Naming-convention reap of scratch dirs + leases tied to `manager.json` records covers the common case; daemon-crash-while-manager.json-is-stale is the residual gap. |
| Cgroup `cgroup.events: frozen 1` async poll + 2 s timeout + SIGSTOP fallback (R11) | Plan §5 | Stubbed to blind `cgroup.freeze` write. |
| TTL background asyncio sweep task | Plan §5 | `IsolatedWorkspaceManager.ttl_sweep()` is implemented and unit-tested, but no background asyncio task starts it; whoever owns the daemon event loop must schedule it (planned for the same iteration that wires `__main__.py`). |
| Session-resolved `agent_id` (S1) | Plan §2 | Daemon sessions don't carry an `agent_id` today; handlers take it from `args["agent_id"]` as a transitional deviation. |

---

## Files added

```
backend/src/sandbox/daemon/service/isolated_workspace.py     (795 LoC; manager + handle + _LinuxRuntime stubs)
backend/src/sandbox/daemon/service/isolated_network.py       (246 LoC; bridge, veth, IPPool, nft rules)
backend/src/sandbox/daemon/handler/isolated_workspace.py     (123 LoC; enter/exit/status + lazy bootstrap)
backend/src/sandbox/daemon/handler/isolated_workspace_ops.py ( 85 LoC; bounded handlers — R3 fence target)
backend/src/sandbox/daemon/scripts/_setns_libc.py            ( 35 LoC; ctypes setns wrapper)
backend/src/sandbox/daemon/scripts/setns_exec.py             ( 58 LoC; R10-bounded setns→fork→exec helper)
backend/src/sandbox/daemon/scripts/ns_holder.py              ( 46 LoC; pidns PID-1 holder with handshake)
backend/src/sandbox/daemon/scripts/in_ns_write.py            ( 34 LoC; in-ns write helper for write_file op)

backend/tests/unit_test/test_sandbox/test_isolated_workspace/__init__.py
backend/tests/unit_test/test_sandbox/test_isolated_workspace/conftest.py            (233 LoC; FakeRuntime, FakeLayerStack, FakeNetwork, FakeAudit, harness fixture)
backend/tests/unit_test/test_sandbox/test_isolated_workspace/test_manager_lifecycle.py (235 LoC)
backend/tests/unit_test/test_sandbox/test_isolated_workspace/test_handle_shape.py     ( 76 LoC; C1/C2 structural)
backend/tests/unit_test/test_sandbox/test_isolated_workspace/test_import_fence.py     (151 LoC; R3/N2/R10)
backend/tests/unit_test/test_sandbox/test_isolated_workspace/test_no_occ_calls.py     ( 70 LoC; R1 behavioral)
backend/tests/unit_test/test_sandbox/test_isolated_workspace/test_network_and_helpers.py (123 LoC; IPPool, in_ns_write smoke)
```

## Files edited

```
backend/src/sandbox/daemon/rpc/dispatcher.py                   (8 ops added in _load_peer_bootstraps)
backend/src/task_center_runner/audit/events.py                 (5 enum values added)
backend/tests/unit_test/test_sandbox/test_daemon/test_routing_invariants.py  (8 expected ops added)
```

## Files explicitly NOT touched (per Principle 2 / plan §8)

```
backend/src/sandbox/daemon/handler/shell.py
backend/src/sandbox/daemon/handler/read.py
backend/src/sandbox/daemon/handler/write.py
backend/src/sandbox/daemon/handler/edit.py
backend/src/sandbox/daemon/handler/search.py
backend/src/sandbox/daemon/service/sandbox_overlay.py
backend/src/sandbox/occ/**
backend/src/sandbox/layer_stack/**
```

---

## Known limitations + design deviations from the plan

1. **`_LinuxRuntime.mount_overlay` raises `NotImplementedError`.** Without a
   real overlay mount, `enter()` will not currently succeed end-to-end on
   Linux. The lifecycle/state-machine work is complete; this single hook is
   the gating Linux deferment.

2. **R1 behavioral test is currently a redundant smoke test.** Because
   `FakeRuntime` never reaches into `sandbox.occ.*`, the mock call count is
   trivially zero regardless of whether the manager is correct. The R3
   import-graph test is the load-bearing OCC fence today. Once
   `_LinuxRuntime.mount_overlay` is filled in, R1 regains its discriminatory
   power and is kept as the defensive complement to R3.

3. **`__main__.py` is not modified.** The plan calls for constructing the
   manager there. The daemon's existing handlers carry `layer_stack_root` in
   each request envelope rather than capturing it at startup, so an eager
   construction has no obvious source for that field. Instead the handler
   lazy-constructs the manager on the first `enter()` call (using
   `args["layer_stack_root"]`), runs `initialize()` once, then caches the
   singleton via `set_manager()`. The follow-up that wires the background
   TTL sweep task is where the eager construction can land cleanly.

4. **Setup-timeout (N1) granularity is collapsed.** Plan specifies per-step
   booleans (`cgroup_created`, `tmpfs_mounted`, `overlay_mounted`,
   `ip_allocated`, `veth_installed`, `dns_configured`) so the rollback can
   return `failed_step` in the error. Current implementation tracks only
   "all-or-nothing": a single try/except around `_wire_handle` rolls back
   every step. Acceptable for iteration 1; if Linux integration reveals
   diagnostic pain, add the per-step booleans then.

5. **Agent identity is request-payload, not session-state.** Plan §2 (S1)
   calls for `session.agent_id` resolution from daemon TCP session. Daemon
   sessions don't carry an `agent_id` field today, so handlers read it from
   `args["agent_id"]`. Migrating to session-state is a follow-on once that
   infra exists.

6. **Test split.** Plan §7 specifies 8 unit + 14 integration + 4 e2e tests.
   We have 49 unit-level tests (more granular than the plan's table, covering
   IPPool corners, schema mismatch, init-complete gating, idempotent
   initialize, etc.) and zero integration/e2e tests. Live tier deferred per
   user direction ("our task is for docker linux, read tests/mock/sandbox").
   The mock-sandbox harness exists at
   `backend/src/task_center_runner/tests/mock/sandbox/`; adding a
   `test_isolated_workspace_mock.py` there is the natural next step.

7. **No edit to `api.runtime.ready`.** Plan §8 specifies a
   `capabilities.isolated_workspace` field. Out of scope for iteration 1;
   `health.runtime_ready` is unmodified.

---

## Pre-existing test failures (NOT caused by this work)

Confirmed via `git stash + pytest`:
```
backend/tests/unit_test/test_sandbox/test_overlay/test_runtime_invoker_cleanup.py::test_no_occ_orchestrator_removes_intermediate_dirs_but_keeps_outputs
backend/tests/unit_test/test_sandbox/test_provider/test_live_harness_provider_resolution.py::test_env_unset_daytona_falls_back_to_settings
backend/tests/unit_test/test_sandbox/test_provider/test_live_harness_provider_resolution.py::test_env_unset_daytona_empty_settings_skips
```
All fail with `_FakeSandboxSettings.daytona` AttributeError at baseline. None
touch the isolated-workspace module.

---

## Cleanliness audit (vs. CLAUDE.md guidelines)

- **No edits to adjacent code beyond the diff scope.** Dispatcher gained 8
  entries + 2 imports; events.py gained 5 enum values; routing-invariants
  test gained 8 expected entries. No "improvements" to surrounding code.
- **No comments explaining WHAT.** All inline comments explain WHY (R3 fence,
  R10 discipline, plan §5 step 0 rationale, init-complete-gates-enter
  rationale).
- **No speculative abstractions.** `_Runtime` protocol exists because tests
  literally need to swap it; `_LayerStackAdapter` exists because
  `workspace_server` provides functions and the manager wants a port object.
  Both pull their weight.
- **One unused import deleted** (`IPPool` from `isolated_workspace.py`).
- **`ctypes` imported in `setns_exec.py` with explicit `# noqa: F401`** —
  intentional R10-parity comment; the test exists to enforce the discipline.
- **No premature handling** of impossible states. The manager trusts its
  internal invariants and only validates at the public boundary (`enter`,
  `exit`, `run_in_handle`).

---

## Next iteration recommendation

In order of payoff:

1. **Wire `_LinuxRuntime.mount_overlay`.** Single function; enables end-to-end
   `enter()` on Linux. Needs a small helper that `setns(mntns_fd)` then calls
   the existing `kernel_mount.mount_overlay`. Cannot import that module from
   the bounded handler — the helper must be a separate subprocess that the
   service-layer (not the handler-layer) spawns.

2. **Add `tests/mock/sandbox/test_isolated_workspace_mock.py`.** The mock
   harness runs the daemon against a real Docker sandbox. Three tests:
   `enter → shell echo → exit`, `quota=1 second-enter rejected`,
   `daemon-restart GC reaps orphan veth`. Together they exercise the
   structural path end-to-end without inventing new infra.

3. **Wire the TTL sweep + `__main__.py` eager construction together.** The
   bootstrap pattern needs an event-loop owner; doing both at once is
   cleaner than two separate iterations.

4. **R11 cgroup freezer poll + SIGSTOP fallback.** Currently blind-write; the
   2-second timeout + `freezer_degraded` flag adds defensive correctness.

5. **Capability advertisement in `api.runtime.ready`.** Lets the agent SDK
   discover whether `enter_isolated_workspace` is available without a
   speculative call.
