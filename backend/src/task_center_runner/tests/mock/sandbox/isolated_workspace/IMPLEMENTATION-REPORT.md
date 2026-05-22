# Implementation Report — isolated_workspace mock-sandbox tier

**Date:** 2026-05-23
**Plan:** `PLAN.md` (v2 — 1076 lines, 72 tests across 10 tiers, 7 PRs)
**Host:** macOS (Docker Desktop 28.3.0 available)

---

## Session 2 — 2026-05-23 (Phase 1 + Phase 2 follow-up)

This section documents the second pass on the iws milestone. It executes
the "Phase 1 — unblock Tier 1 execution" and "Phase 2 — make Tier 1 green"
steps from `NEXT-AGENT-GUIDE.md`.

### What landed

| Slice | File(s) | Status |
|---|---|---|
| Phase 1.1 — `CAP_NET_ADMIN` on Docker run flags | `backend/src/sandbox/provider/docker/client.py` | landed |
| Phase 1.2 — daemon env-flip via fixture | `.../tests/mock/sandbox/isolated_workspace/conftest.py` (`iws_sandbox` now async, writes `/etc/environment` + `pkill -f sandbox.daemon`) | landed |
| Phase 1.3 — preflight script probes `ip link` + `nft` | `backend/scripts/preflight_docker_a2_caps.sh` (2 new probes; `--cap-add=NET_ADMIN`) | landed |
| Phase 1.4 — mount_overlay backstop test | `happy_path/test_mount_overlay_backstop.py` (new; bypasses `enter()`, calls `_LinuxRuntime.mount_overlay` directly, asserts `/proc/<root_pid>/mountinfo`) | landed (code) |
| Phase 2.1 — daemon-side JSONL audit sink + fixture + assertions in 4 happy-path tests | `sandbox/isolated_workspace/handlers.py` (`_JsonlAuditSink` wired into manager) · `conftest.py` (`iws_audit_jsonl` snapshot fixture) · 4 happy_path tests gain `assert_audit_sequence` calls | landed |
| Phase 3 refactor — `iws_sandbox` converted from sync (brittle `asyncio.get_event_loop` try/except) to `async def` | `conftest.py` | landed |

### Why each change

1. **`--cap-add=NET_ADMIN`** — `IsolatedNetwork.initialize()` calls `ip link
   add`, `nft add table`, and rtnetlink operations in the daemon's netns.
   These require `CAP_NET_ADMIN`; `CAP_SYS_ADMIN` (already present for
   overlay + setns) is NOT a superset. Without this flag, every Tier 1
   `enter()` would EPERM at the bridge-install step.

2. **Env-flip + daemon respawn** — the daemon reads
   `EOS_ISOLATED_WORKSPACE_ENABLED` once at startup via
   `_ManagerConfig.from_env()`. The sweevo sandbox is created by an
   unrelated test fixture, so we cannot pass `env_vars=` at create-time.
   Instead, the iws-scoped wrapper writes `/etc/environment` (sourced by
   `bash -lc` in `launch_daemon.sh` via PAM) and SIGTERMs the daemon. The
   next host RPC respawns it with the new env. Idempotent: a grep-guard
   prevents double-appends.

3. **Preflight probes** — extended `preflight_docker_a2_caps.sh` to verify
   the cap actually grants what iws needs: a bridge add+del cycle and a
   `nft` table add+del cycle. Bails out cleanly if `nft` is not installed in
   the runtime image (the iws path requires it; this surfaces that gap).

4. **`mount_overlay` backstop** — a Tier 1 test that bypasses the manager's
   `enter()` and exercises `_LinuxRuntime.mount_overlay` directly through a
   `raw_exec` Python script. Asserts the overlay line appears in
   `/proc/<root_pid>/mountinfo` inside the workspace mntns. This
   structurally separates "mount itself is broken" from "something around
   the mount is broken" (veth, cgroup, dns, handshake) for fast triage on
   Linux CI.

