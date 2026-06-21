# Sandbox Runtime Cgroup Monitor Design

## Goal

Add a read-only cgroup monitor surface to `sandbox-runtime` so callers can
inspect recent cgroup resource usage for a workspace session and, when needed,
for an individual command inside that session.

The design is intentionally small:

- `inspect_cgroup_monitor`
- `read_cgroup_monitor_samples`

The cgroup monitor surface reports cgroup-derived CPU, memory, IO, pressure,
PID, and state data, plus overlay disk and cleanup state. It does not expose
command contents, stdin, stdout, stderr, environment variables, or raw process
arguments.

## Identity Model

The public identity is session-based.

- Public request key: `workspace_session_id`
- Optional command key: `command_session_id`
- No public `workspace_id`

The cgroup tree should also be session-owned:

```text
/sys/fs/cgroup/eos/sessions/<workspace_session_id>/
/sys/fs/cgroup/eos/sessions/<workspace_session_id>/commands/<command_session_id>/
```

The old shape `/sys/fs/cgroup/eos-iws-<workspace-id>/` is stale and should not be
used in new cgroup monitor paths, schemas, or docs.

## Public Operation Folder

Create a new operation service folder:

```text
crates/sandbox-runtime/operation/src/public/cgroup_monitor/
```

This folder should mirror the existing command operation service shape, but only
for read-only cgroup monitor operations.

Expected layout:

```text
crates/sandbox-runtime/operation/src/public/cgroup_monitor/
├── mod.rs
├── service.rs
└── service/
    ├── contract.rs
    ├── core.rs
    ├── error.rs
    ├── impls/
    │   ├── inspect_cgroup_monitor.rs
    │   ├── mod.rs
    │   └── read_cgroup_monitor_samples.rs
    └── types.rs
```

`mod.rs` owns the runtime family metadata for this lane:

```rust
pub(crate) const CGROUP_MONITOR_FAMILY: OperationFamilySpec = OperationFamilySpec {
    id: "cgroup_monitor",
    title: "Cgroup Monitor",
    summary: "Inspect cgroup resource usage and retained samples.",
    description:
        "Inspect session and command cgroup CPU, memory, IO, pressure, PID, disk, and cleanup state.",
};
```

`service/impls/mod.rs` owns the `SPECS` and `OPERATIONS` slices for the
`cgroup_monitor` family. Each operation implementation module owns its local
`SPEC` and dispatch function. `public/mod.rs` aggregates runtime family,
operation, and dispatch slices from both `command` and `cgroup_monitor`.

## Protocol And Help Metadata

The cgroup monitor operations are runtime operations and must use the
protocol-owned catalog metadata model:

- `OperationCatalog.operation_execution_space`: `runtime`
- family id: `cgroup_monitor`
- family title: `Cgroup Monitor`
- operation specs include `family`, `summary`, `description`, `args`, `cli`,
  and `related`
- catalog decoding must reject missing family references and missing related
  operation references

`inspect_cgroup_monitor` metadata:

```rust
OperationSpec {
    name: "inspect_cgroup_monitor",
    family: "cgroup_monitor",
    summary: "Inspect the latest cgroup monitor state.",
    description: "Inspect the latest retained cgroup monitor state for a workspace session or command session.",
    args: INSPECT_CGROUP_MONITOR_ARGS,
    cli: Some(INSPECT_CGROUP_MONITOR_CLI),
    related: &["read_cgroup_monitor_samples"],
}
```

Arguments:

```text
workspace_session_id  string  required  --workspace-session-id
command_session_id    string  optional  --command-session-id
```

CLI metadata:

```rust
CliSpec {
    path: &["runtime", "inspect_cgroup_monitor"],
    usage: "sandbox-cli runtime inspect_cgroup_monitor --workspace-session-id ID [--command-session-id CMD]",
    examples: &[
        "sandbox-cli runtime inspect_cgroup_monitor --workspace-session-id ws-1",
        "sandbox-cli runtime inspect_cgroup_monitor --workspace-session-id ws-1 --command-session-id cmd-1",
    ],
}
```

