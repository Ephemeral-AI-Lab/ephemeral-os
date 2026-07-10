---
title: config — phased implementation plan
tags:
  - ephemeral-os
  - config
  - implementation-plan
status: implementation_plan
updated: 2026-07-10
---

# config — phased implementation plan

Execution plan for the two specs in this folder:

- `spec.md` — config consolidation: hardcoded policy values into `prd.yml`
- `cli-e2e-test-spec.md` — the `config` CLI e2e test family

## Plan rules

```text
Gate rule: a phase is DONE only when every box in its acceptance
criteria is checked. No work item of phase N+1 starts before phase N is
done. Acceptance boxes are checked with evidence (a passing command, a
grep with empty output, a green pytest id) — never on intent.
Ordering rule: the e2e harness lands first (phase 0). Every production
phase after it ships together with the e2e tests that observe it, in
the same phase — landing a knob and deferring its test is not done.
Shipping rule: each phase is independently shippable and committed to
main (project convention: no branches). config/prd.yml is not edited in
any phase; config/bench.yml changes only in phase 1, exactly as spec'd.
```

Resolution of a spec ambiguity: `daemon.http.export.token_ttl_s` reads
`sandbox-protocol/src/export_stream.rs:16`, so it needs the protocol
injection pattern. It lands in **phase 2** with `ProtocolLimits`, not phase
1. Phase 1's `daemon.http.export` covers the two daemon-owned constants
(`frame_bytes`, `channel_frames`) only.

## Phase tracker

| Phase | Name | Depends on | Status |
| --- | --- | --- | --- |
| 0 | e2e config family — harness + present-day coverage | — | done |
| 1 | bench-path knobs + env-var retirement | 0 | done |
| 2 | daemon service limits + injection patterns | 1 | done |
| 3 | runtime operation caps | 2 | done |
| 4 | host-side surfaces (gateway, console, docker timing) | 3 | done |

## Global definition of done (applies to every phase, in addition to its own criteria)

Verified per phase (see each phase's "Global definition of done checked"
line for the phase-scoped evidence); final state after phase 4:

- [x] `cargo build` succeeds
- [x] `cargo test` (whole workspace) passes — 109 ok-suites / 0 failures
- [x] `cargo clippy --all-targets` passes with no new violations — 0
- [x] `cargo fmt` produces no diff — `cargo fmt --check` clean
- [x] No inline comments added to production code; no test code under any `src/`
- [x] `git diff config/prd.yml` is empty
- [x] Crate boundaries hold: no leaf crate (`sandbox-protocol`,
      `sandbox-observability`, `sandbox-runtime/layerstack`,
      `namespace-execution`) gains a `sandbox-config` dependency
      (`grep -l sandbox-config crates/{sandbox-protocol,sandbox-observability}/Cargo.toml crates/sandbox-runtime/{layerstack,namespace-execution}/Cargo.toml` → empty)
- [x] Phase committed to `main` — `7994fca50`, `593200001`, `c02181c39`,
      `955d7a8be`

---

## Phase 0 — e2e config family: harness + present-day coverage

Builds `cli-operation-e2e-live-test/config/` per `cli-e2e-test-spec.md`. No
production Rust changes. Proves the two-lane loading model against the
config surface that exists today, so phases 1–4 each land against a working
verification harness.

### Work items

- [x] `config/__init__.py`, `config/conftest.py` — family-scoped gateway
      fixture + session finalizer restoring the baseline gateway
      (package-scoped `config_family_custody` + module-scoped
      `lane_a_daemon_yaml`)
- [x] `config/helpers.py` — `make_config(overrides)` (pyyaml deep-merge:
      objects merge, scalars/arrays replace, output under pytest tmp),
      `rewrite_daemon_yaml`, `gateway_with_config` context manager,
      in-sandbox command/transcript helpers
- [x] `pytest.ini` — add `config` marker (serial family); `pyyaml` added to
      `requirements.txt`
- [x] `test_daemon_reload.py` — A1 features F1–F7
- [x] `test_validation.py` — A2 features F1–F6
- [x] `test_manager_section.py` — A3 features F1–F5
- [x] `test_phase_knobs.py` — `TestPhase1/2/3` classes, all skip-marked with
      reason `"config consolidation phase N not landed"`

### Acceptance criteria

- [x] `pytest -m config` runs the family serially and green on a machine with
      Docker up — evidence: `20 passed, 11 skipped, 330 deselected in 90.23s`
      (2026-07-10, suite dir, pytest.ini active)
