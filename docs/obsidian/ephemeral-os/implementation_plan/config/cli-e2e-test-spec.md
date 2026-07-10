---
title: config — CLI e2e test spec
tags:
  - ephemeral-os
  - config
  - e2e-test
  - implementation-plan
status: implementation_plan
updated: 2026-07-10
---

# config — CLI e2e test spec

Test plan for a new `config` family in `cli-operation-e2e-live-test/`,
verifying that YAML config values actually govern behavior end to end:
`config file → gateway/daemon load → observable CLI JSON difference`. Two
parts per area, following `manager/management/test_spec.md`:

- **(a) Features to be tested**
- **(b) Test files to be created**

Companion to `spec.md` (config consolidation) in this folder: part A below is
runnable **today** against the existing config surface and proves the config
delivery machinery itself; part B is keyed to consolidation phases 1–3 and
lands as `skip`-marked placeholders that activate per phase.

## The two-lane loading model (drives the whole suite design)

The merged YAML is consumed at **two different times**, which determines what
a test must restart to change a knob:

```text
Lane A — per-create reload (cheap). The Docker installer reads the file
at manager.docker.daemon_config_yaml_path from disk on EVERY
create_sandbox (provider-docker installer.rs:49) and uploads those bytes
into the new container; runtime_workspace_paths (runtime.rs:307) loads
and validates it per create as well. Sections daemon / runtime / runner
/ observability therefore take effect by rewriting the file and creating
a NEW sandbox. No gateway restart. Existing sandboxes keep the config
they were created with — itself a testable property.

Lane B — gateway-start load (expensive). The manager section (and the
future gateway section) is deserialized once when sandbox-gateway
starts. Changing it means stopping the gateway and starting one against
a different SANDBOX_GATEWAY_CONFIG_YAML. Serial, slow-marked.
```

Consequence: the family runs its **own dedicated gateway** pointed at a
suite-owned generated YAML (never the checked-in `config/prd.yml`, which is
never mutated). Lane A tests share one family gateway and rewrite the
generated daemon file per test; Lane B tests restart the gateway per arm.
Because the gateway binds one fixed socket and writes fixed
`/tmp/eos-gateway.{pid,token,log}` paths, config tests cannot coexist with
the shared baseline gateway other families reuse: the family teardown
restores a baseline (`config/prd.yml`) gateway so subsequent families are
undisturbed.

## Conventions

- **CLI-driven, JSON-verified**: every action goes through `sandbox-cli`;
  assertions read structured JSON, never gateway/daemon logs.
- **Generated config, never mutated baseline**: `helpers.make_config(overrides)`
  loads `config/prd.yml` with pyyaml, applies the documented merge semantics
  (objects merge recursively, scalars and arrays replace), and writes the
  result to a per-test temp path. The repo's `prd.yml` and `bench.yml` are
  read-only inputs.
- **Fresh sandbox per arm**: Lane A knobs bind at create; every arm creates
  its own sandbox via the family helper (auto-registered with the session
  cleanup net) and destroys it in teardown.
- **Markers**: all tests carry `config` (serial, gateway-touching; excluded
  from the default parallelizable run), Lane B additionally `slow`. Add both
  to `pytest.ini`.
- **Gateway custody**: a family-scoped fixture stops any running gateway,
  starts the family gateway on the generated YAML, and a finalizer restores
  the baseline gateway even on failure (mirrors the `_session_sandbox_cleanup`
  guarantee).

## Suite layout

```text
cli-operation-e2e-live-test/
└── config/
    ├── __init__.py
    ├── conftest.py                 # family gateway fixture + baseline restore
    ├── helpers.py                  # make_config, rewrite_daemon_yaml,
    │                               # gateway_with_config, in_sandbox helpers
    ├── test_daemon_reload.py       # A1 — Lane A mechanics + daemon-side knobs
    ├── test_validation.py          # A2 — invalid config rejection, both lanes
    ├── test_manager_section.py     # A3 — Lane B manager.docker knobs
    └── test_phase_knobs.py         # B  — consolidation phases 1–3, skip-marked
```

## Area → file → focus (quick map)

| Area | Test file | Lane | Status |
| --- | --- | --- | --- |
| Config delivery mechanics | `test_daemon_reload.py` | A | active |
| Daemon-side behavior knobs | `test_daemon_reload.py` | A | active |
| Invalid config rejection | `test_validation.py` | A + B | active |
| Manager-section knobs | `test_manager_section.py` | B | active |
| Consolidation phase 1–3 knobs | `test_phase_knobs.py` | A + B | deferred (per phase) |

---

## A1. Config delivery mechanics + daemon-side knobs — `test_daemon_reload.py`

Proves the Lane A contract itself, then exercises each daemon-side section
through an observable behavior.

### (a) Features

- **F1 per-create reload** — rewrite the generated daemon YAML between two
  `create_sandbox` calls (e.g. flip a `runner.mount_mask.hidden_paths`
  entry); the second sandbox observes the new value, proving no
  gateway-level caching.
- **F2 create-time binding** — after F1's rewrite, the *first* sandbox still
  exhibits the old value: config binds at create, not live.
