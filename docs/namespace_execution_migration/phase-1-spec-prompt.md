# Prompt: Generate the Phase 1 Spec for the Namespace Execution Engine

Use this prompt to produce a single, implementation-ready spec for **Phase 1 —
New crate + relocate the id** from:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/namespace_execution_migration/migration-phases.md
```

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

## Mission

Write `docs/namespace_execution_migration/phase-1-spec.md`: a precise,
build-to-green implementation spec for Phase 1 only. The output is a **spec, not
code**, but it must be detailed enough that an engineer can implement Phase 1
without re-deriving file placement, public exports, dependency wiring, tests, or
verification commands.

Treat `docs/namespace_execution_migration/migration-phases.md` as the fixed phase
contract and live code as the source of truth. If the phase contract and live code
conflict, preserve the phase objective and correct the implementation details to
match live code.

## How To Run

Use a two-pass workflow:

```text
1. Author pass:
   - Read the source material and live code anchors below.
   - Draft docs/namespace_execution_migration/phase-1-spec.md.
   - Include every required deliverable, especially the resulting file/folder
     structure, touched-file LOC ledger, and Acceptance Criteria checklist.

2. Verifier pass:
   - Re-open every cited file and every command in the draft.
   - Confirm each live-code claim as current, stale, or wrong.
   - Check that no Phase 2-6 behavior leaked into the Phase 1 spec.
   - Return defects only; do not rewrite unless defects are found.

3. Finalize:
   - Revise the spec in place until the verifier reports zero stale citations,
     zero scope leaks, and all Acceptance Criteria are testable.
```

This is spec-only. Do **not** implement the crate while generating the spec.

## Source Material

Read these first, in full:

```text
docs/namespace_execution_migration/migration-phases.md
docs/namespace-execution.md
docs/namespace-execution-adversarial-review-results.md
Cargo.toml
crates/sandbox-runtime/operation/Cargo.toml
crates/sandbox-runtime/operation/src/lib.rs
crates/sandbox-runtime/operation/src/namespace_execution.rs
crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/tests/namespace_execution.rs
crates/sandbox-runtime/namespace-process/Cargo.toml
crates/sandbox-runtime/namespace-process/src/runner/protocol.rs
```

Then use `rg` to confirm every call path and import path touched by
`NamespaceExecutionId`:

```sh
rg -n "NamespaceExecutionId|namespace_execution" crates/sandbox-runtime/operation/src crates/sandbox-runtime/operation/tests Cargo.toml
rg -n "pub struct NsFds|NamespaceRunnerRequest|RunResult" crates/sandbox-runtime/namespace-process/src
```

## Phase 1 Scope

Phase 1 stands up `sandbox-runtime-namespace-execution` with **types and traits
only**. It wires the new crate into the workspace and moves
`NamespaceExecutionId` from `sandbox-runtime` (`operation`) into the new crate.
Nothing should call the engine yet, and no command/workspace behavior should
change.

### In Scope

- Add the workspace member:
  `crates/sandbox-runtime/namespace-execution`.
- Add the workspace dependency:
  `sandbox-runtime-namespace-execution = { path = "crates/sandbox-runtime/namespace-execution" }`.
- Create the new crate manifest with workspace package metadata and lints.
- Create the Phase 1 source files named by the parent phase:
  `lib.rs`, `id.rs`, `error.rs`, `target.rs`, `promise.rs`, `execution.rs`,
  `shell.rs`, `observer.rs`, and `registry.rs`.
- Move `NamespaceExecutionId` into `id.rs`.
- Re-export `NamespaceExecutionId` from `crates/sandbox-runtime/operation/src/lib.rs`
  through the new crate so the existing public path still compiles.
- Update internal `operation` imports only as needed for the moved id.
- Add focused tests that prove the id moved without breaking the existing
  operation-facing path.

### Out Of Scope

- No `NamespaceExecutionEngine`.
- No launcher, watcher thread, fake launcher, PTY relocation, or `run_mount`.
- No command path migration.
- No workspace mount/remount migration.
- No registry-backed command queries.
- No cleanup of `CommandProcessStore`, `command/src/process.rs`,
  `command/src/pty.rs`, `run_child`, start-ack, or old launch paths.
- No observability shape changes beyond preserving compile compatibility for the
  existing `NamespaceExecutionId` surface.
- No aliases, shims, dual-write paths, or compatibility layers beyond the required
  public re-export from `operation`.

## Required Design Decisions To Resolve

The generated spec must settle these with live-code evidence:

1. **Crate dependency set.** Decide the exact Phase 1 dependencies for the new
   crate. Start from the parent phase's "only namespace-process plus serde/json,
   rustix, nix, libc" instruction, then remove any dependency not actually needed
   by the Phase 1 type skeleton. If a dependency is deferred to Phase 2, say so.
2. **Public export surface.** List exactly what the new crate exports in Phase 1.
   Keep it narrow; do not export engine concepts that are only stubs.
3. **`NamespaceExecutionId` move.** State the exact source deletion and destination
   insertion, including derives and tuple-field visibility. Existing tests that
   assert `id.0 == "namespace_execution_1"` must still compile through the old
   `sandbox_runtime::NamespaceExecutionId` path.
4. **`NamespaceTarget`.** Define the Phase 1 shape without depending on
   `workspace` types. Its `ns_fds` field must use
   `sandbox_runtime_namespace_process::runner::protocol::NsFds` if included in
   Phase 1.
5. **Promise and execution handle skeletons.** Specify only the methods needed to
   make the type contracts compile now. Defer watcher/wait behavior details to
   Phase 2 unless the Phase 1 file must declare an API for them.
6. **Observer and registry skeletons.** Clarify whether Phase 1 should contain
   no-op traits/types only, or minimal in-memory structures with tests. Avoid
   duplicating the current `NamespaceExecutionStore` behavior in Phase 1.
7. **Operation crate re-export.** Name the exact `Cargo.toml`, `use`, and `pub use`
   edits needed so downstream code keeps using `sandbox_runtime::NamespaceExecutionId`.
8. **Verification boundary.** Identify the smallest meaningful checks for Phase 1
   and whether full workspace `cargo test` is required by the parent phase exit
   criteria.

## Required Deliverables In The Generated Spec

The generated spec must contain all of the following.

### 1. Phase Boundary Statement

One short section that states:

- what Phase 1 delivers;
- what it intentionally does not deliver;
- why behavior must be unchanged at the phase boundary.

### 2. Resulting File/Folder Structure

Show the final tree after Phase 1, including the new crate and every touched
existing file. Use a code block like:

```text
crates/sandbox-runtime/
  namespace-execution/
    Cargo.toml
    src/
      lib.rs
      id.rs
      error.rs
      target.rs
      promise.rs
      execution.rs
      shell.rs
      observer.rs
      registry.rs
  operation/
    Cargo.toml
    src/
      lib.rs
      namespace_execution.rs
