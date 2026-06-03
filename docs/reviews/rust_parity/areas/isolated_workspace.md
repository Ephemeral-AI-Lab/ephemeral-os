# Rust parity audit — Isolated workspace (isolated net, persistent upperdir, never OCC-merged, teardown on exit)

Domain: sandbox. Owner invariants: isolated-never-OCC, enter/exit background gating, plugin/LSP blocked.

Source precedence: Python (`/tmp/oldpy/backend/src/sandbox/isolated_workspace/...` + in-tree
`backend/src/sandbox/host/isolated_workspace_lifecycle.py`) = GROUND TRUTH; `docs/architecture/*.html` =
corroboration; checklist = what to confirm.

## Ground truth

- Host enter/exit (background gating, daemon RPC, typed result):
  `backend/src/sandbox/host/isolated_workspace_lifecycle.py:23-89` (enter/exit), `:240-248`
  (`_daemon_command_session_count`).
- Daemon pipeline (handles, TTL, sampler, audit-only changed_paths, run-in-existing-namespace):
  `/tmp/oldpy/.../isolated_workspace/pipeline.py:51-491`.
- Enter/exit/teardown phases + rollback + drain:
  `/tmp/oldpy/.../_control_plane/workspace_handle_lifecycle.py:39-402`.
- Kernel runtime (unshare flags, ns FDs, overlay mount, DNS, net-ready, cgroup, kill_holder, run_in_handle):
  `/tmp/oldpy/.../_control_plane/namespace_runtime.py:65-301`.
- Types/config (`_PipelineConfig.from_env`, `IsolatedWorkspaceHandle`, constants):
  `/tmp/oldpy/.../_control_plane/types.py:18-257`.
- Network (bridge/veth/nft/IP pool, RFC1918, IMDS):
  `/tmp/oldpy/.../isolated_workspace/network.py:27-311`.
- ns_holder handshake + IPv6 purge + /proc rbind:
  `/tmp/oldpy/.../isolated_workspace/scripts/ns_holder.py:29-119`.
- setns exec / overlay mount helpers:
  `/tmp/oldpy/.../scripts/setns_exec.py`, `setns_overlay_mount.py`.
- Orphan reaper: `/tmp/oldpy/.../_control_plane/orphan_reaper.py:53-399`.
- Plugin/LSP block + drain/quiesce: `/tmp/oldpy/.../daemon/rpc/dispatcher.py:90-114,251-275`; arch
  `docs/architecture/tools/isolated-workspace.html:149-169`, `docs/architecture/sandbox/workspaces.html:209-294`.

## Rust mapping

- `sandbox/crates/eos-isolated/src/session.rs` — `IsolatedSession` enter/exit/ttl_sweep/teardown/reap,
  host-capacity, persist, audit emit. Ports `LayerStackSnapshotPort` + `NamespaceRuntimePort` injected by daemon.
- `sandbox/crates/eos-isolated/src/network.rs` — bridge/veth/nft via rtnetlink + NETLINK_NETFILTER.
- `sandbox/crates/eos-isolated/src/caps.rs` — `ResourceCaps::from_env` (env keys/defaults).
- `sandbox/crates/eos-isolated/src/audit.rs` — JSONL sink.
- `sandbox/crates/eos-ns-holder/src/lib.rs` — holder: in-process `unshare`, handshake, IPv6 hardening, ns-veth.
- `sandbox/crates/eos-daemon/src/isolated.rs` — daemon-local session singleton, op_enter/exit/status/list,
  `DaemonNamespaceRuntime` (spawns `eosd ns-holder`/`ns-runner`), command-session gate, TTL hook.
- `sandbox/crates/eos-daemon/src/command.rs` + `dispatcher.rs` — isolated routing for exec/file verbs (no OCC),
  `record_tool_call`.
- `sandbox/crates/eos-daemon/src/plugin/mod.rs:325-337` — plugin-family block (`ForbiddenInIsolatedWorkspace`).
- `agent-core/crates/eos-sandbox-host/src/isolated_workspace.rs` — host enter/exit + background gate.
- `agent-core/crates/eos-tools/src/model_tools/isolated.rs` — `enter/exit_isolated_workspace` tools keyed on `agent_id`.
- `agent-core/crates/eos-tools/src/{execution.rs,hooks.rs}` + `eos-sandbox-api/.../control.rs:88` —
  `BlockInIsolatedMode` prehook + `isolated_active` wrapper.

## Invariant table

