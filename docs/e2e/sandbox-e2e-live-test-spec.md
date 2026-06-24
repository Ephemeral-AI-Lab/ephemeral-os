# `sandbox-e2e-live-test` — Live End-to-End Test Runner Spec

This is the implementation spec for the new crate `crates/sandbox-e2e-live-test`.
It is a **black-box live E2E runner**: it drives real Docker-container sandboxes
exclusively through the public `sandbox-cli` → `sandbox-gateway` boundary, runs
multiple sandboxes in parallel with configurable concurrency, monitors
performance through observability, and produces run-scoped, reproducible
artifacts with run-scoped cleanup.

## Ownership Boundary (read first)

This spec keeps a strict black-box boundary, fixed by three product decisions:

1. **Sandbox and image operations are performed by `sandbox-cli`.** The runner
   never injects a `SandboxRuntime` or calls manager/runtime internals. Every
   sandbox lifecycle and runtime command — including `--image` provisioning —
   goes out as a `sandbox-cli` invocation against a gateway socket. The runner's
   only job is to *drive the CLI, capture typed responses, assert, monitor, and
   clean up*.
2. **No manager-side observability sink is required.** Performance monitoring
   uses the existing public `manager get_observability_tree` plus daemon-local
   spans. The runner does **not** depend on any new manager trace store. Manager
   create/destroy phase timing, if needed, is measured by the runner as
   wall-clock around the `sandbox-cli` call, not from an internal span.
3. **Linux + Docker only.** The sandbox container is a Docker container. There is
   no non-Linux code path; off-Linux the runner exits with a clear precondition
   error. Run-scoped cleanup keys on a Docker label in addition to path
   namespacing.

Prerequisite (outside this crate): the runner targets a `sandbox-gateway` that is
wired with the **real Docker-backed runtime** (the shipped `sandbox-gateway`
binary wires `UnconfiguredRuntime`/`UnconfiguredDaemonInstaller` stubs that always
error — `crates/sandbox-gateway/src/gateway/main.rs:94-146`). The runner does not
provide that runtime; it either spawns a gateway binary that has it, or attaches
to an externally started one (`--gateway-socket`). Wiring the Docker runtime into
the gateway is a separate work item; this crate consumes it.

## Live Checkout Anchors

The current checkout has these relevant shapes:

- The crate is **already a workspace member** (`Cargo.toml:17`) but the directory
  `crates/sandbox-e2e-live-test/` is **empty** (no `Cargo.toml`, no `src/`).
  Consequence: a workspace-wide `cargo build` fails today until the manifest
  exists. Scaffolding it is step zero.
- Workspace conventions: `resolver = "2"`, `edition 2021`, `rust-version 1.85`,
  centralized `[workspace.dependencies]` consumed via `dep.workspace = true`
  (`Cargo.toml:2,19-23,25-71`). Available deps: `tokio` "full" (`:46`),
  `tokio-util` (`:47`), `futures-util` (`:50`), `clap` v4 derive (`:42`),
  `anyhow`/`thiserror` (`:39-40`), `serde`/`serde_json` (`:26-27`), `uuid`
  (v4-only, `:38`), `time` (`:36`), `sha2` (`:43`).
- The public CLI client connects to a Unix socket, writes one JSON line,
  half-closes, reads exactly one newline-terminated JSON line back as a
  `serde_json::Value` (`crates/sandbox-gateway/src/cli/client.rs:30-95`).
- CLI surface: `manager <op> [args]` (System scope) and
  `runtime --sandbox-id <id> <op> [args]` (Sandbox scope); scope/id resolution at
  `crates/sandbox-gateway/src/cli/request_builder.rs:74-98`; `--sandbox-id`
  selects scope only and is never placed in `request.args`.
- CLI exit codes: `0` ok, `1` operation/connection failure, `2` usage/build error
  (`crates/sandbox-gateway/src/cli/output.rs:21-23`). Success vs failure is the
  presence of a top-level `error` key in the response
  (`output.rs:266-272`; `crates/sandbox-protocol/src/response.rs:30-49`).
