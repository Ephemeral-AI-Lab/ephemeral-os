# Multi-Agent Adversarial Review: Phase 1 — Correctness & Completeness

Use this prompt to run a **read-only, multi-agent** adversarial review of the
**implemented** Phase 1 of the Namespace Execution Engine migration — the new
crate `sandbox-runtime-namespace-execution` plus the relocation of
`NamespaceExecutionId`. The review target is **live code as committed**, judged
against its contract:

```text
docs/namespace_execution_migration/phase-1-spec.md          # the build target (§1–§7)
docs/namespace_execution_migration/migration-phases.md      # § "Phase 1" contract; §"Phase 2-6" = what must NOT appear
crates/sandbox-runtime/namespace-execution/                 # the implementation under review
crates/sandbox-runtime/operation/src/namespace_execution.rs # the move's source + re-export shim
```

## Mission & Premise

The premise is **not** that Phase 1's direction is up for debate. Treat its scope
as fixed and correct:

- a new, compiling, **workspace-agnostic** library crate carrying the engine's
  **types and traits only** (`id`, `error`, `target`, `promise`, `execution`,
  `shell`, `observer`, `registry`), wired to nothing;
- `NamespaceExecutionId` **moved** out of `operation` into `id.rs`, with
  `operation` **re-exporting** it so every existing path keeps resolving;
- **behavior unchanged**: no runtime path, DTO, or observability record changes;
  nothing references the engine.

Your job is to prove the implementation **correctly and completely** delivers
that contract — or to find precisely where it does not. Every finding must end in
a **concrete code edit** (a missing derive added, a wrong signature corrected, a
broken resolution path fixed, a leaked Phase-2 symbol deleted, an inadequate test
strengthened) backed by **proof**: a failing gate, a divergent `file:line`, a
resolution that won't compile, or a test that does not actually test its claim.

"Looks correct" is not an acceptable verdict for any reviewer. If a reviewer's
axis is genuinely clean, it must say so **with the evidence that proves it**
(the exact derives, the exact resolution paths, the exact green gate) and still
report its counts.

This is a **review-only** task. Do **not** implement Phase 2+. Do **not** rewrite
the code unless explicitly asked after the review. The spec is the contract;
**live code is the source of truth** for what actually shipped.

## How To Run (multi-agent)

```text
1. Orchestrator: run the bootstrap once, then spawn the 5 reviewer agents IN
   PARALLEL. Each is blind to the others and owns exactly one lens. Give each
   only: this prompt, its own section, the shared reading list, and the targets.
2. Each reviewer returns findings in the per-reviewer output contract.
3. Orchestrator: spawn the Synthesis agent with ALL five reviewers' findings; it
   merges, de-duplicates, resolves conflicts, and produces one verdict + edit list.
4. Orchestrator: return the synthesis verbatim, plus an appendix of raw findings.
```

Reviewers must not coordinate or converge prematurely — lens diversity is the
point. Overlap is resolved by synthesis, not by reviewers deferring to each other.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

Bootstrap (orchestrator runs once, shares results; `export PATH="$PWD/bin:$PATH"` first):

```sh
git status --short
git log --oneline -6
# The new crate and its single dependency:
sed -n '1,12p' crates/sandbox-runtime/namespace-execution/Cargo.toml
# The id is defined in exactly one place — confirm count is 1:
rg -n "pub struct NamespaceExecutionId" crates/
# The re-export shim that must keep every path resolving:
rg -n "pub use .*NamespaceExecutionId" crates/sandbox-runtime/operation/src
# No Phase 2+ symbol may exist in the new crate (expect: no matches):
rg -n "NamespaceExecutionEngine|NsRunnerLauncher|run_shell_interactive|run_mount|PtyMaster|RunnerChild|fn watcher|spawn_pty|spawn_piped" \
  crates/sandbox-runtime/namespace-execution/src
# The gates (each must be green; record exact output):
cargo check  -p sandbox-runtime-namespace-execution
cargo test   -p sandbox-runtime-namespace-execution
cargo check  -p sandbox-daemon
cargo test   -p sandbox-runtime --tests
cargo test
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
```

## Shared Required Reading

Implementation under review (read first, in full):

