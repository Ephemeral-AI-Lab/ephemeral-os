---
title: Config consolidation — hardcoded policy values into prd.yml
tags:
  - ephemeral-os
  - config
  - implementation-plan
status: implementation_plan
updated: 2026-07-10
---

# Config consolidation — hardcoded policy values into prd.yml

Origin: a full-workspace sweep (2026-07-10) of production `src/` across every
crate, judged against the rubric `config/README.md` already states — *runtime
and test-harness policy belongs in YAML; static contracts belong in Rust code
near their owner*. The sweep found ~40 hardcoded policy values (timeouts, size
caps, concurrency limits, retry cadences, compression levels) with no YAML
path, three of which already have **env-var side channels**
(`EOS_REMOUNT_SWEEP_WIDTH`, `EOS_EXPORT_MAX_DECOMPRESSED_BYTES`,
`EOS_EXPORT_MAX_ENTRIES`) — proof the tuning demand is real while bypassing
the typed, validated, `deny_unknown_fields` config system built to carry it.
The clearest symptom: `config/bench.yml` must smuggle the remount sweep width
into containers via `manager.docker.container_env.EOS_REMOUNT_SWEEP_WIDTH`
and a `__SWEEP_WIDTH__` template substitution, because no
`runtime.layerstack` section exists for the daemon to read.

Two whole surfaces have no YAML section at all: the **gateway** (bind
address, PID path, connection cap live only as CLI-flag defaults) and the
**console** (bind, all five client timeouts hardcoded).

## Goal

Every value an operator or benchmark would plausibly tune loads from the
merged sandbox YAML (`config/prd.yml` + overrides), through a typed schema in
`sandbox-config`, with its default in Rust and validation at load. Values
that are contracts — protocol vocabulary, file modes, kernel constants, wire
formats — stay hardcoded, deliberately and documented as such.

Policy:

```text
Policy goes to YAML; contracts stay in Rust. The dividing question is
"would an operator or a bench sweep ever set this per deployment?" —
not "is this a constant?".
Every new field carries a Rust-side default (serde default fn, the
ObservabilityConfig precedent). prd.yml stays minimal: it lists the
required fields it lists today plus deliberate overrides, never the
full schema. Missing keys inherit; the schema is the maximal shape.
deny_unknown_fields everywhere. Schema lands before any YAML key
appears in prd.yml, bench.yml, or test overrides.
One tuning path. When a value's YAML field lands, its env-var side
channel retires in the same change. Config wins; there is no dual
lookup, no precedence dance between env and YAML for the same knob.
Leaf crates stay config-free. sandbox-protocol, sandbox-observability,
sandbox-runtime/layerstack, and namespace-execution never import
sandbox-config; the daemon (or the operation layer) reads the section
and injects values at construction — the existing ObserverConfig
mapping precedent.
CLI flags outrank YAML, YAML outranks Rust defaults, for surfaces that
have flags (gateway, console). A flag the operator typed is the most
explicit intent expressed.
Units follow the owning section's existing convention: `_s` (f64
seconds) in daemon/runtime/console sections (setup_timeout_s
precedent), `_ms` (u64) where the section already speaks milliseconds
(manager.docker.readiness_timeout_ms precedent), `_bytes` for sizes.
```

## Inventory

Tier ordering is landing order: demonstrated tuning demand first.

### Tier 1 — env side channels and the export/squash bench path

