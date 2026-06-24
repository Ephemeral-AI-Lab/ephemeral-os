# Dead-code removal ledger

Scope: find and remove redundant/unused/dead/legacy code across the Rust
workspace **without** deleting anything the namespace-execution migration
(`docs/namespace_execution_migration/`) or the e2e migration (`docs/e2e/`) still
needs.

Method per item: prove zero live references workspace-wide (`rg` / compiler),
check the staged-removal guardrail, make the minimal edit, re-run the gates.

> **Concurrency note.** This cleanup ran while a concurrent session under the
> same git identity was performing an overlapping review/cleanup **and** the
> namespace-execution Phase 3 implementation. That session independently
> surfaced the **same** dead items and committed them in
> `43b999276 "Prune unused helpers; add NoopObserver and phase-2 review doc"`.
> The removals below therefore landed in committed history rather than as a
> standalone working-tree diff — the end state is identical. All remaining
> candidates fall in either the boundary `sandbox-protocol` crate or crates the
> concurrent worker is actively editing, so they were **deferred** rather than
> risk overwriting in-flight work (`CLAUDE.md`: "never revert or overwrite
> changes you did not make").

---

## Applied removals (committed in `43b999276`)

Each item proven dead by a workspace-wide zero-reference search (`rg -w` across
`crates/` + `xtask/`, including `tests/`, after accounting for trait dispatch,
`#[from]`, `cfg`, and serde).

| # | Item | Location | Deadness proof |
|---|---|---|---|
| 1 | `pub fn require_u32_at_least` | `sandbox-config/src/configs/validate.rs` | 0 external refs (`rg -w require_u32_at_least` → only the def). Sibling validators all have callers; the family is add-as-needed (no `require_u32_at_most`/`require_f64_*` symmetry kept), so the unused variant is dead, not intentional symmetry. |
| 2 | `pub fn require_usize_at_most` | `sandbox-config/src/configs/validate.rs` | 0 external refs (`rg -w require_usize_at_most` → only the def). |
| 3 | `#[doc(hidden)] pub fn build_compaction_checkpoint` | `sandbox-runtime-layerstack/src/stack/ops/squash.rs` | 0 refs anywhere incl. `tests/` (`rg -w build_compaction_checkpoint` → only the def). A thin validating wrapper over `build_projected_checkpoint`, which is independently called by `build_copy_through_checkpoint` and `squash`. `#[doc(hidden)]` test seam with no test consumer. Removal also dropped the now-unused `LayerRef` import. |
| 4 | `pub fn WorkspaceBinding::layer_path_from_relative` | `sandbox-runtime-layerstack/src/workspace_base/binding.rs` | 0 refs (`rg -w layer_path_from_relative` → only the def). Production path→layer mapping uses `LayerPath::parse` + the free `layer_path_from_relative_or_drop` (capture.rs / layer/read.rs), never this method. |
| 5 | `pub fn WorkspaceBinding::layer_path_from_absolute` | `sandbox-runtime-layerstack/src/workspace_base/binding.rs` | 0 refs (`rg -w layer_path_from_absolute` → only the def). Superseded as #4. |
| 6 | private `fn required_path`, `fn normalize_layer_path` | `sandbox-runtime-layerstack/src/workspace_base/binding.rs` | Cascade: their only callers were #4/#5. Compiler-verified dead after #4/#5 removed. Removal also dropped the now-unused `PathBuf` import. |
| 7 | `#[must_use] pub fn OverlayError::failing_path` | `sandbox-runtime-overlay/src/lib.rs` | 0 refs (`rg -w failing_path` → only the def). A `Capture`-path accessor on a public error enum with no downstream reader in this non-published workspace crate. `Path` import stays (used by `overlay_writable_root`). |

**Provenance check** (not migration-staged): all seven were last modified in
`e52749c57 (2026-06-21) "Move daemon crates into sandbox runtime"` — a
structural relocation, **not** one of the active `2026-06-25` migration commits
— and none appears in any spec under `docs/` (verified by
`rg -w <sym> docs/`). The layerstack items are settled leftovers from earlier
layerstack refactoring ("prune layerstack commits" in history); the
`occ_merge_publish` area is dormant (README only).

### numstat (removal portion of `43b999276`)

```
0   32  crates/sandbox-config/src/configs/validate.rs
1   15  crates/sandbox-runtime/layerstack/src/stack/ops/squash.rs
1   47  crates/sandbox-runtime/layerstack/src/workspace_base/binding.rs
0    8  crates/sandbox-runtime/overlay/src/lib.rs
-----------------------------------------------------------------
2  102  → net −100 LOC (removals dominate)
```

---

## Verify (gate snapshot @ `b3a1291d4`, which contains these removals)

Run against the committed tree at `b3a1291d4` (child of the removal commit):

| Gate | Result |
|---|---|
| `cargo fmt --check` | **OK** |
| `cargo build --workspace` | **OK** |
| `cargo clippy --workspace --all-targets -- -D warnings` | **OK** |
| `cargo run -q -p xtask -- check-cfg` | **OK** |
| `cargo run -q -p xtask -- check-mod-lib-size` | **OK** |
| `git diff --check` | **OK** |
| `cargo run -q -p xtask -- check-inline-tests` | **FAIL — not in scope** |
| `cargo run -q -p xtask -- check-crate-source-size` | **FAIL — not in scope** |

The two failing gates are **not caused by this cleanup**:

- **`check-inline-tests`** — `namespace-execution/src/launcher.rs:357,361`
  (`#[cfg(test)]` / `#[test]` in `src/`). Introduced by the concurrent worker's
  `b3a1291d4 "Add namespace execution setup timeout handling"` (03:19:52,
  mid-session); **absent** at the session-start baseline `294f4c726`
  (`git show 294f4c726:.../launcher.rs | rg -c '#\[test\]'` → 0). This is the
  worker's in-flight Phase 3 code (they relocate such tests to `tests/`, cf.
  `705aa86f0`). Untouched by this cleanup.
