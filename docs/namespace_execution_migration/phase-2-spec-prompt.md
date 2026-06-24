# Prompt: Generate the Phase 2 Spec for the Namespace Execution Engine

Use this prompt to produce a single, implementation-ready spec for **Phase 2 —
Launcher + engine dispatch + watcher** from:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/namespace_execution_migration/migration-phases.md
```

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Mission

Write `docs/namespace_execution_migration/phase-2-spec.md`: a precise,
build-to-green implementation spec for Phase 2 only. The output is a **spec, not
code**, but it must be detailed enough that an engineer can implement Phase 2
without re-deriving the fake-launcher test seam, file placement, the
watcher/thread model, dependency wiring, public exports, tests, or verification
commands.

Phase 2 makes the engine **functional end to end against a fake launcher**, so it
is fully unit-tested before any real caller depends on it. **Phase 1 is already
implemented** — the `sandbox-runtime-namespace-execution` crate exists with the
type/trait skeleton. Phase 2 fills that skeleton in and adds three new files
(`engine.rs`, `launcher.rs`, `pty.rs`). No command, workspace, or daemon behavior
changes; nothing outside the crate's own tests calls the engine yet.

Treat `migration-phases.md` § "Phase 2" as the fixed phase contract and the **live
checkout** as the source of truth. If the phase contract or the design doc
(`namespace-execution.md`, which shows *final* shapes that Phases 3–6 reach) and
the live code conflict, preserve the Phase 2 objective and correct the
implementation details to match live code. Build **on** the live Phase 1 skeleton;
do not re-derive it.

## How To Run

Use a two-pass workflow:

```text
1. Author pass:
   - Read the source material and live code anchors below in full — including the
     LIVE Phase 1 skeleton files (they exist now) and the live spawn/PTY/protocol/
     daemon-child anchors the Phase 2 launcher and watcher unify.
   - Draft docs/namespace_execution_migration/phase-2-spec.md.
   - Include every required deliverable, especially the fake-launcher test seam,
     the resulting file/folder structure, the touched-file LOC ledger, and the
     Acceptance Criteria checklist.

2. Verifier pass:
   - Re-open every cited file and every command in the draft.
   - Confirm each live-code claim as current, stale, or wrong.
   - Confirm no Phase 3-6 behavior leaked into the Phase 2 spec.
   - Confirm the two hard calls — the fake-launcher seam and the terminal-status
     type location — are settled with live-code evidence, not hand-waved.
   - Return defects only; do not rewrite unless defects are found.

3. Finalize:
   - Revise the spec in place until the verifier reports zero stale citations,
     zero scope leaks, and all Acceptance Criteria are testable.
```

This is spec-only. Do **not** implement the engine while generating the spec.

## Source Material

Read these first, in full.

The phase contract and the proven Phase 1 artifacts (mirror their structure and
rigor):

```text
docs/namespace_execution_migration/migration-phases.md          # § "Phase 2" is the contract; also the cross-phase sequencing notes (start-ack stays until Phase 6)
docs/namespace_execution_migration/phase-1-spec-prompt.md        # the spec-prompt structure to mirror
docs/namespace_execution_migration/phase-1-spec.md               # the output-quality bar; its Appendix enumerates every Phase 2 deferral this spec now lands
docs/namespace_execution_migration/phase-1-implementation-prompt.md  # its MUST-NOT (Phase 2+) list — the work this phase now does
```

The design (read the Phase-2-relevant sections in full):

```text
docs/namespace-execution.md
  ## Software Patterns Applied          (Strategy / Template Method / Bridge / Future / Observer)
  ### Handles and promise               (ExecutionHandle / InteractiveExecution final API; wait_timeout → Option<&T>; cancel → killpg)
  ### The two families                  (ShellOperation; RunnerOutcome::status/exit_code/payload; run_mount closures)
  ### The engine                        (NamespaceExecutionEngine; NsRunnerLauncher::spawn_pty/spawn_piped; RunnerChild::wait_completion; the dispatch skeleton, steps 1-8)
  ## Finalization / Terminal Semantics  (watcher / cancel-independent-of-watcher / finalize-inline invariants)
  ## Resulting File Tree & LOC          (engine.rs ≈180 / launcher.rs ≈180 / pty.rs ≈120; the dep additions)
  ## File Plan → New crate              (per-file responsibilities)
  ## Test Plan → namespace-execution    (the exact engine unit tests Phase 2 must pass)
