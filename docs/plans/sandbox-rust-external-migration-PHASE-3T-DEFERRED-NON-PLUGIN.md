# Sandbox Rust Migration - Phase 3T Deferred Non-Plugin Items

**Status:** Sidecar deferred-item list after the Phase 3T PTY command
implementation.
**Date:** 2026-06-01.
**Primary plan:** `docs/plans/sandbox-rust-external-migration-PHASE-3T-SHELL-SESSIONS.md`.
**PTY addendum:** `docs/plans/sandbox-rust-external-migration-PHASE-3T-PTY-COMMAND-DESIGN.md`.
**PTY report:** `docs/plans/sandbox-rust-external-migration-PHASE-3T-PTY-COMMAND-DESIGN-iteration-report.md`.

This document tracks the remaining Phase 3T work after the PTY command
implementation, intentionally excluding:

- plugin PPC execution and AV-10 plugin parity;
- Daytona live execution.

## Next Agent Handoff - 2026-06-01

Current state:

- CP-4t is closed for Docker shared-workspace command/PTY paths under the final
  `exec_command` / PTY tool names and `/eos` runtime paths.
- Model-facing generic background tools are retired from runtime/catalog/facade
  exposure.
- The `BaseTool.background` / `@tool(background=...)` concept is removed.
  Background-manager attachment is hard-coded in `engine.background.policy` for
  subagent launch and PTY session surfaces.
- Subagent launch/progress/cancel is typed: `run_subagent` returns
  `subagent_session_id`, `check_subagent_progress` and `cancel_subagent` accept
  `subagent_session_id`, and subagent supervisor records no longer use a hidden
  `bg_N` alias.
- Subagent mock-loop evidence is closed for natural completion, no-terminal
  failure, explicit cancel, and parent terminal submission while a subagent is
  active. Parent terminal exit now terminates active subagents as
  `non_cancellation_tool_request` and emits typed notification/audit evidence.
- Generic background tool implementation classes still exist only as direct
  legacy probe/test code. Do not re-expose them to models or recreate
  `make_background_tools()`.
- Rust `eos-daemon` now backs the public isolated-workspace lifecycle/status op
  names with daemon-local `eos-isolated` session state when
  `EOS_ISOLATED_WORKSPACE_ENABLED=true`: `enter` opens an agent-keyed private
  scratch/lease handle, `status`/`list_open` report live handles, `exit`
  discards scratch, and `test_reset` clears the singleton under the test
  harness gate. Linux `exec_command` / PTY start now consult the active
  `agent_id` handle and return isolated/no-OCC-publish results; active PTY
  records block `exit` unless the caller uses the force-cancel path.
- Rust `eos-runner` now has a Linux `RunMode::SetNs` implementation instead of
  the previous `todo!()` body: it validates namespace FDs, joins the optional
  isolated cgroup, calls `setns` in `user` -> `mnt` -> `pid` -> `net` order, and
  reuses the fresh-namespace command/search execution primitive. Its
  `setns_overlay_mount` helper now enters `user`+`mnt` and delegates to the
  overlay mount port.
- Rust `eos-ns-holder` now performs the first holder syscall slice: unshare the
  user/mount/pid-for-children/net namespace stack, pin namespace FDs, best-effort
  `/proc` rbind, pipe handshake, RA-disable hook, and pause-until-kill
  lifecycle. Rust `eos-daemon` now spawns `eosd ns-holder`, opens inheritable
  namespace FDs from `/proc/<pid>/ns`, mounts the isolated overlay through
  `eosd ns-runner --mount-overlay`, signals the holder ready handshake, tracks
  and kills the holder child, and closes retained namespace/control FDs on
  isolated teardown. Linux command/PTY requests choose `RunMode::SetNs` when the
  active isolated handle has namespace FDs. Holder-side loopback-up,
  IPv6-default-route deletion, namespace-side veth link/address/default-route
  programming, daemon-side bridge/veth creation, and static nftables NAT/filter
  table/chain/rule setup now use shell-free netlink hooks. The local amd64
  Docker/dask proof passed with the target image lacking `ip` and `nft`: the
  Rust path created the shared bridge and veth pair, exposed the namespace-side
  interface/default route, installed static nftables NAT/filter rules through
  netlink, kept finite command and PTY writes private to isolated scratch,
  blocked non-forced exit while a PTY was active, force-exited cleanly, closed
  status/list state, removed the host veth, and left shared OCC unpublished.