| Source | Value | Target key |
| --- | --- | --- |
| `operation/src/layerstack/service/impls/squash.rs:196` (`DEFAULT_REMOUNT_SWEEP_WIDTH`, env read `:204`) | 4 | `runtime.layerstack.remount_sweep_width` |
| `sandbox-manager/src/export_apply.rs:22` (`MAX_STREAM_BYTES`, no override today) | 2 GiB | `manager.export.max_stream_bytes` |
| `sandbox-manager/src/export_apply.rs:23` (`DEFAULT_MAX_DECOMPRESSED_BYTES`, env `EOS_EXPORT_MAX_DECOMPRESSED_BYTES`) | 8 GiB | `manager.export.max_decompressed_bytes` |
| `sandbox-manager/src/export_apply.rs:24` (`DEFAULT_MAX_APPLY_ENTRIES`, env `EOS_EXPORT_MAX_ENTRIES`) | 1,000,000 | `manager.export.max_apply_entries` |
| `sandbox-daemon/src/http/export.rs:24` (`STREAM_FRAME_BYTES`, A/B-tuned 2026-07-09) | 1 MiB | `daemon.http.export.frame_bytes` |
| `sandbox-daemon/src/http/export.rs:25` (`STREAM_CHANNEL_FRAMES`) | 4 | `daemon.http.export.channel_frames` |

### Tier 2 — daemon service limits and observability

| Source | Value | Target key |
| --- | --- | --- |
| `sandbox-daemon/src/rpc/lifecycle.rs:12` (`MAX_CONCURRENT_CONNECTIONS`) | 256 | `daemon.server.max_concurrent_connections` |
| `sandbox-protocol/src/limits.rs:1` (`MAX_REQUEST_BYTES`) | 16 MiB | `daemon.server.max_request_bytes` (injected) |
| `sandbox-protocol/src/limits.rs:2` (`REQUEST_READ_TIMEOUT_S`) | 30.0 | `daemon.server.request_read_timeout_s` (injected) |
| `sandbox-protocol/src/export_stream.rs:16` (`EXPORT_STREAM_TOKEN_TTL_S`) | 30 | `daemon.http.export.token_ttl_s` (injected) |
| `sandbox-daemon/src/http/forward/proxy.rs:22` (`CONNECT_TIMEOUT`) | 10 s | `daemon.http.forward.connect_timeout_s` |
| `sandbox-daemon/src/http/forward/proxy.rs:23` (`RESPONSE_TIMEOUT`) | 30 s | `daemon.http.forward.response_timeout_s` |
| `sandbox-daemon/src/observability/mod.rs:9` (`MAX_RESOURCE_WINDOW_MS`) | 600,000 | `observability.views.resource_window_ms` |
| `sandbox-daemon/src/observability/view/layerstack.rs:11` (`DEFAULT_LAYER_DELTA_LIMIT`) | 500 | `observability.views.layer_delta_default_limit` |
| `sandbox-daemon/src/observability/view/layerstack.rs:12` (`MAX_LAYER_DELTA_LIMIT`) | 5,000 | `observability.views.layer_delta_max_limit` |
| `sandbox-observability/src/record.rs:19` (`MAX_LINE_BYTES`) | 16 KiB | `observability.max_line_bytes` (injected) |
| `sandbox-observability/src/collect/disk.rs:7-8` (`MAX_DISK_SAMPLE_NODES/DEPTH`) | 1024 / 64 | `observability.sampling.max_walk_nodes` / `.max_walk_depth` (injected) |
| `sandbox-observability/src/collect/layerstack.rs:15-16` (`MAX_LAYER_WALK_NODES/DEPTH`) | 1024 / 64 | same two keys (one budget governs both walks) |

### Tier 3 — runtime operation caps