5. **Audit-sink wiring** — the manager already accepted an `AuditSink` port
   but `handlers.py` passed `None`, so the 5 lifecycle events fell on the
   floor. Wired a `_JsonlAuditSink` that appends to
   `/tmp/sandbox_isolated_workspace_events.jsonl` (env-overrideable via
   `EOS_ISOLATED_WORKSPACE_AUDIT_PATH`). Live tests pull the file with
   `raw_exec(cat …)` into a host `tmp_path` and feed it to the existing
   `_iws_invariants.assert_audit_sequence` helper.

### Verification (static surface, runnable on macOS)

```text
$ .venv/bin/python -m pytest \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/pre_flight/ \
    backend/tests/unit_test/test_sandbox/test_daemon/ \
    backend/tests/unit_test/test_sandbox/test_import_fence.py \
    backend/tests/unit_test/test_audit/ \
    backend/tests/unit_test/test_task_center/test_audit/
152 passed in 1.29s

$ .venv/bin/ruff check \
    backend/src/sandbox/isolated_workspace/ \
    backend/src/sandbox/provider/docker/client.py \
    backend/src/task_center_runner/audit/events.py \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/
All checks passed!
```

### Deferred — what this session could NOT do

| Item | Why | Owner / next trigger |
|---|---|---|
| **Live Tier 1 execution** (4 happy-path + 1 backstop) | Requires Linux host + sweevo Docker image + `runner.live_e2e.heavy_enabled = true` + database URL — none reachable from a macOS dev host. Code is in place; bug-fix loop (PLAN §4 phase 2 step 2) is deferred-pending-live-execution. | Linux CI runner |
| **`runner.live_e2e.heavy_enabled = true` config** | Deployment precondition, not a code change. The skipif decorators already gate cleanly. | central config rollout |
| **Tiers 2–9 (66 tests)** | Sequenced after Tier 1 is green per PLAN §7. | next session |
| **Async-blocking subprocess refactor** (`_LinuxRuntime.{mount_overlay, configure_dns, spawn_ns_holder}`) | Becomes a flake source under Tier 6 concurrent N=5 enters. Not on the critical path for Tier 1/2 green. | Phase 7 prerequisite (see NEXT-AGENT-GUIDE §4.2/7.7) |
| **`api.test_only.iws_reset` RPC** | Per-agent `exit()` loop in `iws_clean_sandbox` is adequate while only 5 known agent ids exist. | When concurrency tests (phase 7) reveal handle leaks |
| **Backstop test `PYTHONPATH` risk** | `test_mount_overlay_backstop.py` runs `python3 - <<PY` via `raw_exec`. That requires `sandbox.isolated_workspace.manager` to be importable from a bare `python3` invocation — i.e. the daemon's runtime bundle path must be on `sys.path` for that shell's environment. If live CI surfaces `ModuleNotFoundError: sandbox.isolated_workspace`, the fix is either `PYTHONPATH=<bundle_dir> python3 -` or wrapping via the existing thin-client mechanism. | First Linux-CI run of the backstop |

### How to verify on Linux CI

```bash
# 1. Daemon-cap preflight (verifies CAP_NET_ADMIN actually grants what we need)
bash backend/scripts/preflight_docker_a2_caps.sh

# 2. Tier 1 happy-path against a live sweevo sandbox
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
    .venv/bin/python -m pytest \
        backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/happy_path/ -v
```

Tier 1 expectations: 5 tests pass (4 lifecycle + 1 mount_overlay backstop).
Each lifecycle test asserts both the RPC response AND the expected
`sandbox_isolated_workspace_{enter,tool_call,exit}` audit sequence.

---

## 1. Scope landed this session