- Manager ops and response shapes:
  `crates/sandbox-manager/src/operation/impls/management/` — `create_sandbox`
  requires `--image` + absolute `--workspace-root`
  (`create_sandbox.rs:6-44`; absolute check `management/mod.rs:63-72`); records
  serialize as `{ id, workspace_root, state, daemon: { socket_path } | null }`
  (`management/mod.rs:88-95`); `get_observability_tree` is bounded fan-out (cap 8
  concurrent, 1500 ms/daemon, traces off by default, `trace_limit ≤ 100`,
  `resource_window_ms ≤ 600000`)
  (`management/get_observability_tree.rs:11,13,88-206`).
- Runtime ops: `crates/sandbox-runtime/operation/src/cli_definition/*` —
  `exec_command`, `write_command_stdin`, `read_command_lines`,
  `create_workspace_session`, `destroy_workspace_session`, `squash`. Command
  yields carry `{ status, exit_code, start_offset, end_offset, total_lines,
  output, command_session_id? }`; `command_session_id` present iff
  `status == "running"`.
- Per-sandbox isolation is inherent: daemon state lives at
  `{runtime_root}/{sandbox_id}/runtime.sock|runtime.pid`
  (`crates/sandbox-manager/src/daemon_install.rs:52-57`), and the observability
  DB path is derived from the socket path:
  `{socket.parent}/observability/observability.sqlite`
  (`crates/sandbox-observability/src/paths.rs:19-35`).
- Sandbox ids are caller-supplied strings validated `[A-Za-z0-9._-]`, non-empty
  (`crates/sandbox-manager/src/model.rs:10-22`).
- Async concurrency idiom in-tree is `Arc<Semaphore>` + `tokio::spawn` (no
  `JoinSet` anywhere): `crates/sandbox-gateway/src/gateway/lifecycle.rs:18,45-56`.
- Existing repeatable-runner precedent: `experiments/sandbox-cli-latency/run.py`
  builds binaries, writes a timestamped run dir with `samples.jsonl` /
  `summary.json`, and records per-invocation `duration_ms`, `returncode`, byte
  counts + sha256.
- Observability records exclude command/env/file contents: schema V5 dropped the
  command text column (`crates/sandbox-observability/src/store.rs:237-241`);
  bounds `MAX_ERROR=4096`, `MAX_ID=256`, `MAX_PATH=4096`
  (`crates/sandbox-observability/src/records.rs:3-11`). CPU/memory samples are
  always `NULL` today (cgroup only `unavailable()` —
  `crates/sandbox-daemon/src/observability/cgroup.rs:12-20`;
  `.../service.rs:283,436`); namespace executions record a single `started_at` at
  `Starting` with no enqueue/`Running` timestamp
  (`crates/sandbox-runtime/operation/src/namespace_execution.rs:177,204`).

## Resulting File And Folder Structure

```text
docs/e2e/
  sandbox-e2e-live-test-spec.md          # this file

crates/sandbox-e2e-live-test/
  Cargo.toml                             # bin-only; step zero unblocks workspace build
  src/
    main.rs                              # #[tokio::main] -> ExitCode; parse -> Runner -> summary
    config.rs                            # RunConfig + clap Args; flag > env > default
    runner.rs                            # build, start/attach gateway, schedule suites, aggregate
    gateway.rs                           # spawn-or-attach gateway on run-scoped socket; shutdown
    scheduler.rs                         # Semaphore(max_parallel) + spawn + join_all
    cli_client.rs                        # invoke sandbox-cli; capture {response, exit, stdio, latency}
    docker.rs                            # run-scoped Docker label + container discovery for cleanup
    suite/
      mod.rs                             # Suite/Step/CliOp/Assertion model + static registry + selection
      lifecycle.rs                       # create_sandbox .. destroy_sandbox skeleton
      exec.rs                            # exec / session / stdin / offset / observability suites
    assertion.rs                         # Assertion kinds + evaluation over captured JSON/stdio/exit
    observe.rs                           # poll get_observability_tree; snapshot to artifacts
    report.rs                            # run-scoped artifact writer (dirs, summary.json, timing.json, jsonl)
    outcome.rs                           # StepOutcome / SuiteOutcome / RunOutcome (serde)
    cleanup.rs                           # RAII run guard: docker rm (label) -> gateway stop -> dirs
```

