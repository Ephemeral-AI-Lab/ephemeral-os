# Sandbox Rust Migration - Phase 3T Deferred Non-Plugin Items

**Status:** Closed for the deferred non-plugin Phase 3T sidecar. Plugin PPC
execution and AV-10 remain out of scope for this document.
**Date:** 2026-06-01.
**Primary plan:** `docs/plans/sandbox-rust-external-migration-PHASE-3T-SHELL-SESSIONS.md`.
**PTY addendum:** `docs/plans/sandbox-rust-external-migration-PHASE-3T-PTY-COMMAND-DESIGN.md`.
**PTY report:** `docs/plans/sandbox-rust-external-migration-PHASE-3T-PTY-COMMAND-DESIGN-iteration-report.md`.

This document tracks the remaining Phase 3T work after the PTY command
implementation, intentionally excluding:

- plugin PPC execution and AV-10 plugin parity;
- Daytona live execution.

## Current Closeout Refresh - 2026-06-01

Latest non-plugin evidence is green on current amd64 artifact
`81eb221542666647a3b0a80a0ed254dff674a0ead27d814bfcea26bd14996d53`:

- `bench/phase3t-rust-isolated-inspection-docker-20260601.json`
  (`run_id=local-7d7cbbea6e84`) passed `gate_pass=true` with 74/74 scenario
  checks green. It now covers isolated PTY stdin, progress, natural completion
  notification, timeout notification, explicit cancel, cancel duplicate
  suppression, and the literal two-agent port `3000` isolation case: both
  isolated agents bind `127.0.0.1:3000`, each reaches its own server, and
  cross-agent access is blocked.
- `bench/phase3t-pty-command-docker-20260601-current-eos-paths-post-notify.json`
  (`run_id=local-7b9deab71f9f`) passed `gate_pass=true`,
  `operation_samples_ok=true`, and `load.gate_pass=true`; all 50 operation
  samples were green across finite command, PTY true/no-op, PTY progress,
  PTY stdin echo, and PTY cancel. Shared-workspace `nohup ... 2>&1 &`
  descendant cleanup remains green for both `tty=false` and `tty=true`.
- The PTY bench harness now waits for the child `ready` marker before measuring
  stdin echo latency, so the stdin-write gate measures PTY input delivery rather
  than Python child startup timing.

Non-plugin Phase 3T deferred items remain closed. The only Phase 3T work not
closed by this document is plugin PPC execution/AV-10, intentionally excluded
above.

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
- The live Docker/dask isolated inspection rerun is closed on the current amd64
  artifact
  `81eb221542666647a3b0a80a0ed254dff674a0ead27d814bfcea26bd14996d53`:
  `bench/phase3t-rust-isolated-inspection-docker-20260601.json`
  (`run_id=local-7d7cbbea6e84`) passed 74/74 checks with the target image
  lacking `ip` and `nft`. It verifies holder process teardown, zero mountinfo
  refs, removed cgroup/scratch/upper/workdir, released leases, removed host
  veth, cleared active PTY state after force cancel, isolated PTY
  stdin/progress/natural-exit/timeout/cancel behavior, literal port `3000`
  network isolation for two agents, and JSONL audit alignment.
- CP-4/AV-4 mixed non-plugin load is closed for the sidecar scope:
  `bench/phase3t-mixed-non-plugin-cp4-av4-20260601.json` passed with
  `run_id=local-12cb8bd20f51`, current amd64 artifact SHA
  `81eb221542666647a3b0a80a0ed254dff674a0ead27d814bfcea26bd14996d53`,
  44 green cells across the 1/3/5/10 matrix, 418 load samples, final-state
  hash `83312110b4ab6fffcd046279741d8b5c8d283617c9a6995d1d0a783d2bd6926d`,
  and attached AV-4 audit artifact paths
  `bench/phase3t-mixed-non-plugin-cp4-av4-20260601.sandbox_events.jsonl`,
  `.performance_report.json`, and `.performance_report.md`.
- CP-5 cache-lock churn is closed for the sidecar scope:
  `bench/phase3t-cache-lock-churn-cp5-20260601.json` passed with
  `run_id=local-56cf60c52d6f`, 260 synthetic `layer_stack_root` values, 260/260
  writes and 260/260 same-path readbacks, distinct per-root contents, bounded
  LRU size 256, 5 evictions, reuse cache hit, and OCC cache-lock max wait
  `0.0265 ms`.
- AV-7 forward/back on-disk parity is closed:
  `bench/phase3t-av7-forward-back-parity-20260601.json` passed with
  `run_id=local-a82fa8f20194`, Python reading Rust-published state, Rust
  reading Python-published state, byte-identical non-base `layer_digest`
  streams, equal final workspace hashes, and identical duplicate-head dedup
  decisions.