- **`check-crate-source-size`** — `sandbox-observability/src/store.rs` is 1584
  lines (> 1000). **Pre-existing**: 1584 lines at baseline `294f4c726` and at
  `HEAD~10`. Untouched by this cleanup.

`cargo test --workspace` was not captured as a clean snapshot: after
`b3a1291d4` the concurrent worker began a live refactor (adding a generic `<V>`
to `ExecutionRegistry`/`NamespaceExecutionEngine`, removing `CompletedExecution`,
adding an e2e `cleanup` module), leaving the working tree intermittently
non-compiling. The four removed-from crates (`sandbox-config`,
`sandbox-runtime-layerstack`, `sandbox-runtime-overlay`) build and clippy clean
in isolation (`cargo build/clippy -p …`), and full-workspace `build` + `clippy
--all-targets` were green at `b3a1291d4`.

---

## Deferred — possibly staged or boundary-sensitive (NOT removed)

### A. Migration-staged — do **not** remove until the owning phase

Per `docs/namespace_execution_migration/migration-phases.md` (repo is at end of
Phase 2 / pre-Phase 3 — old command store, `run_child`, `process.rs`/`pty.rs`,
and start-ack all still present):

- Entire `sandbox-runtime-namespace-execution` engine surface (Phases 1–2
  scaffolding; not yet wired into the command path).
- `command/src/{process,pty}.rs`, the result-fd reader thread — Phase 6.
- `--start-ack-fd` / start-ack wiring in the engine launcher **and**
  `daemon/src/runner.rs` — Phase 6 atomic cut.
- `workspace/.../setns_runner.rs::run_child`, `ns_runner_request`,
  `isolated-{mode}-{id}` — Phase 4.
- `NamespaceExecutionStore` (not yet `…Ledger`), `request_id` (not yet
  `origin_request_id`) in `operation/src/namespace_execution.rs` — Phase 3.