`Cargo.toml` (bin-only; suites drive the system over the socket, so no
manager/runtime internal crates are needed for the black-box path):

```toml
[package]
name = "sandbox-e2e-live-test"
version.workspace = true
edition.workspace = true
rust-version.workspace = true
license.workspace = true

[[bin]]
name = "sandbox-e2e-live-test"
path = "src/main.rs"

[dependencies]
clap.workspace = true
tokio.workspace = true
tokio-util.workspace = true     # CancellationToken for shutdown
futures-util.workspace = true   # join_all
anyhow.workspace = true
thiserror.workspace = true
serde = { workspace = true }
serde_json.workspace = true     # parse the NDJSON response line
uuid.workspace = true           # internal request correlation only (NOT run_id)
time.workspace = true           # UTC timestamps for run dirs
sha2.workspace = true           # deterministic run-id slug

[lints]
workspace = true
```

Optional: add `sandbox-protocol.workspace = true` only if typed request/response
DTOs are preferred over `serde_json::Value`. Default is `serde_json::Value`, to
stay strictly behind the public socket boundary.

## Runner Architecture

Bin-only workspace tool (same category as `xtask`), **not** a `cargo test`
integration crate: the harness needs first-class run-ids, run-scoped artifact
roots, configurable container concurrency, and a long-lived background gateway —
none of which `cargo test` models, and the privileged Docker setup would
otherwise always-skip or break CI.

Data flow:

```text
main
 └─ RunConfig (flag > env > default); allocate/validate run_id
 └─ RunReport::create(run_root) -> run-manifest.json (git HEAD, config, env, clock)
 └─ Phase A  BUILD (untimed by runner; recorded in timing.build.*)
      cargo build sandbox-gateway/sandbox-cli (+ daemon) --profile package-fast
      [skipped when --prebuilt-bin-dir or --gateway-socket is given]
 └─ Phase B  RUNNER CLOCK STARTS
      gateway.rs: spawn gateway on {run_root}/gateway.sock  (or attach to --gateway-socket)
                  wait_for_path(gateway.sock)                # readiness poll
      scheduler: Semaphore(max_parallel)
        for each selected suite -> acquire permit -> tokio::spawn:
           sandbox_id = {run_id}-s{NN}
           sandbox-cli manager create_sandbox --image I --workspace-root {run_root}/work/{id}
             -> capture id + daemon.socket_path
           sandbox-cli runtime --sandbox-id id <ops...>     # exec/session/stdin/read/squash
           sandbox-cli manager get_observability_tree --sandbox-id id --include-recent-traces 1
           evaluate assertions; write reports/{id}/*
           sandbox-cli manager destroy_sandbox --sandbox-id id
        join_all
      RUNNER CLOCK STOPS
 └─ report: summary.json + timing.json
 └─ cleanup (per policy): docker rm -f (label run_id) -> gateway shutdown -> remove run_root
 └─ ExitCode::SUCCESS iff summary.status == "passed"
```

Every `sandbox-cli` invocation is captured by `cli_client.rs` as a record:
`{ argv, request_json?, response_json, exit_code, stdout, stderr, latency_ms }`.
Response parsing is `serde_json::from_slice::<Value>` on the single response line.

## Config Schema