- The Section 7 non-plugin differential/property gate is closed:
  `bench/phase3t-section7-non-plugin-differential-20260601.json` passed with
  `run_id=local-42770354ec75`; Python and Rust produced equal canonical
  outcome classes, equal conflict counts, equal final workspace hash
  `30c095482f7ea23fd5d777e4563aaeb5a725cea2bfb2d8a8ec73dd11a37e01a9`, and
  bounded post-squash manifest depth 16 in both runtimes.
- Rust `api.v1.pty.progress` now consults the PTY completion mailbox before
  returning `pty_session_not_found`, so natural PTY completion can be collected
  through the progress API without a harness-only `collect_completed` fallback.
- Rust daemon OCC publishes now run the existing LayerStack auto-squash
  maintenance path after successful publishes. This keeps Rust direct/OCC
  manifest depth aligned with Python under the Section 7 squash pressure lane.
- Python `LayerPublisher` now skips non-regular files during the staging fsync
  pass, avoiding `EACCES` on kernel whiteout device entries emitted by delete
  capture.

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
  `81eb221542666647a3b0a80a0ed254dff674a0ead27d814bfcea26bd14996d53` and
  arm64 `e07a59546cecf931922386a91bf08a8ee5e1fa08747cbc45ee56462eeac4417b`.
- Live isolated inspection rerun passed:
  `uv run python backend/scripts/bench_rust_daemon_isolated_inspection.py
  --artifact sandbox/dist/eosd-linux-amd64 --report
  bench/phase3t-rust-isolated-inspection-docker-20260601.json`; report
  `run_id=local-7d7cbbea6e84`, artifact SHA
  `81eb221542666647a3b0a80a0ed254dff674a0ead27d814bfcea26bd14996d53`, 74/74
  scenario checks passed, preflight showed `ip=nft=`, cgroup writable, and the
  exit inspection reported zero leaked leases/mountinfo refs/cgroup/scratch
  state. The same report now covers isolated PTY stdin/progress/natural
  notification/timeout notification/cancel behavior and the two-agent
  port-3000 network isolation case.
- A broader mock contract spike was attempted but did not reach the relevant
  assertions because the live SWE-EVO fixture failed setup on `/eos`
  writability in the existing container. Treat that as environment/setup debt,
  not evidence against the typed subagent/background cleanup.

No non-plugin sidecar gates remain open. Do not reintroduce
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
- isolated PTY stdin write, progress polling, natural-exit notification,
  timeout notification, explicit cancel, and cancel duplicate suppression are
  covered under the active isolated agent handle;
- isolated network namespace coverage includes the literal same-port case: two
  agents both host on port `3000` without bind conflict, each reaches its own
  localhost, and cross-agent peer-IP access is blocked;
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
namespace/runtime, bridge/veth/nftables, finite command, PTY, forced exit,
shared-publish isolation, and daemon-local audit/leak inspection slice. The
remaining Phase 3T non-plugin gates live outside this isolated slice.

Required work:

- preserve the live Docker isolated proof asserting the new exit inspection
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
- ✅ live Docker rerun asserting the new inspection fields show no leaked
  holder, mountinfo refs, cgroups, leases, scratch dirs, or active PTY records
  under the real isolated kernel path:
  `bench/phase3t-rust-isolated-inspection-docker-20260601.json`.
- ✅ live Docker isolated PTY-control coverage for progress, stdin write,
  natural-exit notification, timeout notification, explicit cancel, and cancel
  duplicate suppression in
  `bench/phase3t-rust-isolated-inspection-docker-20260601.json`
  (`run_id=local-7d7cbbea6e84`);
- ✅ live Docker isolated network same-port coverage: two agents both bind TCP
  port `3000`, each reaches its own localhost server, cross-agent access is
  blocked, force exit cancels the server PTYs, and served files are not
  published after exit.

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

Status: closed on 2026-06-01 for the sidecar non-plugin scope, with plugin
interleave explicitly out of scope.

Completed work:

- ran contention against `read_file`, `write_file`, `edit_file`,
  `exec_command(tty=false)`, `exec_command(tty=true)`, search/glob/grep, and
  LayerStack maintenance;
- included read-heavy, write-heavy, conflict-heavy, PTY long-session, PTY input,
  and mixed shared-workspace load cells;
- verified disjoint writes publish, overlapping writes conflict without silent
  clobber, PTY leases pin lower layers while active, and GC reclaims only
  unleased layers after finalization;
- kept plugin operations out of this gate.

Minimum evidence:

- ✅ 1/3/5/10 concurrency matrix for the mixed non-plugin cells:
  `bench/phase3t-mixed-non-plugin-cp4-av4-20260601.json` passed
  `cp4.gate_pass=true` with 44 green cells, 418 samples, worst p95
  `434.037 ms` in the `pty_input` concurrency-10 cell, and no failed cells;