```text
crates/sandbox-runtime/namespace-execution/src/{lib,id,error,target,promise,execution,shell,observer,registry}.rs
crates/sandbox-runtime/namespace-execution/Cargo.toml
crates/sandbox-runtime/operation/src/namespace_execution.rs   # :8 shim; :16/:34/:50/:68 Store/Record/Lifecycle/TerminalStatus; :143-145 allocate→format!
crates/sandbox-runtime/operation/Cargo.toml                   # the new dependency line
Cargo.toml                                                    # members += namespace-execution; [workspace.dependencies] path dep
```

Contract:

```text
docs/namespace_execution_migration/phase-1-spec.md            # §5.3–5.11 signatures; §7 Acceptance Criteria; §8 Anchor Ledger
docs/namespace_execution_migration/migration-phases.md        # §"Phase 1"; Appendix = Phase 2+ deferrals
```

Live code — verify the implementation's reuse and the unchanged consumers; do not
trust any citation, re-check every `file:line`:

```text
crates/sandbox-runtime/namespace-process/src/runner/protocol.rs  # :14 NsFds; :21-35 NamespaceRunnerRequest(8 fields); :37-41 RunResult{exit_code:i32,payload:Value}
crates/sandbox-runtime/operation/src/lib.rs                      # :8 mod namespace_execution; :22 pub use {…NamespaceExecutionId…}
crates/sandbox-runtime/operation/src/services.rs                 # imports id via crate::namespace_execution::… (must still resolve)
crates/sandbox-runtime/operation/src/command/service/{core,finalize,process_store,impls/exec_command}.rs  # internal importers (Phase 3 deletion targets; untouched now)
crates/sandbox-runtime/operation/tests/{namespace_execution,exec_command}.rs  # assert id.0 == "namespace_execution_1" via the re-export
crates/sandbox-daemon/src/observability/service.rs               # :17 imports NamespaceExecutionId; :271 returns Vec<NamespaceExecutionId>
crates/sandbox-daemon/tests/unit/observability.rs                # :13 import; :90/137/238/1028/1048/1152 construct NamespaceExecutionId("…".to_owned())
```

## Shared Ground Rules

- Live code wins over the spec; the spec wins over your assumptions.
- Every finding cites `file:line` (code and/or spec) and ends in a concrete edit
  + the proof (failing gate, wrong derive, broken resolution, vacuous test).
- Stay inside the fixed scope (skeleton + id move, behavior unchanged, dep set =
  `namespace-process` only). You may attack any **divergence, omission, incorrect
  primitive, behavior change, or Phase-2 leak**; you may not relitigate the
  skeleton-only scope, the deferral list, or the single-dependency decision.
- Separate live-code facts from inferred advice. A fix built on a misread anchor
  is worse than none — verify before you assert.

## Quantitative Targets

Each reviewer reports the relevant counts. Synthesis drives every one to its
required value.

```text
Definition sites of NamespaceExecutionId:                          exactly 1 (namespace-execution/src/id.rs)
Derives on NamespaceExecutionId + field visibility:               exactly Debug,Clone,PartialEq,Eq,Hash,PartialOrd,Ord + `pub` tuple field
Resolution paths that must still compile:                          sandbox_runtime::NamespaceExecutionId; crate::namespace_execution::NamespaceExecutionId; daemon's sandbox_runtime::{…} — count any broken (target 0)
Public re-exports from lib.rs:                                     exactly 8 (id,error,target,ExecutionHandle,InteractiveExecution,ShellOperation,RunnerOutcome,ExecutionObserver); CompletionPromise/ExecutionRegistry pub(crate), NOT exported
New-crate dependencies:                                            exactly 1 (namespace-process); serde/serde_json/rustix/nix/libc/thiserror present = finding
Phase 2+ symbols in namespace-execution/src:                      target 0
Per-type signature divergences from §5.3–5.11:                    target 0, list each
Verification gates green (the 6 commands):                         all pass; any failure proven pre-existing on main or it is a finding
Inline tests that vacuously pass (assert nothing load-bearing):    target 0, list each
```

---

## Reviewer 1 — The Move & Re-export Resolution

**Lens:** the one externally observable change Phase 1 makes — the resolution path
of a single type name. If any consumer stops resolving, or the symbol's identity
changed, Phase 1 has broken its core guarantee.

**Mandate:** prove `NamespaceExecutionId` is the *same* type, defined *once*, with
every prior path still resolving through the shim — or name the exact breakage.