- [x] `pytest -m "not config"` still selects the pre-existing suite
      (330/361 collected, 31 deselected); `manager/` after a full `config/`
      run matches the no-config control run — after-config: 97 passed /
      6 failed; control (no config first, fresh baseline gateway): 101
      passed / 2 failed; the only deterministic failure in both orderings is
      export HRD-05, which depends on operator-shell `EOS_EXPORT_*` gateway
      env the suite never sets (passed 2026-07-08 with those vars exported;
      fails on any freshly script-started gateway, config family or not —
      see phase 1 note below). Remaining diffs are load flakes that pass in
      isolation and flip between orderings (MED-08 failed only in the
      control run). Baseline restore itself verified: post-family smoke
      `17 passed in 22.51s` and 5/6 after-config failures green when rerun
      on the restored gateway.
- [x] Lane A mechanics proven: `test_rewrite_applies_to_next_sandbox` green
      (rewrite observed by next create; prior sandbox unaffected)
- [x] Deterministic behavior probes green: mount-mask visibility (A1-F3),
      tiny `setup_timeout_s` session failure with timeout-classed error
      (A1-F4; landed as `1e-9` — 1 ms races the ns-holder on fast hosts,
      observed passing at 1 ms on Apple Silicon), observability toggle
      (A1-F6; disabled arm answers with an *empty* events view — no-op
      observer — not a structured error)
- [x] Validation negatives green on both lanes: unknown daemon key and
      invalid values fail `create_sandbox` with structured error + rollback
      (A2-F1..F3, F6); unknown/invalid manager key fails gateway start
      (A2-F4, F5)
- [x] Lane B probes green: `container_env` nonce round-trip (A3-F1/F2;
      the runner builds command envs from an allowlist — HOST_KEYS in
      `shell_exec/request.rs` — so the nonce rides `NO_PROXY`, an
      allowlisted var the baseline already sets); `memory_bytes` vs
      `/sys/fs/cgroup/memory.max` (A3-F3, green — no skip needed on this
      host)
- [x] `test_phase_knobs.py` collects as skipped (3 classes, reasons name the
      pending phase; `SKIPPED [4]+[2]+[5]` in the family run)
- [x] `git status` shows `config/prd.yml` and `config/bench.yml` untouched;
      generated YAMLs live only under pytest tmp
- [x] Suite README (`cli-operation-e2e-live-test/README.md`) layout section
      updated to list the `config/` family

### Phase 0 findings (drift + notes for later phases)

- `create_workspace_session` is no longer a public runtime-CLI operation;
  `exec_command` auto-creates a publish_then_destroy session, so all
  in-sandbox probes are one-shot `exec_command` calls (the mount mask applies
  there too). The e2e spec's session helpers landed on this surface.
- A3-F4 landed as the spec's alternate arm: the CLI rejects `--image ""`
  (`image must be non-empty`), so the test pins explicit-flag-over-
  `default_image` precedence.
- `runtime.workspace.layer_stack_root` relocation is broken today: the
  manager pins the shared-base mount target to `CONTAINER_LAYER_STACK_ROOT`
  (`create_sandbox.rs`), so a relocated root panics daemon boot at
  workspace-base init (`services.rs:82`). A1-F5 relocates the two scratch
  roots only and records this coupling.
- **Phase 1 planning note:** export HRD-05 (`manager/management/export`,
  zstd/entry bombs) only passes when the gateway process carries
  `EOS_EXPORT_MAX_DECOMPRESSED_BYTES`/`EOS_EXPORT_MAX_ENTRIES` — the very
  side channels phase 1 deletes. When the caps move to `manager.export`,
  HRD-05 must get its lowered caps through a generated-config gateway arm
  (config-family pattern) instead of ambient env, or it fails against the
  8 GiB/1e6 defaults.

---

## Phase 1 — bench-path knobs + env-var retirement

`spec.md` tier 1 minus `token_ttl_s` (see resolution note). The set with
demonstrated tuning demand; retires all three `EOS_*` side channels and the
`bench.yml` container_env smuggle.

### Phase 1 drift note (2026-07-10, recorded while landing)