`read_cgroup_monitor_samples` metadata:

```rust
OperationSpec {
    name: "read_cgroup_monitor_samples",
    family: "cgroup_monitor",
    summary: "Read retained cgroup monitor samples.",
    description: "Read the retained cgroup monitor sample window for a workspace session or command session.",
    args: READ_CGROUP_MONITOR_SAMPLES_ARGS,
    cli: Some(READ_CGROUP_MONITOR_SAMPLES_CLI),
    related: &["inspect_cgroup_monitor"],
}
```

Arguments:

```text
workspace_session_id  string   required  --workspace-session-id
command_session_id    string   optional  --command-session-id
limit                 integer  optional  --limit
```

CLI metadata:

```rust
CliSpec {
    path: &["runtime", "read_cgroup_monitor_samples"],
    usage: "sandbox-cli runtime read_cgroup_monitor_samples --workspace-session-id ID [--command-session-id CMD] [--limit N]",
    examples: &[
        "sandbox-cli runtime read_cgroup_monitor_samples --workspace-session-id ws-1 --limit 100",
        "sandbox-cli runtime read_cgroup_monitor_samples --workspace-session-id ws-1 --command-session-id cmd-1 --limit 50",
    ],
}
```

Runtime help output is generated from the runtime catalog. Usage and examples
must start with `sandbox-cli runtime`, must not include `--sandbox-id`, and must
not use daemon/manual wording.

## Recent Review Findings To Preserve

- The catalog is not flat. Runtime cgroup monitor operations must be represented
  as a `Cgroup Monitor` family in the protocol-owned operation catalog.
- Operation specs must carry protocol help metadata: `family`, `summary`,
  `description`, `args`, `cli`, and `related`.
- Runtime CLI help and examples must use `sandbox-cli runtime ...`; sandbox
  selection is contextual and must not appear as `--sandbox-id` in operation
  usage/examples.
- `cgroup_monitor` is a peer runtime lane beside `command`, not a command
  submodule and not a separate operation execution space.
- Command child cgroup mechanics stay in `sandbox-runtime-command`, with a
  dedicated `command/src/cgroup.rs` helper. Per-command CPU, memory, IO, and
  cleanup data require command-owned child cgroup creation, final sampling, and
  cleanup.
- `namespace-process` only joins the cgroup path in
  `NamespaceCommandRequest.cgroup_path` and reports join failures; it does not
  own retained samples, monitor loops, directory creation, or cleanup.
- Retention expiry, if implemented, is based on cgroup sample timestamps and
  monitor configuration, not command process-store completion timestamps.

## Public Methods

### `inspect_cgroup_monitor`

Returns the latest cgroup monitor state for a session or command.

Request:

```json
{
  "workspace_session_id": "wss_...",
  "command_session_id": "cmd_..."
}
```

`command_session_id` is optional. When absent, the response is for the session
cgroup and session overlay/disk state.

Response:

```json
{
  "workspace_session_id": "wss_...",
  "command_session_id": "cmd_...",
  "target": {
    "kind": "command",
    "cgroup_path": "/sys/fs/cgroup/eos/sessions/wss_.../commands/cmd_..."
  },
  "monitor": {
    "enabled": true,
    "sample_interval_ms": 1000,
    "retained_samples": 100,
    "last_sampled_at_unix_ms": 1782028800000,
    "read_error_count": 0
  },
  "latest": {
    "sample_kind": "periodic",
    "sampled_at_unix_ms": 1782028800000,
    "interval_ms": 1000,
    "cpu": {
      "usage_usec": 1200000,
      "user_usec": 800000,
      "system_usec": 400000,
      "delta_usage_usec": 25000,
      "percent_over_interval": 2.5,
      "nr_periods": 10,
      "nr_throttled": 1,
      "throttled_usec": 5000
    },
    "memory": {
      "current_bytes": 67108864,
      "peak_bytes": 134217728,
      "anon_bytes": 33554432,
      "file_bytes": 16777216,
      "kernel_bytes": 8388608,
      "events": {
        "low": 0,
        "high": 0,
        "max": 0,
        "oom": 0,
        "oom_kill": 0
      }
    },
    "io": {
      "read_bytes": 1048576,
      "write_bytes": 2097152,
      "read_ops": 128,
      "write_ops": 256,
      "discard_bytes": 0,
      "discard_ops": 0
    },
    "pids": {
      "current": 3,
      "peak": 8,
      "sampled": [12345, 12346, 12347]
    },
    "pressure": {
      "cpu": {
        "some_avg10": 0.0,
        "some_avg60": 0.0,
        "some_avg300": 0.0,
        "some_total_usec": 0
      },
      "memory": {
        "some_avg10": 0.0,
        "some_avg60": 0.0,
        "some_avg300": 0.0,
        "some_total_usec": 0,
        "full_avg10": 0.0,
        "full_avg60": 0.0,
        "full_avg300": 0.0,
        "full_total_usec": 0
      },
      "io": {
        "some_avg10": 0.0,
        "some_avg60": 0.0,
        "some_avg300": 0.0,
        "some_total_usec": 0,
        "full_avg10": 0.0,
        "full_avg60": 0.0,
        "full_avg300": 0.0,
        "full_total_usec": 0
      }
    },
    "disk": {
      "upperdir_bytes": 4096,
      "upperdir_files": 2,
      "upperdir_dirs": 1,
      "upperdir_symlinks": 0,
      "upperdir_scan_truncated": false,
      "upperdir_read_error_count": 0
    },
    "state": {
      "cgroup_exists": true,
      "cgroup_populated": true,
      "frozen": false,
      "read_error": null
    }
  },
  "cleanup": {
    "final_sample_recorded": false,
    "cgroup_exists_after_destroy": null,
    "last_cleanup_error": null
  }
}
```

### `read_cgroup_monitor_samples`

Returns the latest retained samples. This is deliberately trivial in v1: no
offsets, no cursors, and no paging contract.

Request:

```json
{
  "workspace_session_id": "wss_...",
  "command_session_id": "cmd_...",
  "limit": 100
}
```

Rules:

- `command_session_id` is optional.
- `limit` is optional and defaults to the configured retained sample count.
- The response returns newest retained samples in chronological order.
- If paging is needed later, prefer a timestamp request such as
  `before_sampled_at_unix_ms`, not start/end offsets.

Response:

```json
{
  "workspace_session_id": "wss_...",
  "command_session_id": "cmd_...",
  "target": {
    "kind": "command",
    "cgroup_path": "/sys/fs/cgroup/eos/sessions/wss_.../commands/cmd_..."
  },
  "samples": [
    {
      "sample_kind": "periodic",
      "sampled_at_unix_ms": 1782028800000,
      "interval_ms": 1000,
      "cpu": {
        "usage_usec": 1200000,
        "user_usec": 800000,
        "system_usec": 400000,
        "delta_usage_usec": 25000,
        "percent_over_interval": 2.5,
        "nr_periods": 10,
        "nr_throttled": 1,
        "throttled_usec": 5000
      },
      "memory": {
        "current_bytes": 67108864,
        "peak_bytes": 134217728,
        "anon_bytes": 33554432,
        "file_bytes": 16777216,
        "kernel_bytes": 8388608,
        "events": {
          "low": 0,
          "high": 0,
          "max": 0,
          "oom": 0,
          "oom_kill": 0
        }
      },
      "io": {
        "read_bytes": 1048576,
        "write_bytes": 2097152,
        "read_ops": 128,
        "write_ops": 256,
        "discard_bytes": 0,
        "discard_ops": 0
      },
      "pids": {
        "current": 3,
        "peak": 8,
        "sampled": [12345, 12346, 12347]
      },
      "pressure": {
        "cpu": {
          "some_avg10": 0.0,
          "some_avg60": 0.0,
          "some_avg300": 0.0,
          "some_total_usec": 0
        },
        "memory": {
          "some_avg10": 0.0,
          "some_avg60": 0.0,
          "some_avg300": 0.0,
          "some_total_usec": 0,
          "full_avg10": 0.0,
          "full_avg60": 0.0,
          "full_avg300": 0.0,
          "full_total_usec": 0
        },
        "io": {
          "some_avg10": 0.0,
          "some_avg60": 0.0,
          "some_avg300": 0.0,
          "some_total_usec": 0,
          "full_avg10": 0.0,
          "full_avg60": 0.0,
          "full_avg300": 0.0,
          "full_total_usec": 0
        }
      },
      "disk": {
        "upperdir_bytes": 4096,
        "upperdir_files": 2,
        "upperdir_dirs": 1,
        "upperdir_symlinks": 0,
        "upperdir_scan_truncated": false,
        "upperdir_read_error_count": 0
      },
      "state": {
        "cgroup_exists": true,
        "cgroup_populated": true,
        "frozen": false,
        "read_error": null
      }
    }
  ]
}
```