- **F3 mount mask honored** — with the default `hidden_paths: [/eos]`, a
  workspace-session command `ls /eos` fails or sees an empty mask; with an
  additional hidden path (e.g. `/opt`) covering a directory the image ships,
  that directory is invisible in the new sandbox. Verified from command
  transcript JSON via the runtime CLI.
- **F4 setup timeout enforced** — `runtime.workspace.setup_timeout_s: 0.001`
  → workspace session creation fails with a timeout-classed error in the
  operation JSON (deterministic: no real setup completes in 1 ms). The
  baseline arm (30 s) succeeds. This is the cleanest "a runtime float from
  YAML changed daemon behavior" probe.
- **F5 scratch/layer roots relocated** — set
  `runtime.workspace.{layer_stack_root,scratch_root}` and
  `runtime.namespace_execution.scratch_root` to alternate absolute paths →
  sandbox creates ready, a session runs a command, and a file write
  round-trips (functional invariance; the paths are container-internal so
  only behavior is observable).
- **F6 observability disabled** — `observability.enabled: false` → the
  observability views/snapshot for the new sandbox report no spans/samples
  (empty or unavailable per the view contract) while operations still
  succeed; the `true` arm returns populated views.
- **F7 worker threads invariance** — `daemon.server.max_worker_threads: 1` →
  sandbox is ready and a short command sequence completes (accepted +
  functional; no stronger observable exists via CLI).

### (b) Test files

- `test_daemon_reload.py`
  - `test_rewrite_applies_to_next_sandbox` (F1, F2) — one test, two
    sandboxes, both assertions.
  - `test_mount_mask_hides_paths` (F3) — parametrized over baseline and
    extended mask.
  - `test_setup_timeout_tiny_fails_session` (F4) — asserts error kind from
    session-create JSON; baseline arm as control.
  - `test_relocated_roots_functional` (F5)
  - `test_observability_toggle` (F6)
  - `test_single_worker_thread_functional` (F7)

---

## A2. Invalid config rejection — `test_validation.py`

`deny_unknown_fields` and `validate()` failures must surface as structured
errors at the right boundary, and must not wedge lifecycle state.

### (a) Features

- **F1 unknown key, daemon side** — an unrecognized key under `runtime.workspace`
  → `create_sandbox` fails with the deserialize-section error surfaced in the
  error JSON; no sandbox appears in `list_sandboxes` (rollback holds).
- **F2 semantic violation, daemon side** — `setup_timeout_s: 0` (violates
  `> 0`) → `create_sandbox` error mentions the invalid runtime config; also
  `exit_grace_s: -1`, and a relative `scratch_root` (violates
  `require_absolute`), parametrized.
- **F3 filesystem-root guard** — `layer_stack_root: /` → rejected
  (`reject_dangerous_root`), same structured path as F2.
- **F4 unknown key, manager side (Lane B)** — a gateway started against a
  YAML with an unknown `manager.docker` key fails to come up: the start
  wrapper reports failure / the readiness poll never answers. Assertion is
  process-level (exit status / poll timeout), the one place log-free
  verification is impossible pre-RPC; the test asserts *failure to serve*,
  not log content.
- **F5 semantic violation, manager side (Lane B)** — `readiness_timeout_ms: 0`
  → same failure-to-start contract as F4.
- **F6 recovery after invalid** — after F1/F2, restoring a valid daemon YAML
  makes the next `create_sandbox` succeed with no gateway restart (Lane A
  reload also recovers).

### (b) Test files

- `test_validation.py`
  - `test_unknown_daemon_key_fails_create` (F1)
  - `test_invalid_daemon_values_fail_create` (F2, F3) — parametrized over
    `(override, expected substring)` pairs.
  - `test_unknown_manager_key_fails_gateway_start` (F4) — `slow`.
  - `test_invalid_manager_value_fails_gateway_start` (F5) — `slow`.
  - `test_valid_config_recovers` (F6) — appended to F1/F2 flow.

---

## A3. Manager-section knobs — `test_manager_section.py` (Lane B, `slow`)

Each arm = one gateway start against a generated YAML, then create/observe.

### (a) Features

- **F1 container_env injected** — add
  `manager.docker.container_env.E2E_CONFIG_PROBE: <nonce>` → in-sandbox
  `printenv E2E_CONFIG_PROBE` returns the nonce (transcript JSON). Proves the
  whole Lane B path with a deterministic in-container observable.
- **F2 container_env removed** — a config without the probe var → `printenv`
  fails / empty (control arm for F1; can share one gateway with F1 by
  ordering arms).
- **F3 memory cap applied** — `manager.docker.memory_bytes: 268435456`
  (256 MiB) → in-sandbox `cat /sys/fs/cgroup/memory.max` equals the value
  (cgroup v2; skip if the probe file is absent in the image/runtime).
- **F4 default_image honored** — set `manager.docker.default_image`, create
  **without** `--image` → sandbox ready; in-sandbox `/etc/os-release` matches
  the configured image family. (If the CLI requires `--image`, this pins the
  precedence contract instead: explicit flag wins.)
