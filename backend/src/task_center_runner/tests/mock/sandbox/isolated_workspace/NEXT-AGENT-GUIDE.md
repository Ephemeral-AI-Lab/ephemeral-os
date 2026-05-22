# Next-Agent Guide — isolated_workspace deferred work

**Audience:** the agent (human or LLM) picking up the remaining 66 tests and
the open follow-ups.

**Why this file exists:** in the prior session, I (the previous agent) wrote
a parallel implementation of overlay mount syscalls because I did not first
read what was already in `sandbox/`. That added ~80 LoC of duplicated
`fsopen / fsconfig / fsmount / move_mount` wrappers when
`sandbox.execution.overlay.kernel_mount.mount_overlay` already implemented
exactly what was needed. This guide exists so you do not make the same kind
of mistake.

**Rule of thumb:** before adding any new file under
`sandbox/isolated_workspace/`, grep `sandbox/` for the capability you're
about to write. If something close exists, reuse it (deferred import after
`setns` if the helper is not R10-clean).

---

## 1. Where things live (current layout, 2026-05-23)

```
backend/src/sandbox/isolated_workspace/          ← all iws production code
├── __init__.py            feature overview + cross-package reuse contract
├── manager.py             state machine, _PhaseTimer, _LinuxRuntime, _Runtime Protocol
├── network.py             bridge + nftables + veth + IP pool
├── handlers.py            api.isolated_workspace.{enter, exit, status}
├── ops_handlers.py        api.isolated_workspace.{shell, read_file, write_file, edit_file, search_content}
└── scripts/               single-threaded subprocess helpers (R10)
    ├── _setns_libc.py     libc setns(2) ctypes wrapper
    ├── ns_holder.py       PID 1 of the workspace namespace stack
    ├── setns_exec.py      generic "setns then fork/exec" for ops_handlers
    ├── setns_overlay_mount.py  setns then call kernel_mount.mount_overlay
    ├── configure_dns_in_ns.py  setns then rewrite /etc/resolv.conf
    └── in_ns_write.py     write_file body via stdin (avoids argv E2BIG)

backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/  ← all iws tests
├── PLAN.md                  the 1076-line spec — read §11–§23 for v2 enrichments
├── IMPLEMENTATION-REPORT.md what landed each session
├── NEXT-AGENT-GUIDE.md      this file
├── conftest.py              iws_sandbox, iws_clean_sandbox, iws_audit_tail,
│                            iws_capability_probe, iws_latency_baseline
├── _iws_rpc.py              thin async wrapper around call_daemon_api
├── _iws_invariants.py       audit-event helpers + SUBSET-COVER assertions
├── _iws_fixtures.py         peer-publish, sentinel-layer, capability probes
├── pre_flight/              Tier 0 — structural fences (R3, R10, N2, C1, C2)
└── happy_path/              Tier 1 — golden enter/shell/exit (live, skipped without sweevo)
```

Outside the iws directory, **production code MUST stay where the import
fences say it stays.** No file in `daemon/handler/`, `daemon/service/`, or
`daemon/scripts/` should regrow an iws-specific name.

---

## 2. The reuse map — sandbox/ modules iws already leans on

Before writing new code, check whether one of these already does the job.