**Seed hunt list (find more):**

- **Byte-identity of the moved type.** `id.rs` must carry exactly the 7 derives
  (`Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord`) and the **`pub` tuple
  field**. A dropped `Ord`/`Hash` breaks the `HashMap` key
  (`namespace_execution.rs` active map) and the `.cmp()` sort; a non-`pub` field
  breaks the daemon's `NamespaceExecutionId("…".to_owned())` construction
  (`observability.rs:90`). Diff against the deleted def in the spec (§5.4).
- **Single definition.** `rg "pub struct NamespaceExecutionId"` must return one
  hit (`id.rs`). Any second definition (a forgotten copy, a `type` alias) is L0.
- **Every resolution path.** Confirm `operation/src/lib.rs:22` still re-exports
  `NamespaceExecutionId` *through* `namespace_execution`; that `services.rs` and
  the four `command/service/*` importers still resolve via
  `crate::namespace_execution::NamespaceExecutionId`; that the daemon's
  `sandbox_runtime::{…NamespaceExecutionId…}` (`service.rs:17`) and its tests
  resolve. Count any path that no longer compiles.
- **Identity, not just name.** Is the re-exported symbol the *same* type (a
  `pub use`), or did the shim accidentally introduce a wrapper/newtype/alias that
  changes trait impls or field access? Construction-by-field and `id.0` reads must
  behave identically.
- **Allocation stayed put.** `allocate_namespace_execution_id` /
  `format!("namespace_execution_{n}")` must remain on `NamespaceExecutionStore`
  (`namespace_execution.rs:143-145`), so `id.0 == "namespace_execution_1"` still
  holds (`tests/namespace_execution.rs`, `tests/exec_command.rs`). Confirm it was
  not moved or duplicated into the new crate.

**Output:** an id-identity ledger (derive-by-derive + field visibility, claimed →
actual), a resolution-path table (path → resolves? → `file:line`), and a verdict
on whether the move preserved the symbol exactly.

## Reviewer 2 — Skeleton Fidelity (no missing, no extra)

**Lens:** whether each of the 8 modules matches the spec's named shape at the
minimum depth that compiles — nothing required dropped, nothing extra smuggled in.

**Mandate:** for every type/trait, diff the implemented signature against
§5.3–5.11; report each divergence as missing / wrong / extra.

**Seed hunt list (find more):**

- `target.rs`: `NamespaceTarget` exactly 5 fields
  (`workspace_root, layer_paths, upperdir, workdir, ns_fds`) with
  `ns_fds: sandbox_runtime_namespace_process::runner::protocol::NsFds` **reused,
  not redefined**; no `timeout`/`workspace`/`WorkspaceSessionId` field; no
  `From<WorkspaceEntry>`; no `serde`.
- `error.rs`: 3 variants exactly (`Spawn(String)`, `Finalize(String)`,
  `Admission { max_active: usize }`); **hand-rolled** `Display` + `Error` (no
  `thiserror`); no `Cancelled`/`TimedOut`.
- `shell.rs`: `RunnerOutcome(RunResult)` exposing **only** `exit_code() -> i64`
  (an `i32` widened — no `serde_json`); no constructor; `ShellOperation` with
  `Output`, `operation_name`, `command`, `timeout_seconds`, `finalize`; **no**
  `status()`/`payload()`, no `MountOperation`, no `InteractiveShellOperation`.
- `observer.rs`: `ExecutionObserver: Send + Sync` with **`on_running` only**; no
  `on_terminal`/`begin`.
- `execution.rs`: `ExecutionHandle<T>{id,promise}` + `InteractiveExecution<T>{exec}`
  by **composition** (no `Deref`); inherent/forwarding methods exactly as §5.8;
  **no** `Execution<T>` trait, **no** PTY field, **no**
  `write_stdin`/`read_output_since`/`output_len`/`cancel`/peeking `wait_timeout`.
- `promise.rs`/`registry.rs`: `CompletionPromise`/`ExecutionRegistry` are
  `pub(crate)` and **not** re-exported by `lib.rs`; `ExecutionRegistry` is the
  capacity-only placeholder (`new` + `max_active`), with **no** maps / lookup /
  `try_reserve`.
- `lib.rs`: 8 `mod`s + exactly the 8 `pub use`s; **no** `#![forbid(unsafe_code)]`.