A concurrent refactor series (`refactor(manager): page exports through
authenticated RPC` + the uncommitted export-stream removal that followed it)
deleted the daemon HTTP export spool stream while phase 1 was in flight:
`sandbox-daemon/src/http/export.rs` left the module tree, the manager now
pages every export through `read_export_chunk` RPC, and the protocol's
`export_stream.rs` (token vocabulary + TTL) is gone. Consequences applied
here, per the spec's own no-dead-schema policy:

- `daemon.http.export` (`frame_bytes`, `channel_frames`) is **dropped from
  phase 1** — its one consumer no longer exists. A schema test now pins the
  opposite contract: `daemon.http` is an *unknown key*.
- P1-F4 is adapted from frame-shape to **chunk-shape invariance**: the
  transport-shape knob end to end is `runtime.layerstack.export_chunk_bytes`
  (the RPC page size), exercised with a multi-chunk spool.
- Phase 2's `token_ttl_s` work item is void (its target was deleted); its
  spec entry needs the same drift treatment when phase 2 starts.
- `manager.export.max_stream_bytes` now also gates the daemon-declared
  `spool_bytes` before the first page — a strictly earlier rejection than the
  spec described.

### Work items

- [x] Schema: `configs/runtime.rs` — new `runtime.layerstack` subsection
      (`remount_sweep_width`, `export_chunk_bytes`, `spool_zstd_level`),
      `#[serde(default)]`, validation (`width >= 1`, `chunk >= 1`,
      `zstd level 1..=22`)
- [x] Schema: `configs/manager.rs` — new `manager.export` subsection
      (`max_stream_bytes`, `max_decompressed_bytes`, `max_apply_entries`),
      defaults preserving today's values, validation `>= 1`;
      `ManagerConfig::validate()` added (export + docker), called by the
      gateway
- [x] ~~Schema: `configs/daemon.rs` — new `daemon.http.export` subsection~~
      dropped per drift note; `config_daemon_rejects_unknown_http_subsection`
      pins the surface's absence
- [x] `configs/validate.rs` — `require_i32_in_range` added
- [x] Wiring: squash remount sweep reads width from the layerstack service
      config (constructor path); `sweep_width()` env fn deleted
      (`operation/src/layerstack/service/impls/squash.rs`)
- [x] Wiring: export chunk cap and spool zstd level flow from
      `RuntimeConfig` through the operation layer (`emit_delta_stream` takes
      the level as a parameter; layerstack crate stays config-free)
- [x] Wiring: `sandbox-manager/src/export_apply.rs` — `ExportApplyCaps`
      value type injected by the gateway from `ManagerConfig`; `env_cap`,
      `max_decompressed_bytes()`, `max_apply_entries()` env fns deleted
- [x] ~~Wiring: daemon HTTP export stream frame params~~ void per drift note
      (surface deleted concurrently)
- [x] `config/bench.yml` — `container_env.EOS_REMOUNT_SWEEP_WIDTH` smuggle
      replaced by `runtime.layerstack.remount_sweep_width: __SWEEP_WIDTH__`;
      header comment updated
- [x] Bench driver (`ab_driver.py`) substitution updated to the YAML key
      (docstrings in `ab_driver.py`/`ab_compare.py`; the textual
      `__SWEEP_WIDTH__` replace already lands on the YAML key)
- [x] Schema tests in `crates/sandbox-config/tests/` — defaults, overrides,
      validation rejections for the two landed subsections + the
      `daemon.http` unknown-key pin + a bench-template round-trip test
- [x] Unskip + adapt `TestPhase1` in `test_phase_knobs.py` (P1-F1..P1-F4,
      F4 as chunk-shape invariance); export HRD-05 moved onto a lowered-caps
      generated-config gateway (config-family custody pattern, `config`
      marker) since its ambient-env channel died with the `EOS_EXPORT_*` vars

### Acceptance criteria

- [x] `grep -rn "EOS_REMOUNT_SWEEP_WIDTH\|EOS_EXPORT_MAX_DECOMPRESSED_BYTES\|EOS_EXPORT_MAX_ENTRIES" crates/ config/ cli-operation-e2e-live-test/` → empty (2026-07-10)
- [x] `grep -rn "env_cap\|std::env::var" crates/sandbox-manager/src/export_apply.rs crates/sandbox-runtime/operation/src/layerstack/service/impls/squash.rs` → empty (2026-07-10)
- [x] `cargo test -p sandbox-config` passes with new schema tests covering:
      field defaults equal today's constants; unknown key under each new
      subsection rejected; each validation bound rejected at its edge
      (width 0, zstd 0 and 23; the frame_bytes 4095 edge is void with the
      dropped `daemon.http.export` — its replacement pin is
      `config_daemon_rejects_unknown_http_subsection`) — evidence: 44 passed
      (`config_layerstack_*`, `config_manager_export_*`,
      `config_validation_rejects_*`)