| Need | Use | Already used by iws? |
|---|---|---|
| Mount an overlay filesystem | `sandbox.execution.overlay.kernel_mount.mount_overlay` — modern `fsopen/fsconfig/fsmount/move_mount`, FD-pinned paths via `validate_mount_inputs` | yes (`scripts/setns_overlay_mount.py`, deferred import after `setns`) |
| Probe kernel overlay support | `sandbox.execution.overlay.capability.new_mount_api_supported` — picks up `EOS_OVERLAY_FORCE_MATERIALIZE` kill-switch | yes (`_iws_fixtures.can_mount_overlay_natively`) |
| Walk upperdir for change capture | `sandbox.execution.overlay.capture.walk_upperdir` — handles whiteouts, opaque dirs, sparse files | **not yet** — `manager._du_bytes` is a hand-rolled walk. If you need anything beyond byte counting (e.g., for the Tier 7 `test_upperdir_fully_discarded_on_normal_exit`), use `walk_upperdir` instead of reinventing |
| New mount API syscall constants | `sandbox.execution.overlay.new_mount_api` (`SYS_fsopen`, `SYS_fsconfig`, `SYS_fsmount`, `SYS_move_mount`, etc.) | partially — the syscall numbers are inlined in `scripts/setns_overlay_mount.py` *only* because the helper imports `kernel_mount.mount_overlay` after setns. If you ever need the raw syscalls outside a setns helper, import from `new_mount_api`, do not reinline |
| Lease + snapshot lifecycle | `sandbox.daemon.workspace_server.{prepare,release}_workspace_snapshot` | yes (`handlers._LayerStackAdapter`) |
| Scratch root resolution | `sandbox.execution.scratch.command_exec_scratch_root` | yes (`handlers._ensure_manager`) |
| Daemon RPC client | `sandbox.host.daemon_client.call_daemon_api` | yes (`_iws_rpc`) |
| Audit event types | `task_center_runner.audit.events.EventType` — the 5 `SANDBOX_ISOLATED_WORKSPACE_*` enum members are already defined | yes (events emitted via `_emit` in `manager`) |
| Overlay path validation | `sandbox.execution.env_policy.validate_overlay_path_text` + the `MountInputs` returned by `validate_mount_inputs` | **not yet** — `setns_overlay_mount.py` passes raw paths. When Tier 4 fault-injection tests for symlink-escape land, switch to FD-pinned paths via `validate_mount_inputs` |
| Path-policy enforcement | `sandbox.execution.env_policy.DEFAULT_COMMAND_EXEC_POLICY` | **not yet** — Tier 2 chmod-escape test (`test_chmod_uid_in_userns_does_not_escape`) may benefit |

**Anti-pattern:** writing a new helper file under `sandbox/isolated_workspace/`
that duplicates one of the modules above. Always grep before writing.

---

## 3. Constraints — R3, R10, N2, C1, C2

These are pinned by Tier 0 tests. Do NOT relax them without re-reading
PLAN §0 and §5.

### R3 — `ops_handlers.py` transitive imports must not include OCC

The bounded module is `sandbox.isolated_workspace.ops_handlers`. Its
transitive import closure MUST NOT contain `sandbox.occ.*` or
`sandbox.daemon.service.sandbox_overlay`. Pinned by
`pre_flight/test_import_graph_fence.py`.

If you need OCC-shaped behavior (e.g., to commit upperdir changes back to
the layer stack), DO NOT add an import here. The whole point of the
isolated workspace is that its exit path is structurally distinct from OCC
commit. The upperdir is *discarded*, not committed.

### R10 — setns helpers must be single-threaded at setns-call time

`setns(CLONE_NEWUSER)` from libc requires the calling process to have
exactly one thread. `logging`, `asyncio`, `subprocess`, `threading`,
`concurrent.futures`, and `multiprocessing` are forbidden at module-level
in any file under `scripts/`.

**Function-body imports AFTER `setns` are OK.** The R10 fence test
(`pre_flight/test_setns_exec_discipline.py`) only inspects `tree.body`,
not nested imports. This is how `scripts/setns_overlay_mount.py` reuses
`kernel_mount.mount_overlay` (which transitively pulls `subprocess`).

Allowlist for module-level imports under `scripts/`:
```
{__future__, ctypes, json, os, sys,
 sandbox.isolated_workspace.scripts._setns_libc,
 sandbox.isolated_workspace.scripts}
```

### N2 — no dynamic imports in the ops_handlers closure

`importlib.import_module` and `__import__` calls are forbidden inside any
module transitively reachable from `ops_handlers`. Pinned by
`pre_flight/test_import_graph_fence.py::test_isolated_workspace_ops_closure_has_no_dynamic_imports`.

### C1 — handle shape is distinct from OCC

