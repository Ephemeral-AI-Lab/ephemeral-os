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

## 4. Deferred items, ordered by which PR unblocks which

### 4.1 Linux-only verification of what's already in tree

| Item | Why deferred | What unblocks it |
|---|---|---|
| Tier 1 (4 happy-path tests) verification | Tests are written but skipped without a configured sweevo Docker host | Set `runner.live_e2e.heavy_enabled = true` + `EOS_SANDBOX_PROVIDER=docker` + database URL, run on a Linux CI runner |
| `_LinuxRuntime.mount_overlay` correctness | Calls `kernel_mount.mount_overlay` through the setns helper. Cannot verify the mount(2) syscall path from macOS | First Tier 1 test run on Linux CI is the verification |
| `_LinuxRuntime.signal_net_ready` handshake | The `net-ready` write + `ready` read between manager and `ns_holder.py`. Was a latent bug before — could not run before the helper write paths landed | Same as above |

**Action:** the FIRST thing the next-Linux-session agent does is run
`pytest .../isolated_workspace/happy_path/ -v` on a Linux CI host. Iterate
on any failures before adding Tier 2+ tests.

### 4.2 PR 0 follow-ups (already in IMPLEMENTATION-REPORT §7)

| § | Item |
|---|---|
| 7.2 | PR 0 acceptance backstop — call `_LinuxRuntime.mount_overlay` directly (not through manager) and assert `/proc/<pid>/mountinfo` reflects the mount. Add as a Tier 1 test once the kernel path is wired. |
| 7.7 | `mount_overlay` + `configure_dns` use synchronous `subprocess.run` from `async def _wire_handle`. This serializes Tier 6 concurrent-enter tests. Switch to `asyncio.create_subprocess_exec` in a follow-up PR; protocol method becomes `async def`. |
| 7.8 | Tier 1 tests do not yet assert audit events. Helpers exist (`_iws_invariants.assert_audit_sequence`, `assert_event_payload`, `assert_handle_ids_unique_per_enter`). Thread the audit log path through Tier 1 test bodies. |

### 4.3 Tier 2–9 implementation (the bulk of remaining work)

Follow PLAN §7 ordering verbatim:

1. **Tier 0** (done) — structural fences
2. **Tier 1** (skeleton landed) — happy path
3. **Tier 2** — isolation (5 tests): peer-publish pinning, upperdir discard, cross-agent unreachable
4. **Tier 7** — GC + persistence (10 tests, possibly +4 from v2 §19.5)
5. **Tier 3** — network (11 tests, +4 inbound-rejection from v2 §19.3)
6. **Tier 4** — failure modes (8 tests)
7. **Tier 5 + 6** — resource controls + concurrency (14 tests, +4 N=5 from v2 §19.4)
8. **Tier 8** — stress (4 tests, +1 disk-at-rest from v2 §19.6)
9. **Tier 9** — performance (7 tests, all capability-gated per §18) + `latency_budget.json` refresh

Each tier's test catalogue is in PLAN §5 (Tiers 1–8) and §19 (v2 additions).

### 4.4 Open infrastructure work

| Item | Where described | Notes |
|---|---|---|
| `EOS_ISOLATED_WORKSPACE_ENABLED` plumbing into sweevo daemon `/etc/environment` | IMPLEMENTATION-REPORT §7.5 | Until this is wired, the daemon comes up with `_ManagerConfig.enabled=False` and every `enter()` returns `feature_disabled` |
| `api.test_only.iws_reset` RPC | IMPLEMENTATION-REPORT §7.6, PLAN §9.1 | Gated by `EOS_ENABLE_TEST_RPCS=true`; adds a forced-reset RPC the conftest can call before each test |
| 4-phase `tool_call` widening | PLAN §15.2, IMPLEMENTATION-REPORT §7.4 | Sunset trigger: `tool_call.exec` P95 > 500 ms on reference CI over a rolling 7-day window |
| `latency_budget.json` (PR 7) | PLAN §15.1, §17 | Lands after PR 6 (Tier 9 fixtures). Reference-CI runs 100 iterations and dumps medians |

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
