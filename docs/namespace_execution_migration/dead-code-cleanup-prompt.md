/goal Find and remove **redundant, unused, dead, or legacy** code — methods, fields, structs/enums, functions, whole files, and stale dependencies — across the Rust workspace, without deleting anything a later migration phase still needs.

## Contract
Removal must leave the workspace **green and behavior-preserving**: every gate below passes before and after, and no externally observable behavior changes. Respect the boundary law in `README.md` and the engineering practice in `CLAUDE.md` ("Prefer less", SRP, no inline comments in production code, no test code in `src/`). Make **additive, localized** edits; never revert or overwrite a parallel worker's in-flight changes. Prefer the smallest change that removes the dead thing — do not refactor surrounding code beyond what the removal forces.

## The load-bearing guardrail — do NOT remove migration-staged code
This repo is mid-migration (`docs/namespace_execution_migration/migration-phases.md`, `docs/e2e/`). A lot of code looks "redundant" because it is **deliberately duplicated/retained until a specific later phase deletes it atomically**. Removing it early breaks the migration. Before deleting anything, confirm it is NOT on a staged-removal list. Known retained-on-purpose items (verify against the live phase docs, this list may be stale):
- **Start-ack** wiring (`--start-ack-fd`, the ack byte) in the engine launcher AND `daemon/src/runner.rs` — removed only in **Phase 6** (atomic cut across both). Keep.
- `command/src/pty.rs`, `command/src/process.rs`, the result-fd reader thread on surviving paths — deleted only in **Phase 6**. The engine's `pty.rs`/`launcher.rs` intentionally *relocate-not-replace* these until then. Keep both copies.
- `workspace/.../setns_runner.rs::run_child`, `ns_runner_request`, the `isolated-{mode}-{id}` format — deleted in **Phase 4**. Keep.
- `NamespaceExecutionStore` (not yet renamed to `…Ledger`), `request_id` (not yet `origin_request_id`) — **Phase 3**. Keep.
- `#[cfg(feature = "test-support")]` seams and `tests/support` fixtures — these are *used by tests*, not dead; "unused without the feature" is expected. Keep.
If you cannot prove an item is not future-staged, **leave it and list it under "Deferred — possibly staged"** with the question to resolve. When in doubt, do not delete.

## What to hunt (in priority order)
1. **Truly dead production code**: `pub`/private items never constructed or called anywhere (after accounting for trait-object dispatch, `cfg` branches, and macro use). Lean on the compiler: per-crate `cargo clippy --all-targets --no-deps -- -D warnings` already denies many; also scan with `RUSTFLAGS="-W dead_code -W unused" cargo check --workspace --all-targets` and read every warning.
2. **Unused dependencies / features**: run `cargo machete` (or `cargo +nightly udeps` if available); cross-check `[dependencies]`/`[features]` against actual `use`/`dep::` sites. Remove unused crate deps and unused feature flags (but keep `test-support` self-deps that tests rely on).
3. **Dead fields / variants**: fields written-but-never-read, enum variants never constructed, struct methods with no caller. Confirm via `rg` for every reference, not just a grep of the name.
4. **Superseded / redundant helpers**: two functions doing the same thing where one has no callers; re-exports nobody imports; `pub` that should be `pub(crate)`/private (narrow visibility instead of deleting when the item is still used internally).
5. **Orphan files / modules**: `.rs` files not reachable from any `mod`/`lib.rs`/`main.rs`; empty or placeholder modules; commented-out blocks; stale `tests/` fixtures with no consumer.
6. **Legacy leftovers**: TODO-marked temporary code whose condition has passed, `_for_test` items no longer referenced, dead `cfg` arms for platforms/features no longer built.

## Method
Go one logical removal at a time. For each candidate: (a) prove deadness — show with `rg`/compiler that there are zero live references across the whole workspace (including tests, benches, xtask, `cfg(not(...))` arms, and trait impls used via `dyn`); (b) check the staged-removal guardrail above; (c) make the minimal edit (delete, or narrow visibility if still used internally); (d) re-run the gates; (e) only then move to the next. Batch related trivial removals, but never let the tree go red between batches. Narrowing visibility (`pub` → `pub(crate)` → private) is preferred over deletion when an item is still used — it shrinks API surface and lets the compiler catch future dead code.

## Verify (must pass after every batch; the whole set must pass at the end)
```
export PATH="$PWD/bin:$PATH"
cargo fmt --check
cargo build --workspace
cargo test  --workspace            # if a target is blocked by a pre-existing host/Linux constraint, record it and confirm it also fails on the pre-change tree
cargo clippy --workspace --all-targets -- -D warnings
cargo run -q -p xtask -- check-inline-tests
cargo run -q -p xtask -- check-cfg
cargo run -q -p xtask -- check-mod-lib-size
cargo run -q -p xtask -- check-crate-source-size
git diff --check
```
A removal that turns any gate red must be reverted or rescoped — do not suppress with `#[allow(...)]` (the xtask policies forbid broad allows in `src/` anyway).

## Output
1. The applied removals in the working tree (green per the gates above).
2. A removal ledger (e.g. `docs/cleanup/dead-code-removal-ledger.md`): per item — what was removed/narrowed, `file:line`, the evidence it was dead (the zero-reference proof or the compiler warning), and the gate run that confirms green. Plus a "Deferred — possibly staged" section listing anything you suspected but did not touch, with the reason and the question to resolve.
3. `git diff --numstat` summary (lines removed should dominate).

Done when the gates are all green, every applied removal has a deadness proof in the ledger, no migration-staged item was deleted, and the deferred list captures every uncertain candidate.