| Slice | Status | Verified on macOS |
|---|---|---|
| Tier 0 — pre-flight structural fences (4 tests + PhaseTimer unit test) | **landed** | yes, 14 tests passing in <0.2 s |
| Scaffolding — conftest, `_iws_rpc`, `_iws_invariants`, `_iws_fixtures` | **landed** | yes, imports clean |
| PR 1 — audit-event enrichment (`_PhaseTimer`, additive `phases_ms` + `total_ms` on 5 events) | **landed** | yes, structural + unit checks pass |
| PR 0 — `_LinuxRuntime.mount_overlay` + `configure_dns` live wiring | **landed (code)** | no — requires Linux kernel + sweevo image |
| Tier 1 — happy-path tests (4 tests, skip-gated on Linux + live e2e) | **landed (skeleton)** | yes, 4 tests skip cleanly on macOS |
| Bug-fix bundle (pre-existing `ns_fds["_control"]` overwrite, premature `r_parent` close, missing `net-ready` handshake) | **landed** | yes |

**Verification gate (macOS, single session):**
- `pytest .../isolated_workspace/`: **17 passed, 4 skipped, 0 failed** (0.16 s)
- `pytest .../test_sandbox/test_daemon/` (broader daemon suite): **106 passed**
- `pytest .../test_audit/`, `.../test_task_center_runner/test_audit_recorder_*`, `.../test_benchmarks/test_sweevo_audit_recorder`: **29 passed**
- `ruff check` on touched files: **All checks passed**

The four Tier 1 tests are written to run end-to-end against the sweevo Docker
sandbox on Linux CI; they `skipif sys.platform != "linux" or not
live_e2e_heavy_enabled()` cleanly on this host.

---

## 2. Sandbox-module restructure

Per a follow-up scoping request, all isolated_workspace production code was
consolidated into a single top-level subpackage so the feature reads as a
unit:

```
backend/src/sandbox/isolated_workspace/
├── __init__.py
├── manager.py          (was daemon/service/isolated_workspace.py)
├── network.py          (was daemon/service/isolated_network.py)
├── handlers.py         (was daemon/handler/isolated_workspace.py)
├── ops_handlers.py     (was daemon/handler/isolated_workspace_ops.py)
└── scripts/
    ├── __init__.py
    ├── _setns_libc.py
    ├── ns_holder.py
    ├── setns_exec.py
    ├── setns_overlay_mount.py
    ├── configure_dns_in_ns.py
    └── in_ns_write.py
```

The previous scattered locations (``daemon/service/``, ``daemon/handler/``,
``daemon/scripts/``) no longer contain any iws-specific files.

### Cross-package reuse (the minimalist-change goal)

| Reused module | Where iws calls it | Saves |
|---|---|---|
| ``sandbox.execution.overlay.kernel_mount.mount_overlay`` | ``scripts/setns_overlay_mount.py`` — deferred-import *after* setns so R10 single-thread discipline is preserved at module-load time | ~80 LoC of duplicated ``fsopen / fsconfig / fsmount / move_mount`` syscall wrappers. One source of truth for overlay mount mechanics across the daemon. |
| ``sandbox.execution.overlay.capability.new_mount_api_supported`` | ``_iws_fixtures.can_mount_overlay_natively`` | A bespoke ``/proc/filesystems`` scan. Picks up the existing ``EOS_OVERLAY_FORCE_MATERIALIZE`` kill-switch for free. |
| ``sandbox.daemon.workspace_server.{prepare,release}_workspace_snapshot`` | ``handlers._LayerStackAdapter`` | Existing lease/snapshot lifecycle — no parallel implementation. |
| ``sandbox.host.daemon_client.call_daemon_api`` | ``_iws_rpc`` | Existing daemon RPC client. |
| ``sandbox.execution.scratch.command_exec_scratch_root`` | ``handlers._ensure_manager`` | Existing scratch-root resolution. |

## 3. Files added (new this session)