## Sample Kinds

```text
periodic
command_final
session_final
cleanup
```

`periodic` samples are emitted by the monitor loop. Final samples are emitted at
command/session teardown. `cleanup` captures post-destroy cgroup cleanup state.

## Runtime Architecture

The public cgroup monitor service should stay thin. It should validate
operation inputs, look up retained cgroup monitor state, and serialize the
response. It should not own cgroup parsing, process lifecycle, or monitor
scheduling.

Recommended ownership:

- `workspace` owns session cgroup path construction and session-level sampling.
- `command` owns command child cgroup creation, launch request wiring, final
  command cgroup sampling, and best-effort command cgroup cleanup.
- `operation/public/command` passes the workspace session and command session
  context into the command crate, then records the returned command cgroup
  monitor target in the process store or cgroup monitor registry.
- `operation/public/cgroup_monitor` owns only the public read operations.
- `operation/internal/services.rs` wires `CgroupMonitorOperationService` into
  the runtime aggregate.
- `namespace-process` only joins the requested cgroup and reports join failures.

The operation catalog has first-class family metadata. Add `Cgroup Monitor` as a
peer runtime family beside `Command`, not as another command operation group and
not as a separate execution space. The runtime catalog should expose:

```text
operation_execution_space: runtime
families:
  command
  cgroup_monitor
operations:
  exec_command
  write_command_stdin
  poll_command
  read_command_lines
  cancel_command
  inspect_cgroup_monitor
  read_cgroup_monitor_samples
```

Expose cgroup-monitor-specific filtering only if a concrete caller needs it
later. The v1 catalog should rely on normal family grouping and related
operation metadata.

## Command Cgroup Ownership

Command cgroup mechanics should live in `sandbox-runtime-command`, not in
`operation/public/command`.

The command crate already owns:

- command artifact directory creation
- `CommandProcessSpawn::prepare`
- `CommandProcess::spawn`
- `NamespaceCommandRequest` construction
- `CommandProcess::cancel_process`
- `CommandProcess::take_exit`

That is the right boundary for the command child cgroup. The operation layer
should not manually create a command cgroup and then pass it down as loose
launch state. Instead, it should pass enough context for the command crate to
derive the child cgroup from the session cgroup already carried by
`WorkspaceEntry`.

Required command-side shape for per-command monitoring:

```text
crates/sandbox-runtime/command/src/cgroup.rs
crates/sandbox-runtime/command/src/process.rs
```

`cgroup.rs` is the expected home for command child cgroup lifecycle helpers:

- derive `<session-cgroup>/commands/<command_session_id>/`;
- create the command cgroup during command preparation;
- expose the selected command cgroup path to `process.rs`;
- build or carry the `command_final` sample before cleanup;
- remove the command cgroup after the runner has exited and the cgroup is empty.

