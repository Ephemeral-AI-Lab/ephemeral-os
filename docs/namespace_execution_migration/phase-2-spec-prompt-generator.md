# Prompt: Generate the Phase 2 *Spec Prompt* for the Namespace Execution Engine

This is a **prompt-generator**. Running it produces one file —
`docs/namespace_execution_migration/phase-2-spec-prompt.md` — which is itself the
prompt that (when later run) produces `phase-2-spec.md`. You are writing a prompt,
not the spec and not code.

## Vocabulary (keep the three layers straight)

- **You** = the agent running *this* generator. Your only deliverable is the file
  `phase-2-spec-prompt.md`.
- **The spec prompt** = `phase-2-spec-prompt.md`, the file you write.
- **The spec** = `phase-2-spec.md`, the downstream artifact the spec prompt will
  later produce. You do **not** write it here.
- **The implementer** = whoever later builds Phase 2 from the spec.

The proven chain is `phase-N-spec-prompt.md` → (run) → `phase-N-spec.md` →
(derive) → `phase-N-implementation-prompt.md`. Phase 1 already ran the full chain;
Phase 2 starts it again. Model the *structure and rigor* of your output on the
Phase 1 spec prompt; specialize the *content* to Phase 2.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Mission

Write `docs/namespace_execution_migration/phase-2-spec-prompt.md`: a precise,
self-contained prompt that, when executed against this repo, yields a
build-to-green implementation spec for **Phase 2 — Launcher + engine dispatch +
watcher** and nothing else.

The spec prompt you write must mirror the section shape of the Phase 1 spec
prompt (`phase-1-spec-prompt.md`): Repo · Mission · How To Run (two-pass) · Source
Material · Phase Scope (In/Out) · Required Design Decisions To Resolve · Required
Deliverables In The Generated Spec · Ground Rules · Output. Reuse its discipline
verbatim where it still applies (anchor ledger, LOC ledger, two-pass
author/verifier, "live code wins over prose"); change only what Phase 2 demands.

Treat `migration-phases.md` § "Phase 2" as the fixed phase contract and the **live
checkout** as the source of truth. Phase 1 is already implemented; the spec prompt
must direct its reader to build *on top of the live Phase 1 skeleton*, not to
re-derive it. Where the design doc (`namespace-execution.md`) shows a *final*
shape that Phases 3–6 will reach, the spec prompt must carve out only Phase 2's
slice and push the rest to the out-of-scope list.

## How To Run

Two passes, then finalize.

```text
1. Author pass:
   - Read every item under "Source Material" below, in full — including the LIVE
     Phase 1 skeleton files (they exist now) and the live spawn/PTY/protocol/daemon
     anchors the Phase 2 launcher unifies.
   - Draft phase-2-spec-prompt.md so that a downstream agent could produce a
     complete phase-2-spec.md from it without re-deriving file placement, the
     fake-launcher test seam, dependency wiring, the watcher/thread model, or the
     verification commands.
   - Embed the Phase 2 scope (In/Out), the Required Design Decisions, and the
     Required Deliverables list so the spec prompt is self-sufficient.

2. Verifier pass:
   - Re-open every file the draft cites; confirm each path, symbol, and line
     anchor against the live checkout — current, stale, or wrong.
   - Confirm no Phase 3–6 work leaked into the Phase 2 scope you defined.
   - Confirm the draft tells its reader to read live Phase 1 code, not the Phase 1
     spec's prose, for the starting state.
   - Return defects only; do not rewrite unless defects are found.

3. Finalize:
   - Revise phase-2-spec-prompt.md until the verifier reports zero stale anchors,
     zero scope leaks, and every Required Deliverable is present and testable.
```

This is prompt-only. Do **not** write `phase-2-spec.md`, and do **not** implement
Phase 2, while generating the spec prompt.

## Source Material (you must read before writing)

The template and the quality bar:

```text
docs/namespace_execution_migration/phase-1-spec-prompt.md     # the structure to mirror
docs/namespace_execution_migration/phase-1-spec.md            # the output-quality bar; its Appendix lists every Phase 2 deferral
docs/namespace_execution_migration/phase-1-implementation-prompt.md  # the MUST-NOT (Phase 2+) list it enumerates
docs/namespace_execution_migration/migration-phases.md        # § "Phase 2" is the fixed contract; also the cross-phase sequencing notes
```

The design (read the Phase-2-relevant sections in full):