- Rust isolated exit now returns daemon-local inspection fields and mirrors them
  into the exit audit event: handle/agent map counts, lease-release status,
  active lease count, holder PID/kill error, namespace FD count, cgroup
  existence, scratch/upper/workdir existence, mountinfo reference count when
  `/proc/self/mountinfo` is available, and PTY force-cancel cleanup arrays.

Last focused verification:

- `198 passed` for the engine/background/subagent/tool focused unit slice:
  `backend/tests/unit_test/test_engine/test_subagent_mock_loop.py`,
  `backend/tests/unit_test/test_engine/test_background_tasks.py`,
  `test_background_task_emitters.py`, `test_background_unit.py`,
  `test_provider_history.py`, `test_spawn_agent.py`,
  `test_tool_call_dispatch_lifecycle.py`, `test_prompt/test_runtime_prompt.py`,
  `test_tools/test_schema_summary.py`, `test_no_claude_code_collision.py`,
  `test_sandbox_toolkit/test_toolkit.py`, `test_subagent_retry.py`, and
  `test_tool_execution.py`.
- `backend/tests/unit_test/test_task_center_runner/test_probe_bridge.py`
  passed.
- `ruff check` passed on the touched Python files.
- `git diff --check` passed.
- Rust focused isolated checks passed:
  `cargo test -p eos-runner` (`7 passed`), `cargo check -p eos-runner --target
  x86_64-unknown-linux-musl`, `cargo check -p eos-isolated`,
  `cargo check -p eos-isolated --target x86_64-unknown-linux-musl`,
  `cargo test -p eos-ns-holder` (`5 passed`),
  `cargo check -p eos-ns-holder --target x86_64-unknown-linux-musl`,
  `cargo test -p eos-daemon isolated_workspace --test phase2_read_paths`
  (`3 passed`), `cargo test -p eos-daemon
  active_pty_records_block_exit_until_cleared` (`1 passed`),
  `cargo check -p eos-daemon --target x86_64-unknown-linux-musl`, and
  `cargo check -p eosd --target x86_64-unknown-linux-musl`. Existing warnings
  are from pre-existing `eos-overlay`/`eos-ephemeral` code.
- Follow-up focused inspection checks passed after the exit-inspection update:
  `cargo fmt --all --check`, `cargo check -p eos-ns-holder -p eos-isolated -p
  eos-daemon --target x86_64-unknown-linux-musl`, `cargo check -p
  eos-ns-holder -p eos-isolated -p eos-daemon --target
  aarch64-unknown-linux-musl`, `cargo clippy -p eos-ns-holder -p
  eos-isolated -p eos-daemon --target x86_64-unknown-linux-musl --all-targets`
  (pre-existing adjacent warnings only), `cargo test -p eos-isolated`,
  `cargo test -p eos-runner`, `cargo test -p eos-ns-holder`, `cargo test -p
  eos-daemon isolated_workspace --test phase2_read_paths`, `cargo test -p
  eos-daemon active_pty_records_block_exit_until_cleared`, and both
  `xtask package` targets. Current packaged SHAs are amd64
  `6f94b650023186b9b4e282d20ad1bd0cd53b97c44759c313547c47f158ebecf6` and
  arm64 `f2ef28b4a0a5c93b78c16ae47a064a39e59a2add8e25e329c8c2c52b97b3fc08`.