- `RemountCancellationToken::request_cancel`
  (`operation/src/workspace_remount/service/command/quiesce.rs:85`) — 0 current
  refs, but `RemountCancellationToken` is the object the **Phase 5** remount
  coordinator rewires onto. Defer to Phase 5.
- `#[cfg(feature = "test-support")]` seams + `tests/support` fixtures; the
  `test-root-override` feature (overlay) — used by tests, expected-unused
  without the feature. Keep.

### B. Dead but on the boundary `sandbox-protocol` crate — held for review

`sandbox-protocol` owns the cross-boundary DTO/help vocabulary and is being
catalogued **by file+line** by the active e2e migration (e.g.
`docs/e2e/sandbox-e2e-live-test-phase-2-spec.md` pins `response.rs:20-22`,
`:25-27`, `:30-49`). Editing it shifts those "confirmed" references and risks
colliding with the concurrent worker (who pruned other crates but left protocol
untouched). All proven dead, but deferred under "when in doubt, do not delete":

| Item | Location | Proof / note |
|---|---|---|
| `Request::required_path` | `request.rs:50` | 0 refs. (Its earlier apparent refs were name-collisions with the layerstack `required_path` removed in #6.) |
| `Request::optional_path` | `request.rs:54` | 0 refs; body uses `optional_string` (stays live). |
| `Request::required_usize` | `request.rs:85` | 0 refs. **Cascade**: its only call to `required_u64` is internal, so removing it also makes `pub required_u64` (`request.rs:68`) dead — a chain best done deliberately, not mid-migration. |
| `Response::service_error` | `response.rs:20` | 0 refs; superseded by direct `fault_with_details("operation_failed", …)`. **But** the e2e phase-2 spec catalogs `response.rs:20-22` as "confirmed". Defer. |
| `Scope::is_system` | `scope.rs:32` | 0 refs; complement of `is_sandbox` (used by `daemon/.../dispatch.rs:181`). |

Recommendation: prune these in one focused follow-up commit on `sandbox-protocol`
**after** the e2e + namespace migrations settle, re-running the gate set.

### C. Dead but in actively-edited / migration-hot crates — verify post-migration

| Item | Location | Note |
|---|---|---|
| `ConfigDocument::to_yaml_string` | `sandbox-config/src/document.rs:48` | 0 refs, but its doc comment frames it as e2e test-harness infra and the e2e migration is mid-flight (phase-3 spec is uncommitted in the tree). Defer to e2e. |
| `assertion_count` | `sandbox-e2e-live-test/src/assertion.rs:15` | 0 refs; the e2e crate is being actively rewritten by the worker (new `cleanup` module). Defer. |
| `CommandServiceError::MissingLayerStackService` | `operation/src/command/error.rs:55` | No construction site found (not `#[from]`). Possibly dead, but `CommandServiceError` is reworked in Phase 3 (variant merges/renames). Verify after Phase 3. |
| `WorkspaceSessionError::PublishCapturedChanges` | `operation/src/workspace_session/error.rs:47` | No construction site found (not `#[from]`). In the migration-hot `operation` crate. Verify after the migration. |

---

## What was checked and found clean (no action)

- **Unused dependencies / features:** `cargo machete` → none. All `[features]`
  are `test-support` / `test-root-override` seams (guardrail: keep).
- **Compiler-visible dead code:** `RUSTFLAGS="-W dead_code -W unused" cargo
  check --workspace --all-targets` → no warnings (clippy `-D warnings` is a
  standing gate, so no private dead code / unused imports exist).
- **Unused `pub use` re-exports:** scripted cross-crate scan → 0 (every
  re-exported symbol has an external or test consumer).
- **Orphan modules:** none (`src/bin/eos-e2e.rs` is an auto-discovered bin
  target, not an orphan).
- **Dead `#[from]` error variants:** `StoreError::{Sqlite,InvalidRecord}`,
  `LayerStackError::Cas`, `ConfigError::Merge` flagged by a naive variant grep
  are **not** dead — `#[from]` constructs them via `?`/`.into()`.
- **Commented-out code / `TODO`/`FIXME`/`legacy` markers in `src/`:** none.