```rust
struct RunConfig {
    run_id: String,            // --run-id | derived "r{ts}-{sha256(HEAD‖suites‖salt)[..8]}";
                               //   must match SandboxId charset [A-Za-z0-9._-]
    max_parallel: usize,       // --max-parallel | EOS_E2E_MAX_PARALLEL |
                               //   available_parallelism().min(8); 1 = serial
    suites: SuiteSelection,    // All | Names(Vec<String>) | RerunFailedFrom(PathBuf)
    image: String,             // --image (e.g. "ubuntu:24.04"); passed verbatim to create_sandbox
    run_root: PathBuf,         // ${EOS_E2E_RUN_ROOT:-$TMPDIR/eos-e2e}/{run_id}
    gateway_socket: Option<PathBuf>, // attach mode; if None, spawn a gateway
    cargo_profile: String,     // default "package-fast"
    prebuilt_bin_dir: Option<PathBuf>, // skip Phase A; build.*_ms = 0
    cli_timeout: Duration,     // per CLI call, default 30s
    suite_timeout: Duration,   // whole-suite cap, default 300s
    gateway_ready_timeout: Duration, // socket-bind wait, default 5s
    cleanup: CleanupPolicy,    // Always | OnSuccess (default) | Never ; --keep-artifacts
    build: bool,               // default true
}

enum SuiteSelection { All, Names(Vec<String>), RerunFailedFrom(PathBuf) }
enum CleanupPolicy { Always, OnSuccess, Never }
```

Honor existing env names: `SANDBOX_GATEWAY_SOCKET`, `SANDBOX_DEFAULT_ID`
(`crates/sandbox-config/src/configs/cli.rs:6-7`), `CARGO_TARGET_DIR`. Durations in
serialized form are `f64` seconds. Reject a `run_id` containing characters outside
`[A-Za-z0-9._-]` at parse time, because it prefixes sandbox ids.

## Suite Model

A suite = one sandbox lifecycle + an ordered sequence of CLI ops + assertions,
mapping 1:1 to an artifact subdirectory.

```rust
struct Suite { name: String, image: Option<String>, steps: Vec<Step> }
struct Step  { label: String, op: CliOp, assertions: Vec<Assertion> }

enum CliOp {
    Manager { operation: String, argv: Vec<String> },
    Runtime { sandbox: SandboxRef, operation: String, argv: Vec<String> },
}
enum SandboxRef { Literal(String), FromStep { step: String, json_pointer: String } }

enum Assertion {
    ExitCodeIs(i32),
    NoErrorField,                                   // response has no "error" key
    HasErrorKind(String),                           // error.kind == ...
    JsonPointerExists(String),
    JsonPointerEquals { ptr: String, value: serde_json::Value },
    JsonPointerIsType { ptr: String, ty: JsonType },
    OffsetsMonotonic { start: String, end: String, total: String }, // start<=end<=total
    StdoutContains(String),
    LatencyUnderMs(u64),
}
```

Suites are a static registry in `suite/mod.rs`, selected by `RunConfig.suites`.
`SandboxRef::FromStep` lets later steps reference the `id` captured from
`create_sandbox` (json pointer `/id`) or a `command_session_id` from a running
`exec_command`.

## Manager and Runtime CLI Test Matrix

All ops are driven via `sandbox-cli` against the gateway socket (this exercises
both the System and Sandbox routing arms enforced by
`crates/sandbox-manager/src/router/dispatch.rs:8-31`). Assertions read typed JSON
fields, never string formatting.