Keeping these helpers in a separate command module prevents `process.rs` from
absorbing cgroup v2 path, sampling, and cleanup mechanics while preserving the
correct owner: the command process lifecycle. Inlining this code into
`operation/public/command` would make operation dispatch own process resources;
putting it in `workspace` would make session setup own per-command lifecycle;
putting it in `namespace-process` would put retained state and cleanup in the
short-lived runner after it has joined the target cgroup.

`CommandProcessSpawn::prepare` should create:

```text
<session-cgroup>/commands/<command_session_id>/
```

and store the command cgroup path in the process runtime. `CommandProcess::spawn`
should put that child path into `NamespaceCommandRequest.cgroup_path`, so the
existing namespace runner joins the command cgroup instead of the session cgroup.

`CommandProcess::take_exit` should record or build the `command_final` cgroup
sample before cleanup. `CommandProcess::cancel_process` should only request
termination; it should not remove the cgroup or emit the final sample because
the process can still be alive immediately after cancellation. The final sample
belongs to the exit/finalization path.

The public cgroup monitor registry can still be above the command crate. The
command crate should expose the command cgroup path and final sample through
process metadata or `CommandProcessExit`; the operation/cgroup_monitor layer can
retain those samples under `(workspace_session_id, command_session_id)` without
making `sandbox-runtime-command` depend on public operation APIs.

## Namespace Runner Placement

`sandbox-runtime-namespace-process/src/runner/` is not the right home for the
main cgroup monitor logic.

The runner owns short-lived child-side mechanics:

- `runner/protocol.rs` carries `NamespaceCommandRequest.cgroup_path`.
- `runner/setns.rs` writes the current runner PID into `cgroup.procs`.
- `runner/setns.rs` joins user/mount/pid/network namespaces.
- `runner/shell_exec.rs` executes the shell command and returns the command
  result payload.
- `runner/shell_exec/wait.rs` waits for the command process group to drain.

That makes the runner the correct place for cgroup join error reporting. It is
not a good place for retained cgroup monitor state because it has no workspace
session registry, no command registry, no public operation context, and no
lifetime after the command runner exits.

It is also not a good place for cleanup. The runner is itself inside the command
cgroup after `join_cgroup`, so it cannot remove that cgroup before it exits. The
daemon-side command process owner must do command cgroup cleanup after the
runner has exited and final samples have been retained.

The runner may optionally include tiny one-shot telemetry in `RunResult.payload`
for debugging, such as cgroup join duration or a join failure detail. It should
not own:

- periodic cgroup monitor sampling
- retained sample ring buffers
- `inspect_cgroup_monitor` or `read_cgroup_monitor_samples`
- session cgroup path construction
- command cgroup directory creation
- command/session cgroup cleanup
- overlay upperdir disk scans

Keep the reusable cgroup v2 parsers in `workspace/src/namespace/cgroup_monitor.rs`
or another daemon-side shared module. Let the runner consume only the selected
`cgroup_path` and report whether it joined successfully.

## Cgroup Lifecycle

Session creation:

1. Create `/sys/fs/cgroup/eos/sessions/<workspace_session_id>/`.
2. Attach the namespace holder process by writing its PID to `cgroup.procs`.
3. Register the session cgroup with the cgroup monitor registry.
4. Start or attach the monitor loop.

Command execution:

1. `operation/public/command` resolves the workspace session and allocates the
   `command_session_id`.
2. `sandbox-runtime-command` creates
   `/sys/fs/cgroup/eos/sessions/<workspace_session_id>/commands/<command_session_id>/`
   from the session cgroup path in `WorkspaceEntry`.
3. `sandbox-runtime-command` passes the command cgroup path through the existing
   namespace command request path.
4. The namespace runner writes the runner PID to that cgroup's
   `cgroup.procs`.
5. `operation/public/command` records the returned command target with the
   cgroup monitor registry or process store.

Command completion:

1. `CommandProcess::take_exit` builds the `command_final` cgroup sample before
   command cgroup cleanup.
2. `operation/public/command/service/finalize.rs` retains that final sample
   under `(workspace_session_id, command_session_id)`.