- [x] A YAML without any of the new keys deserializes to today's behavior —
      defaults tests assert exact values 4, 2 MiB, 3 (layerstack) and 2 GiB,
      8 GiB, 1e6 (manager.export); the 1 MiB / 4 frame values are void per
      the drift note
- [x] `pytest -m config` fully green including unskipped `TestPhase1`:
      P1-F1 sweep-width 1 vs 4 squash invariance, P1-F2 stream cap error,
      P1-F3 entry cap error, P1-F4 chunk-shape invariance (adapted) —
      evidence: `25 passed, 7 skipped, 331 deselected in 121.99s`
      (2026-07-10, includes the config-marked export HRD-05; the 7 skips are
      the TestPhase2/3 placeholders)
- [x] Bench config round-trip: generated bench arm YAML (width substituted)
      loads through `sandbox-config`
      (`bench_template_round_trips_after_width_substitution` green) and
      `bench.yml` contains no `EOS_` string (`grep -c EOS_ config/bench.yml`
      → 0)
- [x] Squash + export e2e regressions green: existing
      `manager/management/squash` and `export` suites pass unchanged —
      evidence: `pytest manager/management/squash manager/management/export
      -m "not bench and not runnable and not config"` → `86 passed,
      16 deselected in 231.55s` (2026-07-10; the config-marked HRD-05 ran
      green inside the `-m config` lane, bench stays explicit-only per its
      module docstring)
- [x] Global definition of done checked — build green, whole-workspace
      `cargo test` 108 suites ok / 0 failures, `cargo clippy --all-targets`
      0 warnings, `cargo fmt --check` clean, no inline comments added to
      production code, `git diff config/prd.yml` empty, leaf-crate
      `sandbox-config` grep empty, committed to `main` (2026-07-10)

---

## Phase 2 — daemon service limits + injection patterns

`spec.md` tier 2. Establishes the two injection patterns (protocol value
type, leaf observability mapping) that phase 3 reuses.

### Phase 2 drift note (2026-07-10, recorded while landing)

- `daemon.http.export.token_ttl_s` and "export token TTL from config" are
  **void**: the token machinery died with the export stream (phase-1 drift
  note). `daemon.http` returns to the schema carrying `forward` only; the
  schema test pinning `daemon.http.export` as an unknown key replaces the
  phase-1 whole-`http` pin.
- `ProtocolLimits` carries the shipped defaults as associated consts
  (`DEFAULT_MAX_REQUEST_BYTES`, `DEFAULT_REQUEST_READ_TIMEOUT_S`) because
  pure clients (CLI, manager forward deadline, gateway read path, console
  body cap) consume them in const contexts; the daemon alone injects
  configured values.
- The `max_line_bytes` injection covers the daemon-side `Sink`; the
  namespace-process runner (config-free leaf, its own process) keeps the
  shipped `MAX_LINE_BYTES` default for np-* records.
- The forward deadlines ride `ServerConfig::forward`
  (`DaemonHttpForwardConfig`) into `proxy::run` — this also replaced the
  interim `forward_response_timeout` seam introduced while fixing a
  concurrent `#[cfg(test)]` policy violation; the daemon http test now
  injects its 100 ms deadline through the same config field.

### Work items

- [x] Schema: `configs/daemon.rs` — `daemon.server` gains
      `max_concurrent_connections >= 1`, `max_request_bytes >= 65536`,
      `request_read_timeout_s > 0`; ~~`daemon.http.export` `token_ttl_s`~~
      void per drift note; new `daemon.http.forward`
      (`connect_timeout_s`, `response_timeout_s`, both `> 0`)
- [x] Schema: `configs/observability.rs` — `max_line_bytes`, new `sampling`
      (`max_walk_nodes >= 1`, `max_walk_depth >= 1`) and `views`
      (`resource_window_ms >= 1`, `layer_delta_default_limit >= 1`,
      `layer_delta_max_limit >= 1`, cross-field default ≤ max);
      `ObservabilityConfig::validate()` now runs in the daemon's config load