| # | Invariant | Status | Sev | Python file:line | Rust file:line | Note |
|---|-----------|--------|-----|------------------|----------------|------|
| 1 | enter/exit keyed on agent_id handle (no public isolated_workspace_id param) | match | none | host:23-89; pipeline.py:156-158 (`get_handle(agent_id)`) | model_tools/isolated.rs:55-83 (`agent_id`, no id param); session.rs:320,435 (`enter/exit(&AgentId)`); isolated.rs:547 (`agent_has_active_handle`) | Faithful. |
| 2 | Isolated session gets its OWN network namespace | match | none | namespace_runtime.py:84-96 (`unshare --net ...`) | ns-holder lib.rs:840-841 (`unshare(NEWUSER\|NEWNS\|NEWPID\|NEWNET)`); isolated.rs:207 (`/proc/<pid>/ns/net`) | Consolidated `unshare(1)`→in-process; intentional. |
| 3 | upperdir PERSISTENT across the isolated session | match | none | pipeline.py:169-214 (handle persistent, no per-call mount); workspace_handle_lifecycle.py:71-90 | session.rs:354-362 (scratch/upper/work once on enter); command.rs:70-72 routes to persistent handle; record_tool_call only updates last_activity | Faithful. |
| 4 | Writes captured + audited but NEVER OCC-published | match | none | pipeline.py:169-201 (`changed_paths` audit-only, no OCC); workspaces.html:243 | eos-isolated/Cargo.toml:11 (no eos-occ — build-time guard); command.rs:1259,1283 (`"published": false`); dispatcher.rs:763 (`workspace: isolated`, no OCC) | Build-time no-publish guard is stronger than Python. |
| 5 | Exit tears down ns + releases lease + removes scratch; changes discarded | match | none | workspace_handle_lifecycle.py:207-355 (kill_holder/teardown_veth/release_lease/rmtree) | session.rs:435-480 + teardown_handle:928-1009 (kill_holder, teardown_veth, release_lease, cgroup rmdir, remove scratch) | Faithful incl. ordering (handle removed from maps first). |
| 6 | Enter REJECTS active sandbox bg work; exit CANCELS/drains it | partial | medium | host:30-42 (`max(local, daemon)>0` → `ephemeral_jobs_in_flight`); exit:74-89 + daemon `begin_exit_drain` | host isolated_workspace.rs:50-88 (same MAX gate + cancel_by_agent); isolated.rs:407-434 (active_command_sessions gate) | Enter gate + exit cancel present. BUT daemon-side dispatch quiesce/exit-drain (`exit_pending`/`acquire_dispatch_slot`/`begin_exit_drain`) is ABSENT — see D1. |
| 7 | plugin/LSP BLOCKED while isolated active for that agent | match | none | dispatcher.py:90-114,251-275 (`forbidden_in_isolated_workspace` for `api.plugin.*`/`plugin.*`) | plugin/mod.rs:325-337 (`agent_has_active_handle`→`ForbiddenInIsolatedWorkspace`); error.rs:95 maps to `forbidden_in_isolated_workspace` | Faithful (PluginError is the plugin/LSP path). |

Additional constant/behavior parity (verified literals):

