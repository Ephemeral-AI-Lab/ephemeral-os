# Agent Prompt — Author the Phase 4 Spec (Mount family onto the engine)

You are a software architect. Your job is to **write a complete, standalone,
implementation-ready specification** for **Phase 4** of the namespace-execution
migration — *not* to implement it. A separate implementation agent will follow
your spec. Read this whole prompt before starting.

**Deliverable:** a single markdown document at
`docs/namespace_execution_migration/phase-4-spec.md`. The **only** thing you may
write is that file. **Do not modify any production code, tests, or `Cargo.toml`.**
You may read and run read-only commands (`rg`, `cargo check`, `ls`) freely to
ground the spec; treat the source tree as read-only.

## Inputs to study (read these first)

- `docs/namespace-execution.md` — the design of record. Internalize **"The two
  families"** (mount has **NO `MountOperation` trait** — two fixed `run_mount`
  call sites, each a `(mode_flag, parse_closure)` pair), **"Decoupling
  `shell_exec` From Workspace"** (the `NamespaceTarget` boundary type), and the
  `run_mount` signature under **"The engine"**.
- `docs/namespace_execution_migration/migration-phases.md` — the **Phase 4**
  section (Edit / Delete, exit criteria, verify block) plus **"Invariants held at
  every phase boundary"**. Binding; your spec refines, never contradicts.
- The actual code (see the current-state map below — **verify and extend it
  yourself**; line numbers drift and other agents may be editing concurrently).

## Prerequisite the spec must declare — the Phase 2 engine API

Phase 4 builds on the engine from Phase 2, which **does not exist yet** (the crate
is currently a Phase-1 skeleton behind the `test-support` feature). Your spec must
open with a **"Consumed Phase 2 API"** section pinning the exact surface:

```rust
NamespaceExecutionEngine::run_mount<O: Send + 'static>(
    &self, mode_flag: &'static str, target: NamespaceTarget, id: NamespaceExecutionId,
    parse: impl FnOnce(RunnerOutcome) -> Result<O, NamespaceExecutionError> + Send + 'static,
) -> Result<ExecutionHandle<O>, NamespaceExecutionError>;
NamespaceExecutionEngine::allocate_id() -> NamespaceExecutionId;
// ExecutionHandle<O>::wait() -> Result<O, NamespaceExecutionError>   (sync callers .wait())
// RunnerOutcome::payload() -> &serde_json::Value
// NamespaceTarget { workspace_root, layer_paths, upperdir, workdir, ns_fds }   (already exists)
```

Phase 2's launcher **still passes `--start-ack-fd`** (removed atomically in
Phase 6); the spec must state Phase 4 leaves start-ack untouched. Phase 4 *does*
touch `daemon/src/runner.rs`, but only the `MountOverlay` payload behavior and a
parameter rename — never the start-ack plumbing.

## Current-state map to spec against (verify; go deeper as needed)

`crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs`:
- `mount_overlay(handle, layer_paths, setup_timeout_s)` (~:43) → `ns_runner_request`
  → `mount_overlay_child` with `"--mount-overlay"`.
- `remount_overlay(handle, layer_paths, probe, setup_timeout_s)` (~:61) → returns
  `RemountOverlayResult` parsed from the child's `RunResult` payload.
- `ns_runner_request(handle, request, args, layer_paths)` (~:134) → builds
  `NamespaceRunnerRequest`; **the `isolated-{request}-{workspace_id}` id format is
  here** (~:141).
- `mount_overlay_child` (~:153), `remount_overlay_child` (~:171).
- **`run_child(request, mode_arg, setup_timeout_s)` (~:194)** — the duplicate
  spawn/pipe/wait path (pipes → spawn `current_exe ns-runner {mode} --request-fd
  --result-fd` → write request → wait → read pipes → `Output`).
- `wait_for_child` (~:235), `terminate_child` (~:268), `read_pipe` (~:278).

`crates/sandbox-runtime/workspace/src/model.rs`:
- `WorkspaceEntry` (~:295): `{ workspace_root: PathBuf, layer_paths: Vec<PathBuf>,
  upperdir: PathBuf, workdir: PathBuf, ns_fds: WorkspaceEntryFds }`.
- `WorkspaceEntryFds` (~:313) and `From<WorkspaceEntryFds> for NsFds` (~:333).

`crates/sandbox-runtime/workspace/src/lifecycle/remount/result.rs`:
- `RemountOverlayResult { mount_verified, failure_summary }`,
  `from_payload(&Value) -> Self` (~:17).