3. Stop tracking the command target for periodic sampling.
4. Retain its final sample and latest retained samples until session cleanup or
   retention expiry.
5. `sandbox-runtime-command` removes the command cgroup after it is empty.

Command cancellation:

1. `cancel_command` still requests cancellation through
   `CommandProcess::cancel_process`.
2. The command cgroup remains in place while the process is terminating.
3. The final command cgroup monitor sample is emitted from the later
   exit/finalization path, not from the cancellation request path.

Session destruction:

1. Record `session_final`.
2. Stop periodic session sampling.
3. Remove command cgroups first.
4. Remove the session cgroup.
5. Record `cleanup` with `cgroup_exists_after_destroy`.

`cgroup.procs` is a virtual kernel file. It does not accumulate stale text rows.
After all processes leave, reads should be empty. A cgroup directory can only be
removed once it has no live member processes and no child cgroups.

## Error Handling

Cgroup monitor reads should be best-effort.

- Missing optional cgroup files should produce a partial sample with
  `state.read_error`, not fail the whole response.
- Missing session target should return a normal operation fault.
- Missing command target under an existing session should return a normal
  operation fault.
- Permission failures should be visible in `state.read_error` and monitor
  `read_error_count`.
- Disk scans should use existing truncation/read-error fields instead of making
  cgroup monitor calls fail on large or partially unreadable overlay trees.

## Retention

Suggested defaults:

```yaml
cgroup_monitor:
  enabled: true
  sample_interval_ms: 1000
  retained_samples_per_target: 100
  include_pids: true
  include_pressure: true
  include_disk: true
```

The retention buffer should be per target:

- one ring buffer for the session target
- one ring buffer for each command target

This keeps `read_cgroup_monitor_samples` cheap and avoids pagination in v1.
Retention expiry, if added, should be based on sample timestamps and cgroup
monitor retention configuration. Do not couple expiry to command process-store
record timestamps.

## Impacted And Added File Shape

Expected production shape:

```text
crates/sandbox-runtime/operation/src/
├── lib.rs                                      # export cgroup monitor public types if needed
├── operation.rs                                # existing OperationEntry wiring
├── internal/
│   └── services.rs                             # add Arc<CgroupMonitorOperationService>
└── public/
    ├── mod.rs                                  # aggregate command + cgroup_monitor families/specs/entries
    ├── command/
    │   └── service/
    │       ├── finalize.rs                     # retain command_final samples during finalization
    │       ├── process_store.rs                # store command cgroup monitor target/final samples if registry is not separate
    │       └── impls/
    │           ├── exec_command.rs             # pass command/session context and register command target
    │           └── cancel_command.rs           # keep final sampling exit-driven
    └── cgroup_monitor/
        ├── mod.rs                              # owns CGROUP_MONITOR_FAMILY
        ├── service.rs                          # public service facade
        └── service/
            ├── contract.rs                     # service trait/input contract
            ├── core.rs                         # service construction/shared helpers
            ├── error.rs                        # service error mapping
            ├── types.rs                        # request/response/sample DTOs
            └── impls/
                ├── mod.rs                      # owns SPECS + OPERATIONS slices
                ├── inspect_cgroup_monitor.rs   # SPEC + dispatch + implementation
                └── read_cgroup_monitor_samples.rs

crates/sandbox-runtime/workspace/src/
├── model.rs                                    # session monitor target metadata
├── lifecycle/
│   ├── create.rs                               # register session monitor target
│   └── destroy.rs                              # session_final + cleanup samples
└── namespace/
    ├── cgroup.rs                               # session-owned cgroup path construction
    ├── cgroup_monitor.rs                       # cgroup v2 parsers/sample builder
    └── mod.rs                                  # internal exports

crates/sandbox-runtime/command/src/
├── cgroup.rs                                   # command child cgroup helper/final sample/cleanup
├── process.rs                                  # store command cgroup handle and pass request cgroup_path
└── lib.rs                                      # exports needed by operation tests/callers

crates/sandbox-runtime/namespace-process/src/runner/
├── protocol.rs                                 # existing NamespaceCommandRequest.cgroup_path
└── setns.rs                                    # runner joins requested cgroup path

crates/sandbox-runtime/config/src/configs/
└── daemon.rs                                   # cgroup_monitor config

config/
└── prd.yml                                     # production cgroup monitor defaults
```