- A broader mock contract spike was attempted but did not reach the relevant
  assertions because the live SWE-EVO fixture failed setup on `/eos`
  writability in the existing container. Treat that as environment/setup debt,
  not evidence against the typed subagent/background cleanup.

Next work should start with hardening/verifying Rust isolated-workspace
command/PTY semantics under live Docker, then finishing the remaining Rust
isolated network proof: live validation of bridge/veth, static nftables
NAT/filter, and holder netlink hooks. Do not reintroduce
`shell(background=true)`, `BaseTool.background`, model-facing generic
background controls, or `bg_N` as a subagent reference.

## Completed Baseline

The PTY command implementation is accepted for the Docker shared-workspace path:

- model-facing tools exist for `exec_command`, `write_pty_command_stdin`,
  `check_pty_command_progress`, and `cancel_pty_command`;
- Rust protocol and daemon ops exist for `api.v1.exec_command`, PTY controls,
  and the internal PTY completion collector;
- finite `exec_command(tty=false)` uses the non-login Bash contract and rejects
  raw argv at the public boundary;
- native Rust PTY compiles for `x86_64-unknown-linux-musl`;
- Docker PTY/load/p95 gates passed at 1/3/5/10 concurrency for finite no-op,
  finite write, and PTY no-op operations;
- full tiered Docker live E2E tiers 0-6 passed under
  `EOS_SANDBOX_RUNTIME=rust`;
- PTY natural exit, timeout, progress polling, stdin write, cancel, and
  `nohup ... 2>&1 &` descendant cleanup are covered for `tty=false` and
  `tty=true`;
- PTY completion notifications now fire once for natural exit and timeout, and
  explicit cancel suppresses duplicate completion notification.

## Deferred Items

### 1. Rust Isolated-Workspace Command/PTY Integration

Rust daemon isolated-workspace public lifecycle op names are now backed by
daemon-local `eos-isolated` session state. The first Rust slice replaces the
disabled stubs with enabled lifecycle/status/list/exit behavior, adds JSONL
audit emission, and routes Linux `exec_command` / PTY start through the active
agent handle with isolated/no-OCC-publish result metadata. Active PTY records
now block non-forced isolated exit.

The local amd64 Docker/dask live proof is now closed for the Rust
namespace/runtime, bridge/veth/nftables, finite command, PTY, forced exit, and
shared-publish isolation slice. The implementation still needs broader
daemon-local audit/leak inspection before treating isolated mode as fully
closed, and the remaining Phase 3T non-plugin gates still live outside this
isolated slice.

Required work:

- run the live Docker isolated proof again and assert the new exit inspection
  fields under the real kernel path for holder, mountinfo, cgroup, lease,
  scratch, and PTY force-cancel cleanup;
- preserve the proven Rust ns-holder/setns handoff under live Docker, including
  namespace FD inheritance, overlay mount persistence, cgroup join, and holder
  kill/cleanup behavior;
- preserve the live Docker proof that finite `exec_command` writes stay private
  to the isolated workspace and unpublished to shared OCC;
- preserve isolated PTY start/progress/write/natural-exit behavior
  against the active agent handle;
- preserve the explicit force-cancel exit path under a real running PTY and the
  non-forced active-PTY exit block;
- preserve natural PTY exit visibility only inside the same isolated workspace
  until isolated exit;
- preserve isolated exit scratch discard and pinned snapshot lease release.

Minimum evidence:

- ✅ focused Rust coverage for enabled lifecycle ops and active PTY records
  blocking isolated lifecycle;
- ✅ runner-side setns request-shape coverage and Linux target compile check;
- ✅ ns-holder handshake unit coverage plus Linux target compile checks for
  `eos-ns-holder`, `eos-daemon`, and `eosd`;
- ✅ bridge/veth netlink slice compiles for Linux target and preserves host
  `eos-isolated` checks through target-gated Linux dependencies;
- ✅ static nftables NAT/filter netlink slice compiles for Linux target without
  adding an `nft` binary dependency;