| Constant / behavior | Python | Rust | Status |
|---|---|---|---|
| TTL default | `1800` s (types.py:166) | `1800.0` (caps.rs:45,68,92) | match |
| total_cap default | `5` (types.py:167) | `5` (caps.rs:47,70,93) | match |
| upperdir_bytes default | `1024*1024*1024` (types.py:168-170) | `1_073_741_824` (caps.rs:49,71) | match |
| memavail_fraction | `0.5` (types.py:171) | `0.5` (caps.rs:51,73) | match |
| setup_timeout_s | `30` (types.py:172) | `30.0` (caps.rs:53,99) | match |
| exit_grace_s default + clamp ≥0 | `max(0.0, 0.25)` (types.py:173-176) | `0.25`, `.max(0.0)` (caps.rs:55,103-104) | match |
| fallback_dns | `1.1.1.1` (types.py:180) | `1.1.1.1` (caps.rs:59,75) | match |
| rfc1918 default | `allow` (types.py:177-179) | `Allow` (caps.rs:57,105-110) | match |
| capacity gate operator | `required > budget` (pipeline.py:123) | `required_bytes > budget_bytes` (session.rs:1146) | match (`>` not `>=`) |
| capacity `required` formula | `(len+1)*upperdir_bytes` (pipeline.py:122) | `(open+1)*upperdir_bytes` (session.rs:1155-1159) | match |
| host budget on meminfo read fail | `2**62` (pipeline.py:135) | `1<<62` (session.rs:26,1166) | match |
| BRIDGE/GW/CIDR | `eos-shared0`/`10.244.0.1`/`10.244.0.0/24` (network.py:27-29) | same (network.rs:38-42) | match |
| IP pool range | `.2`–`.254` (network.py:54-57) | `POOL_FIRST_HOST=2..=254` (network.rs:60-62,136) | match |
| veth name shape | `eos-iws-`+`hid[:6]`+`h/n`, IFNAMSIZ 15 (network.py:231-235) | `veth_names` chars().take(6) (network.rs:82-90) | match |
| IMDS drop | `169.254.169.254` (network.py:32,213) | `IMDS_ADDR` drop (network.rs:50,401,560-566) | match |
| persisted schema version | `1` (types.py:18) | `1` (caps.rs:9) | match |
| ISOLATED_WORKSPACE_ROOT | `/testbed` (types.py:21) | `/testbed` (caps.rs:23) | match |
| audit default path | `/tmp/sandbox_isolated_workspace_events.jsonl` (registry) | same (audit.rs:19) | match |
| ns FD order user,mnt,pid,net | namespace_runtime.py:118-125 | isolated.rs:203-208 | match |
| sample_interval default | `0.5`, clamp ≥0.01 (types.py:159,181-184) | `0.5`, `.max(0.01)` (caps.rs:61,115-119) | match (but sampler loop unused — see D3) |

## Disparities

### D1 — Daemon-side exit drain / per-agent dispatch quiesce is ABSENT (medium)
Python closes a check-to-teardown race with a per-agent dispatch slot: `acquire_dispatch_slot` holds a short
`entry_lock`, checks `exit_pending`, and increments an `inflight` counter; `exit` calls `begin_exit_drain` to
flip `exit_pending` and wait for in-flight foreground dispatches before mutating handle maps, and returns a
`exit_drain_timeout` payload if the drain times out
(`/tmp/oldpy/.../workspace_handle_lifecycle.py:207-253,371-401`; arch `workspaces.html` and
`isolated-workspace.html:172-180`). The Rust daemon exit (`isolated.rs:397-449`) only checks
`active_command_sessions` records and, on `force_cancel`, cancels them and busy-waits a grace deadline; there is
no `exit_pending` gate guarding concurrent `command_handle_for_args` / file-verb dispatch, no `begin_exit_drain`,
and no `exit_drain_timeout` kind.
- Evidence: grep for `exit_pending|begin_exit_drain|acquire_dispatch_slot|entry_lock|inflight` in
  `sandbox/crates/eos-daemon/src/*.rs` returns only the unrelated background `inflight_count`.
- Why it matters: a file/exec verb that already resolved a handle via `command_handle_for_args`
  (`isolated.rs:525-545`) can run concurrently with `op_exit` removing the handle and `rmtree`-ing scratch; the
  command-session record gate does not cover non-session file verbs (`isolated_read/write/edit_file`,
  configure-dns/mount children). The Python design explicitly added this to "close the lockless-probe race".
- Fix: add a per-agent `exit_pending` flag + in-flight counter around `command_handle_for_args`-routed dispatch,
  drain before `session.exit`, and surface a `exit_drain_timeout` payload on timeout. Label: BUG/MISSING dynamic
  (not an intentional migration omission — the architecture page documents this as a current Rust slice concern).

### D2 — `exit_isolated_workspace` host gate cancels rather than refusing; prehook layering not ported here (low/medium)
Python's `RequireNoInflightBackgroundTasks` prehook makes `exit_isolated_workspace` REFUSE while sandbox-bound
background work is in flight (agent must cancel via `api.v1.command.cancel`, then retry); the lifecycle drain is
"defense-in-depth" (`isolated-workspace.html:157,166`). The Rust host `exit_isolated_workspace`
(`isolated_workspace.rs:78-88`) unconditionally drains via `cancel_by_agent` with no inflight refuse, and the
daemon `op_exit` refuses only on `active_command_sessions` (not the local background manager count). The prehook
`RequireNoInflightBackgroundTasks` equivalent could not be located wired onto the isolated tools in
`agent-core/crates/eos-tools`.
- Evidence: `isolated_workspace.rs:84-87` (`cancel_by_agent` then `daemon_exit`, no refuse). `execution.rs:293`
  wires `BlockInIsolatedMode` on `exec_command`; `RequireNoInflightBackgroundTasks` on enter/exit/terminals not
  found.