```text
docs/namespace-execution.md
  ## Software Patterns Applied        (Strategy / Template Method / Bridge / Future / Observer)
  ### Handles and promise             (ExecutionHandle/InteractiveExecution final API; wait_timeout → Option<&T>)
  ### The two families                (ShellOperation; RunnerOutcome::status/exit_code/payload; run_mount closures)
  ### The engine                      (NamespaceExecutionEngine, NsRunnerLauncher::spawn_pty/spawn_piped, RunnerChild::wait_completion, the dispatch skeleton)
  ## Finalization / Terminal Semantics (watcher/cancel/finalize-inline invariants)
  ## Resulting File Tree & LOC         (engine.rs ≈180 / launcher.rs ≈180 / pty.rs ≈120 sizes; dep additions)
  ## File Plan → New crate             (per-file responsibilities)
  ## Test Plan → namespace-execution   (the exact engine unit tests Phase 2 must pass)
docs/namespace-execution-adversarial-review-results.md         # rationale / simplification scorecard (skim for the "no Backing/NsRunnerMode/FinalizeCx" decisions)
```

The **live Phase 1 skeleton** Phase 2 extends (read each; this is the starting
state — live code wins over the Phase 1 spec's prose):

```text
crates/sandbox-runtime/namespace-execution/Cargo.toml          # 1 dep today; Phase 2 adds serde/serde_json + fork/PTY/signal crates
crates/sandbox-runtime/namespace-execution/src/lib.rs          # module decls + narrow re-exports
crates/sandbox-runtime/namespace-execution/src/promise.rs      # CompletionPromise: wait_timeout(Duration)->bool today
crates/sandbox-runtime/namespace-execution/src/execution.rs    # no PTY field, no write_stdin/cancel/wait_timeout-peek today
crates/sandbox-runtime/namespace-execution/src/registry.rs     # capacity placeholder; no maps/try_reserve/lookup today
crates/sandbox-runtime/namespace-execution/src/shell.rs        # RunnerOutcome::exit_code() only today; no status()/payload()
crates/sandbox-runtime/namespace-execution/src/observer.rs     # on_running only today; no on_terminal
crates/sandbox-runtime/namespace-execution/src/target.rs       # NamespaceTarget (5 fields)
crates/sandbox-runtime/namespace-execution/src/error.rs        # Spawn/Finalize/Admission variants (unconstructed today)
```

The **live spawn / PTY / protocol / daemon-child** anchors the Phase 2 launcher
and watcher unify (the launcher *relocates and merges* these; reuse their crate
choices and argv/fd contract — do not invent a new one):

```text
crates/sandbox-runtime/command/src/process.rs                  # spawn_current_exe_ns_runner: request build + fork + result-fd read (command path)
crates/sandbox-runtime/command/src/pty.rs                      # PtyMaster + transcript reader + the command-path spawn (moves into engine pty.rs/launcher.rs)
crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs # run_child / wait_for_child / terminate_child / read_pipe / ns_runner_request (mount path; the duplicate fork)
crates/sandbox-daemon/src/runner.rs                            # the in-namespace child CLI: RunnerCliConfig flags (--request-fd/--result-fd/--start-ack-fd/--mount-overlay/…), wait_for_start_ack, MountOverlay arm, RunResult emit
crates/sandbox-runtime/namespace-process/src/runner/protocol.rs # NamespaceRunnerRequest, RunResult { exit_code: i32, payload: Value }, NsFds
```

Then confirm every call path with `rg` rather than memory, e.g.:

```sh
rg -n "spawn_current_exe_ns_runner" crates/sandbox-runtime/command/src
rg -n "fn run_child|fn ns_runner_request|fn wait_for_child|fn read_pipe" crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs
rg -n "start_ack|--request-fd|--result-fd|RunnerCliConfig|dispatch_runner_mode|MountOverlay" crates/sandbox-daemon/src/runner.rs
rg -n "struct NamespaceRunnerRequest|struct RunResult|struct NsFds" crates/sandbox-runtime/namespace-process/src/runner/protocol.rs
rg -n "NamespaceExecutionTerminalStatus" crates/sandbox-runtime/operation/src
```

## Phase 2 Scope (the spec prompt must define this precisely)

Phase 2 makes the engine functional **end to end against a fake launcher**, so it
is fully unit-tested before any real caller depends on it. No command, workspace,
or daemon behavior changes. The spec prompt must direct the spec to build on the
live Phase 1 skeleton and add only Phase 2's slice.

### In Scope

- **New files in the engine crate:** `engine.rs` (`NamespaceExecutionEngine`,
  `run_shell_interactive`, `run_mount`, the single Template-Method dispatch
  skeleton, the watcher thread), `launcher.rs` (`pub(crate) NsRunnerLauncher` with
  `spawn_pty`/`spawn_piped`; `RunnerChild` with one blocking `wait_completion()`),
  `pty.rs` (`PtyMaster` + transcript reader, adapted from `command/src/pty.rs`).
- **Skeleton fill-ins (edit existing Phase 1 files) — only as Phase 2 needs:**
  - `registry.rs`: real live + completed maps keyed by `NamespaceExecutionId`,
    `try_reserve()` admission against `max_active`, `insert`/`complete`/id lookup.
  - `observer.rs`: add `on_terminal(...)` (the watcher calls it; the *implementer*
    is Phase 3 — Phase 2 tests use a fake observer).
  - `shell.rs`: `RunnerOutcome::status()` / `payload()` (and any constructor the
    launcher needs to build an outcome).
  - `execution.rs`: the `InteractiveExecution` PTY field + `write_stdin` /
    `read_output_since` / `output_len` / `cancel`; the `wait_timeout` peek **iff**
    the spec resolves it into Phase 2 (see Design Decisions).
  - `promise.rs`: any change required by the resolved `wait_timeout` peek.
  - `lib.rs` / `Cargo.toml`: new public exports (engine) and new dependencies.
- **A fake `NsRunnerLauncher`** returning a fake `RunnerChild`, so the engine is
  tested with no real fork.
- **The engine unit tests** enumerated in `migration-phases.md` § "Phase 2" exit
  criteria and `namespace-execution.md` ## Test Plan.

### Out Of Scope (scope-leak guardrails — name these, do not build them)

- No `ExecCommand` / `CommandExecution`; no command-service migration
  (`exec_command` / `write_command_stdin` / `read_command_lines`); no `core.rs`
  engine wiring; no `CommandOutput` DTO merge. (Phase 3.)
- No `NamespaceExecutionStore` → `NamespaceExecutionLedger` rename, no
  `impl ExecutionObserver` on the ledger, no `request_id` → `origin_request_id`.
  (Phase 3 — except the *minimum* terminal-status relocation Phase 2 may require;
  see Design Decision on the status type, and flag it as the one allowed boundary
  touch if the spec resolves it that way.)
- No mount migration: no `From<WorkspaceEntry>`, no rewrite of
  `setns_runner.rs`, no deletion of `run_child` / `ns_runner_request`. (Phase 4.)
- No remount coordinator changes. (Phase 5.)
- No deletion of `command/src/pty.rs` or `command/src/process.rs`; **no start-ack
  removal**. (Phase 6.)
- Engine stays workspace-agnostic: **no `workspace` dependency**.
- Observability surface unchanged: no `execution_kind` / `backing` / new
  classification axis.

## Required Design Decisions To Resolve (the spec prompt must force each, with live-code evidence)

The spec prompt must require the spec to settle every one of these. List them so
the spec author cannot skip them:

1. **Fake-launcher seam.** How is `NsRunnerLauncher` (concrete, `pub(crate)`, held
   on the engine per the Bridge decision) made fakeable in tests *without* a public
   `dyn`? Resolve the exact mechanism — e.g. make the engine generic
   `NamespaceExecutionEngine<L: NsRunnerLauncher>` over a `pub(crate)` launcher
   trait (real impl + `#[cfg(test)]` fake), or inject a `RunnerChild` factory.
   Pin the choice and show it keeps the public surface unchanged and the
   fork↔server swap intact.
2. **`RunnerChild` + `wait_completion()`.** Define `RunnerChild` (fork backing:
   child handle + result-fd) and its single blocking
   `wait_completion() -> Result<RunResult, NamespaceExecutionError>` (`child.wait()`
   + inline result-fd read; **no** result-fd reader thread, **no** poll). The fake
   must be able to block until signaled so the cancel-while-blocked test is real.
3. **PTY provisioning under the fake.** `spawn_pty` returns `(RunnerChild,
   PtyMaster)`. Resolve what `PtyMaster` interactive tests receive (real `openpty`
   loopback vs. an in-memory transcript sink) and how `write_stdin` /
   `read_output_since` / `output_len` are exercised without a real child.
4. **Thread model & lifecycle.** Two threads per exec — the PTY-reader (drains the
   master into the transcript) and the watcher (blocks on `wait_completion`, runs
   `finalize`/`parse` inline, resolves the promise, `registry.complete(id)`, then
   `observer.on_terminal`). Specify spawn/join/detach, transcript-buffer
   ownership, shutdown ordering, and the PTY-reader's fate on child exit. State the
   lock-acquisition budget (the design targets ~3).
5. **`wait_timeout` peek — Phase 2 or Phase 3?** Phase 1 ships
   `CompletionPromise::wait_timeout(Duration) -> bool`; the design's *final* handle
   API is `wait_timeout(&self, d) -> Option<&T>`. The borrow-out-of-`Mutex` peek
   exists for the command *yield* path, which is Phase 3. Resolve whether Phase 2
   needs only `is_finished()` + `wait_timeout(Duration)->bool` for engine tests, or
   must land the `Option<&T>` peek now — and if now, the concrete mechanism that
   coexists with single-consumer `wait(self)`.
6. **Terminal-status type location (the trickiest call).** The watcher calls
   `observer.on_terminal(id, status, exit_code)` and `RunnerOutcome::status()`
   returns a status — but `NamespaceExecutionTerminalStatus` lives in `operation`
   (`operation/src/namespace_execution.rs`), and the engine must not depend on
   `operation` (cycle). Resolve, with evidence: does Phase 2 relocate the
   terminal-status enum down into the engine crate (with `operation` re-exporting,
   mirroring the Phase 1 id move), define a minimal engine-local status, or keep
   `status()` parsing out of Phase 2? Whatever is chosen must let the watcher call
   `on_terminal` and keep observability unchanged.
7. **Observer `on_terminal`.** Add it to the trait; the implementer
   (`NamespaceExecutionLedger`) is Phase 3, so Phase 2 tests use a fake observer
   that records `on_running` / `on_terminal` calls. Tie its `status` parameter to
   Decision 6.
8. **Registry (live + completed + admission).** Turn the placeholder into the real
   registry: `try_reserve()`, `insert(id, live{ promise, child/pgid, pty_master })`,
   `complete(id)` (live → completed), id-keyed lookup. Resolve the Phase-2 stored
   value shape and what "completed" retains *generically* — **no command types**
   (`CompletedCommandRecord` is Phase 3).
9. **`NamespaceRunnerRequest` construction (argv + fds).** The launcher builds the
   request from `(target + op.command()/timeout_seconds() + id)` and forks
   `current_exe ns-runner [--mode] --request-fd … --result-fd … --start-ack-fd …`.
   Resolve the exact request fields and argv against `daemon/src/runner.rs`'s
   `RunnerCliConfig` and the current spawn sites; `namespace_execution_id` **is**
   the runner `request_id` and the registry key.
10. **Dependency set additions.** Phase 2 adds `serde`/`serde_json` (request +
    `payload()`) and the fork/PTY/signal crates. Resolve `rustix` vs `nix` vs
    `libc` by matching what `command/src/{pty,process}.rs` and
    `workspace/.../setns_runner.rs` actually use today (the launcher relocates
    their logic). State each addition's trigger; add nothing the Phase 2 code does
    not call.
11. **Start-ack KEEP (sequencing).** The new launcher **must still pass
    `--start-ack-fd`** and the child still `read_exact`s it — removal is the Phase 6
    atomic cut. The spec must state this and wire it, citing the Phase 2 sequencing
    note in `migration-phases.md`.
12. **Public export delta.** What does `lib.rs` newly export — `NamespaceExecutionEngine`
    (public; Phase 3+ callers need it) and what stays `pub(crate)`
    (`NsRunnerLauncher`, `RunnerChild`, `PtyMaster`, `CompletionPromise`,
    `ExecutionRegistry`). Resolve precisely.

## Required Deliverables In The Generated Spec (the spec prompt must demand all of these)

The spec prompt must instruct the spec to contain, in this order, each modeled on
the Phase 1 spec:

1. **Phase Boundary Statement** — what Phase 2 delivers, what it intentionally does
   not, and why externally observable behavior is unchanged (nothing calls the
   engine yet; only crate-local tests exercise it).
2. **Resulting File/Folder Structure** — the engine-crate tree after Phase 2
   (`← NEW` engine/launcher/pty; `△` registry/observer/shell/execution/promise/
   lib/Cargo edited; `[unchanged]` elsewhere), including any test module paths.
   Crate-local unit tests live in inline `#[cfg(test)] mod` blocks (the launcher
   seam and registry are `pub(crate)`); say so.
3. **Touched-File LOC Change Ledger** — per-file `+N`/`-N`/`~0` deltas for every
   added/edited file, seeded from the design's sizes (`engine.rs ≈180`,
   `launcher.rs ≈180`, `pty.rs ≈120`, plus the skeleton fill-ins), and the
   instruction to report actuals with `git diff --numstat`.