- ✅ final workspace-state hash and conflict check:
  `83312110b4ab6fffcd046279741d8b5c8d283617c9a6995d1d0a783d2bd6926d`,
  81 checked paths, no mismatches, and a single conflict winner;
- ✅ phase timing breakdowns for snapshot, mount, exec/session, capture, OCC, audit,
  cleanup, and release.

### 5. CP-5 OCC Service Cache-Lock Churn

Status: closed on 2026-06-01 for the sidecar non-plugin scope.

Completed work:

- drove 260 distinct layer-stack roots through the Rust OCC runtime services;
- forced LRU eviction churn while writes and reads continued;
- measured cache-lock wait, service creation/reuse, eviction, write publish
  latency, and readback latency;
- used same-path per-root readbacks to prove no lost writes, duplicate root
  contents, or stale service reuse.

Minimum evidence:

- ✅ `bench/phase3t-cache-lock-churn-cp5-20260601.json` with wait, create/reuse,
  eviction, write, and readback metrics;
- ✅ proof that cache churn does not introduce lost writes, duplicate writers, or
  stale service reuse;
- ✅ clear pass/fail threshold recorded in the progress tracker: `samples_ok`,
  `readbacks_ok`, `distinct_root_contents`, `reuse_hit`, `cache_bounded`,
  `evicted_after_churn`, and `metrics_reported` must all be true.

### 6. AV-4 Audit Pull Under Non-Plugin CP-4 Load

Status: closed on 2026-06-01 for the sidecar non-plugin CP-4 load.

Completed work:

- attached daemon audit pull to the CP-4 mixed-load run;
- preserved `sandbox_events.jsonl`, `performance_report.json`, and
  `performance_report.md`;
- verified `dropped_event_count == 0` and `lost_before_seq == 0`;
- verified report artifact size and audit buffer pressure remain within gates.

Minimum evidence:

- ✅ CP-4 performance report with daemon audit pull stats:
  `bench/phase3t-mixed-non-plugin-cp4-av4-20260601.performance_report.json`
  and `.md`;
- ✅ host artifact with zero lost/dropped audit events:
  `bench/phase3t-mixed-non-plugin-cp4-av4-20260601.sandbox_events.jsonl`,
  2,422 pulled events, 1,253,654 bytes, buffer pressure `0.1574`,
  `dropped_event_count=0`, and `lost_before_seq=0`;
- ✅ no missing PTY lifecycle, OCC publish/conflict, lease, cleanup, or GC events.

### 7. AV-7 Forward/Back On-Disk Parity

Status: closed on 2026-06-01 for the sidecar non-plugin scope.

Completed work:

- proved Python can read Rust-published LayerStack/OCC state;
- proved Rust can read Python-published LayerStack/OCC state;
- compared canonical typed results, final workspace hashes, `layer_digest`
  byte streams, and head-dedup decisions;
- included command-produced writes from the final non-login Bash contract.

Minimum evidence:

- ✅ bidirectional parity fixture artifact:
  `bench/phase3t-av7-forward-back-parity-20260601.json`;
- ✅ byte-identical `layer_digest` streams for equivalent states:
  Rust-first and Python-first non-base digest streams are identical;
- ✅ identical head-dedup decisions for duplicate captures in both directions.

### 8. Section 7 Differential/Property Contention Suite

Status: closed on 2026-06-01 for the sidecar non-plugin scope.

Completed work:

- drove identical operation sequences through Python and Rust against separate
  state under parallel contention;
- included `read_file`, `write_file`, `edit_file`, search/glob/grep, and
  `exec_command` shell verbs using `/bin/bash --noprofile --norc -c <cmd>`;
- included conflict, atomic multi-path, delete/whiteout, symlink, no-op capture,
  squash/GC, and PTY finalization cases;
- excluded plugin/PPC operation lanes.

Minimum evidence:

- ✅ canonical result equality:
  `bench/phase3t-section7-non-plugin-differential-20260601.json` reports
  `canonical_result_classes_equal=true`;
- ✅ equal final workspace-state hash:
  `30c095482f7ea23fd5d777e4563aaeb5a725cea2bfb2d8a8ec73dd11a37e01a9`;
- ✅ no lost writes or silent clobbers: both runtimes report one conflict winner
  and four loser attempts, plus equal final file view;
- ✅ property-test seed/log artifact for the closing run:
  `bench/phase3t-section7-non-plugin-differential-20260601.json`.

## Suggested Order

All non-plugin sidecar items are closed. Resume plugin PPC execution/AV-10 from
the main Phase 3T plan when that scope is no longer skipped.
