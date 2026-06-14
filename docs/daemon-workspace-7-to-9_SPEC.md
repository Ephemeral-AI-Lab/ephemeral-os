# Daemon Workspace 7-to-9 Remediation Spec

Status: Draft
Date: 2026-06-14
Owner: `crates/daemon`
Source review: daemon workspace hard-core review on 2026-06-14
Target score: move the daemon workspace from roughly **7/10** to **9/10**.

This is a remediation spec, not a Rust patch. It converts the review findings
into small phases with concrete evidence, acceptance criteria, and verification
gates.

## 1. Scope

In scope:

- `crates/daemon/{command,config,core,eosd,layerstack,namespace,operation,overlay,plugin,workspace}`
- Isolated workspace filesystem safety, holder lifecycle, scratch cleanup, and
  crash recovery.
- Command process lifecycle and orphan recovery.
- Plugin LSP process correctness and fast unit coverage.
- Daemon operation contract fixtures and `xtask check-contract` coverage.
- Crate-boundary cleanup only where the current graph creates concrete
  dependency drag.

Out of scope:

- Host/gateway redesign.
- Broad package renames.
- Replacing Linux namespaces, overlayfs, or Docker as the sandbox substrate.
- Manually editing generated docs or immutable golden fixtures.
- Style-only refactors before correctness and verification gaps are closed.

## 2. Current Baseline

The current daemon workspace is healthy enough to repair in place, but not
9/10. The score is held down by concrete safety and verification gaps, not by
basic compilation health.

Observed baseline:

| Gate | Result |
|---|---|
| `cargo metadata --format-version 1 --no-deps` | Green for daemon packages. |
| `cargo machete --with-metadata` | Green; no unused dependencies reported. |
| Focused daemon package tests | Green for `daemon`, `operation`, `plugin`, `workspace`, `namespace`, `command`, `layerstack`, `overlay`. |
| `cargo run -p xtask -- check-contract` | Green. |
| `cargo test -p plugin --locked -- --list` | 0 unit tests and 0 doc tests. |
| `cargo clippy -p daemon --all-targets --locked -- -D warnings` | Red on `clippy::type_complexity` in `plugin/src/pyright_lsp/process.rs`. |

The target score is 9/10 only after the P1 correctness gaps are closed, the
clippy gate is green, high-risk plugin and recovery paths have fast tests, and
the largest boundary leaks are reduced.

## 3. Score Definition

Target 9/10 means:

1. No open P0/P1 finding from the June 14 daemon review.
2. All P2 findings either fixed or explicitly downgraded by new tests and
   documentation.
3. `cargo clippy -p daemon --all-targets --locked -- -D warnings` is green.
4. `xtask check-contract` covers every owner-local daemon contract fixture,
   including operation fixtures.
5. `plugin` has fast deterministic tests for LSP pending-request failure,
   response parsing, and path/projection behavior.
6. No crate imports a much larger daemon implementation graph only to use wire
   DTOs.
7. Large-file splits are behavior-preserving and follow ownership boundaries,
   not line-count targets.

Score caps if work is partial:

| Remaining issue | Score cap |
|---|---:|
| Any P1 safety/recovery finding remains open | 7.5 |
| Daemon clippy gate remains red | 8.0 |
| `plugin` still has zero fast tests | 8.0 |
| Operation fixtures remain outside `check-contract` | 8.5 |
| `plugin -> operation -> command/layerstack/namespace/overlay/workspace` remains for DTO use only | 8.7 |
| God-file splits land before behavior fixes | no score lift |

## 4. Finding Evidence