4. **File-By-File Implementation Spec** — for each touched file: responsibility;
   exact public/`pub(crate)` items, signatures, derives, visibility; import/dep
   changes; the tests tied to it; and explicit Phase 3–6 non-goals for that file.
   Prefer signature blocks and tables over prose.
5. **Acceptance Criteria Checklist** — concrete, testable, and at minimum covering
   every Phase 2 exit test: child-exit → promise resolves with finalized `Output`;
   `finalize` error → terminal error; `wait_timeout` blocks then returns on resolve
   (no poll); `cancel()`/`killpg` responsive while the watcher blocks in
   `wait_completion()`; admission rejects past `max_active`; `run_mount(flag, …,
   parse)` resolves the parsed `Output` and sync `.wait()` works;
   `namespace_execution_id` is the runner `request_id` and registry key with
   `origin_request_id` distinct; plus the absence checks proving no Phase 3–6
   symbols and no command/workspace/daemon change leaked.
6. **Verification Commands** — exact, ordered, e.g.:
   ```sh
   cargo fmt --check
   cargo check  -p sandbox-runtime-namespace-execution --tests
   cargo test   -p sandbox-runtime-namespace-execution
   cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
   cargo test   -p sandbox-runtime --tests        # re-export consumer: no regression
   cargo check  -p sandbox-daemon
   rg -n "ExecCommand|CommandExecution|run_child|From<WorkspaceEntry>|NamespaceExecutionLedger|origin_request_id" \
     crates/sandbox-runtime/namespace-execution/src || echo "no Phase 3-6 leak ✓"
   git diff --check
   git diff --numstat
   ```
   with a note on any command that is host-blocked (the dev host is darwin; the
   namespace paths are `cfg(target_os = "linux")`-gated) and the narrower
   authoritative substitute, matching how the Phase 1 spec handled it.