- [x] `sandbox-protocol/src/limits.rs` — `ProtocolLimits` value type
      (`max_request_bytes`, `request_read_timeout_s`) with `Default`
      preserving today's constants; protocol crate gains no config
      dependency; bare consts deleted (clients use the associated defaults)
- [x] Daemon wiring: `serve.rs` constructs `ProtocolLimits` from
      `daemon.server` and threads it down both listeners' read paths and the
      HTTP API body cap; RPC connection semaphore takes the config value
      (`rpc/lifecycle.rs` const deleted); forward proxy deadlines injected
      via `ServerConfig::forward`; ~~export token TTL~~ void
- [x] Observability wiring: the daemon's leaf mapping extended —
      `max_line_bytes` into `Sink::new`, the shared sampling budget as a
      leaf-owned `WalkBudget` into `sample_upperdir`/`sample_layerstack`;
      leaf consts in `collect/disk.rs`, `collect/layerstack.rs` replaced by
      the injected budget (one budget for both walks, decision 8)
- [x] Views wiring: `observability/mod.rs` window cap and
      `view/layerstack.rs` delta limits from `observability.views` (held on
      `DaemonObservability`)
- [x] Schema tests: defaults, rejections, cross-field rule (52 config tests)
- [x] Unskip + adapt `TestPhase2` (P2-F1 request cap via `file_write`;
      P2-F2 layer-delta limit via the authenticated internal
      `get_observability` call — the CLI catalog exposes only the inventory
      shape)

### Acceptance criteria

- [x] `grep -rn "const MAX_CONCURRENT_CONNECTIONS\|const MAX_REQUEST_BYTES\|const REQUEST_READ_TIMEOUT_S\|const EXPORT_STREAM_TOKEN_TTL_S" crates/sandbox-daemon/src crates/sandbox-protocol/src` → empty (2026-07-10); the shipped defaults live as
      `ProtocolLimits` associated consts, no call-site consts
- [x] `sandbox-protocol/Cargo.toml` and `sandbox-observability/Cargo.toml`
      unchanged w.r.t. dependencies (no `sandbox-config` edge; leaf grep
      empty 2026-07-10)
- [x] `cargo test -p sandbox-config` passes: new fields default to today's
      values (256, 16 MiB, 30.0, 10.0, 30.0, 16 KiB, 1024, 64, 600000,
      500, 5000 — the TTL `30` is void per the drift note);
      `layer_delta_default_limit > layer_delta_max_limit` rejected —
      evidence: 52 passed
- [x] `cargo test -p sandbox-daemon -p sandbox-protocol` passes, including
      `read_request_line_rejects_oversized_payloads` — a lowered injected
      64 KiB `max_request_bytes` rejects a one-byte-over envelope (and its
      companion accepts within the cap) — within the whole-workspace run:
      108 suites ok, 0 failures (2026-07-10)
- [x] `pytest -m config` fully green including unskipped `TestPhase2`:
      P2-F1 64 KiB request cap rejects an oversized `file_write` while the
      default arm accepts it; P2-F2 layer-delta view honors a lowered
      default limit (3 entries + truncated) and rejects a request above the
      lowered max — evidence: `27 passed, 5 skipped, 331 deselected in
      129.56s` (2026-07-10; the 5 skips are the TestPhase3 placeholders)
- [x] Observability e2e regression: phase 0's `test_observability_toggle`
      green inside the same family run
- [x] ~~Export e2e regression: token-gated export stream TTL~~ void per the
      drift note (token machinery removed with the stream); the export
      regressions ran in phase 1 against the RPC paging path
- [x] Global definition of done checked — build green, workspace test 108
      suites ok / 0 failures, clippy 0, fmt clean, `git diff config/prd.yml`
      empty, leaf-crate grep empty, committed to `main` (2026-07-10)

---

## Phase 3 — runtime operation caps

`spec.md` tier 3. Mechanical application of phase 2's construction-injection
pattern across command/file/namespace-execution services.

### Phase 3 drift note (2026-07-10, recorded while landing)

- The leaf gains one `ExecutionCaps` value type (`Default` preserves the
  shipped policy) consumed by the engine constructors; the production
  `ForkRunnerLauncher` carries the stdin deadline and runner-result cap so
  the `NsRunnerLauncher` trait (and every test fake) keeps its signature.
- The CLI materializes catalog argument defaults client-side
  (`request_builder.rs`), so `read_lines_default` knobs govern the raw
  operation surface; P3-F2 probes `file_read` through the authenticated
  internal call, as P3-F1 does for the CLI-less `file_list`.