`IsolatedWorkspaceHandle` MUST NOT subclass `OperationOverlayHandle` (or
any `*OverlayHandle`) and MUST NOT have any attribute named `publish*`.
Pinned by `pre_flight/test_handle_shape_no_publish.py`.

### C2 — exit/teardown does not call OCC commit primitives

The strings `apply_changeset`, `commit_prepared`, `commit_transaction`,
`CommitQueue`, `apply_sync` MUST NOT appear in the textual bodies of
`IsolatedWorkspaceManager.exit`, `_teardown`, or `_rollback_partial`.
Pinned by `pre_flight/test_exit_path_no_occ.py`.

This is a textual scan because the bug it prevents is "a shared cleanup
helper that imports under a different name." If you find yourself wanting
to call a common cleanup function across iws and OCC, the answer is
*don't* — write the iws cleanup inline.

---

## 4. Phased plan — implement in this order

Each phase has a single, narrow goal. Do not start phase N+1 until phase N's
done-criteria are met. The dependency between phases is real: skipping ahead
means debugging compound failures (one bug across three layers) instead of
isolated ones.

---

### Phase 0 (done) — Tier 0 + scaffolding + PR 0 + PR 1

What landed in prior sessions. Verifies: structure, audit-payload shape,
manager state machine, `_PhaseTimer` invariants, helper-script R10
discipline. Static-only. **Do not redo.**

---

### Phase 1 — unblock Tier 1 execution (no new tests)

**Goal:** get the existing 4 Tier 1 happy-path tests to *attempt* a real
`enter()` end-to-end. Currently they fail before they start because the
daemon comes up with `_ManagerConfig.enabled = False`.

**Steps:**

1. Plumb `EOS_ISOLATED_WORKSPACE_ENABLED=true` into the sweevo daemon
   startup. Inspect `task_center_runner/environments/sweevo_image/fixtures.py`
   + `benchmarks/sweevo/sandbox.py` to find where the container env is
   set; add the flag there. Probably one of:
   - `/etc/environment` write in the container bootstrap
   - Direct env arg to the daemon launcher (`launch_daemon.sh`)
   - A new bootstrap step in `create_sweevo_test_sandbox`