`crates/sandbox-daemon/src/runner.rs`:
- `dispatch_runner_mode(mode, request, runner_config)` (~:37). `MountOverlay` arm
  (~:53) calls `setns_overlay_mount` then returns `ok_result()`; on failure
  `?`-propagates. `RemountOverlay` arm (~:43) already puts its report in
  `RunResult.payload`. `RunResult { exit_code: i32, payload: Value }` (2 fields).

`crates/sandbox-runtime/namespace-process/.../protocol.rs`:
- `NamespaceRunnerRequest { request_id, args, workspace_root, layer_paths,
  upperdir, workdir, ns_fds, timeout_seconds }`, `NsFds`. **Unchanged** by Phase 4.

## The hard design question the spec must resolve

The mount call sites today hold a **`WorkspaceModeHandle`**, *not* a
`WorkspaceEntry` (`ns_runner_request(handle, ..)`). The migration doc only
prescribes `From<WorkspaceEntry> for NamespaceTarget` (which Phase 3 also needs).
The spec must **decide and justify** how the mount path obtains a `NamespaceTarget`:
reuse `From<WorkspaceEntry>` (if a `WorkspaceEntry` is reachable from the handle),
or add a `From<&WorkspaceModeHandle>` / inline builder
(`workspace_root`, `dirs.upperdir/workdir`, `layer_paths`,
`ns_fds_from_mode(handle.ns_fds)`) — **without duplicating fd-mapping logic**.
Investigate the real `WorkspaceModeHandle` type and pick the smaller option.

## Required structure of the spec you write

Make `phase-4-spec.md` self-contained. Include, at minimum:

1. **Objective & scope** — what Phase 4 does and does not do.
2. **Consumed Phase 2 API** — pinned exactly (above).
3. **The `NamespaceTarget` sourcing decision** — your resolution to the
   `WorkspaceModeHandle` vs `WorkspaceEntry` question, with the chosen
   conversion(s) fully specified and the orphan-rule reasoning.
4. **File-by-file change plan** — every Edit / Delete with before→after and why:
   - `setns_runner.rs`: replace `run_child`/`wait_for_child`/`terminate_child`/
     `read_pipe`/`ns_runner_request` with the two call sites
     `engine.run_mount("--mount-overlay", target, id, |_| Ok(())).wait()` and
     `engine.run_mount("--remount-overlay", target, id, |o| Ok(RemountOverlayResult::from_payload(o.payload()))).wait()`;
     id now from `engine.allocate_id()`; delete the `isolated-{mode}-{id}` format.
   - `model.rs`: the `From` impl(s) per the decision above.
   - `daemon/src/runner.rs`: `MountOverlay` arm writes failure text into
     `RunResult.payload` (so the 2-field `RunResult` carries mount diagnostics);
     rename the `dispatch_runner_mode` parameter for clarity.
5. **Cross-phase coordination** — `From<WorkspaceEntry> for NamespaceTarget` is the
   single edit shared with Phase 3; state who owns it so they don't collide.
6. **Invariants preserved** — overlay mount + live remount still succeed via
   `engine.run_mount`; the remount report still parses; **failure surfaces as a
   terminal error via `payload`**; setup-timeout semantics preserved (timeout now
   on the op/request the engine builds); no `execution_kind`/`backing`; mount
   executions are tracked + promised through the engine, not a side path. State
   **how** each is upheld.
7. **Test plan** — which workspace/daemon tests must keep passing, which move,
   which are new (mount + remount through `engine.run_mount`; report parses;
   failure → terminal error). Honor the repo rule: **no inline tests in production
   sources**; unit tests in integration suites.
8. **Verification** — exact commands (fmt, `cargo test -p sandbox-runtime-workspace`,
   `cargo build -p sandbox-daemon`, clippy `-D warnings`, the absence-greps:
   `fn run_child|fn ns_runner_request|fn wait_for_child|fn terminate_child|fn read_pipe`
   gone, `isolated-` gone).
9. **Risks & open decisions** — anything genuinely ambiguous, with a recommended
   resolution.
10. **Definition of done & LOC estimate**.

## Design constraints the spec must honor (from `CLAUDE.md`)

- SRP/SOLID; `workspace` depends on the engine's narrow `run_mount`, not its
  internals; the engine crate keeps **zero** `workspace` dependency.
- Prefer less — net deletion; **no `MountOperation` trait, no `ops.rs`, no
  `Backing`/`NsRunnerMode` enum** — two call sites, two closures.
- No re-complication: mount stays behind the `NsRunnerLauncher` seam; no second
  spawn/wait/pipe path; no shims/aliases/dual-write.
- No inline comments in production code; `///` on public items only.

## Report back

When done, give me: the path you wrote, a 5–10 line outline of the spec's
sections, your resolution to the `NamespaceTarget`-sourcing question, the top 3
risks/open decisions, and any place the existing docs were ambiguous or
internally inconsistent. Do not commit or push.