| Source | Value | Target key |
| --- | --- | --- |
| `operation/src/command/service/core.rs:10` (`MAX_ACTIVE_COMMANDS`) | 256 | `runtime.command.max_active` |
| `operation/src/command/service/core.rs:12` (`COMMAND_ENGINE_SETUP_TIMEOUT_S`) | 30.0 | **collapses into** `runtime.workspace.setup_timeout_s` (decision 6) |
| `operation/src/command/service/read_command_lines.rs:7-8` | 200 / 1000 | `runtime.command.read_lines_default` / `.read_lines_max` |
| `operation/src/file/service/support.rs:12` (`DEFAULT_READ_LIMIT`) | 2000 | `runtime.file.read_lines_default` |
| `operation/src/file/service/support.rs:13` (`MAX_OUTPUT_BYTES`) | 256 KiB | `runtime.file.max_output_bytes` |
| `operation/src/file/service/support.rs:14` (`MAX_EDIT_BYTES`) | 4 MiB | `runtime.file.max_edit_bytes` |
| `operation/src/file/service/impls/list.rs:19` (`MAX_LIST_ENTRIES`) | 2000 | `runtime.file.max_list_entries` |
| `operation/src/layerstack/service/impls/export.rs:46` (`MAX_CHUNK_BYTES`, chunk fallback) | 2 MiB | `runtime.layerstack.export_chunk_bytes` |
| `layerstack/src/stack/projection/emit_stream.rs:18` (`ZSTD_SPOOL_LEVEL`) | 3 | `runtime.layerstack.spool_zstd_level` (injected) |
| `namespace-execution/src/quiesce.rs:13` (`DEFAULT_FREEZE_BUDGET`) | 500 ms | `runtime.namespace_execution.freeze_budget_s` |
| `namespace-execution/src/pty.rs:20` (`STDIN_WRITE_DEADLINE`) | 2 s | `runtime.namespace_execution.stdin_write_deadline_s` |
| `namespace-execution/src/registry.rs:12` (`MAX_TERMINAL_ENTRIES`) | 512 | `runtime.namespace_execution.max_terminal_entries` |
| `namespace-execution/src/transcript_rows.rs:7` (`MAX_TRANSCRIPT_WINDOW_BYTES`) | 1 MiB | `runtime.namespace_execution.max_transcript_window_bytes` |
| `namespace-execution/src/launcher.rs:33` (`MAX_RUNNER_RESULT_BYTES`) | 8 MiB | `runtime.namespace_execution.max_runner_result_bytes` |

### Tier 4 — host-side surfaces

| Source | Value | Target key |
| --- | --- | --- |
| `sandbox-config/src/configs/gateway.rs:5-7` (CLI-flag defaults only) | `127.0.0.1:7878`, `/tmp/eos-gateway.pid`, 256 | `gateway.bind_addr` / `.pid_path` / `.max_concurrent_connections` |
| `sandbox-console/src/config.rs:11-12` | `127.0.0.1:7880`, 120 s | `console.bind_addr` / `.rpc_timeout_s` |
| `sandbox-console/src/health.rs:20` (`PROBE_TIMEOUT`) | 2 s | `console.health_probe_timeout_s` |
| `sandbox-console/src/proxy.rs:22-23` | 10 s / 30 s | `console.proxy_connect_timeout_s` / `.proxy_response_timeout_s` |
| `sandbox-console/src/endpoint.rs:18-19` (`RESOLVE_TIMEOUT`, `ENDPOINT_CACHE_TTL`) | 5 s / 3 s | `console.endpoint_resolve_timeout_s` / `.endpoint_cache_ttl_s` |
| `sandbox-provider-docker/src/engine.rs:29` (`CONNECT_TIMEOUT_SECS`) | 120 s | `manager.docker.connect_timeout_s` |
| `sandbox-provider-docker/src/engine.rs:571-572` (`PORT_PUBLISH_ATTEMPTS/RETRY_DELAY`) | 40 × 50 ms | `manager.docker.port_publish_attempts` / `.port_publish_retry_delay_ms` |
| `sandbox-provider-docker/src/installer.rs:21` (`STOP_TIMEOUT_SECS`) | 5 s | `manager.docker.stop_timeout_s` |
| `sandbox-provider-docker/src/installer.rs:22` (`READINESS_POLL`) | 250 ms | `manager.docker.readiness_poll_ms` (cadence under the existing `readiness_timeout_ms`) |
| `sandbox-manager/src/operation/management/service/impls/observability_snapshot.rs:13-14` | 8 / 1500 ms | `manager.observability_snapshot.max_concurrent_requests` / `.timeout_ms` |
| `sandbox-manager/src/daemon_install.rs:27-30` (local daemon ready/stop) | 2 s / 20 ms ×2 | `manager.local_daemon.ready_timeout_s` / `.stop_timeout_s` (polls stay hardcoded) |