| ID | Priority | Evidence | Required outcome |
|---|---|---|---|
| F1 | P1 | `crates/daemon/operation/src/file/isolated.rs:68-71` prepares a target then writes with `std::fs::write`; pre-checks are at `:188-209` and `:235-243`; tests at `:256-296` cover pre-existing symlinks, not races. | Isolated writes cannot escape `upperdir` through a symlink time-of-check/time-of-use window. |
| F2 | P1 | `crates/daemon/workspace/src/isolated_workspace/manager/lifecycle.rs:212-216` and `:254` ignore `persist_handles`; recovery only kills persisted holder pids at `manager/recovery.rs:45-52`; holder pauses forever at `namespace/src/holder/mod.rs:113-121`; persistence writes temp+rename without fsync at `manager/recovery.rs:197-210`. | Open/exit cannot hide persistence failure, and crash recovery has durable holder metadata. |
| F3 | P1 | `crates/daemon/workspace/src/isolated_workspace/manager/recovery.rs:133-147` deletes every directory under `scratch_root` except `manager.json`; config only requires an absolute path at `config/src/configs/isolated_workspace.rs:66`. | Recovery reaps only daemon-owned scratch directories. |
| F4 | P1 | `plugin/src/pyright_lsp/process.rs:26`, `:377`, `:400`, `:475` trigger `clippy::type_complexity`; `docs/sandbox-architecture-7-to-9_SPEC.md:272-277` names clippy with `-D warnings` as a full gate. | Daemon clippy is green without suppressing the lint. |
| F5 | P2 | Command metadata is written before spawn at `operation/src/command/prepare.rs:108-121`; child spawn occurs at `command/src/pty.rs:287-298`; process metadata is written after spawn at `command/src/process.rs:189-193` and `:420-423`; recovery kills only if `process.json` exists at `operation/src/command/service/lifecycle.rs:191-199` and `:351-367`, then removes the dir at `:224`. | A daemon crash after spawn cannot leave an untracked command process group. |
| F6 | P2 | Contract root says owner-local operation fixtures are binding artifacts at `CONTRACT.md:6-10` and immutable at `:120-125`; `operation/src/command/contract.rs:450-455` asserts an operation fixture; `xtask/src/main.rs:211-230` lists daemon, layerstack, host, and gateway conformance suites, but not operation. | `check-contract` runs the operation fixture assertions. |
| F7 | P2 | `cargo tree -p plugin --edges normal --depth 2` shows `plugin -> operation -> command/layerstack/namespace/overlay/workspace`; `plugin/Cargo.toml` depends on `operation` and `workspace`. | `plugin` consumes narrow contract/runtime types, not the whole operation implementation graph. |
| F8 | P2 | `overlay/src/path_change.rs:286-292` converts symlink targets with `to_string_lossy`; `:307-311` does the same for relative path components; `layerstack/src/model.rs:21-47` stores `LayerPath` as `String`. | Non-UTF-8 overlay paths are rejected explicitly or represented losslessly; no silent lossy identity. |
| F9 | P3 | `workspace/src/isolated_workspace/namespace/runner_launcher.rs:24` defines `NsRunnerLauncher`; live runtime launches `std::env::current_exe()` directly at `namespace/mod.rs:97` and `:499`; `plugin/src/state.rs:24-28` accepts but ignores the launcher. | Remove or wire the launcher abstraction; do not keep ignored runtime plumbing. |

## 5. Phase Plan

### Phase 0 - Freeze The Baseline

Intent: make later fixes easy to score.

Tasks:

- Capture a fresh baseline with an isolated target dir:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo metadata --format-version 1 --no-deps
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo machete --with-metadata
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test --no-run -p daemon -p operation -p plugin -p workspace -p namespace -p command -p layerstack -p overlay -p eosd
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p daemon -p operation -p plugin -p workspace -p namespace -p command -p layerstack -p overlay
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo run -p xtask -- check-contract
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo clippy -p daemon --all-targets --locked -- -D warnings
```

- Record expected red/green status in the implementation PR.
- Do not update fixtures or generated docs in this phase.

Acceptance:

- The only expected red gate is daemon clippy.
- Any additional failure blocks Phase 1 until classified.

### Phase 1 - Close P1 Safety And Recovery Gaps

#### 1A. Harden isolated file writes

Tasks:

- Replace `prepare_upperdir_target` plus `std::fs::write` with a no-follow
  parent walk and same-directory temp-file replace.
- Parent creation must reject symlink components at every level.
- The final write must not follow a target symlink. Prefer:
  - create parent directories through directory fds with `O_DIRECTORY` and
    no-follow semantics;
  - write a unique temp file in the final parent;
  - flush the file;
  - atomically rename over the final entry.
- Preserve the existing contract for pre-existing symlink targets: return
  `invalid_request`, and do not mutate the outside target.

Tests:

- Keep the existing symlink-parent and symlink-target tests.
- Add a test that pre-creates an outside symlink, races or swaps the final path
  before commit, and proves the outside file is unchanged.
- Add a test for a non-directory parent component.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p operation file::isolated
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p daemon phase3_write_paths
```

#### 1B. Make isolated holder persistence load-bearing

Tasks:

- In open/create paths, treat `persist_handles()` failure as a setup failure.
  If persistence fails after holder/network wiring, teardown the holder and
  return an error.
- In exit paths, surface persistence failure in the response or error channel;
  do not silently discard it.
- Make `persist_handles()` durable enough for recovery:
  - write temp file;
  - flush file contents;
  - rename;
  - fsync the parent directory where supported.
- Persist enough data before returning success that `reap_persisted_holder()`
  can kill the holder after daemon restart.

Tests:

- Fake persistence failure during open: open returns an error and the holder is
  killed.
- Fake persistence failure during exit: caller receives a cleanup/persistence
  failure signal.
