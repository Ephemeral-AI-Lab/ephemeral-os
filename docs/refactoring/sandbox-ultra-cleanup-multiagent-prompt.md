# Sandbox Ultra Cleanup Multi-Agent Prompt

Use this prompt after the sandbox refactor package shape is compiling and you
want a source-focused cleanup pass that aggressively deletes
backward-compatible surfaces, aliases, fallbacks, legacy names, and unused code
from production Rust/source and tooling paths.

```text
You are the cleanup orchestrator for EphemeralOS sandbox refactor cleanup.

Working directory:

/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os

Task:

Run a multi-agent ultra-cleanup pass in aggressive source-removal mode. Remove
backward-compatible wrappers, aliases, fallbacks, legacy surfaces, stale names,
unused source code, and unused dependencies from production Rust/source and
tooling paths. The default action is deletion or source caller migration
followed by deletion.

This is not a compatibility-preserving cleanup. The preferred outcome is one
current path, one current name, one current protocol shape, one current package
shape, and no hidden old entrypoints. Do not add deprecation shims. Do not keep
old aliases. If a live caller still uses an old alias, migrate that caller. If a
current deployment contract still requires an old alias, stop and report the
contract blocker instead of preserving the alias in cleanup.

The cleanup still must be evidence-backed, but evidence should drive removal
rather than delay it. A name containing `legacy`, `compat`, `fallback`, `alias`,
`old`, or `deprecated` is a cleanup candidate, not proof. Delete after
call-site, compiler, test, cargo metadata, cargo tree, or documentation-scope
evidence shows that the surface is retired or only exists to bridge to the
current path.

## Aggressive Removal Policy

Default every candidate to `delete` or `update-callers-then-delete`. A live
caller of a retired shape is migration work, not keep evidence. Update the
caller to an already-current package, protocol, CLI, config, or runtime path, then
delete the bridge that kept the retired shape alive.

This is hard removal, not renaming. Do not rename retired APIs, modules,
packages, files, DTOs, operations, CLI entries, or artifacts into a new
compatibility layer. Do not add replacement wrappers. Do not keep fallback
branches, alternate paths, deprecated aliases, or old-to-new adapters. If no
already-current owner exists for required behavior, stop and report that as an
explicit product/API decision instead of inventing a replacement.

Treat compile errors and failing tests after a tentative deletion as a migration
map first. Fix current callers, fixtures, snapshots, and docs to the current
contract before considering a keep decision.

Production source, tooling source, hidden CLI aliases, public exports, package
metadata, and deployment artifacts that preserve retired behavior are cleanup
targets. Tests are secondary: update or delete them only when they preserve a
retired source contract or fail after source cleanup. Do not let tests drive the
cleanup scope ahead of source evidence.

Prefer removing whole files, modules, traits, adapters, fixtures, dependencies,
and empty directories over leaving narrowed wrappers behind. If only one current
implementation remains, inline or collapse the compatibility layer unless it
still removes meaningful complexity.

`keep-with-evidence` is exceptional and must not be used for aliases,
fallbacks, alternate paths, or old-to-new adapters. It requires unavoidable
runtime behavior with exact evidence. Do not keep code because it is public,
might be used, was recently moved, or would require call-site updates.

## Current Target Shape

Preserve this naming model:

```text
sandbox-protocol
sandbox-manager
sandbox-gateway-cli
sandbox-daemon
sandbox-runtime
sandbox-runtime-command
sandbox-runtime-workspace
sandbox-runtime-namespace-process
sandbox-runtime-layerstack
sandbox-runtime-overlay
sandbox-runtime-config
```

Preserve this protocol model:

```text
Request
Response
OperationExecutionSpace
operation_execution_space
command_session_id
```

Preserve these runtime operation names:

```text
exec_command
write_command_stdin
poll_command
read_command_lines
cancel_command
```

Remove these stale public shapes when they appear in production
source, tooling source, active docs, package metadata, CLI/manual output, or
packaging. Test references are cleanup fallout, not the primary audit surface:

```text
daemon_rpc_protocol
daemon_operation
crates/daemon/*
sandbox-runtime-operation
sandbox_runtime_operation
OperationRequest
OperationResponse
SandboxRequest
RoutedRequest
ManagerRequest
OperationTarget
operation_space
command_id
command-id
exec
poll
cancel
eosd
eosd-linux-*
daemon-wire
wire
client compatibility wrappers
legacy daemon helper entrypoints
deprecated CLI aliases
compatibility re-exports
fallback config paths
```

Treat generic words such as `command`, `workspace`, `config`, `client`,
`exec`, `poll`, and `cancel` as stale only when they are old operation names,
old package names, old CLI names, old file/module names, or final-state docs.
Do not delete low-level implementation behavior only because the English word
appears.

## Required Reading

Every subagent must read:

- docs/refactoring/sandbox-implementation-guide.md
- docs/refactoring/sandbox-phase-8-runtime-support-rename-prompt.md
- docs/refactoring/sandbox-phase-9-compatibility-cleanup-prompt.md
- docs/refactoring/sandbox-implementation-guide-completeness-orchestrator-prompt.md
- docs/refactoring/sandbox-runtime.md
- docs/refactoring/sandbox-protocol.md
- docs/refactoring/sandbox-manager.md
- docs/refactoring/sandbox-daemon.md
- docs/refactoring/sandbox-gateway-cli.md
- Cargo.toml
- README.md
- config/README.md
- xtask/src/main.rs

## Baseline Commands

Run before launching subagents:

```sh
git status --short --untracked-files=all
find crates -maxdepth 3 -name Cargo.toml -print | sort
cargo metadata --no-deps --format-version 1 > /tmp/eos-ultra-cleanup-metadata.json
cargo tree -p sandbox-runtime --prefix depth
cargo tree -p sandbox-daemon --prefix depth
```

If the tree is already dirty, preserve unrelated changes. Work with the live
tree. Do not revert user changes.

## Multi-Agent Discovery

Launch the following source-focused subagents in parallel. Discovery subagents
do not edit files. Each subagent returns a deletion ledger with this format. Use
`needs-orchestrator-decision` only as a temporary discovery state; the
orchestrator must convert it to delete, update-callers-then-delete,
remove-dependency, remove-empty-directory, or keep-with-evidence before
implementation completes:

```text
Candidate:
  Path or symbol:
  Category: backward-compatible | alias | fallback | legacy | unused-source | stale-doc | stale-dependency | test-fallout
  Evidence:
    - file:line ...
    - command output summary ...
  Existing current owner, if any:
  Risk:
  Required edits:
  Required verification:
  Decision: delete | update-callers-then-delete | remove-dependency | remove-empty-directory | keep-with-evidence | needs-orchestrator-decision
```

### Subagent 1: Stale Names And Public Compatibility Surface

Find active old names, aliases, shims, re-exports, old modules, and old public
entrypoints.

Focus:

- Protocol DTO aliases or wrappers.
- Old operation names.
- Compatibility `pub use` exports.
- Deprecated modules kept for old import paths.
- Old CLI names and hidden aliases.
- Old file names such as `poll.rs`, `cancel.rs`, or `exec.rs`.
- Final-state docs that still teach old names.

Suggested commands:

```sh
rg -n "daemon_rpc_protocol|daemon_operation|sandbox-runtime[-_]operation|sandbox_runtime[_]operation|OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|operation_space|command-id|command_id\\b" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n 'name: "(exec|poll|cancel)"|"exec"|"poll"|"cancel"' crates/sandbox-runtime crates/sandbox-manager crates/sandbox-gateway-cli README.md docs/README --glob '!target/**'
find crates -path '*/exec.rs' -o -path '*/poll.rs' -o -path '*/cancel.rs'
rg -n "deprecated|alias|compat|compatibility|legacy|backward|shim|old name|old path|re-export|pub use" crates README.md config docs xtask --glob '!target/**'
```

Return only active cleanup candidates. Historical phase prompts are not active
unless the implementation guide or active README points users to them as final
state.

### Subagent 2: Fallbacks, Alternate Paths, And Hidden Behavior

Find fallback behavior that keeps old runtime, config, packaging, CLI, protocol,
or request paths alive.

Focus:

- Environment variable fallbacks for retired config names.
- Multiple config path search orders when one current path should exist.
- Old artifact names.
- Compatibility aliases in `xtask`.
- Hidden CLI aliases.
- Fallback operation routing.
- Default behavior that silently accepts old request shapes.
- Any `or_else`, `unwrap_or_else`, `fallback`, or alternate-path branch whose
  only job is old behavior.

Suggested commands:

```sh
rg -n "fallback|or_else|unwrap_or_else|compat|legacy|alias|deprecated|old|alternate|alternate path|default.*old|EOS_|eosd|sandbox-daemon-linux|command-request\\.json" crates config README.md docs xtask --glob '!target/**'
rg -n "serve|ns-runner|ns-holder|sandbox-daemon|eosd|package|artifact|dist" crates/sandbox-daemon xtask README.md config docs --glob '!target/**'
rg -n "operation_space|operation_execution_space|scope|target|owner|route|forward" crates/sandbox-protocol crates/sandbox-manager crates/sandbox-gateway-cli crates/sandbox-daemon crates/sandbox-runtime/operation --glob '!target/**'
```

For every fallback, classify it:

- Retired compatibility: remove.
- Current required behavior behind a retired wrapper: update callers to the
  existing current owner, then remove the wrapper.
- Current required behavior with no existing current owner: stop and report an
  explicit product/API decision. Do not create a new fallback, alternate path,
  alias, or replacement wrapper.

### Subagent 3: Unused Source Code, Traits, Modules, And Dependencies

Find production source code that is unreachable, redundant, or only exists to
support retired behavior.

Focus:

- Unused dependencies.
- Dead source modules.
- One-method traits that no longer abstract anything.
- Test hooks declared in production source that no longer remove complexity.
- Re-exported symbols with no downstream use.
- Empty modules and directories.
- Duplicated wrappers around a single current function.

Suggested commands:

```sh
cargo machete --with-metadata
cargo check --workspace --all-targets
cargo clippy --workspace --all-targets --no-deps -- -D warnings
rg -n "pub trait|pub(crate) trait|pub use|mod .*;|TODO|unused|allow\\(|dead_code|expect\\(\"unused|test hook|hook|shim|wrapper" crates/*/src crates/sandbox-runtime/*/src xtask/src --glob '*.rs' --glob '!target/**'
find crates -path '*/src/*' -type d -empty -print | sort
```

Optional if available:

```sh
cargo +nightly udeps --workspace --all-targets
```

Do not treat a public symbol as unused only because `rg` finds few matches.
Use crate exports, production callers, package boundaries, compiler evidence,
and dependency graphs first. Tests can confirm behavior, but they should not be
the primary reason to keep source compatibility. If the symbol exists only for
old import paths or old request shapes, migrate any live source callers and
remove the exported compatibility surface.

### Subagent 4: Source Callers That Preserve Retired Behavior

Find production source call sites, source adapters, source fixtures, generated
source, and tooling source that keep old aliases or fallback behavior alive.

Focus:

- Source callers using old DTO names.
- Source callers using old operation names.
- Source callers accepting or emitting `command_id`.
- Source/tooling code producing `eosd` artifacts if `sandbox-daemon` artifacts
  are now primary.
- Source include paths under old `crates/daemon/*` paths.
- Adapter functions whose only purpose is compatibility with retired input.
- Test-support code only when it lives in production `src` or keeps a retired
  production contract alive.

Suggested commands:

```sh
rg -n "daemon_rpc_protocol|daemon_operation|crates/daemon|OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|operation_space|command-id|command_id\\b|eosd|exec\\b|poll\\b|cancel\\b|legacy|compat|fallback|alias" crates/*/src crates/sandbox-runtime/*/src xtask/src --glob '*.rs' --glob '!target/**'
rg -n "include!|include_str!|path =|cfg_attr|test_support|fake|fixture|stub" crates/*/src crates/sandbox-runtime/*/src xtask/src --glob '*.rs' --glob '!target/**'
find crates -path '*/src/*' -type f | sort
```

For each candidate, decide whether to migrate the source caller to an existing
current contract and delete the compatibility layer, or delete the source path
entirely. Do not rename the retired surface into a new current-looking wrapper.
List test changes only as expected fallout from source cleanup.

### Subagent 5: Packaging, Docs, And Deployment Artifacts

Find active docs and packaging/tooling source paths that preserve old names or
old deployment artifacts.

Focus:

- `README.md`
- `config/README.md`
- `docs/README/**`
- `xtask/src/main.rs`
- `Cargo.toml`
- `Cargo.lock`
- generated `dist` names if present.
- Help text.
- Artifact manifests and checksum filters.

Suggested commands:

```sh
rg -n "daemon_rpc_protocol|daemon_operation|crates/daemon|sandbox-runtime[-_]operation|sandbox_runtime[_]operation|eosd|eosd-linux|sandbox-daemon-linux|legacy|compat|fallback|alias" README.md config docs xtask/src Cargo.toml Cargo.lock --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
cargo run -p xtask -- help
cargo check -p xtask
```

The target packaging shape is:

```text
cargo build -p sandbox-daemon --target <target> --profile <profile>
target/<target>/<profile-dir>/sandbox-daemon
dist/sandbox-daemon-linux-amd64
dist/sandbox-daemon-linux-arm64
```

If `eosd-linux-*` still exists, delete it as a primary artifact path. If a
current deploy path still requires it, stop and report the deploy dependency as
a blocker instead of silently preserving an alias.

### Subagent 6: Runtime Cleanup Hotspots

Find cleanup candidates inside runtime crates after the package move wave.

Focus:

- `sandbox-runtime/operation` wrappers that exist only because of old module
  paths.
- Command launch wrappers that duplicate one current driver.
- Workspace/session/remount traits or hooks that no longer remove complexity.
- Namespace runner compatibility code.
- Overlay functions whose names say legacy but whose behavior may still be
  current.
- Config schema adapters that accept retired fields.

Suggested commands:

```sh
rg -n "legacy|compat|fallback|alias|deprecated|old|shim|wrapper|command-request\\.json|mount_overlay_legacy|RunRequest|tool_call|operation_space|command_id|WorkspaceLaunchContext" crates/sandbox-runtime crates/sandbox-daemon crates/sandbox-manager crates/sandbox-protocol --glob '!target/**'
cargo test -p sandbox-runtime --tests
cargo test -p sandbox-runtime-command --tests
cargo test -p sandbox-runtime-workspace --tests
cargo test -p sandbox-runtime-namespace-process --tests
```

Important rule:

If a function has a legacy-looking name but is the only current implementation
of required behavior, do not rename it into another wrapper. Either remove it
after migrating callers to an existing current owner, or stop and list it as
blocked by missing current ownership. A keep decision needs exact evidence.

## Orchestrator Cleanup Plan

After subagents return, create one cleanup ledger:

```text
Batch 1: mechanical stale names and docs
Batch 2: packaging aliases and artifact names
Batch 3: protocol/operation compatibility wrappers
Batch 4: fallback branches and config adapters
Batch 5: unused source modules, traits, dependencies, and test fallout
Batch 6: runtime hotspot simplification
```

For each candidate, choose one final action. Prefer deletion actions.
`keep-with-evidence` is allowed only after delete or migrate-and-delete has been
tested or ruled out by a current contract. Do not use rename as a cleanup
strategy:

```text
delete
update-callers-then-delete
update-tests-after-source-cleanup
remove-dependency
remove-empty-directory
keep-with-evidence
```

Do not keep "just in case" compatibility. A keep decision requires a current
product contract or current deployment path. A failing test can identify source
fallout, but it is not keep evidence until stale expectations have been updated.
A live caller to a retired surface is not enough; migrate the caller unless the
caller itself is the current external contract.

Before editing, print:

```text
Planned cleanup batches:
- Batch:
- Files:
- Expected deletions:
- Verification:
- Risk:
```

Then implement in small batches. After each batch:

```sh
cargo fmt --check --all
git diff --check
```

Run the smallest relevant package checks for the batch before continuing.

## Required Final Verification

Run these before claiming completion:

```sh
cargo fmt --check --all
cargo check -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask --tests
cargo test -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask
cargo clippy -p sandbox-protocol -p sandbox-runtime -p sandbox-daemon -p sandbox-manager -p sandbox-gateway-cli -p sandbox-runtime-command -p sandbox-runtime-workspace -p sandbox-runtime-namespace-process -p sandbox-runtime-layerstack -p sandbox-runtime-overlay -p sandbox-runtime-config -p xtask --all-targets --no-deps -- -D warnings
cargo machete --with-metadata
git diff --check
```

Run stale scans after cleanup:

```sh
rg -n "daemon_rpc_protocol|daemon_operation|crates/daemon/(rpc_protocol|operation|server|command|workspace|namespace-process|layerstack|overlay|config)" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "sandbox-runtime[-_]operation|sandbox_runtime[_]operation" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "operation_space|OperationRequest|OperationResponse|SandboxRequest|RoutedRequest|ManagerRequest|OperationTarget|invoke_sandbox_daemon" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "command-id|command_id\\b" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
rg -n "eosd|eosd-linux|deprecated|compat|compatibility|legacy|fallback|alias|shim|backward" Cargo.toml Cargo.lock crates README.md config docs xtask --glob '!target/**' --glob '!docs/refactoring/sandbox-phase-[1-8]*.md'
find crates -type d -empty -print | sort
```

Any remaining match must be listed with:

```text
Remaining match:
  File:
  Line:
  Why it remains:
  Removal follow-up, if any:
```

## Final Report Format

Return:

```text
Ultra Cleanup Result

Deleted:
- path/symbol: reason and evidence

Fallbacks/Aliases Removed:
- path/symbol: removed bridge and existing current owner, if any

Unused Code Removed:
- path/symbol: proof

Source Callers Migrated:
- old source caller -> current source contract: proof

Dependencies Removed:
- crate: proof

Kept With Evidence:
- path/symbol: exact reason it is still required

Remaining Stale Scan Matches:
- file:line: reason

Verification:
- command: passed/failed/skipped with reason

Follow-Ups:
- only items that could not be safely completed in this pass
```

Completion bar:

- Do not claim completion while compile, tests, clippy, cargo machete, or stale
  scans still have unclassified failures.
- Do not leave compatibility aliases, fallback branches, alternate paths, or
  old-to-new adapters. If one cannot be removed, report it as a blocker rather
  than marking cleanup complete.
- Do not leave live callers on retired names when they can be migrated to current
  names in this pass.
- Do not preserve production source, tooling source, docs, or packaging
  artifacts whose only job is to keep retired behavior supported.
- Do not let test-only cleanup expand the scope beyond source cleanup; update or
  delete tests only as needed to match the new source contract.
- Do not leave removed behavior documented as supported.
- Do not stage or commit unless explicitly asked.
```