2. Add the **PR 0 acceptance backstop** (PLAN §16 PR 0 Critic follow-up #6):
   a Tier 1 test that calls `_LinuxRuntime.mount_overlay` directly (NOT
   through the manager), reads `/proc/<helper_pid>/mountinfo`, and asserts
   the overlay line appears. Goes in `happy_path/test_mount_overlay_backstop.py`.
3. Confirm `runner.live_e2e.heavy_enabled = true` and database URL are
   reachable from the dev environment so `database_configured()` returns
   True. Otherwise Tier 1 still skips.

**Done criteria:**
- `pytest .../isolated_workspace/happy_path/ -v` no longer reports SKIP for
  config reasons.
- Tests either pass or report a concrete kernel/wiring error — NOT
  `feature_disabled` or `database not configured`.

**Out of scope for phase 1:** writing new Tier 1 tests. The 4 that exist
are enough surface to find the wiring bugs.

---

### Phase 2 — make Tier 1 green

**Goal:** the 4 existing happy-path tests pass against a real sweevo
container.

**Steps:**

1. Run `pytest .../happy_path/ -v`. Expect failures in this priority order
   (they are listed by likelihood of breakage, derived from
   §5.4 lesson):
   - **The `net-ready` / `ready` pipe handshake.** This codepath never
     executed before — `ns_holder` waiting for `net-ready` and the parent's
     `signal_net_ready` write must agree on pipe lifetime + ordering.
   - **`open_ns_fds.update()` merge.** Confirm `spawn_ns_holder` stashes
     `readiness_fd` + `control_fd` on the handle before `open_ns_fds`
     populates `ns_fds`. Bug masked all session; now reachable.
   - **`mount_overlay` lowerdir-paths-in-mntns visibility.** The unshare's
     mount-copy semantics MUST make the layer-stack lowerdirs visible
     inside the workspace mntns. Verify via host-side
     `nsenter -t <pid> -m ls <lowerdir_path>`.
   - **`configure_dns_in_ns` symlink-following.** Per PLAN §19.3, when
     `/etc/resolv.conf` is a symlink to `/run/systemd/resolve/...`, the
     detection must resolve INSIDE the workspace mntns. Check what the
     sweevo image's `/etc/resolv.conf` actually looks like first.
2. Fix one bug, re-run, repeat. Atomic commits per fix.
3. Add `_iws_invariants.assert_audit_sequence` calls to the 4 tests once
   the basic flow works — currently they only assert RPC responses
   (NEXT-AGENT-GUIDE §4.2/7.8 was the prior placeholder for this).

**Done criteria:**
- 4 Tier 1 tests pass, including the new mount_overlay backstop from
  phase 1.
- Each test asserts both the RPC response AND the expected
  `sandbox_isolated_workspace_{enter,tool_call,exit}` audit sequence.
- `phases_ms` is non-empty in the captured enter event (proves PR 1's
  instrumentation fires end-to-end).

**Out of scope:** Tier 2 tests. Resist the urge — verifying Tier 1 first
isolates the kernel-wiring bugs from the design-of-tests bugs.

---

### Phase 3 — Tier 2 (isolation, 5 tests)

**Goal:** the security argument. Prove iws upperdir is discarded on exit,
peer-publishes don't bleed into open workspaces, and cross-agent
networking is unreachable.

**Why next** (per PLAN §7): "Land before production rollout." These tests
make the case that the structural separation actually holds at runtime.

**Tests** (PLAN §5 catalogue):

| Test | Property |
|---|---|
| `test_full_cycle_never_calls_occ` | OCC commit primitives never reached during full enter→tool_call→exit (R1 behavioral counterpart to the C2 source-scan fence) |
| `test_upperdir_discarded_on_exit` | Re-enter after writing `scratch.txt` returns no-such-file; host-side scratch_root has no leftover upper/ |
| `test_lowerdir_pinned_against_peer_publish` | Agent-A's view sees the snapshot-at-enter content even after agent-B publishes a contradictory layer (A1 design property) |
| `test_default_mode_unaffected_during_pinned` | Same agent's default `api.write_file` still works concurrently with their isolated ws; layerstack tip advances; iws view unchanged |
| `test_cross_agent_unreachable` | ws-A pings/curls ws-B's bridge IP → fail. Mechanism is bridge port-isolation flag, NOT an nft rule (so dropping `bridge-nf-call-iptables` doesn't accidentally open it) |

**Done criteria:** all 5 pass, all 5 assert the expected audit sequence,
`assert_no_event(jsonl, "sandbox_occ_changeset_received")` holds for the
isolated cycle in `test_full_cycle_never_calls_occ`.

**Out of scope:** Tier 3 (network) tests. The isolation argument needs
to be locked down before adding nftables/inbound-rejection surface.

---

### Phase 4 — Tier 7 (GC + persistence, 10 tests)

**Why next over Tier 3** (per PLAN §7): GC is "the largest design surface
that can silently break." Land before a second daemon restart in
production.

**Tests** (PLAN §5 catalogue + v2 §19.5 additions): `manager.json`
roundtrip + schema-mismatch handling; daemon-restart reaps orphan veth /
cgroup / scratch / netns + releases orphan lease + reconciles IP pool;
GC ordering (unfreeze before kill); v1 nft-table migration sweep; new v2
tests for `lowerdir_layer_paths_shared`, `lowerdir_disk_usage_is_o1`,
`upperdir_fully_discarded_on_normal_exit`,
`upperdir_discarded_on_abnormal_exit_daemon_kill`.

**Reuse opportunity** for `upperdir_*` tests: use
`sandbox.execution.overlay.capture.walk_upperdir` instead of
`os.walk` if you need anything beyond byte counting (NEXT-AGENT-GUIDE
§2).