**Output:** a per-module fidelity table (spec signature → implemented signature →
`match / missing / wrong / extra`), with each divergence's exact `file:line` and
the one-line edit that closes it.

## Reviewer 3 — Phase-Boundary Discipline (leakage AND premature omission)

**Lens:** the two-sided boundary — Phase 1 must contain everything the contract
puts in Phase 1 and **nothing** from Phase 2+.

**Mandate:** scan for both directions and report each violation.

**Seed hunt list (find more):**

- **No Phase 2+ leak** (grep + read): `NamespaceExecutionEngine`, `NsRunnerLauncher`,
  watcher thread, `PtyMaster`, `RunnerChild`, `run_shell_interactive`/`run_mount`,
  `spawn_pty`/`spawn_piped`, admission enforcement, registry maps/id lookup/
  `try_reserve`, `Store`→`Ledger` rename, `origin_request_id`, `on_terminal`,
  any `impl ExecutionObserver`, `From<WorkspaceEntry>`, any
  `execution_kind`/`backing` observability axis. Each present = finding (note:
  the grep must also stay clean of these tokens in **doc comments**, not just
  code — a deferral note that names a Phase-2 type still trips the guard).
- **Nothing required wrongly deferred:** all 8 modules present and compiling; the
  8 re-exports present; the inline tests the spec requires (`id` newtype+Hash;
  `CompletionPromise` resolve/timeout; handle composition+forwarding; registry
  capacity) all present and run.
- **Observability untouched:** `NamespaceExecutionStore`/`*Record`/`*Lifecycle`/
  `*TerminalStatus`/`RuntimeNamespaceExecutionSnapshot` remain in `operation`,
  unrenamed, no `ExecutionObserver` impl. Confirm against `namespace_execution.rs`
  (`:16/:34/:50/:68`) and the daemon observability code.

**Output:** a two-column violation list (`Phase-2 leaked in` | `Phase-1 wrongly
omitted`), each with `file:line` and the corrective edit (delete the leak / add
the omission).

## Reviewer 4 — Behavior-Unchanged Proof

**Lens:** the headline guarantee. Phase 1 is a move + an unreferenced skeleton, so
**every** downstream consumer must compile and behave exactly as before.

**Mandate:** prove no observable change — or find the one that slipped through.

**Seed hunt list (find more):**

- **The gates actually pass.** Run all six bootstrap commands; record exact
  output. `cargo test -p sandbox-runtime --tests` must show `exec_command` and
  `namespace_execution` green (the regression proof via the re-export);
  `cargo check -p sandbox-daemon` and the whole-workspace `cargo test` green. Any
  failure: prove it is **pre-existing on `main`** (the new crate is
  platform-neutral; it adds no `cfg(target_os)` code) or it is a finding.
- **No DTO / record / wire change:** the daemon observability rows, the
  `NamespaceExecutionRecord` shape, and any serialized form are byte-identical.
  Phase 1 added no `serde` derive to the moved id (it had none).
- **No new public symbol on the `operation` surface** beyond the re-exported id;
  `lib.rs:21-25` exports the same set as before.
- **The internal importers and tests are literally untouched** (the move is
  source-transparent): confirm `services.rs`, the four `command/service/*` files,
  and the two test files have no diff attributable to this phase.

**Output:** a consumer-by-consumer "unchanged?" table (consumer → compiles+behaves
identically? → evidence), the six gate results, and an explicit statement of any
observable behavior delta (ideally: none, proven).

## Reviewer 5 — Primitive Correctness & Test Adequacy

**Lens:** the one place Phase 1 carries real logic — `CompletionPromise` — and
whether the inline tests actually prove what they claim. A skeleton that compiles
but whose only live primitive is subtly wrong is the worst outcome.

**Mandate:** audit the condvar protocol for correctness, then judge every inline
test for whether it exercises a load-bearing property or passes vacuously.

**Seed hunt list (find more):**