- Why it matters: behavior diverges — Python surfaces `ephemeral_jobs_in_flight` at the tool boundary on exit
  for local background work; Rust silently drains it. The owner invariant "exit cancels or drains" is satisfied,
  but the stricter "refuse + retry" contract (and the bailout fail-open nuance) is not reproduced.
- Fix: confirm whether the prehook layer is intentionally deferred; if not, wire the inflight prehook on the
  isolated lifecycle tools. Label: DIVERGENT (partial), needs owner confirmation.

### D3 — Sampler loop (`isolated_workspace.sampled` cadence) not implemented (low)
Python `initialize` starts `_sampler_task` ticking every `sample_interval_s` (default 0.5s) emitting
`isolated_workspace.sampled` with holder-alive/upperdir-cap (`pipeline.py:246-294,339-364`). Rust carries
`sample_interval_s` in `ResourceCaps` (caps.rs:61) but no sampler task exists; `record_tool_call`
(session.rs:544-562) only emits on tool calls.
- Evidence: grep `sampler|sample_interval` in `eos-isolated/src` finds only the cap field; no loop.
- Why it matters: observability-only; tier-3 tests parsing `isolated_workspace.sampled` would have no event.
- Fix: optional — add a daemon sampler tick or document the cap field as reserved. Label: MISSING (low,
  observability).

### D4 — TTL sweep cadence is a fixed 500ms, not `max(0.5, min(ttl_s/2, 30.0))` (low)
Python ticks the TTL loop at `max(0.5, min(ttl_s/2, 30.0))` (pipeline.py:264). Rust drives `isolated::ttl_sweep`
from a fixed `Duration::from_millis(500)` server loop (`server.rs:131-143`). The eviction CONDITION matches
(`now - last_activity > ttl_s` and active-agent skip, session.rs:486-497 vs pipeline.py:295-303), so correctness
holds; only the heartbeat frequency differs (default 1800s TTL: Python sweeps every 30s, Rust every 0.5s).
- Why it matters: negligible correctness impact; slightly higher wake frequency. Label: DIVERGENT (low).

### D5 — TTL eviction "active" definition differs: command-session presence vs `active_calls==0` (low/medium)
Python skips a handle only when `active_calls == 0` — a per-call in-flight counter incremented in `run_in_handle`
and decremented in `finally` (pipeline.py:302-303,366-396), covering ANY foreground tool call. Rust skips when
the agent has a live `active_command_sessions` record (`isolated.rs:564-569` → `session.ttl_sweep(active_agents)`,
session.rs:486-497). There is no `active_calls` counter; short file verbs (`isolated_read/write/edit_file`) and the
mount/dns children do not register a command session, so a TTL boundary crossing during such a call is not
protected the way Python's `active_calls` guard protects it.
- Evidence: session.rs has no `active_calls` field on `WorkspaceHandle` (struct at session.rs:57-95);
  `record_tool_call` only bumps `last_activity`.
- Why it matters: a TTL eviction could in principle race a long file-verb (rare given short verbs + last_activity
  bump), but the daemon ttl_sweep takes the global lock so a sweep cannot interleave a single dispatch mid-flight
  in practice. Lower severity than D1. Label: DIVERGENT (partial).

## Extra findings

- E1 (no-publish guard is STRONGER in Rust, good): `eos-isolated/Cargo.toml:11` documents the deliberate absence
  of `eos-occ`; `lib.rs:1-28` states the build-time guarantee. Isolated file/exec results stamp
  `"published": false` (command.rs:1259,1283) and `workspace: isolated` (dispatcher.rs:763). This is a build-time
  enforcement Python lacks. Confirmed: no OCC apply on the isolated branches.

- E2 (network: extra peer-isolation drop rules — intentional hardening, not Python parity): Rust installs
  `nft_peer_isolation_rule` on the inet forward chain AND a bridge-family `eos_iws_bridge_filter` table
  (network.rs:405-438,570-619) dropping bridge-CIDR↔bridge-CIDR (non-gateway) traffic. Python only installs
  MASQUERADE + IMDS drop + optional RFC1918 (network.py:186-223) and relies on the `bridge_slave isolated on`
  flag for L2 peer isolation. The Rust port keeps the `isolated(true)` bridge-port flag too
  (network.rs:1292-1304) and ADDS explicit L3/L2 nft drops. This is a behavior ADDITION (closes same-bridge
  peer-to-peer reachability more aggressively). Not a regression; flag as DIVERGENT-additive.