**Done criteria:** 10 base + up to 4 v2 = up to 14 tests pass; daemon
SIGKILL → restart leaves zero `eos-iws-*` veth, zero `eos-iws-*`
cgroups, zero scratch dirs, the IP pool reconciles correctly, the
`gc_orphan` audit event carries `phases_ms.{discover, reap}` per-orphan.

---

### Phase 5 — Tier 3 (network, 11 + 4 = 15 tests)

**Why this position** (per PLAN §7): can land incrementally per nft rule;
needs a stable iws lifecycle (phases 2–4) underneath.

**Tests:**
- MASQUERADE egress + IMDS drop + RFC1918 deny opt-in + IPv6 default-route
  purge (11 tests per PLAN §5)
- Inbound-rejection via `unshare -n` host-netns probe (4 tests per v2 §19.3)

**Helpers to add this phase** (deferred from prior sessions, NEXT-AGENT-GUIDE
§5.3): re-add `unshare_netns_probe`, `tiny_http_server`, `find_free_port`
in `_iws_fixtures.py` ONLY when the tests that need them land — do not
pre-scaffold.

**Done criteria:** all 15 tests pass; idempotent rule reinstall verified
across daemon restart; conntrack RELATED/ESTABLISHED holds for return
traffic on 10 MB downloads.

---

### Phase 6 — Tier 4 (failure modes, 8 tests)

Adversarial / partial-rollback coverage. Setup timeout wedge, ns_holder
crash before ready, overlay mount EBUSY, veth install EEXIST, DNS helper
failure, SIGKILL fallback, freezer stall → SIGSTOP fallback (this is when
`freezer_degraded=True` finally fires — wire it up in `_LinuxRuntime.freeze`),
argv E2BIG via in_ns_write.

**Cross-ref this phase** to project memory entry
`'checked batch apply failed' = argv E2BIG` — same bug class, same fix
(stream payload via stdin).

---

### Phase 7 — Tier 5 + Tier 6 (resource controls + concurrency, 14 + 4 = 18 tests)

**Before starting this phase:** address NEXT-AGENT-GUIDE §4.2/7.7 — the
synchronous `subprocess.run` in `_LinuxRuntime.mount_overlay` and
`configure_dns` will make Tier 6's `test_5_concurrent_isolated_workspaces`
contention-bound flake. Switch those to
`asyncio.create_subprocess_exec`; the `_Runtime` Protocol methods become
`async def`. Update FakeRuntime accordingly.

**Tier 5:** quota-per-agent, total cap=5, host-RAM gate, TTL evict + audit,
TTL doesn't evict active, ENOSPC backpressure, freeze/thaw idempotent.

**Tier 6:** two agents same port, concurrent enter no IP double-alloc,
concurrent default+isolated, handle-lock serializes tool calls,
map-lock serializes enter/exit only, init_complete blocks during startup
GC, re-enter after exit gets fresh handle; plus 4 N=5 noisy-neighbor
tests from v2 §19.4.

---

### Phase 8 — Tier 8 (stress, 4 + 1 = 5 tests, marked slow)

5-concurrent-workspaces, rapid create/destroy cycle, long-running idle
freeze-at-rest, pip-install-then-run e2e, disk-at-rest bounded (v2).

Gate behind `--run-slow` per PLAN §9.5.

---

### Phase 9 — Tier 9 (performance, 7 tests, capability-gated)

**Prerequisites:** every prior phase landed; `latency_budget.json` does
NOT yet exist.

**Steps:**
1. Add the `performance/` directory + 7 tests per PLAN §19.7.
2. Add the `latency_baseline` session fixture (3 warm-up cycles, computes
   median per-phase ms from audit events) + `LatencyBudget` helper class.
3. Make all 7 tests capability-gated via `iws_capability_probe` (already in
   conftest); add the reference-CI fail-loud policy from PLAN §18.
4. After (1)-(3) land and pass, do the **first `latency_budget.json` refresh**
   (PR 7 per PLAN §16): 100-iteration distribution dump on the reference
   CI host into `_data/latency_budget.json`.