Cargo.toml
```

If the spec adds tests, include their exact paths in this tree.

### 3. Touched-File LOC Change Ledger

Show an estimated per-file LOC delta for every file the implementation is
expected to touch. Use this table shape:

```text
| File | Change | Estimated LOC delta | Why |
|---|---:|---:|---|
| Cargo.toml | edit | +2 | workspace member + dependency |
| crates/sandbox-runtime/namespace-execution/Cargo.toml | add | +N | new crate manifest |
```

Rules:

- Include added, edited, and deleted files.
- Use `+N`, `-N`, or `~0` style deltas.
- Keep estimates honest and narrow; do not hide broad rewrites behind `~0`.
- The spec must also instruct the implementer to report actual LOC deltas after
  implementation with:

```sh
git diff --numstat
```

### 4. File-By-File Implementation Spec

For each touched file, include:

- responsibility;
- exact public items, signatures, derives, and visibility;
- import/dependency changes;
- tests or compile assertions tied to that file;
- explicit non-goals for that file when Phase 2 work is tempting.

Prefer signature blocks and tables over long prose.

### 5. Acceptance Criteria Checklist

Include a checklist with concrete, testable items. At minimum:

```text
- [ ] `cargo check -p sandbox-runtime-namespace-execution` passes.
- [ ] `cargo test -p sandbox-runtime-namespace-execution` passes if the spec adds crate-local tests.
- [ ] `cargo test -p sandbox-runtime --tests` passes.
- [ ] `cargo test` for the whole workspace passes, or the spec records the exact blocker with evidence.
- [ ] `rg -n "pub use .*NamespaceExecutionId" crates/sandbox-runtime/operation/src` shows the operation re-export.
- [ ] Existing operation tests still compile through `sandbox_runtime::NamespaceExecutionId`.
- [ ] No Phase 2 symbols exist yet: `NamespaceExecutionEngine`, `NsRunnerLauncher`, watcher thread, and engine PTY modules are absent unless explicitly deferred as empty declarations with justification.
- [ ] `git diff --check` passes.
```

Add any acceptance item needed to prove the Phase 1 objective without testing
later phases.

### 6. Verification Commands

List exact commands in the order they should run:

```sh
cargo fmt --check
cargo check -p sandbox-runtime-namespace-execution
cargo test -p sandbox-runtime-namespace-execution
cargo test -p sandbox-runtime --tests
cargo test
rg -n "pub use .*NamespaceExecutionId" crates/sandbox-runtime/operation/src
rg -n "NamespaceExecutionEngine|NsRunnerLauncher|run_shell_interactive|run_mount" crates/sandbox-runtime/namespace-execution/src || true
git diff --check
git diff --numstat
```

If the generated spec decides any command is too broad or currently blocked, it
must name the narrower replacement and explain the evidence-backed reason.

### 7. Anchor Ledger

Include a table of every live-code citation used by the spec:

```text
| Anchor | Fact Used | Verdict |
|---|---|---|
| Cargo.toml:<line> | workspace members and dependency table location | confirmed |
```

Every row must be verified against the live checkout while generating the spec.
Do not cite stale line numbers from memory or prior reports.

## Ground Rules

- Spec only. Do not implement Phase 1 while generating the spec.
- Keep Phase 1 mechanical and independently shippable.
- Prefer deletion/move over aliasing. The only compatibility surface allowed in
  Phase 1 is the `operation` crate's public re-export of the moved id.
- Do not add a dependency, field, module, trait, or test helper unless Phase 1
  needs it to compile or to prove the move.
- Keep the new crate independent of `workspace` types.
- Keep observability unchanged: no `execution_kind`, `backing`, or new public
  classification axis.
- Use live code for every claim; use `rg` and direct file reads instead of broad
  guesses.
- If an implementation detail is not needed until Phase 2, name it in the
  out-of-scope section instead of designing it early.

## Output

Write the result to:

```text
docs/namespace_execution_migration/phase-1-spec.md
```

Lead with the phase boundary, then the resulting file/folder structure, then the
LOC ledger, then file-by-file implementation details, then verification commands,
then the Acceptance Criteria checklist, then the anchor ledger.