- Terminal-retention eviction surfaces as the *empty terminal read*
  (`read_command_lines` on an evicted id answers the empty window), not a
  structured missing-entry error; P3-F5 pins that landed contract.
- The freeze budget rides `ResourceCaps` through the workspace manager into
  the remount quiesce spec (the workspace crate stays config-free).

### Work items

- [x] Schema: `configs/runtime.rs` — new `runtime.command`
      (`max_active >= 1`, `read_lines_default >= 1`, `read_lines_max >= 1`,
      cross-field default ≤ max), new `runtime.file`
      (`read_lines_default`, `max_output_bytes`, `max_edit_bytes`,
      `max_list_entries`, all `>= 1`), `runtime.namespace_execution` gains
      `freeze_budget_s >= 0`, `stdin_write_deadline_s > 0`,
      `max_terminal_entries >= 1`, `max_transcript_window_bytes >= 1`,
      `max_runner_result_bytes >= 1`
- [x] Wiring: command service (`core.rs` consts deleted;
      `COMMAND_ENGINE_SETUP_TIMEOUT_S` collapsed into
      `runtime.workspace.setup_timeout_s` — decision 6);
      `read_command_lines.rs` limits from config
- [x] Wiring: file service (`support.rs`, `impls/list.rs` consts deleted;
      values via `FileService::open` construction)
- [x] Wiring: namespace-execution (freeze budget via `ResourceCaps` →
      `QuiesceSpec.freeze_budget`, `DEFAULT_FREEZE_BUDGET` deleted; stdin
      deadline + runner result cap via the production launcher; terminal
      retention initialized from `ExecutionCaps` with
      `set_terminal_retention` retained; transcript window threaded through
      `CommandExecValue`)
- [x] Schema tests: defaults, rejections, both cross-field rules (56 config
      tests green)
- [x] Unskip + adapt `TestPhase3` (P3-F1..P3-F5, adaptations per drift note)

### Acceptance criteria

- [x] `grep -rn "COMMAND_ENGINE_SETUP_TIMEOUT_S" crates/` → empty (collapsed,
      not renamed; verified 2026-07-10)
- [x] `grep -rn "const MAX_ACTIVE_COMMANDS\|const MAX_OUTPUT_BYTES\|const MAX_EDIT_BYTES\|const MAX_LIST_ENTRIES\|const MAX_TERMINAL_ENTRIES\|const MAX_TRANSCRIPT_WINDOW_BYTES\|const MAX_RUNNER_RESULT_BYTES\|const STDIN_WRITE_DEADLINE\|const DEFAULT_FREEZE_BUDGET" crates/sandbox-runtime` shows at most `Default`-impl
      definitions in config-value types, no live call-site consts — grep is
      fully empty (defaults live in `Default` fn bodies / `caps.rs`; test
      fixture consts renamed `TEST_*`)
- [x] `cargo test -p sandbox-config -p sandbox-runtime` passes; defaults
      equal today's constants (256, 200/1000, 2000, 256 KiB, 4 MiB, 2000,
      0.5, 2.0, 512, 1 MiB, 8 MiB) — whole-workspace `cargo test` 108
      ok-suites / 0 failures;
      `config_operation_caps_default_to_shipped_policy` pins every value
- [x] `pytest -m config` fully green including unskipped `TestPhase3`:
      P3-F1 list truncation at 5, P3-F2 read default 10 lines, P3-F3 1 KiB
      edit cap error, P3-F4 `max_active: 1` admission error, P3-F5
      `max_terminal_entries: 2` eviction (oldest drain → missing entry) —
      `32 passed, 331 deselected in 339.73s`, zero skips (P3-F2 probes the
      raw operation via internal call, P3-F5 asserts the empty terminal
      read, per drift note)
- [x] Runtime e2e regressions green: existing file-operation and
      workspace-session suites pass with default config (behavioral
      defaults unchanged end to end) — `pytest runtime -m "not config"`:
      `220 passed, 6 skipped in 522.01s`; every skip environmental (apt
      egress unavailable, `E2E_RETENTION`/`E2E_STORM` opt-in)
- [x] Global definition of done checked — build green, whole-workspace
      `cargo test` 108 ok-suites / 0 failures, `cargo clippy --all-targets`
      0 warnings (two dangling doc comments left by const deletion fixed),
      `cargo fmt` no diff, no inline comments in `src/`,
      `git diff HEAD config/prd.yml` empty, leaf-crate `sandbox-config`
      grep empty, committed to `main`