- ✅ live Docker isolated-workspace scenario on amd64 Docker/dask with no `ip`
  or `nft` in the target image: finite command, PTY start/progress/write,
  natural PTY exit, active-PTY exit blocking, force exit, peer shared-publish
  isolation, status/list closure, host-veth teardown, and no shared publish
  during or after isolated exit;
- ✅ focused daemon-local isolated exit inspection coverage: exit response and
  audit JSONL prove no registered handle/agent remains, active leases return to
  zero, scratch/upper/workdir are removed, cgroup absence is represented, and
  stale PTY force-cancel cleanup clears active PTY state;
- live Docker rerun asserting the new inspection fields show no leaked holder,
  mountinfo refs, cgroups, leases, scratch dirs, or active PTY records under the
  real isolated kernel path.

### 2. Typed Subagent Surface

The shell-session plan says subagents should no longer depend on generic
background task tools. That surface has now been replaced with typed
`subagent_session_id` progress/cancel controls; only deeper parent-loop
evidence remains.

Status: closed on 2026-06-01 with focused unit coverage and mocked query-loop
evidence for parent-loop interactions.

Completed work:

- keep `run_subagent(agent_name, prompt)` as the launch tool, but return a
  model-facing `subagent_session_id`;
- add or expose `check_subagent_progress(subagent_session_id, last_n_messages)`;
- add or expose `cancel_subagent(subagent_session_id)`;
- remove subagent instructions that tell the model to use generic background
  task tools;
- retire the model-facing generic background management tools from runtime and
  catalog exposure;
- remove the `BaseTool.background` / `@tool(background=...)` concept; background
  manager attachment is now hard-coded in `engine.background.policy` for
  subagent launch and PTY session surfaces;
- use `subagent_session_id` as the only subagent supervisor/model reference
  rather than keeping a hidden `bg_N` alias;
- make subagent natural completion, no-terminal failure, explicit cancellation,
  and parent terminal-abandon reasons typed and visible in deeper mock-loop
  notifications/audit evidence.

Minimum evidence:

- ✅ unit tests for launch/progress/cancel identifier separation;
- ✅ provider-history compaction tests that preserve the typed subagent result
  surface;
- ✅ mock-loop tests for natural completion, no-terminal failure, explicit cancel,
  and parent terminal submission while a subagent is active.

### 3. CP-4t Formal Closeout

Status: closed in the phase tracker on 2026-06-01 for Docker shared-workspace
command/PTY paths under the final tool names and `/eos` runtime paths.

Completed work:

- recorded the final PTY report as the CP-4t artifact of record;
- confirmed the accepted samples all go through LayerStack lease, overlay mount,
  command execution, capture, OCC publish/discard, cleanup, and lease release;
- confirmed no model-facing raw-argv performance gate remains;
- refreshed the progress tracker so Phase 3T no longer reads as if the PTY command
  work is still next.

Closeout evidence:

- `bench/phase3t-pty-command-docker-20260601-current-eos-paths-post-notify.json`;
- `bench/phase3t-pty-command-docker-20260601-current-eos-paths-timeout-cancel-fix.json`;
- `bench/phase3t-pty-command-docker-20260601-review-cleanup.json`;
- full tiered Docker summary
  `.omc/results/progressive-test-summary-phase3t-rust-scratch-full-final-20260601.jsonl`;
- final `/eos` timeout/cancel tiered Docker summary
  `.omc/results/progressive-test-summary-phase3t-current-eos-paths-timeout-cancel-fix-tier0-6-20260601.jsonl`;
- final progress-doc update in
  `docs/plans/sandbox-rust-external-migration-PROGRESS.md`.

### 4. CP-4 Mixed Throughput/Contention Without Plugin Interleave

With plugin interleave explicitly out of scope for this sidecar, CP-4 still
needs a mixed non-plugin contention gate using the final non-login Bash command
contract.

Required work:

- run contention against `read_file`, `write_file`, `edit_file`,
  `exec_command(tty=false)`, `exec_command(tty=true)`, search/glob/grep, and
  LayerStack maintenance;