| #  | Op (scope)                       | Precondition            | Invocation                                                                          | Assertions                                                                                          |
|----|----------------------------------|-------------------------|-------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| M1 | create_sandbox (Sys)             | gateway up; abs ws root | `manager create_sandbox --image I --workspace-root {ws}`                            | no `error`; `/id` non-empty; `/state == "ready"`; `/daemon/socket_path` non-null                    |
| M2 | list_sandboxes (Sys)             | after M1                | `manager list_sandboxes`                                                            | `/sandboxes` array contains `{ id, state: "ready" }`                                                 |
| M3 | inspect_sandbox (Sys)            | after M1                | `manager inspect_sandbox --sandbox-id id`                                           | `/id == id`; `/workspace_root`, `/state`, `/daemon` present                                          |
| M4 | get_observability_tree (Sys)     | after M1                | `manager get_observability_tree --sandbox-id id --include-recent-traces 1 --trace-limit 100` | `/sandboxes/0/sandbox_id == id`; `/availability ∈ {available,partial,unavailable}`; keys `resources,workspaces,recent_traces,errors` present |
| M5 | destroy_sandbox (Sys)            | M1, state Ready         | `manager destroy_sandbox --sandbox-id id`                                           | no `error`; returned `/id == id`; follow-up `inspect_sandbox` returns `error` (removed)              |
| R1 | exec_command one-shot (Sbx)      | Ready sandbox           | `runtime --sandbox-id id exec_command pwd`                                          | `/status == "ok"`; `/exit_code == 0`; no `/command_session_id`                                       |
| R2 | create_workspace_session (Sbx)   | Ready                   | `runtime --sandbox-id id create_workspace_session --profile host_compatible`        | `/workspace_session_id` non-empty; `/profile == "host_compatible"`                                   |
| R3 | exec in session (Sbx)            | after R2 (ws)           | `runtime --sandbox-id id exec_command --workspace-session-id ws "echo hi > f"` then a second exec reading `f` | both `/status == "ok"`; second exec observes the first's write (state persists)            |
| R4 | exec long-running (Sbx)          | Ready                   | `runtime --sandbox-id id exec_command --yield-time-ms 0 cat`                         | `/status == "running"`; capture `/command_session_id`                                                |
| R5 | write_command_stdin (Sbx)        | after R4 (cmd)          | `runtime --sandbox-id id write_command_stdin --command-session-id cmd hello`         | `/start_offset`,`/end_offset` are u64; `/output` reflects echoed input                               |
| R6 | read_command_lines offsets (Sbx) | after R4/R5             | `runtime --sandbox-id id read_command_lines --command-session-id cmd --start-offset 0 --limit 100` | `/command_session_id == cmd`; `start_offset ≤ end_offset ≤ total_lines`; re-read from prior `end_offset` ⇒ non-decreasing `start_offset` |
| R7 | destroy_workspace_session (Sbx)  | R2, no active cmds      | `runtime --sandbox-id id destroy_workspace_session --workspace-session-id ws`        | `/destroyed == true`; if active cmds ⇒ `error.details.active_command_session_ids[]`                  |
| R8 | squash (Sbx)                     | Ready, after mutation   | `runtime --sandbox-id id squash`                                                   | no `error`; `/squashed` is bool; if true `/revision/root_hash` non-empty                             |
| N1 | manager op, Sandbox scope        | gateway up              | force sandbox scope on a manager op                                                  | `error` fault "manager operation requires system scope"                                              |
| N2 | unknown system op                | gateway up              | unknown `manager <op>`                                                              | `error` `unknown_op`                                                                                 |
| N3 | runtime op, no sandbox id        | gateway up              | `runtime <op>` without `--sandbox-id`/default                                       | build error "runtime operations require ..."                                                        |

Ordering constraints the registry must encode: `create_sandbox` precedes all
per-id ops; `destroy_sandbox` is rejected while `Creating`/`Stopping`; the tree
only aggregates Ready sandboxes; session-scoped exec needs a prior
`create_workspace_session`; `write_command_stdin`/`read_command_lines` need a
still-running command's `command_session_id`; `destroy_workspace_session` needs no
active commands; `squash` reports `true` only after committed layer changes.

Assertion strategy: discriminate success via absence of the top-level `error` key;
for expected failures assert `error.kind` and inspect `error.details`. Assert field
presence + type + invariants (monotonic offsets, integer-or-null exit codes,
`command_session_id` present iff `status == "running"`). Round-trip ids
(capture → feed → destroy) rather than matching formats. Assert CLI exit codes
when driving through `sandbox-cli` to also cover the stdout/stderr stream contract.

## Observability and Performance Monitoring