**Done criteria for the entire iws feature:** all 9 tiers + v2 additions
green; first `latency_budget.json` committed; PLAN §10 done-definition
satisfied for every tier.

---

### Open infrastructure (touch when relevant phase needs it)

| Item | When to land |
|---|---|
| `EOS_ISOLATED_WORKSPACE_ENABLED` daemon plumbing | Phase 1 — blocks everything else |
| `api.test_only.iws_reset` RPC (PLAN §9.1) | If/when the per-agent exit() loop in `iws_clean_sandbox` becomes inadequate. Could happen at phase 6 (concurrency) if tests start leaking handles to unexpected agent ids. |
| 4-phase `tool_call` widening (PLAN §15.2) | Sunset trigger only: when `tool_call.exec` P95 > 500 ms on reference CI over a rolling 7-day window of budget refreshes. Until then, 3-phase is the v1 contract. |
| Async `subprocess` migration for `_LinuxRuntime` (§4.2/7.7) | Phase 7 prerequisite |

---

## 5. Cautionary tales — concrete mistakes from the prior session

### 5.1 I duplicated `kernel_mount.mount_overlay`

**What I did:** Wrote `scripts/setns_overlay_mount.py` using legacy
`libc.mount(2)` with inline syscall wrappers, ~80 LoC of duplication.

**What I should have done:** Greped `sandbox/` first. Found
`kernel_mount.mount_overlay` (modern `fsopen/fsconfig/fsmount/move_mount`).
Realized the R10 import-discipline blocks a module-level import but a
deferred import inside `main()` (after the `setns` calls) is fine.

**How I fixed it:** Refactored `setns_overlay_mount.py` to defer-import
`kernel_mount.mount_overlay`; updated `pre_flight/test_setns_exec_discipline.py`
to check only `tree.body` so deferred imports stay outside the fence.

**Lesson for you:** before writing any low-level syscall code, search
`sandbox/execution/` for an existing implementation. The codebase has
been around long enough that most kernel-touching primitives already
exist somewhere.

### 5.2 I added `sys.platform != "linux"` branches everywhere

**What I did:** Defensive macOS-degradation branches in `manager.py` and
`network.py` (e.g., `if sys.platform != "linux": return`). Around 8
branches plus a `_require_linux()` helper.

**What I should have done:** The daemon only ever runs in the sweevo
Docker container, which is always Linux. macOS-degradation branches are
dead code at runtime and tested-only theater.

**How I fixed it:** Removed all `sys.platform` branches in production
code, removed `_require_linux()`, dropped the `platform_unsupported`
error kind.