```
backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/
├── IMPLEMENTATION-REPORT.md            (this file)
├── __init__.py
├── conftest.py
├── _iws_rpc.py
├── _iws_invariants.py
├── _iws_fixtures.py
├── pre_flight/
│   ├── __init__.py
│   ├── test_import_graph_fence.py        (R3 + N2)
│   ├── test_setns_exec_discipline.py     (R10, covers 4 helper scripts)
│   ├── test_handle_shape_no_publish.py   (C1)
│   ├── test_exit_path_no_occ.py          (C2 source-scan)
│   └── test_phase_timer_invariants.py    (PR 1 unit-level guard)
└── happy_path/
    ├── __init__.py
    ├── test_enter_then_shell_then_exit.py
    ├── test_server_survives_tool_call_boundary.py
    ├── test_status_reports_open_handle.py
    └── test_lowerdir_visible_inside_mntns.py

backend/src/sandbox/isolated_workspace/scripts/
├── setns_overlay_mount.py     (PR 0 helper — delegates to kernel_mount)
└── configure_dns_in_ns.py     (PR 0 helper)
```

## 4. Files modified

| File | Change |
|---|---|
| ``backend/src/sandbox/isolated_workspace/manager.py`` | PR 1: ``_PhaseTimer`` class + ``_PHASE_TIMER_OVERHEAD_BUDGET_MS``. Instrumented ``enter``, ``_wire_handle``, ``exit``, ``_teardown``, ``run_in_handle``, ``_reap_orphans``, ``ttl_sweep``. Enriched 5 emit sites with ``total_ms`` + ``phases_ms`` (conditional-key per P5) + ``lowerdir_layer_count`` + ``materialize=False`` on enter. PR 0: live ``mount_overlay`` + ``configure_dns`` + new ``signal_net_ready`` Protocol method. Bug fixes: ``IsolatedWorkspaceHandle.readiness_fd`` + ``control_fd`` fields; ``open_ns_fds`` now merges via ``update`` instead of replacing; ``r_parent`` no longer closed eagerly. |
| ``backend/src/task_center_runner/audit/events.py`` | Module docstring documenting the SUBSET-COVER invariant + conditional-key emission rule (PLAN §21 Follow-up #6). No ``EventType`` enum changes. |
| ``backend/src/sandbox/daemon/rpc/dispatcher.py`` | OP_TABLE registration switched from ``sandbox.daemon.handler.isolated_workspace{,_ops}`` to ``sandbox.isolated_workspace.{handlers,ops_handlers}``. |
| ``backend/src/sandbox/host/runtime_bundle.py`` | Added ``sandbox/isolated_workspace/`` to the daemon runtime bundle so the in-sandbox daemon can import the package on startup. |
| ``backend/tests/unit_test/test_sandbox/test_daemon/test_routing_invariants.py`` | Updated imports + OP_TABLE references to the new module paths. |

---

## 4. Production-code contract (PR 1)

Every workspace-lifecycle audit event now carries `total_ms` (float ms) and a
`phases_ms` dict whose keys are emitted **conditionally** — phases that did
not complete successfully stay ABSENT (P5: absence != zero).

The SUBSET-COVER invariant (per PLAN §14):

```
sum(phases_ms.values()) <= total_ms + max(2.0, 0.05 * total_ms)
```

Phase key sets per event:

| Event | Possible phase keys |
|---|---|
| `sandbox_isolated_workspace_enter` | `prepare_snapshot`, `spawn_ns_holder`, `open_ns_fds`, `install_veth`, `mount_overlay`, `configure_dns`, `create_cgroup` |
| `sandbox_isolated_workspace_exit` | `kill_holder`, `teardown_veth`, `release_snapshot`, `cgroup_rmdir`, `rmtree_scratch` |
| `sandbox_isolated_workspace_evicted` | same as `exit` (inherited via `ttl_sweep`) |
| `sandbox_isolated_workspace_tool_call` | `unfreeze`, `exec`, `freeze` (3-phase v1 per PLAN §15.2 — `tool_call.exec` is coarse) |
| `sandbox_isolated_workspace_gc_orphan` | `discover`, `reap` (per-orphan; discover cost is amortized across the pass) |

`enter` additionally carries top-level `lowerdir_layer_count` (int) and
`materialize` (always `false` — tripwire if anyone flips
`prepare_workspace_snapshot(materialize=True)` for the isolated path).

---

## 5. PR 0 wiring details

`_LinuxRuntime.mount_overlay` now invokes
`sandbox.daemon.scripts.setns_overlay_mount` as a single-threaded helper
subprocess. The helper:

1. Reads a JSON payload over stdin: `{ns_fds: {user, mnt}, target,
   lowerdirs, upperdir, workdir}`.
2. Calls `setns(user, CLONE_NEWUSER)` then `setns(mnt, CLONE_NEWNS)` via the
   shared `_setns_libc` wrapper.
3. Calls libc `mount("overlay", target, "overlay",
   MS_NOSUID|MS_NODEV, "lowerdir=...,upperdir=...,workdir=...")`.

`_LinuxRuntime.configure_dns` invokes `configure_dns_in_ns`, which detects a
127.0.0.0/8 nameserver INSIDE the workspace mntns (following the symlink
chain after `setns`) and overwrites `/etc/resolv.conf` with the configured
fallback. The host's resolv.conf is untouched (private propagation).

Both helpers obey the same R10 import-discipline allowlist as the existing
`setns_exec.py`. The extended fence test
`test_setns_overlay_mount_helper_imports_are_minimal` /
`test_configure_dns_in_ns_helper_imports_are_minimal` pins this.

A new Protocol method `_Runtime.signal_net_ready(handle, *, setup_timeout_s)`
completes the `ns_holder` handshake: parent writes `net-ready\n` to the
control pipe after wiring; `ns_holder` brings `lo` up and acks via
`ready\n`. This fixes the latent hang in `ns_holder.py` that was
inevitable before — `ns_holder` was blocked in `os.read(control_fd, ...)`
forever.

Bug-fix delta on `IsolatedWorkspaceHandle`:

- Added `readiness_fd: int = -1` and `control_fd: int = -1` as transient
  fields (NOT persisted via `to_persisted()` — they are FDs).
- `spawn_ns_holder` stashes both FDs on the handle instead of conflating
  the control pipe into `ns_fds["_control"]`. This prevents
  `open_ns_fds` from accidentally evicting it.
- `_teardown` and `_rollback_partial` now close both FDs on exit.

---

## 6. Test inventory (this session)

```
$ pytest backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/ -v
collected 21 items

happy_path/test_enter_then_shell_then_exit.py     SKIPPED
happy_path/test_lowerdir_visible_inside_mntns.py  SKIPPED
happy_path/test_server_survives_tool_call_boundary.py  SKIPPED
happy_path/test_status_reports_open_handle.py     SKIPPED
pre_flight/test_exit_path_no_occ.py               2 PASSED
pre_flight/test_handle_shape_no_publish.py        3 PASSED
pre_flight/test_import_graph_fence.py             2 PASSED
pre_flight/test_phase_timer_invariants.py         6 PASSED
pre_flight/test_setns_exec_discipline.py          4 PASSED

17 passed, 4 skipped in 0.16s
```

Plus 106 pre-existing daemon tests + 29 pre-existing audit tests + the
project-wide routing/import-fence suite: **no regressions introduced**.

(7 pre-existing failures in `test_sandbox/{test_api/test_shell_atomic_by_path_count,
test_provider/test_live_harness_provider_resolution, test_overlay/test_*}`
were confirmed via `git stash` to be unrelated to this work — env pollution
and Daytona-specific cases that fail on `main`.)

---

## 7. Deferred items (next sessions)

### 7.1 Tier 1–9 verification on Linux

The four Tier 1 happy-path tests are written and skip-gated correctly. They
need to run on a Linux CI host with:

- `EOS_SANDBOX_PROVIDER=docker` (default per project memory).
- `runner.live_e2e.heavy_enabled = true` in central config.
- Database URL configured.
- `EOS_ISOLATED_WORKSPACE_ENABLED=true` plumbed into the daemon's
  `/etc/environment` (sweevo bootstrap concern).

Once those pass, the rest of the tier-by-tier expansion follows
PLAN §7 ordering: Tier 2 (isolation) → Tier 7 (GC) → Tier 3 (network) →
Tier 4 (failure modes) → Tier 5–6 (resource controls + concurrency) →
Tier 8 (stress) → Tier 9 (performance).

**Owner:** workspace-platform on-call.
**Trigger:** Linux CI runner allocation per PLAN §22 capability gating.

### 7.2 PR 0 acceptance backstop (Critic follow-up #6)

PLAN PR 0 calls for a backstop test that invokes
`_LinuxRuntime.mount_overlay` directly (not through the manager) and asserts
`/proc/<pid>/mountinfo` reflects the mount. Cannot land here — needs Linux
kernel. **Add to Tier 1 next session.**

### 7.3 Tier 9 fixtures + `latency_budget.json` (PR 6 + PR 7)

`iws_capability_probe` and `iws_latency_baseline` fixtures are stub-wired in
`conftest.py`. The full implementation requires:

- 3 warm-up enter/exit cycles against a live sandbox to populate
  `latency_baseline` medians (PR 6).
- Reference-CI dump of 100-iteration distribution into
  `_data/latency_budget.json` (PR 7).

Both require the Tier 1 path to work first.

### 7.4 4-phase `tool_call` widening (PLAN §15.2)

Deferral ticket placeholder: widen `_Runtime.run_in_handle` protocol to
return per-sub-phase timing. **Sunset trigger:** `tool_call.exec` P95 >
500 ms on reference CI over a rolling 7-day window of
`latency_budget.json` refresh data.

### 7.5 `EOS_ISOLATED_WORKSPACE_ENABLED` plumbing

The manager defaults to `enabled=False` and the bootstrap path in
`handler/isolated_workspace.py` raises `feature_disabled` until the env var
is set. For Linux CI, this needs to be plumbed through the sweevo
bootstrap (probably via `/etc/environment` write + daemon restart). **Add
to Tier 1 conftest enrichment next session.**

### 7.6 `api.test_only.iws_reset` RPC (open question §9.1)

PLAN §9.1 recommends adding a test-only forced-reset RPC gated by
`EOS_ENABLE_TEST_RPCS=true`. The current `iws_clean_sandbox` fixture uses
the per-agent `exit()` loop, which is adequate for the 5 known test agent
ids but does not catch leaked handles from unexpected agent ids. **Add when
needed.**

### 7.7 PR 0 helpers block the asyncio event loop

`_LinuxRuntime.mount_overlay` and `configure_dns` both invoke
`subprocess.run` (synchronous, with 30 s / 10 s timeouts respectively) from
inside the async `_wire_handle`. Same for the pre-existing
`spawn_ns_holder`. Under Tier 6 (concurrent enters at N=5), this will
serialize the per-handle install_veth / mount_overlay / configure_dns
phases — measured `phases_ms.install_veth` for N=5 will look ~5× the
single-handle baseline, and Tier 6 contention-bound asserts may flake.

**Fix in a follow-up PR:** switch the three helpers to
`asyncio.create_subprocess_exec` + `await proc.communicate()`. The
Protocol method becomes `async def mount_overlay(...)`, propagating up
through `_wire_handle`. Tests that mock `_Runtime` need to be updated to
return coroutines.

**Detection:** when Tier 6 lands, the `test_5_concurrent_isolated_workspaces`
contention bound (`max ≤ 5 × median`) will flag this.

### 7.8 Tier 1 audit-event assertions (refinement, not a blocker)

The four Tier 1 happy-path tests I wrote assert RPC responses but do not
yet read the `sandbox_events.jsonl` audit log to verify the enter →
tool_call → exit sequence. PLAN §5 table for `test_enter_then_shell_then_exit`
specifies *"Audit: enter, tool_call, exit"* — and PR 1's additive
`total_ms` / `phases_ms` payload is observable here.

The helpers exist (`_iws_invariants.assert_audit_sequence`,
`assert_event_payload`, `assert_handle_ids_unique_per_enter`) and are
wired up via the `iws_audit_tail` fixture. They need an `audit_dir` /
`sandbox_events.jsonl` path source — the existing
`background_shell_golden` test discovers this via the standard
sweevo fixtures.

**Refinement next session:** thread the audit-log path through Tier 1
test bodies; assert ordered audit sequence + `phases_ms` non-empty.

---

## 8. How to verify this session's work locally

```bash
cd /Users/yifanxu/machine_learning/LoVC/EphemeralOS

# Tier 0 + scaffolding (runs in <1 s on macOS)
.venv/bin/python -m pytest \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/ -v

# Broader sandbox unit tests (filters pre-existing failures unrelated to this PR)
.venv/bin/python -m pytest \
    backend/tests/unit_test/test_sandbox/test_daemon/ -q
.venv/bin/python -m pytest \
    backend/tests/unit_test/test_sandbox/test_import_fence.py -q

# Lint
.venv/bin/ruff check \
    backend/src/sandbox/daemon/service/isolated_workspace.py \
    backend/src/sandbox/daemon/scripts/setns_overlay_mount.py \
    backend/src/sandbox/daemon/scripts/configure_dns_in_ns.py \
    backend/src/task_center_runner/audit/events.py \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/
```

On Linux CI with the sweevo Docker image up:

```bash
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
    .venv/bin/python -m pytest \
        backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/happy_path/ -v
```

---

## 9. Back-compat verification (PR 1 acceptance)

Per PLAN §16 PR 1 row, the audit-event enrichment must not break the
existing `performance_report.py` consumer.

Verified at `performance_report.py:284-301` and `:386-417`:

- `_build_totals` reads `total_ms` via `_as_mapping(item).get("total_ms")
  or 0.0` — defensive against absent keys; the additive `total_ms` from my
  enriched sandbox-event payloads is correctly ignored at the totals
  level (it's a per-tool aggregate that already existed).
- `_normalize_sandbox_event` (line 386) reads only known payload keys
  (`tool_name`, `tool_id`, `status`, `conflict_reason`, `changed_paths`)
  plus `timings`. My new keys (`total_ms`, `phases_ms`,
  `lowerdir_layer_count`, `materialize`) are not touched.
- `_build_sandbox_report` (line 304) iterates `event.get("timings")` —
  separate field from payload. The enriched payload is not inspected.

**Conclusion: PR 1 is fully additive; no existing consumer reads the new
keys, so no migration required.**

The 29 audit-related unit tests pass without modification:

```bash
$ pytest backend/tests/unit_test/test_audit/ \
         backend/tests/unit_test/test_task_center/test_audit/ \
         backend/tests/unit_test/test_task_center_runner/test_audit_recorder_*.py \
         backend/tests/unit_test/test_benchmarks/test_sweevo_audit_recorder.py
29 passed in 0.28s
```

---

## 10. Summary

- Tier 0 fences + scaffolding shipped and verified on macOS.
- PR 1 audit-event enrichment shipped; `_PhaseTimer` invariants pinned by
  6 unit tests; back-compat to `performance_report.py` verified.
- PR 0 live `mount_overlay` + `configure_dns` wired (with R10 single-thread
  helper-subproc discipline); verifiable only on Linux (next session).
- Tier 1 happy-path skeleton (4 tests) ready to run when Linux CI is up;
  audit-event assertions are the planned refinement next session.
- Three latent bugs in the existing iws lifecycle code (fd overwrite,
  premature pipe close, missing handshake) fixed alongside.
- 17 new tests passing, 4 cleanly skipped on macOS, **zero regressions**
  against pre-existing test suites (29 audit + 106 daemon + project
  routing/import fence).
- Tiers 2–9 (66 additional tests) remain as scoped work for subsequent
  sessions; the directory structure, helpers, and fixture-factories they
  depend on are in place.
- Two known follow-ups for future PRs are explicitly documented:
  async-blocking subprocess calls in PR 0 (§7.7) and Tier 1 audit-event
  assertions (§7.8).