No manager observability sink is introduced. Monitoring is read-only over the
public tree plus optional daemon-side spans.

Primary signal — `observe.rs` polls
`manager get_observability_tree --include-recent-traces 1 --trace-limit 100
--resource-window-ms 60000` periodically **during** the run (recent traces age out
of the bounded window) and writes per-sandbox `observability.json` + `traces.json`
snapshots. The tree exposes, per sandbox: `lifecycle_state`, `availability`,
`resources` (latest + history), `workspaces` (+ active namespace executions), and
bounded `recent_traces`
(`crates/sandbox-manager/src/operation/impls/management/get_observability_tree.rs:88-206`).

Runner-measured timing (no internal spans needed): the runner records wall-clock
around each `sandbox-cli` call, so it already captures `create_ms`,
`daemon_ready_ms` (the cost of `create_sandbox` +
`crates/sandbox-manager/src/operation/impls/management/create_sandbox.rs:62-90`),
per-op latency, and end-to-end suite time. This satisfies "performance, not just
correctness" without a manager trace store.

Optional daemon-side enhancements (separate, additive; not required for the runner
to function — file under follow-up work):

- **P1 cgroup CPU/memory in resource samples.** Owner `sandbox-daemon`
  (`observability/cgroup.rs`, `service.rs`). Fills existing schema columns
  (`cpu_usage_usec`, `memory_current_bytes`, `memory_max_*` — V2 schema), no new
  table. Privacy: numeric counters + sandbox-internal cgroup path only, bounded by
  `MAX_PATH`/`MAX_ERROR`. Test: `resource_samples_for_test` asserts
  `cpu_usage_usec.is_some()` after a command. Especially relevant now that
  sandboxes are Docker containers (cgroup is the per-container pressure signal).
- **P2 namespace queue-wait timing.** Owner `sandbox-runtime/operation`
  (`namespace_execution.rs`) + daemon projection + observability schema V6 (two
  additive columns `enqueued_at_unix_ms`, `running_at_unix_ms` ⇒ derive
  `queue_wait_ms`). Privacy: timestamps only. Test:
  `namespace_execution_traces_for_test` asserts `queue_wait_ms >= 0`. This is the
  one gap that separates queue wait from exec time under parallel load.

The runner consumes P1/P2 automatically once present (they surface in the tree and
in read-only `*_for_test` SQLite reads); their absence only reduces diagnostic
resolution, it does not block the runner.

Deliberately **out of scope**: gateway/manager/forwarding spans and a manager
trace store (decision 2). If forwarding latency must be attributed, the runner
infers it from the gap between its own measured CLI latency and the daemon-side
request trace `duration_ms` exposed in the tree.

## Parallel Execution Model

- Unit of parallelism: one suite owns exactly one sandbox — aligns the test
  boundary with the system's natural per-sandbox-id isolation
  (`crates/sandbox-manager/src/daemon_install.rs:55`).
- Mechanism (repo idiom): `Arc<Semaphore>::new(max_parallel)` →
  `acquire_owned().await` per suite → `tokio::spawn` →
  `futures_util::future::join_all`; permit released on drop, mirroring
  `crates/sandbox-gateway/src/gateway/lifecycle.rs:18,45-56`. No `JoinSet`.
- `max_parallel`: `--max-parallel` > `EOS_E2E_MAX_PARALLEL` >
  `available_parallelism().min(8)`; `N = 1` is deterministic serial mode.
- Isolation boundary: one shared gateway per run (stateless routing front door)
  plus a distinct sandbox id + Docker container per suite. Distinct ids already
  give full socket/pid/observability-DB isolation
  (`crates/sandbox-observability/src/paths.rs:28`), so N gateways are unnecessary.

## Reproducibility, Artifacts, and Cleanup

Reproducibility — one `run_root` whose leaf is `run_id`; all paths derive
deterministically:

| Resource          | Value                                                                    |
|-------------------|--------------------------------------------------------------------------|
| sandbox id        | `{run_id}-s{NN}` (validated `[A-Za-z0-9._-]`)                            |
| workspace root    | `{run_root}/work/{sandbox_id}`                                           |
| daemon socket/pid | `{runtime_root}/{sandbox_id}/runtime.{sock,pid}` (inherent)             |
| observability db  | `{...}/{sandbox_id}/observability/observability.sqlite` (auto-derived)  |
| gateway socket/pid| `{run_root}/gateway.{sock,pid}`                                          |
| report dir        | `{run_root}/reports/{sandbox_id}/`                                       |

`run_id`: `--run-id` verbatim, else
`r{ts}-{sha256(git_HEAD ‖ suite_manifest_hash ‖ EOS_E2E_RUN_SALT)[..8]}` using
`sha2` (timestamp pinnable via `EOS_E2E_RUN_CLOCK` for byte-stable reruns). `uuid`
is deliberately avoided for `run_id` (it is v4-random in-tree); it is used only for
internal request correlation where nondeterminism is harmless.

Artifact tree:

```text
{run_root}/                                  # leaf = run_id
  run-manifest.json   summary.json   timing.json   cleanup-report.json
  gateway.sock  gateway.pid  gateway.log
  work/{sandbox_id}/
  reports/{sandbox_id}/
    stdout.log  stderr.log
    exchange.jsonl        # one {argv,request,response,exit_code,latency_ms} per line
    observability.json    # latest get_observability_tree node for this sandbox
    traces.json           # bounded recent-trace summaries
    result.json           # SuiteOutcome (status, assertions, durations)
```

`summary.json`: `{ schema_version, run_id, git_head, started_at, finished_at,
max_parallel, status (passed|failed|error), counts{total,passed,failed,skipped,
errored}, suites[]{ name, index, sandbox_id, status, duration_ms, workspace_root,
report_dir, assertions{total,failed}, failure }, failed_suites[], artifacts_root }`.

`timing.json` separates build from runner wall time:
`{ schema_version, run_id, build{ gateway_build_ms, cli_build_ms, daemon_build_ms,
cargo_profile, cache_hit }, runner{ wall_ms, gateway_startup_ms,
suite_setup_total_ms, suite_exec_total_ms, teardown_ms, max_parallel_observed,
queue_wait_p50_ms, queue_wait_p95_ms }, per_suite[]{ sandbox_id, queue_wait_ms,
create_ms, daemon_ready_ms, exec_ms, teardown_ms, total_ms } }`.

Build vs runner timing: build binaries in **Phase A** (own `Instant`s →
`timing.build.*`); start the **runner clock only after** binaries exist and the
gateway socket is bound. `--prebuilt-bin-dir` / `--gateway-socket` set
`build.*_ms = 0`, keeping `runner.wall_ms` cache-independent.

Run-scoped cleanup — provably this-run-only, keyed on the intersection of three
tags so it can never touch a sibling run or another agent:

1. **Docker label** — every container created for this run carries
   `eos.e2e.run_id={run_id}` (applied by the create path / passed via image
   config). Teardown enumerates `docker ps -aq --filter
   label=eos.e2e.run_id={run_id}` and `docker rm -f` only those.
2. **Sandbox-id prefix** — every id begins with `{run_id}-s`; manager
   `destroy_sandbox` is issued only for ids with that prefix.
3. **Path namespacing** — every artifact/socket/pid/db/workspace lives under
   `{run_root}`; `remove_dir_all(run_root)` cannot reach a sibling run's tree.

Teardown order (each step idempotent):

1. For each `{run_id}-s{NN}`: `sandbox-cli manager destroy_sandbox` (graceful) →
   fall through to (2) on failure.
2. `docker rm -f` of all containers labeled `eos.e2e.run_id={run_id}` (reaps
   orphans even if the manager store was lost).
3. Gateway shutdown via its `CancellationToken` (the gateway self-removes its
   socket+pid — `crates/sandbox-gateway/src/gateway/lifecycle.rs:90-93`); backstop
   `remove_file` of `{run_root}/gateway.{sock,pid}` tolerating `NotFound`.