**Lesson for you:** the daemon is Linux-only. If you find yourself
writing `if sys.platform == "linux"`, stop and ask whether the daemon
actually runs anywhere else (it doesn't).

### 5.3 I wrote unused helpers "for future tiers"

**What I did:** `_iws_fixtures.py` initially shipped `tiny_http_server`,
`unshare_netns_probe`, `find_free_port` — none had callers, all were
"scaffolding for Tier 3."

**What I should have done:** Write helpers when the test that needs
them lands. Unused helpers create the illusion of progress and rot
quietly until someone actually tries to use them and finds them
mis-shaped.

**How I fixed it:** Removed all unused helpers. The Tier 3 PR will add
them with the test that actually exercises them.

**Lesson for you:** add a helper IFF a current test calls it. Defer
"useful for the next tier" work until the next tier.

### 5.4 I latently broke `ns_holder.py` and didn't notice

**What I did:** Original `_LinuxRuntime.spawn_ns_holder` closed
`r_parent` (the readiness pipe reader) immediately after seeing `ns-up`.
But `ns_holder.py` writes `ready\n` to the *writer* end of that same
pipe later — when the reader is closed, the write hits EPIPE and
`ns_holder` dies, taking the entire namespace stack with it.

The whole `mount_overlay live wiring deferred` `NotImplementedError`
covered this up because the flow never reached the `net-ready`
handshake.

**How I fixed it:** Added `IsolatedWorkspaceHandle.readiness_fd` and
`control_fd` fields; `spawn_ns_holder` stashes both on the handle
instead of conflating control into `ns_fds["_control"]`; the new
`signal_net_ready` runtime method does the `net-ready` write +
`ready` read after wiring; `_teardown` and `_rollback_partial` close
both FDs.

**Lesson for you:** when a previously-stubbed kernel codepath comes
online, audit the surrounding integration boundary (pipe lifetimes,
FD ownership, handshake protocols) — bugs that were masked by
`NotImplementedError` will surface immediately on Linux.

### 5.5 I forgot to extend the runtime bundle

**What I did:** Moved iws code into `sandbox/isolated_workspace/`. The
daemon dispatcher imports `sandbox.isolated_workspace.handlers` on
startup. The runtime bundle (`sandbox/host/runtime_bundle.py`) had a
hard-coded list of subpackages to include; my new top-level subpackage
wasn't on it. `test_bundle_extracted_daemon_modules_import_clean`
failed with `ModuleNotFoundError`.

**How I fixed it:** Added `iws_dir = sandbox_dir / "isolated_workspace"`
to `_runtime_bundle_bytes()`.

**Lesson for you:** any new top-level subpackage under `sandbox/`
that the daemon imports MUST be added to
`sandbox/host/runtime_bundle.py:_runtime_bundle_bytes()`. The bundle
upload test (`test_bundle_upload.py`) catches this in CI.

---

## 6. Test commands

The iws test surface splits along **static vs live**, not host OS:

- **Static tests** (Tier 0 fences + `_PhaseTimer` unit tests, plus the
  project-wide daemon / audit / import-fence suites) only parse Python
  files or exercise pure-Python state machines. No Docker, no kernel
  calls. They pass anywhere pytest runs.
- **Live tests** (Tier 1+ happy path, isolation, network, etc.) need a
  configured sweevo Docker sandbox up so the in-container daemon can
  receive `api.isolated_workspace.*` RPCs. They `pytest.skip` when
  `database_configured()` or `live_e2e_heavy_enabled()` is False — that
  is the only gate; there is no host-OS gate.

### Static surface (no Docker required)

```bash
.venv/bin/python -m pytest \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/pre_flight/ \
    backend/tests/unit_test/test_sandbox/test_daemon/ \
    backend/tests/unit_test/test_sandbox/test_import_fence.py \
    backend/tests/unit_test/test_audit/ \
    backend/tests/unit_test/test_task_center/test_audit/ \
    -v
```

Expected: ~152 passed, 0 failed. If anything fails, your changes broke
something.

### Live surface (sweevo Docker sandbox must be reachable)

```bash
EOS_SANDBOX_PROVIDER=docker \
EOS_ISOLATED_WORKSPACE_ENABLED=true \
    .venv/bin/python -m pytest \
        backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/ -v
```

Also requires ``runner.live_e2e.heavy_enabled = true`` and a configured
database URL in central config (see ``_live_config.py``). Tier 1 should
run end-to-end once those are set. Tier 2–9 stay skipped until their
tests are added.

### Lint touched files

```bash
.venv/bin/ruff check \
    backend/src/sandbox/isolated_workspace/ \
    backend/src/task_center_runner/audit/events.py \
    backend/src/task_center_runner/tests/mock/sandbox/isolated_workspace/
```

---

## 7. Final checklist before opening a PR for the next tier

- [ ] Greped `sandbox/` for any capability your tier needs before writing new code
- [ ] All new files have a docstring that names the PLAN section and tier
- [ ] Tier 0 fence tests still pass
- [ ] If you added a setns helper: `pre_flight/test_setns_exec_discipline.py` covers it
- [ ] If you touched the audit payload: `audit/events.py` docstring still describes the SUBSET-COVER contract accurately
- [ ] If you added a new sandbox subpackage: it's in `sandbox/host/runtime_bundle.py`
- [ ] `ruff check` is clean
- [ ] Updated `IMPLEMENTATION-REPORT.md` with what landed
- [ ] Updated this file's deferred-items list with anything new you noticed