- **F5 privileged toggle invariance** — `privileged: true` arm creates ready
  and runs a session (the de-privileged default is exercised by every other
  test; this guards the legacy escape hatch from rotting).

### (b) Test files

- `test_manager_section.py`
  - `test_container_env_probe` (F1, F2) — one gateway, two sandboxes.
  - `test_memory_bytes_cgroup_max` (F3) — conditional skip on missing cgroup
    file.
  - `test_default_image_used_when_flag_absent` (F4)
  - `test_privileged_arm_functional` (F5) — `slow`, optional in CI.

---

## B. Consolidation-phase knobs — `test_phase_knobs.py` (deferred)

Skip-marked placeholders keyed to `spec.md` phases; each activates when its
phase lands. Every entry names its observable contract now, so landing a
phase includes flipping its tests on.

### (a) Features

Phase 1 (`runtime.layerstack`, `manager.export`, `daemon.http.export`):

- **P1-F1 sweep width invariance** — `runtime.layerstack.remount_sweep_width: 1`
  vs `4` → squash succeeds identically in both arms (width is a perf knob;
  correctness invariance is the e2e contract, perf belongs to the bench).
  Also asserts the retired `EOS_REMOUNT_SWEEP_WIDTH` env smuggle is gone from
  the flow (config file only).
- **P1-F2 export stream cap** — `manager.export.max_stream_bytes: 4096` →
  `export_changes` of a delta larger than 4 KiB fails with the cap error
  kind; generous-cap arm succeeds.
- **P1-F3 apply entry cap** — `manager.export.max_apply_entries: 1` → dir-mode
  export of a two-file delta fails with the entry-cap error.
- **P1-F4 frame shape invariance** — `daemon.http.export.frame_bytes: 4096`,
  `channel_frames: 1` → exported bytes identical to the 1 MiB/4 default arm
  (checksum compare of the two archives).

Phase 2 (`daemon.server` limits, `observability.views`):

- **P2-F1 request cap enforced** — `daemon.server.max_request_bytes: 65536`
  → a `write_file` whose payload exceeds 64 KiB fails with the
  request-too-large error; the default arm accepts it.
- **P2-F2 layer-delta view limit** — `observability.views.layer_delta_default_limit: 3`
  → the layerstack view returns ≤ 3 deltas for a sandbox with more than 3
  published layers.

Phase 3 (`runtime.command`, `runtime.file`, `runtime.namespace_execution`):

- **P3-F1 list truncation** — `runtime.file.max_list_entries: 5` → listing a
  directory with 10 files returns exactly 5 entries (+ truncation indicator
  per the operation contract).
- **P3-F2 read default lines** — `runtime.file.read_lines_default: 10` →
  reading a 100-line file without `--limit` returns 10 lines.
- **P3-F3 edit size cap** — `runtime.file.max_edit_bytes: 1024` → a 2 KiB
  edit fails with the size-cap error.
- **P3-F4 command admission** — `runtime.command.max_active: 1` → with one
  long-running command active, a second submission returns the admission
  error naming `max_active`.
- **P3-F5 terminal retention eviction** — `runtime.namespace_execution.max_terminal_entries: 2`
  → run three short commands, then drain the first id → missing-entry error;
  the two newest drain fine.

### (b) Test files

- `test_phase_knobs.py` — one module, `@pytest.mark.skip(reason="config
  consolidation phase N not landed")` per class: `TestPhase1`, `TestPhase2`,
  `TestPhase3`. Phase 4 (gateway/console sections) is intentionally absent:
  gateway bind/PID knobs are exercised implicitly by the family's own gateway
  bring-up, `max_concurrent_connections` has no deterministic CLI observable,
  and the console is outside this suite's `sandbox-cli` charter.

---

## Implementation notes

- `helpers.make_config(overrides: dict) -> Path` implements the documented
  merge semantics (objects merge, scalars/arrays replace) in Python for file
  *generation only* — it is not a test of the Rust merge engine, which is
  covered by `sandbox-config` unit tests. Generated files live under the
  pytest tmp factory, one per test, named for the test id (bench precedent:
  `ab_driver.py`'s generated arm files).
- The family gateway fixture is module-scoped for Lane A (one gateway, many
  rewrites) and function-scoped for Lane B arms. Both funnel through one
  `gateway_with_config(config_path)` context manager in `helpers.py` that
  owns stop → start → poll-ready → yield → stop, with the baseline-restore
  finalizer registered session-wide in `config/conftest.py`.
- Lane A rewrites target the *generated* file the family gateway was started
  with (its `manager.docker.daemon_config_yaml_path` points there); the
  checked-in `config/prd.yml` is never written.
- Timeout-classed assertions (A1-F4) match on error kind/substring from the
  operation JSON, not exact message text, to stay robust across error-message
  wording changes.
- `pytest.ini` gains markers: `config` (serial family; deselect with
  `-m "not config"` in parallel CI lanes) — `slow` already exists per the
  management spec's convention.
- Ordering: `test_validation.py` Lane B cases run last within the family
  (they deliberately leave no gateway running; the finalizer's baseline
  restore covers them).