---

## Phase 4 — host-side surfaces

`spec.md` tier 4: gateway and console sections, Docker/manager timing knobs.
No new e2e knob tests per `cli-e2e-test-spec.md` (phase 4 exclusion
rationale recorded there); coverage is implicit — the config family's own
gateway bring-up and the whole suite exercise these paths.

### Phase 4 drift note (2026-07-10, recorded while landing)

- `configs/gateway.rs` keeps ONE type: `GatewayConfig` is both the YAML
  section and the server runtime config; `auth_token` is `#[serde(skip)]`
  so a YAML `auth_token:` key fails `deny_unknown_fields` (pinned by a
  schema test) and the secret stays flag/env-only.
- Socket precedence is flag > env (`SANDBOX_GATEWAY_SOCKET`) > YAML >
  default — the pre-existing env override keeps outranking YAML, matching
  the console requirement. Resolution is a pure
  `resolve_gateway_config(overrides, env, yaml)` in the gateway crate,
  unit-tested without process env.
- Console resolution mirrors it: pure `ConsoleConfig::from_sources`;
  `EndpointCache` now owns its TTL at construction.
- `manager.local_daemon` maps into `LocalDaemonTimeouts` on
  `LocalSandboxDaemonInstaller` (`with_timeouts`). No production caller
  constructs that installer today (test-only surface), so the section is
  schema-complete and injectable but the gateway wires only
  `observability_snapshot` (ExportApplyCaps precedent) — recorded rather
  than inventing a dead config path.
- `READINESS_IO_TIMEOUT` (250 ms per-probe socket IO) stays a const: the
  spec's target-key table names only `stop_timeout_s`/`readiness_poll_ms`,
  and per-probe IO cadence is a non-goal.

### Work items

- [x] Schema: `configs/gateway.rs` reworked from bare constants into a
      `Deserialize` `gateway` section (`bind_addr` non-empty socket addr,
      `pid_path`, `max_concurrent_connections >= 1`), defaults preserving
      today's constants
- [x] Schema: new `configs/console.rs` — `console` section (bind + five
      timeouts + cache TTL, `_s` f64, all `> 0`)
- [x] Schema: `configs/manager.rs` — `manager.docker` gains
      `connect_timeout_s`, `stop_timeout_s`, `readiness_poll_ms`,
      `port_publish_attempts`, `port_publish_retry_delay_ms`; new
      `manager.observability_snapshot` (`max_concurrent_requests >= 1`,
      `timeout_ms >= 1`); new `manager.local_daemon` (`ready_timeout_s`,
      `stop_timeout_s`, both `> 0`)
- [x] Wiring: gateway `main.rs` reads optional `gateway` section; precedence
      CLI flag > YAML > default implemented and unit-tested
      (`tests/config.rs`, 6 tests)
- [x] Wiring: console gains `--config-yaml` / `SANDBOX_CONSOLE_CONFIG_YAML`
      reading the `console` section; existing flag/env overrides outrank
      YAML (pure `from_sources`, 5 precedence tests)
- [x] Wiring: provider-docker consts (`engine.rs` connect/port-publish,
      `installer.rs` stop/readiness-poll) and manager consts
      (`observability_snapshot.rs` via `ObservabilitySnapshotLimits` on
      `ManagerServices`, `daemon_install.rs` via `LocalDaemonTimeouts`;
      polls stay hardcoded per spec non-goals) replaced by config values
- [x] Schema tests: defaults, rejections, gateway/console precedence tests
      (74 config tests incl. the maximal-YAML cross-phase test)
- [x] `config/README.md` updated: section list now includes `gateway` and
      `console`; static-values paragraph unchanged

### Acceptance criteria

- [x] `cargo test -p sandbox-config -p sandbox-gateway -p sandbox-console`
      passes; precedence tests prove flag > YAML > default for gateway
      socket and console bind — 74 config tests, gateway `tests/config.rs`
      6 precedence tests (+8 server), console `tests/console/config.rs`
      5 precedence tests (34 total)
- [x] A config with only today's `prd.yml` sections starts the gateway and
      console unchanged (defaults test + live check via phase 0 family
      gateway bring-up, which passes an explicit `SANDBOX_GATEWAY_CONFIG_YAML`)
      — the full-suite gateway (phase-4 binary built 17:35, started 17:39
      with `--config-yaml config/prd.yml`, no `gateway` section present)
      served the whole run on defaults;
      `config_gateway_defaults_preserve_shipped_policy` +
      `config_console_defaults_preserve_shipped_policy` pin the constants