4. `remove_dir_all(run_root)` gated by `CleanupPolicy` (default: keep on failure
   for inspection, remove on success; `--keep-artifacts` forces keep).

An RAII drop guard owns `run_root` and the run label so panic / Ctrl-C still tears
down (the gateway already models `ctrl_c` + token —
`crates/sandbox-gateway/src/gateway/main.rs:64-92`). A standalone
`--clean-run {run_id}` repeats steps 1-4 for re-cleanup. `cleanup-report.json`
records which containers, sockets, pid files, and directories were removed.

Linux/Docker precondition: at startup the runner verifies it is on Linux and that
`docker` is reachable; otherwise it exits `2` with a clear precondition message
(no partial setup, no non-Linux path).

## Implementation Phases

- **Phase 0 — Unblock workspace.** Add `Cargo.toml` + `src/main.rs` stub so the
  workspace builds (the member dir is currently empty).
  Verify: `cargo build -p sandbox-e2e-live-test`.
- **Phase 1 — Black-box driver core.** `config.rs`, `cli_client.rs`,
  `gateway.rs` (attach mode first via `--gateway-socket`), one serial
  `lifecycle` suite: `create_sandbox` → `exec_command` → `destroy_sandbox`.
  Verify against a gateway wired with the real Docker runtime:
  `cargo run -p sandbox-e2e-live-test -- --gateway-socket <sock> --max-parallel 1 --suite lifecycle`.
- **Phase 2 — Full CLI matrix + assertions.** Implement M1-M5, R1-R8, N1-N3,
  `assertion.rs`, `exchange.jsonl` capture.
- **Phase 3 — Parallelism, reproducibility, artifacts, cleanup.** `scheduler.rs`,
  deterministic ids/paths, `docker.rs` label cleanup, `report.rs`
  (`summary.json`/`timing.json`), RAII cleanup guard, `--rerun-failed-from`,
  spawn-mode `gateway.rs`.
- **Phase 4 — Observability monitoring.** `observe.rs` polling +
  `observability.json`/`traces.json`; assertions over existing daemon spans;
  consume P1 (cgroup CPU/mem) and P2 (queue-wait) once those land.

## Verification Commands

```sh
cargo build  -p sandbox-e2e-live-test
cargo clippy -p sandbox-e2e-live-test --all-targets -- -D warnings
cargo test   -p sandbox-e2e-live-test          # config/assertion/report unit tests

# PROOF (self-contained; no Makefile in repo). Requires a Linux host with Docker
# and a gateway wired with the real Docker runtime.
cargo run -p sandbox-e2e-live-test --profile package-fast -- \
    --run-id "$(git rev-parse --short HEAD)-proof" \
    --image ubuntu:24.04 \
    --max-parallel 8 \
    --report

# Focused rerun of only failed suites (fresh, independently cleanable namespace):
cargo run -p sandbox-e2e-live-test -- \
    --rerun-failed-from "$TMPDIR/eos-e2e/<run_id>/summary.json" \
    --max-parallel 4
```

## Open Items (carried, not blockers)

1. **Real Docker-runtime gateway wiring** is a prerequisite owned outside this
   crate (the shipped `sandbox-gateway` wires `Unconfigured*` stubs —
   `crates/sandbox-gateway/src/gateway/main.rs:94-146`). The runner attaches via
   `--gateway-socket` or spawns a gateway binary that already has it.
2. **Docker run-label injection point.** `create_sandbox` must stamp
   `eos.e2e.run_id` on the container it provisions for label-based cleanup to
   work. Confirm where the Docker runtime applies labels (image config vs CLI arg)
   so the runner can pass the run id through `sandbox-cli`.
3. **`package-fast` binary discovery.** Confirm the canonical handle to the built
   `sandbox-cli`/`sandbox-gateway` (e.g. `CARGO_BIN_EXE_*` vs
   `target/{profile}/...`) for spawn mode.