docs/namespace-execution-adversarial-review-results.md           # rationale / scorecard (skim for the "no Backing / NsRunnerMode / FinalizeCx / ShellOutcome" decisions)
```

The **live Phase 1 skeleton** Phase 2 extends (read each — this is the starting
state; live code wins over the Phase 1 spec's prose):

```text
crates/sandbox-runtime/namespace-execution/Cargo.toml            # 1 dep today (namespace-process); Phase 2 adds serde / serde_json + fork/PTY/signal crates
crates/sandbox-runtime/namespace-execution/src/lib.rs            # module decls + narrow re-exports; promise + registry are mod (pub(crate)), not re-exported
crates/sandbox-runtime/namespace-execution/src/promise.rs        # CompletionPromise<T> = Mutex<Slot<T>> + Condvar; wait_timeout(Duration)->bool today
crates/sandbox-runtime/namespace-execution/src/execution.rs      # ExecutionHandle{id,promise}; InteractiveExecution{exec} — NO pty field / write_stdin / cancel / wait_timeout-peek today
crates/sandbox-runtime/namespace-execution/src/registry.rs       # ExecutionRegistry{max_active} placeholder — NO maps / try_reserve / lookup today
crates/sandbox-runtime/namespace-execution/src/shell.rs          # RunnerOutcome(RunResult) with exit_code()->i64 only; ShellOperation trait — NO status()/payload()
crates/sandbox-runtime/namespace-execution/src/observer.rs       # ExecutionObserver with on_running only — NO on_terminal
crates/sandbox-runtime/namespace-execution/src/target.rs         # NamespaceTarget (5 fields; ns_fds: protocol::NsFds)
crates/sandbox-runtime/namespace-execution/src/error.rs          # NamespaceExecutionError {Spawn, Finalize, Admission} (unconstructed today)
```

The **live spawn / PTY / protocol / daemon-child** anchors the launcher and
watcher unify (the launcher *relocates and merges* these; reuse their crate
choices and the argv/fd contract — do not invent a new one):

```text
crates/sandbox-runtime/command/src/pty.rs                        # spawn_current_exe_ns_runner (Command::new(current_exe).arg("ns-runner")…process_group(0)); PtyMaster; transcript reader; start_ack pipe; terminate_process_group (killpg SIGTERM→SIGKILL)
crates/sandbox-runtime/command/src/process.rs                    # request build (args Value) + child wait + result-fd read on the command path
crates/sandbox-runtime/command/Cargo.toml                        # the crate-feature set to mirror: nix {process,signal} · rustix {pty,event,pipe} · serde_json
crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs   # run_child / wait_for_child / terminate_child / read_pipe / ns_runner_request (the duplicate mount-path fork the launcher subsumes)
crates/sandbox-daemon/src/runner.rs                              # the in-namespace child CLI: RunnerCliConfig flags, NsRunnerOperation {Run(default), MountOverlay, RemountOverlay}, wait_for_start_ack, RunResult emit
crates/sandbox-runtime/namespace-process/src/runner/protocol.rs  # NamespaceRunnerRequest {request_id, args, workspace_root, layer_paths, upperdir, workdir, ns_fds: Option<NsFds>, timeout_seconds}; RunResult {exit_code: i32, payload: Value}; NsFds
crates/sandbox-runtime/operation/src/namespace_execution.rs      # where NamespaceExecutionTerminalStatus lives today (the cycle constraint on observer.on_terminal / RunnerOutcome::status)
Cargo.toml                                                       # [workspace.dependencies] for the new deps: serde, serde_json, rustix, nix, libc
```

Then use `rg` to confirm every call path and contract before citing it:

```sh
rg -n "fn spawn_current_exe_ns_runner|process_group\(0\)|killpg|openpt|ptsname|start_ack|fn wait" crates/sandbox-runtime/command/src/pty.rs
rg -n "RunnerCliConfig|--request-fd|--result-fd|--start-ack-fd|--mount-overlay|--remount-overlay|NsRunnerOperation|wait_for_start_ack" crates/sandbox-daemon/src/runner.rs
rg -n "struct NamespaceRunnerRequest|struct RunResult|struct NsFds|request_id|args" crates/sandbox-runtime/namespace-process/src/runner/protocol.rs
rg -n "NamespaceExecutionTerminalStatus" crates/sandbox-runtime/operation/src
rg -n "^(nix|rustix|libc|serde|serde_json) " Cargo.toml
```

## Phase 2 Scope

Phase 2 turns the Phase 1 type skeleton into a working, fake-launcher-tested
engine. It touches **only** `crates/sandbox-runtime/namespace-execution/`.

### Starting state (read this into the spec's Phase Boundary)

The engine remains **unreferenced outside the crate's own tests**: command and
workspace migration are Phases 3 and 4. So Phase 2's *behavioral* coverage is
crate-local unit tests against a **fake** launcher; the **real** `NsRunnerLauncher`
(the `std::process::Command` fork) is compile-coverage in Phase 2 and only runs
once a real caller wires it later. The dev host is darwin and the fork/PTY paths
are effectively Linux-only, which is *why* the fake seam is mandatory and is the
authoritative behavioral signal for this phase.

### In Scope

- **New files** in the engine crate:
  - `engine.rs` — `NamespaceExecutionEngine` with `run_shell_interactive` and
    `run_mount`, the single Template-Method dispatch skeleton (reserve → request →
    spawn → insert → `on_running` → watcher{ `wait_completion` → finalize/parse →
    `resolve` → `complete` → `on_terminal` } → return handle), and the watcher
    thread.
  - `launcher.rs` — `pub(crate) NsRunnerLauncher` with `spawn_pty` (PTY-backed
    shell) and `spawn_piped` (mount/batch), plus `RunnerChild` with one blocking
    `wait_completion()`. Unifies `spawn_current_exe_ns_runner` and the mount-path
    `run_child` into one launcher (relocated, not rewritten).
  - `pty.rs` — `PtyMaster` + transcript reader, adapted from `command/src/pty.rs`.
- **Skeleton fill-ins** (edit the existing Phase 1 files), only as Phase 2 needs:
  - `registry.rs` — real live + completed maps keyed by `NamespaceExecutionId`,
    `try_reserve()` admission against `max_active`, `insert` / `complete(id)` /
    id-keyed lookup.
  - `observer.rs` — add `on_terminal(id, status, exit_code)` (the watcher calls
    it; the *implementer* `NamespaceExecutionLedger` is Phase 3 — Phase 2 tests use
    a fake observer).
  - `shell.rs` — `RunnerOutcome::status()` and `payload()` (plus any constructor
    the launcher needs to build an outcome).
  - `execution.rs` — the `InteractiveExecution` PTY field + `write_stdin` /
    `read_output_since` / `output_len` / `cancel`; the `wait_timeout` peek **iff**
    resolved into Phase 2 (see Design Decisions).
  - `promise.rs` — any change the resolved `wait_timeout` peek requires.
  - `lib.rs` / `Cargo.toml` — new public export (`NamespaceExecutionEngine`) and
    the new dependencies.
- A **fake `NsRunnerLauncher`** returning a fake `RunnerChild`, so the engine is
  tested with no real fork.
- The **engine unit tests** named in `migration-phases.md` § "Phase 2" exit
  criteria and `namespace-execution.md` ## Test Plan.

### Out Of Scope (name these in the spec; do not build them)

- No `ExecCommand` / `CommandExecution`; no command-service migration
  (`exec_command` / `write_command_stdin` / `read_command_lines`); no `core.rs`
  engine wiring; no `CommandOutput` DTO merge. **(Phase 3.)**
- No `NamespaceExecutionStore` → `NamespaceExecutionLedger` rename, no
  `impl ExecutionObserver` on the ledger, no `request_id` → `origin_request_id`.
  **(Phase 3** — except the *minimum* terminal-status relocation Phase 2 may
  require; see Design Decision 6, and flag it as the one allowed boundary touch if
  the spec resolves it that way.)
- No mount migration: no `From<WorkspaceEntry>`, no rewrite of `setns_runner.rs`,
  no deletion of `run_child` / `ns_runner_request`. **(Phase 4.)**
- No remount-coordinator changes. **(Phase 5.)**
- No deletion of `command/src/pty.rs` or `command/src/process.rs`; **no start-ack
  removal** — the new launcher MUST keep passing `--start-ack-fd`. **(Phase 6.)**
- Engine stays workspace-agnostic: **no `workspace` dependency**.
- Observability surface unchanged: no `execution_kind` / `backing` / new
  classification axis.

## Required Design Decisions To Resolve

The generated spec must settle each of these with live-code evidence. The first
two are the calls most likely to be fudged — settle them concretely.

1. **Fake-launcher seam (load-bearing).** `NsRunnerLauncher` is concrete and
   `pub(crate)` per the Bridge decision, yet `migration-phases.md` requires "a fake
   `NsRunnerLauncher` returning a fake `RunnerChild`." Resolve the exact mechanism
   that makes it fakeable **without** widening the public surface: e.g. a
   `pub(crate) trait` launcher with the engine generic
   `NamespaceExecutionEngine<L: NsRunnerLauncher = …>` (real impl + `#[cfg(test)]`
   fake), or an injected `RunnerChild` factory. Show it keeps the public API
   unchanged and preserves the fork↔persistent-server swap the Bridge exists for.