Expected test shape:

```text
crates/sandbox-runtime/operation/tests/
├── service_graph.rs                            # runtime catalog has Command + Cgroup Monitor families
├── cgroup_monitor_operations.rs                # operation contracts/response shapes/help metadata
└── support/
    └── mod.rs

crates/sandbox-runtime/workspace/tests/unit/
└── cgroup_monitor.rs                           # parser, partial/malformed files, lifecycle cleanup

crates/sandbox-runtime/command/tests/unit/
└── process.rs                                  # child cgroup path, request wiring, final sample, cleanup

crates/sandbox-runtime/namespace-process/tests/unit/runner/
└── setns.rs                                    # keeps joining NamespaceCommandRequest.cgroup_path

crates/sandbox-gateway/tests/
└── gateway_cli.rs                              # runtime CLI maps cgroup monitor args without --sandbox-id
```

## Change Map and LOC Estimate

Production estimate: 1,055 to 1,900 added LOC.

Test estimate: 760 to 1,340 added LOC.

Total estimate: 1,815 to 3,240 added LOC.

| Path | Change | LOC |
| --- | --- | ---: |
| `crates/sandbox-runtime/operation/src/public/cgroup_monitor/` | New public cgroup monitor runtime family, operation service, metadata, contracts, types, and operation impls. | +320 to +480 |
| `crates/sandbox-runtime/operation/src/public/mod.rs` | Aggregate cgroup monitor family/spec/dispatch entries beside command. | +20 to +40 |
| `crates/sandbox-runtime/operation/src/internal/services.rs` | Add `cgroup_monitor: Arc<CgroupMonitorOperationService>` and construction wiring. | +35 to +70 |
| `crates/sandbox-runtime/operation/src/lib.rs` | Export cgroup monitor public module/types if needed by tests or callers. | +5 to +20 |
| `crates/sandbox-runtime/workspace/src/namespace/cgroup.rs` | Replace stale workspace-id path construction with session-owned cgroup paths. | +120 to +200 |
| `crates/sandbox-runtime/workspace/src/namespace/cgroup_monitor.rs` | New cgroup v2 parsers and sample builder for cpu, memory, io, pids, pressure, and state. | +180 to +280 |
| `crates/sandbox-runtime/workspace/src/namespace/mod.rs` | Export cgroup monitor helpers internally. | +5 to +15 |
| `crates/sandbox-runtime/workspace/src/model.rs` | Add cgroup monitor target metadata and retained sample handles to session state. | +40 to +80 |
| `crates/sandbox-runtime/workspace/src/lifecycle/create.rs` | Register session cgroup monitor target during workspace session creation. | +20 to +50 |
| `crates/sandbox-runtime/workspace/src/lifecycle/destroy.rs` | Record final/cleanup samples and remove remaining session-owned cgroups in order. | +40 to +80 |
| `crates/sandbox-runtime/operation/src/public/command/service/impls/exec_command.rs` | Pass command/session context and retain returned command cgroup monitor target. | +20 to +40 |
| `crates/sandbox-runtime/operation/src/public/command/service/finalize.rs` | Retain command final sample returned by the command process. | +15 to +35 |
| `crates/sandbox-runtime/operation/src/public/command/service/impls/cancel_command.rs` | Do not make cancellation emit monitor samples; final sample remains exit-driven. | +0 to +15 |
| `crates/sandbox-runtime/command/src/cgroup.rs` | New command cgroup helper: create child cgroup, expose path, build final sample, cleanup. | +120 to +220 |
| `crates/sandbox-runtime/command/src/process.rs` | Store command cgroup handle, pass child cgroup path into namespace request, return final cgroup monitor sample on exit. | +70 to +130 |
| `crates/sandbox-runtime/command/src/lib.rs` | Export command cgroup/monitor types needed by operation tests or callers. | +5 to +15 |
| `crates/sandbox-runtime/namespace-process/src/runner/protocol.rs` | No shape change expected; `NamespaceCommandRequest.cgroup_path` already exists. | +0 to +10 |
| `crates/sandbox-runtime/namespace-process/src/runner/setns.rs` | Existing join path should now receive the command cgroup; only error labeling/tests may change. | +0 to +15 |
| `crates/sandbox-runtime/config/src/configs/daemon.rs` | Add cgroup monitor config. | +30 to +70 |
| `config/prd.yml` | Add production cgroup monitor defaults. | +10 to +25 |
| `crates/sandbox-runtime/operation/tests/service_graph.rs` | Update catalog assertions to include the `Cgroup Monitor` family, operation metadata, and runtime CLI constraints. | +40 to +90 |
| `crates/sandbox-runtime/operation/tests/cgroup_monitor_operations.rs` | New operation contract, response-shape, related-operation, and help metadata tests. | +220 to +390 |
| `crates/sandbox-runtime/workspace/tests/unit/cgroup_monitor.rs` | New parser, sample, lifecycle, and cleanup tests. | +250 to +450 |
| `crates/sandbox-runtime/command/tests/` | Command child cgroup creation, namespace request path, cancel behavior, cleanup, and final sample coverage. | +180 to +320 |
| `crates/sandbox-runtime/namespace-process/tests/` | Runner keeps joining `NamespaceCommandRequest.cgroup_path`. | +40 to +80 |
| `crates/sandbox-gateway/tests/gateway_cli.rs` | Runtime CLI maps cgroup monitor flags through catalog metadata without `--sandbox-id` in operation usage. | +30 to +70 |
| `docs/refactoring/sandbox-runtime-cgroup-monitor-design.md` | This spec. | +350 to +500 |

