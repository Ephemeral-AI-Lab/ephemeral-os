# Doc 3 — Dissolve the `sandbox-runtime-command` Crate

Status: ready-to-implement. **Depends on Doc 1** (remount removal): once
`process_group.rs` is deleted, the `command` crate is only thin command-domain
glue with a single consumer (`operation`), and stops earning its own crate.

This is a **relocation**, not a feature change. Code moves to the layer that
already owns its concern; the crate, its manifest entry, and its lock stanza are
deleted.

## 0. Why it can go

- The process/PTY/transcript-**write** substrate already moved to the namespace
  engine in an earlier commit. The crate kept only a command-facing handle,
  transcript-**read** windowing, and process-group inspection.
- Doc 1 deletes `process_group.rs`. What's left is consumed **only** by
  `operation`, which already depends directly on the namespace engine. No third
  party, no new dependency edge.

## 1. State after Doc 1

```
crates/sandbox-runtime/command/src/
  lib.rs              26   module wiring + re-exports         → DELETE
  command_execution.rs 153 CommandExecution (handle wrapper)  → MOVE → operation
  config.rs            14  CommandConfig { scratch_root }      → MOVE → operation
  contract.rs          14  CommandTerminalResult (DTO)         → MOVE → operation
  transcript_rows.rs  304  transcript READ side               → MOVE → namespace-execution
  (process_group.rs)  ---  already deleted by Doc 1
```

Consumers (all in `operation`): `command/service/{core,exec,helpers,transcript,
test_support}.rs`, `command/service/impls/{exec_command,write_command_stdin,
read_command_lines}.rs`, `services.rs`. (`workspace_remount` consumers are gone
after Doc 1.)

## 2. Destinations

### 2.1 `transcript_rows.rs` → `namespace-execution`

The transcript **write** side already lives in the engine; its doc comment even
points at this read side across the crate boundary. Co-locate to own the wire
format in one place.

```
MOVE  command/src/transcript_rows.rs
  →   namespace-execution/src/transcript_rows.rs   (or fold into the existing transcript module)
EXPORT from namespace-execution/src/lib.rs:
  CommandStream, CommandTranscriptRow, CommandTranscriptWindow,
  transcript_window, required_transcript_window
```
Rename note: these are generic transcript types, not command-specific. Optionally
drop the `Command` prefix (`TranscriptRow`, `TranscriptWindow`, `TranscriptStream`)
since the engine is workspace/command-agnostic. Keep names if churn isn't worth
it — call it out, don't silently do half.

Dependency check: `transcript_rows.rs` uses only `std` + `serde_json`. The engine
crate already pulls `serde_json` (transcript write). No new dep. No cycle — the
engine sits below everything.

### 2.2 `CommandExecution`, `CommandConfig`, `CommandTerminalResult` → `operation`

These are command-domain, workspace-aware glue. Their only consumer is the
`operation` command service.

```
MOVE  command/src/command_execution.rs → operation/src/command/execution.rs
MOVE  command/src/config.rs            → operation/src/command/config.rs
MOVE  command/src/contract.rs          → operation/src/command/result.rs (CommandTerminalResult)
WIRE  operation/src/command/mod.rs: add `mod execution; mod config; mod result;` + re-exports
```

No cycle: `operation` is the top crate; it already depends on
`namespace-execution` directly (`CommandExecution` wraps
`InteractiveExecution<CommandTerminalResult>`, and the registry is
`NamespaceExecutionEngine<CommandExecution>` — already constructed in
`operation/src/command/service/core.rs`).

## 3. Dead-method pruning (enabled by Doc 1)

With remount gone, several `CommandExecution` accessors lose their only caller.
During the move, delete the ones now unused and keep the move honest:

- `pgid()` — was used by the remount coordinator only. **Verify dead → remove.**
- `workspace_root()` — same. **Verify dead → remove**, and drop the
  `workspace_root: PathBuf` field + `new()` parameter if nothing else reads it.