## Resulting `prd.yml` shape

The **maximal** shape after all four tiers. Keys marked `# new` gain schema
fields; the checked-in `prd.yml` itself changes almost nothing (decision 2) —
this is what an override *may* say, not what the baseline *must* say.

```yaml
daemon:
  server:
    socket_path: /eos/runtime/daemon/runtime.sock
    pid_path: /eos/runtime/daemon/runtime.pid
    max_worker_threads: 32
    max_concurrent_connections: 256      # new
    max_request_bytes: 16777216          # new — injected into sandbox-protocol
    request_read_timeout_s: 30.0         # new — injected into sandbox-protocol
  http:                                  # new subsection
    export:
      frame_bytes: 1048576               # new
      channel_frames: 4                  # new
      token_ttl_s: 30                    # new — injected into sandbox-protocol
    forward:
      connect_timeout_s: 10.0            # new
      response_timeout_s: 30.0           # new

runtime:
  workspace:
    layer_stack_root: /eos/layer-stack
    scratch_root: /eos/workspace
    setup_timeout_s: 30
    exit_grace_s: 0.25
    rfc1918_egress: allow
  namespace_execution:
    scratch_root: /eos/namespace_execution
    freeze_budget_s: 0.5                 # new
    stdin_write_deadline_s: 2.0          # new
    max_terminal_entries: 512            # new
    max_transcript_window_bytes: 1048576 # new
    max_runner_result_bytes: 8388608     # new
  command:                               # new subsection
    max_active: 256
    read_lines_default: 200
    read_lines_max: 1000
  file:                                  # new subsection
    read_lines_default: 2000
    max_output_bytes: 262144
    max_edit_bytes: 4194304
    max_list_entries: 2000
  layerstack:                            # new subsection
    remount_sweep_width: 4               # retires EOS_REMOUNT_SWEEP_WIDTH
    export_chunk_bytes: 2097152
    spool_zstd_level: 3

runner:
  mount_mask:
    hidden_paths:
      - /eos

observability:
  enabled: true
  max_file_bytes: 8388608
  max_line_bytes: 16384                  # new
  sampling:                              # new subsection
    max_walk_nodes: 1024
    max_walk_depth: 64
  views:                                 # new subsection
    resource_window_ms: 600000
    layer_delta_default_limit: 500
    layer_delta_max_limit: 5000

gateway:                                 # new section
  bind_addr: 127.0.0.1:7878
  pid_path: /tmp/eos-gateway.pid
  max_concurrent_connections: 256

console:                                 # new section
  bind_addr: 127.0.0.1:7880
  rpc_timeout_s: 120.0
  health_probe_timeout_s: 2.0
  proxy_connect_timeout_s: 10.0
  proxy_response_timeout_s: 30.0
  endpoint_resolve_timeout_s: 5.0
  endpoint_cache_ttl_s: 3.0

manager:
  registry_path: null
  export:                                # new subsection — retires EOS_EXPORT_* envs
    max_stream_bytes: 2147483648
    max_decompressed_bytes: 8589934592
    max_apply_entries: 1000000
  observability_snapshot:                # new subsection
    max_concurrent_requests: 8
    timeout_ms: 1500
  local_daemon:                          # new subsection
    ready_timeout_s: 2.0
    stop_timeout_s: 2.0
  docker:
    privileged: false
    daemon_binary_path: dist/sandbox-daemon-linux-arm64
    daemon_config_yaml_path: config/prd.yml
    connect_timeout_s: 120               # new
    stop_timeout_s: 5                    # new
    readiness_poll_ms: 250               # new
    port_publish_attempts: 40            # new
    port_publish_retry_delay_ms: 50      # new
    container_env:
      HTTP_PROXY: http://http.docker.internal:3128
      HTTPS_PROXY: http://http.docker.internal:3128
      NO_PROXY: localhost,127.0.0.1,::1
```