- include read-heavy, write-heavy, conflict-heavy, PTY long-session, PTY input,
  and mixed shared-workspace load cells;
- verify disjoint writes publish, overlapping writes conflict without silent
  clobber, PTY leases pin lower layers while active, and GC reclaims only
  unleased layers after finalization;
- keep plugin operations out of this gate.

Minimum evidence:

- 1/3/5/10 concurrency matrix for the mixed non-plugin cells;
- final workspace-state hashes and manifest metrics;
- phase timing breakdowns for snapshot, mount, exec/session, capture, OCC, audit,
  cleanup, and release.

### 5. CP-5 OCC Service Cache-Lock Churn

CP-5 remains open: OCC service cache-lock wait/contention must be measured under
LRU churn across more than 256 distinct `layer_stack_root` values.

Required work:

- drive >256 distinct layer-stack roots through the Rust OCC runtime services;
- force LRU eviction churn while writes and reads continue;
- measure cache-lock wait, service creation/reuse, eviction, and publish latency;
- compare against the Python service cache behavior where applicable.

Minimum evidence:

- `bench/cache-lock-*.json` or equivalent artifact with wait p50/p95/max;
- proof that cache churn does not introduce lost writes, duplicate writers, or
  stale service reuse;
- clear pass/fail threshold recorded in the progress tracker.

### 6. AV-4 Audit Pull Under Non-Plugin CP-4 Load

Focused PTY runs and prior mock suites show audit pull can be drop-free, but
AV-4 for Phase 3T must be tied to the final CP-4 mixed non-plugin load.

Required work:

- attach daemon audit pull to the CP-4 mixed-load run;
- preserve `sandbox_events.jsonl`, `performance_report.json`, and
  `performance_report.md`;
- verify `dropped_event_count == 0` and `lost_before_seq == 0`;
- verify report artifact size and audit buffer pressure remain within gates.

Minimum evidence:

- CP-4 performance report with daemon audit pull stats;
- host artifact with zero lost/dropped audit events;
- no missing PTY lifecycle, OCC publish/conflict, lease, cleanup, or GC events.

### 7. AV-7 Forward/Back On-Disk Parity

Forward/back on-disk parity remains open.

Required work:

- prove Python can read Rust-published LayerStack/OCC state;
- prove Rust can read Python-published LayerStack/OCC state;
- compare canonical typed results, final workspace hashes, `layer_digest`
  byte streams, and head-dedup decisions;
- include command-produced writes from the final non-login Bash contract.

Minimum evidence:

- bidirectional parity fixture artifacts;
- byte-identical `layer_digest` streams for equivalent states;
- identical head-dedup decisions for no-op and duplicate captures.

### 8. Section 7 Differential/Property Contention Suite

The high-risk OCC/LayerStack differential/property suite remains open for the
non-plugin scope.

Required work:

- drive identical operation sequences through Python and Rust against separate
  state under parallel contention;
- include `read_file`, `write_file`, `edit_file`, search/glob/grep, and
  `exec_command` shell verbs using `/bin/bash --noprofile --norc -c <cmd>`;
- include conflict, atomic multi-path, delete/whiteout, symlink, no-op capture,
  squash/GC, and PTY finalization cases;
- exclude plugin/PPC operation lanes.

Minimum evidence:

- canonical result equality;
- equal final workspace-state hash;
- no lost writes or silent clobbers;
- property-test seed/log artifacts for any failing or minimized case.

## Suggested Order

1. Rerun the live Rust isolated Docker proof with the new exit inspection fields
   and assert no leaked holder, mountinfo refs, cgroups, leases, scratch dirs,
   or active PTY records.
2. Run CP-4 mixed non-plugin load with attached AV-4 audit pull.
3. Run CP-5 cache-lock churn.
4. Run AV-7 forward/back parity.
5. Run the non-plugin Section 7 differential/property contention suite.