- `cancel_handle()` — coordinator-only cloneable cancel. **Verify dead → remove**;
  keep `cancel()` if destroy/limit paths still use it.

Run `grep` for each accessor across `operation/src` after Doc 1; remove any with
zero non-test callers. This is the real LOC win of the move (the wrapper is
mostly forwarders to `InteractiveExecution`).

## 4. Import rewrites

Across the ~12 `operation` files that referenced the crate:

```
use sandbox_runtime_command::CommandExecution        → use crate::command::CommandExecution
use sandbox_runtime_command::CommandConfig           → use crate::command::CommandConfig
use sandbox_runtime_command::CommandTerminalResult   → use crate::command::CommandTerminalResult
use sandbox_runtime_command::{CommandTranscriptRow,  → use sandbox_runtime_namespace_execution::{...}
    CommandTranscriptWindow}
::sandbox_runtime_command::CommandConfig (qualified)  → crate::command::CommandConfig
```

The transcript service (`operation/src/command/service/transcript.rs`) switches
its transcript-type import to the namespace-execution crate.

## 5. Manifest teardown

```
[DELETE] crates/sandbox-runtime/command/                      (whole crate dir)
[EDIT]   Cargo.toml (workspace root):
           - DELETE member entry for crates/sandbox-runtime/command
           - DELETE [workspace.dependencies] line:
             sandbox-runtime-command = { path = "crates/sandbox-runtime/command" }
[EDIT]   crates/sandbox-runtime/operation/Cargo.toml:
           - DELETE `sandbox-runtime-command.workspace = true`
           - ENSURE `sandbox-runtime-namespace-execution.workspace = true` present (already is)
           - `nix`/`serde_json` deps: add to operation only if a moved file needs
             them and they aren't already present (operation already has serde_json)
[EDIT]   Cargo.lock: remove the `sandbox-runtime-command` package stanza and the
           reference under `sandbox-runtime-operation` (or just let `cargo build`
           regenerate it).
```

## 6. Sequencing

1. Move `transcript_rows.rs` into `namespace-execution`, export, build that crate.
2. Move `CommandExecution`/`CommandConfig`/`CommandTerminalResult` into
   `operation/src/command/`, wire `mod`s.
3. Rewrite the ~12 `operation` imports (§4).
4. Prune dead accessors (§3).
5. Delete the crate dir + manifest entries (§5).
6. `cargo build` (regenerates lock), `cargo test -p sandbox-runtime-namespace-execution -p sandbox-runtime`, `cargo clippy --all-targets`, `cargo fmt`.

## 7. Done = these greps return nothing

```sh
grep -rn "sandbox-runtime-command\|sandbox_runtime_command" --include="*.rs" --include="*.toml" crates Cargo.toml
test ! -d crates/sandbox-runtime/command && echo "crate dir gone"
```

## 8. Acceptance

- Workspace builds; `namespace-execution` and `operation` tests green.
- Command exec / write-stdin / read-lines / transcript windowing behave
  identically (the relocated code is byte-for-byte the same logic).
- README component table drops the `sandbox-runtime-command` row; the transcript
  read side is mentioned under the namespace engine.

## 9. Expected LOC impact

Dissolving a crate is mostly **relocation**, not deletion:

- ~510 LOC (`command_execution` + `config` + `contract` + `transcript_rows`)
  **change address**, near LOC-neutral.
- **Genuinely cut:** crate `Cargo.toml` (~15), `lib.rs` re-export wiring (~20 net
  after a few `mod` lines reappear), workspace manifest + lock lines (~13),
  import churn net ~0.
- **Plus** the dead-accessor pruning from §3 (remount made them dead): realistically
  another **~30–60 LOC** off `CommandExecution`, possibly a struct field.

Net genuine deletion ≈ **60–110 LOC**; the headline win is **−1 crate** and a
dependency graph with no thin single-consumer adapter. (Contrast: the big LOC
removal lives in Doc 1, ≈2,850 lines.)