## Schema and wiring

### Schema ownership

Per `config/README.md`, all typed schema lands in
`crates/sandbox-config/src/configs/`: extended `daemon.rs`, `runtime.rs`,
`manager.rs`, `observability.rs`; `gateway.rs` reworked from bare constants
into a `Deserialize` section struct; new `console.rs`. Every new field uses
`#[serde(default = "...")]` inside `deny_unknown_fields` structs so existing
YAML keeps loading, and new *subsections* (`daemon.http`, `runtime.command`,
`runtime.file`, `runtime.layerstack`, `observability.sampling`,
`observability.views`, `manager.export`, `manager.observability_snapshot`,
`manager.local_daemon`) are `#[serde(default)]` wholesale.

### Consumers

- **daemon** — `serve.rs:110-128` already loads `daemon`, `runtime`,
  `observability`, `manager` sections; `runner/mod.rs:53` loads `runner`. New
  fields ride the same loads. The RPC connection semaphore
  (`rpc/lifecycle.rs`), export stream frames (`http/export.rs`), and forward
  proxy timeouts take the values as constructor parameters instead of module
  consts.
- **sandbox-protocol** — stays config-free (decision 4). `limits.rs` grows a
  `ProtocolLimits { max_request_bytes, request_read_timeout_s }` value type
  with `Default` preserving today's constants; the daemon constructs it from
  `daemon.server` at startup and passes it down the read path. The export
  stream token TTL moves the same way.
- **leaf observability** — the daemon already maps
  `observability.{enabled,max_file_bytes}` into leaf-owned `ObserverConfig`;
  `max_line_bytes` and the two sampling budgets extend that same mapping.
  `sandbox-observability` never imports `sandbox-config` (its charter).
- **runtime operations** — command/file/layerstack service constructors take
  their caps from `RuntimeConfig` at daemon startup, replacing module consts.
  `spool_zstd_level` is passed by the operation layer into
  `emit_stream` (the layerstack crate stays config-free).
- **namespace-execution** — freeze budget, stdin deadline, and the three
  retention caps arrive through the existing service-construction path from
  `RuntimeConfig`.
- **gateway** — already loads the YAML document (`SANDBOX_GATEWAY_CONFIG_YAML`
  / `--config-yaml`) for `manager`; additionally reads the optional `gateway`
  section. Precedence: CLI flag > YAML > Rust default.
- **console** — gains an optional `--config-yaml` (env
  `SANDBOX_CONSOLE_CONFIG_YAML`) reading the same document's `console`
  section. Existing bind env/flag overrides keep outranking YAML.
- **provider-docker / manager** — `DockerRuntimeConfig` gains the five timing
  fields (already plumbed end to end); `manager.export`,
  `manager.observability_snapshot`, `manager.local_daemon` flow through
  `ManagerConfig`.

### Validation (extends `configs/validate.rs`)

| Field(s) | Rule |
| --- | --- |
| `remount_sweep_width`, `channel_frames`, `max_active`, `max_concurrent_*`, `port_publish_attempts`, `max_walk_depth`, `read_lines_*`, `layer_delta_*_limit` | `>= 1` |
| `frame_bytes` | `>= 4096` (sub-page frames are pathological) |
| `max_request_bytes` | `>= 65536` |
| `spool_zstd_level` | `1..=22` |
| `read_lines_default <= read_lines_max`; `layer_delta_default_limit <= layer_delta_max_limit` | cross-field |
| `max_stream_bytes`, `max_decompressed_bytes`, `max_apply_entries`, `max_walk_nodes`, byte caps | `>= 1` |
| all `_s` timeouts | `> 0.0` (`require_f64_gt`), except grace-style fields `>= 0.0` |
| `bind_addr` (gateway, console) | non-empty, parses as socket address |

## Phasing