The estimate is higher than a pure operation-service addition because command
cgroup monitoring requires command child cgroups, final samples, cleanup
ordering, cgroup v2 parsers, and command-crate tests around process lifecycle
behavior. A session-only cgroup monitor surface would be materially smaller, but
would not answer per-command CPU, memory, IO, and cleanup questions.

## Verification Plan

Focused checks:

```text
cargo test -p sandbox-runtime cgroup_monitor
cargo test -p sandbox-runtime service_graph
cargo test -p sandbox-runtime-workspace cgroup
cargo test -p sandbox-runtime-command process
cargo test -p sandbox-runtime-namespace-process runner
cargo test -p sandbox-gateway gateway_cli
```

Formatting:

```text
cargo fmt --check
```

Live cgroup checks should be Linux-only and gated, because macOS development
hosts cannot validate real `/sys/fs/cgroup` behavior directly.

## Acceptance Criteria

- Public cgroup monitor operations are limited to `inspect_cgroup_monitor` and
  `read_cgroup_monitor_samples`.
- Public schemas use `workspace_session_id`, not `workspace_id`.
- Session and command cgroup paths are session-owned.
- Runtime catalog includes both the `Command` and `Cgroup Monitor` families.
- Cgroup monitor operation specs use family id `cgroup_monitor`.
- Cgroup monitor operation specs include summaries, descriptions, CLI metadata,
  and valid `related` operation references.
- Runtime cgroup monitor CLI usage and examples start with
  `sandbox-cli runtime` and do not include `--sandbox-id`.
- Runtime help renders the cgroup monitor family and detailed pages for both
  operations.
- `read_cgroup_monitor_samples` has no offsets, cursors, or paging contract in
  v1.
- Final samples are recorded for completed and canceled commands.
- Session cleanup records whether cgroups still exist after destroy.
- Cgroup monitor samples never include command text, env, stdin, stdout, or stderr.
- Unit tests cover cgroup parser behavior for missing, partial, and malformed
  cgroup files.
- Operation catalog tests prove the new cgroup monitor operations are externally
  visible under the runtime execution space.