2. **`RunnerChild` + `wait_completion()`.** Define `RunnerChild` for the fork
   backing (the `std::process::Command` child handle + the `--result-fd` read
   end — confirm the live shape in `command/src/pty.rs`) and its single blocking
   `wait_completion() -> Result<RunResult, NamespaceExecutionError>` (`child.wait()`
   + inline result-fd read; **no** result-fd reader thread, **no** poll). The fake
   `RunnerChild` must block until signaled so the cancel-while-blocked test is real.
3. **PTY provisioning under the fake.** `spawn_pty` returns `(RunnerChild,
   PtyMaster)`. Resolve what `PtyMaster` interactive tests receive (a real
   `openpt` loopback vs. an in-memory transcript sink) and how `write_stdin` /
   `read_output_since` / `output_len` are exercised without a real child.
4. **Thread model & lifecycle.** Two threads per exec — the PTY-reader (drains the
   master into the transcript) and the watcher (blocks on `wait_completion`, runs
   `finalize`/`parse` inline, `promise.resolve`, `registry.complete(id)`, then
   `observer.on_terminal`). Specify spawn/join/detach, transcript-buffer
   ownership, shutdown ordering, the PTY-reader's fate on child exit, and the
   lock-acquisition budget (the design targets ~3). State why cancel
   (`killpg(pgid)` from the caller thread) stays responsive while the watcher
   blocks (per ## Finalization / Terminal Semantics).
5. **`wait_timeout` peek — Phase 2 or deferred?** Phase 1 ships
   `CompletionPromise::wait_timeout(Duration) -> bool`; the design's final handle
   API is `wait_timeout(&self, d) -> Option<&T>`, which exists for the command
   *yield* path (Phase 3). Resolve whether Phase 2's engine tests need only
   `is_finished()` + `wait_timeout(Duration)->bool`, or must land the `Option<&T>`
   peek now — and if now, the concrete mechanism that coexists with single-consumer
   `wait(self)` (which takes the value). Prefer the smallest surface Phase 2 needs.
6. **Terminal-status type location (load-bearing).** The watcher calls
   `observer.on_terminal(id, status, exit_code)` and `RunnerOutcome::status()`
   returns a status, but `NamespaceExecutionTerminalStatus` lives in `operation`
   (`operation/src/namespace_execution.rs`) and the engine must not depend on
   `operation` (cycle). Resolve, with evidence, exactly one path: relocate the
   terminal-status enum down into the engine crate (with `operation` re-exporting,
   mirroring the Phase 1 id move); define a minimal engine-local status; or keep
   `status()` out of Phase 2 and have the watcher derive terminal state from
   `exit_code` only. The choice must let the watcher call `on_terminal`, keep the
   observable enum/values unchanged, and not pull command/observability types into
   the engine. If relocation is chosen, scope it to the *minimum* `operation`
   boundary touch and flag it (it is the one allowed exception to the Phase 3
   no-rename rule).
7. **Observer `on_terminal`.** Add it to the `ExecutionObserver` trait; the
   implementer is Phase 3, so Phase 2 tests use a fake observer recording
   `on_running` / `on_terminal`. Tie its `status` parameter to Decision 6, and keep
   `begin` in the operation layer (the engine drives only running/terminal by id).
8. **Registry: live + completed + admission.** Turn the placeholder into the real
   registry — `try_reserve()` (admission vs `max_active`), `insert(id, live{
   promise, child/pgid, pty_master })`, `complete(id)` (live → completed), id-keyed
   lookup. Resolve the Phase-2 stored value shape and what "completed" retains
   **generically** (no command types — `CompletedCommandRecord` is Phase 3). State
   the lock discipline and how admission composes with the watcher's `complete`.
9. **`NamespaceRunnerRequest` construction (argv + fds).** The launcher builds the
   request from `(target + op.command()/timeout_seconds() + id)` and forks
   `current_exe ns-runner [mode] --request-fd FD --result-fd FD --start-ack-fd FD`.
   Resolve, against `daemon/src/runner.rs` and the live spawn site: the shell path
   passes **no** mode flag (`NsRunnerOperation::Run` is the default), `run_mount`
   passes `--mount-overlay` / `--remount-overlay`; the shell command rides in
   `request.args`; `namespace_execution_id` **is** `request.request_id` and the
   registry key. Confirm field-by-field against `protocol.rs`.
10. **Dependency-set additions.** Phase 2 adds `serde` / `serde_json` (request +
    `payload()`) and the fork/PTY/signal crates. The live command path uses
    `nix {process, signal}` (for `killpg`/`Pid`/`Signal`) + `rustix {pty, event,
    pipe}` + `serde_json` (`command/Cargo.toml`); mirror that and **drop anything
    the relocated Phase 2 code does not call** (justify `libc` only if a relocated
    path needs it). Cite each addition's first call site and confirm the versions
    exist in `[workspace.dependencies]`.
11. **Start-ack KEEP (sequencing).** Per the `migration-phases.md` Phase 2
    sequencing note, the new launcher MUST still create the start-ack pipe and pass
    `--start-ack-fd`, and release the child exactly as `command/src/pty.rs` does
    today; the child still `read_exact`s it. Removal is the Phase 6 atomic cut.
    State this and wire it; do not "simplify" it away.
12. **Public export delta.** Resolve precisely what `lib.rs` newly exports —
    `NamespaceExecutionEngine` (public; Phase 3+ callers construct it) — and what
    stays `pub(crate)` (`NsRunnerLauncher`, `RunnerChild`, `PtyMaster`,
    `CompletionPromise`, `ExecutionRegistry`). Keep the surface as narrow as Phase 1
    kept it.

## Required Deliverables In The Generated Spec

The generated spec must contain all of the following, in this order (model each on
the corresponding section of `phase-1-spec.md`).

### 1. Phase Boundary Statement

State what Phase 2 delivers, what it intentionally does not, and why externally
observable behavior is unchanged (nothing outside crate-local tests calls the
engine; the real launcher is compile-coverage only this phase). Name the fake-seam
and the test-only behavioral coverage explicitly.

### 2. Resulting File/Folder Structure

Show the engine-crate tree after Phase 2 (`← NEW` engine/launcher/pty; `△`
registry/observer/shell/execution/promise/lib/Cargo edited; `[unchanged]`
elsewhere), including the fake launcher and every inline test module. State that
crate-local unit tests live in inline `#[cfg(test)] mod` blocks (the launcher seam,
`RunnerChild`, `PtyMaster`, `CompletionPromise`, and `ExecutionRegistry` are
`pub(crate)` and unreachable from a `tests/` integration dir).

### 3. Touched-File LOC Change Ledger

A per-file `+N`/`-N`/`~0` delta for every added/edited file, seeded from the
design's sizes (`engine.rs ≈180`, `launcher.rs ≈180`, `pty.rs ≈120`, plus the
skeleton fill-ins to `registry`/`execution`/`shell`/`observer`/`promise`/`lib`/
`Cargo.toml`). Keep estimates honest and narrow. Instruct the implementer to
report actuals with:

```sh
git diff --numstat
```

### 4. File-By-File Implementation Spec

For each touched file: responsibility; exact public / `pub(crate)` items,
signatures, derives, visibility; import/dependency changes; the tests or compile
assertions tied to it; and explicit Phase 3-6 non-goals for that file. Prefer
signature blocks and tables over prose. Cover at minimum `engine.rs`,
`launcher.rs`, `pty.rs`, `registry.rs`, `observer.rs`, `shell.rs`, `execution.rs`,
`promise.rs`, `lib.rs`, `Cargo.toml`, and the fake launcher used in tests.

### 5. Acceptance Criteria Checklist

Concrete, testable items. At minimum (each maps to a Phase 2 exit test):

```text
- [ ] child-exit → promise resolves with the finalized `Output` (shell via finalize; mount via the parse closure).
- [ ] `finalize` / parse error → promise resolves with a terminal `NamespaceExecutionError`.
- [ ] `CompletionPromise::wait_timeout` blocks then returns on resolve (no poll).
- [ ] `cancel()` (`killpg`) is responsive while the watcher blocks in `wait_completion()`.
- [ ] admission rejects past `max_active`.
- [ ] `run_mount(flag, target, id, parse)` resolves the parsed `Output`; sync `.wait()` works.
- [ ] `namespace_execution_id` is the runner `request_id` and the registry key.
- [ ] the new launcher still passes `--start-ack-fd` (Phase 6 removes it).
- [ ] no Phase 3-6 symbol leaked into the crate (absence grep), and no command/workspace/daemon file changed.
- [ ] `cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps` is clean.
- [ ] `git diff --check` passes; actual LOC reported via `git diff --numstat`.
```