7. **Anchor Ledger** — a table of every live-code citation (file:line, fact used,
   verdict), every row verified against the live checkout while authoring — no
   line numbers from memory or from the Phase 1 spec.

## Ground Rules (for you, the spec-prompt author)

- **Prompt only.** Your output is `phase-2-spec-prompt.md`. Do not write the spec;
  do not implement Phase 2.
- **Mirror the proven format.** Reuse the Phase 1 spec prompt's section shape and
  its two-pass / anchor-ledger / LOC-ledger discipline. A reader of your spec
  prompt should feel the same rigor.
- **Build on live Phase 1 code.** Make the spec prompt tell its reader to read the
  live skeleton files for the starting state, and to treat live code as
  authoritative over both the design doc and the Phase 1 spec when they conflict.
- **Hold the phase boundary.** Phase 2 is engine-internal and tested only against a
  fake launcher; every command/workspace/daemon migration and the start-ack cut
  belong to later phases. Make the spec prompt enumerate these as out-of-scope, not
  design them.
- **Force the hard decisions.** The fake-launcher seam (Decision 1) and the
  terminal-status type location (Decision 6) are the two calls most likely to be
  fudged; the spec prompt must require both to be settled with live-code evidence
  before the spec is considered complete.
- **No new axis.** Keep observability unchanged; the engine stays workspace-agnostic.
- Prefer `rg` + direct file reads over guesses for every claim the spec prompt
  makes or requires.

## Output

Write the result to:

```text
docs/namespace_execution_migration/phase-2-spec-prompt.md
```

Lead it with Repo + Mission, then the two-pass How To Run, then Source Material
(template, design, **live Phase 1 skeleton**, live spawn/PTY/protocol anchors),
then Phase 2 Scope (In/Out), then Required Design Decisions, then Required
Deliverables, then Ground Rules, then Output — matching the Phase 1 spec prompt's
ordering so the chain stays uniform across phases.

> The same generator generalizes to Phases 3–6: swap the phase number, the
> contract section, the live anchors, and the in/out scope. Phase 2 is the first
> non-mechanical phase, so the fake-launcher seam and the watcher/thread model are
> its distinctive load-bearing decisions.
```