- Recovery reads a persisted holder pid and calls the runtime kill path.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p workspace isolated_workspace
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p namespace holder
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p daemon isolated_workspace
```

#### 1C. Restrict scratch orphan cleanup to owned directories

Tasks:

- Stop deleting every directory under `isolated_workspace.scratch_root`.
- Introduce an owned directory prefix or marker file, for example
  `scratch_root/eos-isolated/<workspace_id>` or a per-run marker JSON file.
- Reap only paths with the marker/prefix and valid workspace id shape.
- Reject obviously dangerous scratch roots during config validation, at least
  `/`, the filesystem root equivalent, and paths that resolve to the configured
  workspace root.

Tests:

- A foreign directory under `scratch_root` survives recovery.
- A daemon-owned marked directory is reaped.
- A dangerous scratch root fails config validation.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p config isolated_workspace
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p workspace isolated_workspace
```

### Phase 2 - Close Command Crash Windows

Intent: make command orphan recovery reliable across daemon crashes.

Tasks:

- Add a child-start barrier to `ns-runner` command launch:
  - child starts and waits on an inherited ack pipe before executing the request;
  - parent gets the child pid/process group;
  - parent writes and fsyncs `process.json`;
  - parent sends ack;
  - if the parent dies before ack, the child exits or never starts the user
    command.
- Recovery must distinguish:
  - prepared but never spawned;
  - spawned and tracked by `process.json`;
  - malformed or partial metadata.
- Do not remove a command dir until the recovery classification has produced a
  terminal completion or a cleanup error entry.

Tests:

- Simulated crash before ack: no workload process is left running.
- Persisted `process.json`: recovery sends SIGTERM/SIGKILL to the process
  group and records an orphan completion.
- Missing `process.json`: recovery finalizes as prepared-never-started, not as
  a silently deleted command.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p command process pty
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p operation command::service
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p daemon command
```

### Phase 3 - Make Verification Match The Contract

#### 3A. Fix clippy without hiding complexity

Tasks:

- Introduce local type aliases in `plugin/src/pyright_lsp/process.rs`, for
  example `PendingRequestSender`, `PendingRequests`, and `SharedPendingRequests`.
- Keep the behavior unchanged.
- Do not add `#[allow(clippy::type_complexity)]` for this case.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo clippy -p daemon --all-targets --locked -- -D warnings
```

#### 3B. Add fast plugin tests

Tasks:

- Add unit tests around `read_lsp_message`, response dispatch, EOF behavior, and
  pending request failure.
- On EOF from `read_lsp_message`, fail pending requests immediately instead of
  leaving callers to timeout.
- Add projection/path tests for URI and workspace path conversions used by
  Pyright LSP.

Acceptance:

- `cargo test -p plugin --locked -- --list` no longer reports zero tests.
- EOF and malformed response tests are deterministic and do not require a real
  Pyright process.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p plugin --locked
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo clippy -p daemon --all-targets --locked -- -D warnings
```

#### 3C. Put operation fixtures under `check-contract`

Tasks:

- Add operation fixture assertions to the conformance gate.
- Preferred small implementation: add `crates/daemon/operation/tests/contract.rs`
  for fixture-backed contract tests, then extend `CONFORMANCE_SUITES` with
  package `operation`, test `contract`.
- If keeping unit tests is necessary, extend `xtask` to support filtered unit
  tests explicitly and document that behavior in `xtask/src/main.rs`.

Acceptance:

- `cargo run -p xtask -- check-contract` fails if
  `crates/daemon/operation/fixtures/command_finalize_conflict_response.json`
  drifts from live serialization.
- No immutable fixture is edited to match code.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p operation --test contract
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo run -p xtask -- check-contract
```

### Phase 4 - Reduce Boundary Leaks

Intent: remove artificial crate coupling that makes maintenance worse.

#### 4A. Split operation contracts from operation implementation

Tasks:

- Create a narrow contract package or module boundary for operation DTOs and
  catalog data, for example `operation-contract` importing as
  `operation_contract`.
- Move only wire DTOs, catalog identity, error envelopes, and fixture-facing
  serialization helpers into that boundary.
- Keep implementation logic in `operation`.
- Update `plugin` and `core` to consume the contract boundary where they only
  need DTOs.

Acceptance:

- `cargo tree -p plugin --edges normal --depth 2` no longer pulls
  `command`, `namespace`, `overlay`, or `workspace` only through `operation`.
- `operation` remains the owner of command/file/checkpoint implementation
  behavior.
- `ops.json` output is byte-identical unless a deliberate contract change is
  made and documented.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo tree -p plugin --edges normal --depth 2
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p operation -p plugin -p daemon
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo run -p xtask -- check-contract
```