Add any item needed to prove the Phase 2 objective without testing later phases.

### 6. Verification Commands

List exact commands in run order, e.g.:

```sh
cargo fmt --check
cargo check  -p sandbox-runtime-namespace-execution --tests
cargo test   -p sandbox-runtime-namespace-execution
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
cargo test   -p sandbox-runtime --tests          # re-export consumer: no regression
cargo check  -p sandbox-daemon
# Phase 3-6 work MUST be absent from the engine crate:
rg -n "ExecCommand|CommandExecution|run_child|From<WorkspaceEntry>|NamespaceExecutionLedger|origin_request_id" \
  crates/sandbox-runtime/namespace-execution/src || echo "no Phase 3-6 leak ✓"
# start-ack still wired (Phase 6 removes it):
rg -n "start[-_]ack" crates/sandbox-runtime/namespace-execution/src
git diff --check
git diff --numstat
```

If the spec decides any command is too broad or host-blocked (the dev host is
darwin; the fork/PTY paths are effectively Linux-only), it must name the narrower
authoritative substitute and the evidence-backed reason — matching how
`phase-1-spec.md` § 6 handled the host constraint. The fake-launcher engine tests
are the authoritative behavioral signal regardless of host.

### 7. Anchor Ledger

A table of every live-code citation the spec uses (`file:line` · fact used ·
verdict). Every row verified against the live checkout while generating the spec —
no line numbers from memory, from `phase-1-spec.md`, or from the design doc.