- E3 (IPv6 hardening replaced shell with netlink — faithful, slight order change): Python ns_holder brings `lo`
  up then `_purge_ipv6_default_routes` (sysctl accept_ra=0 for all ifaces, then `ip -6 route flush default`)
  AFTER reading net-ready (ns_holder.py:109-110). Rust `finish_ready` (ns-holder lib.rs:298-308) does:
  `bring_loopback_up` → `configure_namespace_veth` (NEW: programs the ns-side veth IP/route inside the holder) →
  `disable_ipv6_ra` → `flush_ipv6_default_route`. Two notable shifts: (a) Rust moves ns-side veth IP/link/route
  programming INTO the holder via the `net-ready <iface> <ip> <prefix> <gw>` payload (isolated.rs:307-318,
  lib.rs:311-327,436-444), whereas Python does it from the daemon via `nsenter ... ip` (network.py:134-141). This
  is an intentional shell-free redesign; the daemon `signal_net_ready` now carries the veth params. (b) Order of
  accept_ra-vs-loopback differs but both are best-effort and idempotent — no correctness impact.

- E4 (`_rbind_proc_into_new_mntns` ported faithfully): Python `mount --rbind /proc /proc` (ns_holder.py:81-86) →
  Rust raw `mount(MS_BIND|MS_REC)` (ns-holder lib.rs:336-352), best-effort. Match.

- E5 (`signal_net_ready` timing parity is correct and deliberate): Python calls `signal_net_ready` OUTSIDE any
  `t.measure` block, between `configure_dns` and `create_cgroup` (workspace_handle_lifecycle.py:181-194). Rust
  `wire_handle` (session.rs:891-910) reproduces this exactly — net-ready runs untimed between the configure_dns
  and create_cgroup phase measures, with an in-code comment citing the Python line. Good attention to detail.

- E6 (rollback ordering on enter failure): Python `_rollback_partial` order: teardown_veth → kill_holder →
  close_fds → rmtree (workspace_handle_lifecycle.py:196-205). Rust `rollback_partial` order: close_fds →
  teardown_veth → kill_holder → rmtree (session.rs:917-926). FD close happens FIRST in Rust vs LAST in Python.
  Functionally equivalent (all best-effort, suppress errors), but the ordering inverted — low risk; noted for
  completeness.

- E7 (`run_in_handle` active_calls + thread-pool offload not ported): Python offloads `setns_exec` subprocess to
  a thread pool and tracks `active_calls` (pipeline.py:366-415). The Rust isolated exec path goes through
  command sessions (`start_isolated_command_session`, command.rs:681-706) which run via the runner; the
  `active_calls` foreground counter is not modeled (see D5). Different but coherent with the command-session
  architecture.

- E8 (orphan reaper holder-process scan is NARROWER in Rust): Python `_reap_orphan_holder_processes` scans
  `/proc` for any `ns_holder` cmdline marker and kills stale trees with a SIGCONT→SIGTERM→SIGKILL dance
  (orphan_reaper.py:257-310). Rust `reap_startup_orphans` only reaps holders recorded in persisted
  `manager.json` rows (`reap_persisted_holder`, session.rs:687-703) plus naming-convention veth/cgroup/scratch
  (session.rs:759-819); it does NOT scan `/proc` for orphan `eosd ns-holder` processes lacking a persisted row.
  After a daemon crash that loses `manager.json`, stale holders would not be swept by name. Severity low-medium;
  label MISSING (partial GC coverage).

## Open questions

1. Is the daemon-side exit-drain / `exit_pending` quiesce (D1) intentionally deferred for the current Rust slice,
   or a genuine gap? The architecture page (`isolated-workspace.html:172-180`) documents it as an existing
   invariant; if deferred it should be called out there.
2. Is `RequireNoInflightBackgroundTasks` wired anywhere onto `exit_isolated_workspace` in agent-core (D2)? I
   located only `BlockInIsolatedMode` on `exec_command`. If the inflight-refuse prehook is unported, the
   exit-refuse-vs-drain contract diverges.
3. Should the Rust orphan reaper scan `/proc` for `eosd ns-holder` processes without a persisted row (E8), to
   match Python's crash-recovery breadth?
4. Is the sampler cadence (D3) and its `isolated_workspace.sampled` event intentionally dropped, given the cap
   field is still carried?