#### 4B. Separate overlay mount mechanics from layer capture

Tasks:

- Keep kernel overlay mount/writable-dir mechanics in `overlay`.
- Move layer-change capture that depends on `LayerPath`/`LayerChange` into
  `layerstack` or a narrow `overlay-capture` package with an explicit reason to
  depend on `layerstack`.
- Remove `namespace -> overlay -> layerstack` transitive pressure if namespace
  only needs mount mechanics.

Acceptance:

- `cargo metadata` shows no unnecessary namespace dependency on layerstack
  through overlay.
- Capture behavior and CAS fixtures remain unchanged.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo metadata --format-version 1 --no-deps
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p overlay -p layerstack -p namespace
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo run -p xtask -- check-contract
```

#### 4C. Remove ignored launcher plumbing

Tasks:

- Either wire `NsRunnerLauncher` through all runtime paths that launch
  `ns-runner`, or delete the abstraction and use direct launch helpers.
- If deleted, remove:
  - the trait and unused launcher exports;
  - `PluginRuntime::with_commit_options` launcher parameter;
  - related test-only `NoLaunch` scaffolding that only exists to satisfy the
    ignored parameter.

Acceptance:

- No constructor accepts a launcher it ignores.
- Tests that need fake process launch have a real seam at the launcher call
  site, not dead dependency injection.

Verification:

```sh
rg -n "NsRunnerLauncher|_launcher|NoLaunch" crates/daemon
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p workspace -p plugin -p daemon
```

### Phase 5 - Make Path Identity Explicit

Intent: avoid silent lossy path identity in overlay/layer contracts.

Tasks:

- Replace `to_string_lossy()` in overlay path capture with explicit conversion:
  either `to_str().ok_or(...)` rejection, or a lossless byte representation if
  the contract is expanded.
- For the current `LayerPath(String)` contract, prefer explicit rejection of
  non-UTF-8 paths with a clear `InvalidPathChange` error.
- Add Unix-only tests using non-UTF-8 `OsString` components and symlink targets.
- Document that daemon layer paths are UTF-8 contract paths if rejection is the
  chosen policy.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p overlay path_change
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p layerstack model
```

### Phase 6 - Split God Files Only After Behavior Is Stable

Intent: improve maintenance without hiding behavior changes inside refactors.

Candidate splits:

| File | Current size | Split boundary |
|---|---:|---|
| `crates/daemon/layerstack/src/stack/mod.rs` | 980 LOC | manifest assembly, view planning, leases, whiteout handling. |
| `crates/daemon/core/src/runtime/workspace.rs` | 610 LOC | workspace routing, isolated session lifecycle, response shaping. |
| `crates/daemon/operation/src/command/trace.rs` | 587 LOC | event model, append/loss recording, serialization. |
| `crates/daemon/layerstack/src/workspace.rs` | 554 LOC | filesystem scan, mutation collection, manifest update helpers. |
| `crates/daemon/plugin/src/pyright_lsp/process.rs` | 547 LOC | transport framing, pending requests, diagnostics, lifecycle. |

Rules:

- Split only after Phase 1 through Phase 5 are green.
- Preserve public paths through parent modules where downstream imports exist.
- Move tests with the behavior they verify.
- Do not widen production visibility for tests.

Verification:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p <touched-package>
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo clippy -p daemon --all-targets --locked -- -D warnings
```

## 6. Final Verification Ladder

The final 9/10 proof is this command set:

```sh
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo fmt --check
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo metadata --format-version 1 --no-deps
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo machete --with-metadata
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test --no-run -p daemon -p operation -p plugin -p workspace -p namespace -p command -p layerstack -p overlay -p eosd
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo test -p daemon -p operation -p plugin -p workspace -p namespace -p command -p layerstack -p overlay
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo run -p xtask -- check-contract
CARGO_TARGET_DIR=/tmp/eos-daemon-9-target cargo clippy -p daemon --all-targets --locked -- -D warnings
```

Run live Linux/Docker E2E before calling the work production-ready if a phase
changes namespace holder startup, overlay mount/capture, command process spawn,
or isolated workspace cleanup. The local macOS proof can score the daemon
workspace, but it cannot prove Linux namespace behavior end to end.

## 7. Expected End State

| Area | Current | Target |
|---|---:|---:|
| Correctness and safety | 6.5 | 9.0 |
| Crate boundaries | 7.0 | 8.8 |
| Test and contract coverage | 7.0 | 9.0 |
| Maintainability | 6.5 | 8.8 |
| Overall daemon workspace score | 7.0 | 9.0 |

The work is done when the evidence that held the score at 7/10 no longer
exists, not when the code merely looks cleaner.