## Ground Rules

- Spec only. Do not implement Phase 2 while generating the spec.
- Build **on** the live Phase 1 skeleton; read those files for the starting state
  and treat live code as authoritative over both the design doc and the Phase 1
  spec when they conflict.
- Keep Phase 2 engine-internal and independently shippable: it touches only
  `crates/sandbox-runtime/namespace-execution/`, and its behavioral tests run
  against the fake launcher.
- Settle the two load-bearing decisions (fake-launcher seam, terminal-status type
  location) with live-code evidence before considering the spec complete.
- Do not pull Phase 3-6 forward: name every later-phase item in the out-of-scope
  list instead of designing it. The only permitted cross-crate touch is the
  minimal terminal-status relocation, *iff* Decision 6 chooses it.
- Keep the engine workspace-agnostic and observability unchanged (no
  `execution_kind` / `backing` axis).
- Add no dependency, field, module, trait, or test helper unless Phase 2 needs it
  to compile or to prove the engine.
- Use `rg` and direct file reads for every claim; do not guess.

## Output

Write the result to:

```text
docs/namespace_execution_migration/phase-2-spec.md
```

Lead with the Phase Boundary Statement, then the resulting file/folder structure,
then the LOC ledger, then the file-by-file implementation details (engine /
launcher / pty / the skeleton fill-ins / the fake launcher), then the verification
commands, then the Acceptance Criteria checklist, then the Anchor Ledger —
matching `phase-1-spec.md`'s ordering so the migration's spec set stays uniform.