- [x] `grep -rn "const CONNECT_TIMEOUT_SECS\|const STOP_TIMEOUT_SECS\|const READINESS_POLL\|const PORT_PUBLISH\|const DAEMON_READY_TIMEOUT\|const DAEMON_STOP_TIMEOUT\|const MAX_CONCURRENT_DAEMON_SNAPSHOT_REQUESTS\|const DEFAULT_DAEMON_SNAPSHOT_TIMEOUT_MS" crates/sandbox-provider-docker/src crates/sandbox-manager/src` shows at most `Default`-impl definitions
      (readiness/stop *poll* constants may remain — spec non-goal — but the
      four timeout/attempt knobs must be config-fed) — grep is fully empty
      (defaults live in `Default` impls of the config/value types; only
      `DAEMON_READY_POLL`/`DAEMON_STOP_POLL`/`READINESS_IO_TIMEOUT` polls
      remain, per non-goals); console consts
      (`PROBE_TIMEOUT`/`RESOLVE_TIMEOUT`/`ENDPOINT_CACHE_TTL`/proxy pair)
      also grep-empty
- [x] Full live suite green end to end: `pytest` (all families) — the
      gateway the suite starts now loads its own `gateway` section —
      2026-07-10 full run: `355 passed, 6 skipped in 17:45` (skips all
      environmental: apt egress, `E2E_RETENTION`/`E2E_STORM`). Two
      non-product blips, both green on isolated rerun: `RUN-06` (parallel
      worker's npm-native-build case, `e7e3c5077`) failed on flapping
      package egress (prebuild download blocked → gyp → image has no
      Python; sibling RUN-03 ran the identical `npm install` green in the
      same run, and RUN-06 passed once egress recovered), and the file
      smoke test hit a transient fixture error, passing standalone. Every
      collected test has a passing result on today's binaries.
- [x] `pytest -m config` green: A2-F4/F5 (invalid manager key/value fails
      gateway start) still behave identically with the enlarged manager
      schema — config family fully green inside the full run (32 tests,
      zero skips), same behavior as the phase-3 standalone lane
- [x] Console smoke: console starts against a YAML with a `console` section
      and serves its health probe (manual or scripted check recorded here) —
      scripted 2026-07-10: `sandbox-console --config-yaml` with a full
      `console` section bound the YAML `127.0.0.1:7899`; `GET /` → 200;
      `GET /api/sandboxes/nope/health` → structured `gateway_error` JSON
      (health route exercised through endpoint resolution against the
      running gateway, which rejected the smoke token as expected)
- [x] Global definition of done checked — build green, whole-workspace
      `cargo test` 109 ok-suites / 0 failures, `cargo clippy --all-targets`
      0 warnings (two `field_reassign_with_default` test lints fixed via
      struct-update syntax), `cargo fmt --check` clean, no inline comments
      in `src/`, `git diff HEAD config/prd.yml` empty, leaf-crate
      `sandbox-config` grep empty, committed to `main`

---

## Cross-phase completion checklist (the plan is done when)

- [x] All four consolidation phases + phase 0 committed to `main`, each gated
      — phase 0 (pre-plan), 1 `7994fca50`, 2 `593200001`, 3 `c02181c39`,
      4 `955d7a8be`, each with its acceptance boxes evidenced above
- [x] `test_phase_knobs.py` contains zero skip markers —
      `grep -c "skip" config/test_phase_knobs.py` → 0 (all three phase
      classes implemented; 32-test config lane green)
- [x] The maximal YAML shape in `spec.md` loads through `sandbox-config` in
      one piece (a final schema test deserializes the full example document)
      — `lib_tests::maximal_config_shape_loads_through_every_section_schema`
      deserializes and validates all seven sections (minus the decision-11
      drift: no `daemon.http.export`, no `token_ttl_s`)
- [x] `spec.md` and `cli-e2e-test-spec.md` statuses flipped from
      `implementation_plan` to done/landed, with any drift between spec and
      landed reality recorded in their decision logs — both frontmatters now
      `status: landed`; spec.md decision 12 records the phases 2-4 deltas,
      cli-e2e-test-spec.md gained a phases 2-4 landed-reality log