- **`CompletionPromise` protocol correctness:**
  - `wait` is single-consumer and **takes** the value (`Ready → Taken` via
    `mem::replace`); a second `wait` must not double-deliver. Is the `Taken`/
    `Pending` arm after the `while` loop genuinely unreachable, and is
    `unreachable!` the right call (vs a value loss)?
  - `resolve` is write-once: returns `true` on `Pending → Ready`, `false`
    thereafter, and `notify_all`s exactly once.
  - `wait_timeout(Duration)` returns `is_resolved()` at wake, blocks (no busy
    poll), and is correct under a spurious wake-up.
  - Spurious-wakeup safety: the `while matches!(Pending)` guard, not an `if`.
  - Poisoned-mutex handling uses `.expect(...)`, never `.unwrap()`
    (`Cargo.toml` warns `unwrap_used`). Confirm no `unwrap` slipped in.
- **Test adequacy (per inline test):** does
  `resolve_then_wait_yields_value` prove the *value* survives resolve→wait *and*
  the second-resolve rejection? does `wait_timeout_on_pending_returns_false`
  actually bound the wait (not flake)? does the `id` test exercise **`Eq + Hash`**
  (insert twice) and not just field read? does the handle test prove **forwarding
  for both `is_finished` states** and `id()`/`execution()` agreement, or only the
  resolved path? does the registry test do more than echo a constant? List each
  vacuous or partial test and the missing case.
- **The `#[cfg_attr(not(test), allow(dead_code))]` mechanism:** verify it keeps
  the `dead_code` lint **live under `cfg(test)`** (so a test dropping a method is
  still caught) while silencing the unreferenced-skeleton lib build — and that it
  is applied to exactly the test-only `pub(crate)` items, masking nothing that is
  genuinely removable (cross-check with Reviewer 3 / the cleanness review).

**Output:** a `CompletionPromise` correctness ledger (property → holds? →
evidence/counterexample), a per-test adequacy table (test → proves what → vacuous?
→ missing case), and the single highest-risk correctness gap.

---

## Synthesis Agent — Verdict & Edit List

**Input:** all five reviewers' raw findings.

**Method:**

1. De-duplicate; keep the strongest `file:line` evidence.
2. Resolve cross-lens conflicts explicitly (e.g. R2 calls a method "extra" but R3
   shows the contract requires it present — decide and state why).
3. Drive every Quantitative Target to its required value; show actual vs required.
4. Produce one **PASS / FAIL verdict per Acceptance Criterion** (§7 of the spec)
   and the precise edits that close any gap.

**Output:**

```text
Acceptance Verdict (§7 checklist, each: PASS/FAIL + evidence)

Scorecard
  <each quantitative target: required → actual, with the gap if any>

Findings (merged, severity-ordered, deduped)
  N. [Lens] [Severity] Title
     Evidence:  file:line (code and/or spec)
     Proof:     <failing gate | wrong derive | broken resolution | vacuous test>
     Edit:      <concrete code change>
     Risk:      <constraint, if any>

Conflicts Resolved
  <lens-vs-lens decisions and rationale>

Residual Risk / Deferred-correctly-to-Phase-2 (so the next phase inherits a clean base)
```

## Severity Scale

Severity = distance from the Phase-1 contract, not future runtime risk:

```text
L0  A core guarantee is broken as shipped: the id lost a derive / its pub field,
    a resolution path won't compile, CompletionPromise can lose or double-deliver
    a value, or a required gate fails (and not pre-existing). Must fix.
L1  A required element is missing, or a Phase-2 symbol leaked in.
L2  A signature/visibility/derive divergence from §5.3–5.11 with no current
    breakage (compiles, but not as specified).
L3  A test passes vacuously, or a clarity/consistency issue that future readers
    will trip on.
```

## Forbidden Recommendations

- Do not propose implementing any Phase 2+ behavior (engine, launcher, PTY,
  watcher, admission, `on_terminal`, observer impls, the `Store→Ledger` rename,
  `origin_request_id`) — that is out of scope and a separate phase.
- Do not propose adding `serde`/`thiserror`/`rustix`/`nix`/`libc` to the crate;
  the single-dependency decision is settled (§2 Decision 1).
- Do not relitigate the skeleton-only scope or which types Phase 1 declares —
  Reviewer 3 polices the boundary against the contract, not against taste.
- No "looks fine" verdicts; no broad rewrites where a one-line edit suffices.

## Rules

- Lead with concrete findings, not summaries. Cite `file:line`.
- Every finding ends in a concrete edit with its proof.
- Separate live-code facts from inferred advice.
- A reviewer who finds little still reports its counts and names the single
  biggest residual risk on its axis.
- The synthesis agent outputs one verdict + one edit list, not a menu.