| Phase | Contents | Why this order |
| --- | --- | --- |
| 1 | `runtime.layerstack`, `manager.export`, `daemon.http.export`; retire the three `EOS_*` env vars; update `bench.yml` template to substitute `remount_sweep_width` directly | Demonstrated tuning demand (squash width sweep, export A/B); kills the `container_env` smuggling |
| 2 | `daemon.server` extensions + `ProtocolLimits` injection, `daemon.http.forward`, `observability.{max_line_bytes,sampling,views}` | Daemon service limits; one injection pattern (protocol, leaf observability) established here |
| 3 | `runtime.command`, `runtime.file`, `runtime.namespace_execution` extensions; collapse `COMMAND_ENGINE_SETUP_TIMEOUT_S` | Mechanical once phase 2's construction-injection pattern exists |
| 4 | `gateway` + `console` sections, `manager.docker` timing fields, `manager.observability_snapshot`, `manager.local_daemon` | Host surfaces; independent of the in-sandbox path |

Each phase is independently shippable: schema + wiring + validation +
`sandbox-config` tests land together; `prd.yml` needs no edit in any phase.

## Expected file/folder structure with LoC change

```text
crates/sandbox-config/src/configs/
  daemon.rs          +90   server extensions, new http subsection
  runtime.rs         +150  command/file/layerstack/namespace_execution
  manager.rs         +90   export, observability_snapshot, local_daemon, docker timing
  observability.rs   +60   max_line_bytes, sampling, views
  gateway.rs         +50   const-only file becomes a Deserialize section
  console.rs         +70   new
  validate.rs        +40   range + cross-field helpers
crates/sandbox-config/tests/                +250  per-section schema/validation
crates/sandbox-protocol/src/limits.rs       +30   ProtocolLimits value type
crates/sandbox-daemon/src/                  +80   serve wiring, semaphore/export/proxy params
crates/sandbox-runtime/operation/src/       +70   service constructor params, consts removed
crates/sandbox-runtime/namespace-execution/ +50   ditto
crates/sandbox-manager/src/                 +60   export_apply env fns removed, sections wired
crates/sandbox-provider-docker/src/         +40   timing fields consumed
crates/sandbox-gateway/src/main.rs          +30   gateway section read, flag precedence
crates/sandbox-console/src/                 +40   console section read
config/prd.yml                              ±0
config/bench.yml                            -3/+2 container_env sweep-width smuggle
                                                  replaced by runtime.layerstack key
```

Net: roughly +1,100 LoC, dominated by schema and its tests; production
call-site diffs are const-to-parameter substitutions.

## Non-goals — stays hardcoded, deliberately

Per the rubric's contract side and the workspace "prefer less" rule; no
operator would tune these, and schema surface is a cost:

```text
Micro poll intervals: 200 µs freeze poll (quiesce.rs:15), 1 ms setup
wait poll (launcher.rs:27), 10 ms kill-grace polls, 20 ms local-daemon
polls, 100 ms PTY reader sleep — implementation cadence, not policy.
IO buffer sizes: 64 KiB drain buffers, 16 KiB HTTP head buffer
(export_changes.rs:31).
Contracts: protocol op names, schema versions, RPC field names,
handshake tokens, file modes (archive.rs 0o755/0o644), HTTP status
codes, cgroup fs root, whiteout/opaque marker names.
CLI catalog argument bounds (cli_definition/file_operations.rs:17
READ_LIMIT_MAX): part of the operation contract surface, not runtime
policy — the service-side cap is the configurable one.
Diagnostics capture (engine.rs:30-31 log tail 200 / 8 KiB): deferred,
revisit if container-failure triage ever needs more.
LATEST_SAMPLE_WINDOW_MS (i64::MAX/4): internal sentinel, not a window.
xtask lint thresholds: code-quality policy, enforced at build, not
runtime.
Bind-loopback constants inside the Docker provider (ENDPOINT_HOST):
correct by construction for published container ports.
```

## Decision log

1. **Rubric restated, not invented.** The policy/contract split is
   `config/README.md`'s own law; this spec only applies it. Anything the
   README names as static (op names, kernel/netlink/nft constants, layout
   names, handshake tokens, package contract defaults) is out of scope
   regardless of how tunable it looks.
2. **Defaults in Rust; `prd.yml` stays minimal.** The `ObservabilityConfig`
   `#[serde(default)]` pattern generalizes. The maximal shape lives in the
   schema and this spec; the baseline file does not enumerate it. A field
   missing from every YAML layer is not "unconfigured" — it is configured to
   the default.
3. **Env side channels retire on landing.** `EOS_REMOUNT_SWEEP_WIDTH`,
   `EOS_EXPORT_MAX_DECOMPRESSED_BYTES`, `EOS_EXPORT_MAX_ENTRIES` are deleted
   in phase 1, same change as their YAML fields. One source of truth; the
   bench template substitutes into the YAML key. No compatibility window —
   both env vars are repo-internal tuning knobs, not published interface.
4. **`sandbox-protocol` stays config-free.** Its limits become a
   `ProtocolLimits` value type defaulting to today's constants, constructed
   by the daemon from `daemon.server`. The protocol crate owning wire
   *vocabulary* must not grow a config dependency; enforcement *thresholds*
   are daemon policy that happens to be checked in protocol code.
5. **Leaf injection over leaf config.** `sandbox-observability`,
   `layerstack`, and `namespace-execution` receive values through existing
   construction paths (the `ObserverConfig` mapping precedent). No leaf crate
   gains a `sandbox-config` edge; crate boundaries in `README.md` hold.
6. **`COMMAND_ENGINE_SETUP_TIMEOUT_S` collapses into
   `runtime.workspace.setup_timeout_s`.** It duplicates the same 30 s value
   guarding the same class of engine setup; two fields for one policy
   violates prefer-less. If command-engine setup ever needs a distinct
   budget, split it then, with evidence.
7. **Gateway and console get top-level sections.** They are separate binaries
   with separate bind addresses — the same reasoning that keeps `daemon` and
   `manager` separate. Flag > YAML > default, because a typed flag is the
   most explicit operator intent.
8. **One sampling budget for both walks.** Disk and layerstack samplers use
   identical 1024/64 budgets today; `observability.sampling` deliberately
   configures them together. Diverging them is a future decision needing a
   measured reason.
9. **Units per section convention.** `_s` f64 where the section already
   speaks seconds, `_ms` u64 where it speaks milliseconds. No section mixes
   both for new fields except `manager.docker`, which inherits its existing
   `readiness_timeout_ms` convention for cadences while Docker-API-facing
   `connect_timeout_s`/`stop_timeout_s` stay in the seconds the API takes.
10. **Phasing by demonstrated demand.** Phase 1 is exactly the set of values
    the squash/export benchmarks already tune through side channels; the
    patterns it establishes (env retirement, bench template substitution)
    and phase 2's injection patterns make phases 3-4 mechanical.
11. **Phase-1 landed drift (2026-07-10).** A concurrent refactor removed the
    daemon HTTP export spool stream (manager pages every export through
    `read_export_chunk` RPC; `sandbox-protocol/src/export_stream.rs` and the
    token/TTL machinery are gone). Consequences: `daemon.http.export`
    (`frame_bytes`, `channel_frames`) is dropped — no consumer — and a schema
    test pins `daemon.http` as an unknown key; phase 2's `token_ttl_s` target
    no longer exists and that work item is void; the transport-shape knob end
    to end is `runtime.layerstack.export_chunk_bytes` (the RPC page size),
    which is what P1-F4 exercises; `manager.export.max_stream_bytes` is
    additionally enforced against the daemon-declared `spool_bytes` before
    the first page. The maximal `prd.yml` shape above still shows the
    pre-drift `daemon.http` subsection — read it minus decision 11.
