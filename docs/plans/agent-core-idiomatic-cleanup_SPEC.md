# SPEC: agent-core Idiomatic OO & Refactor Cleanup

Status: DRAFT
Date: 2026-06-05
Owner workspace: `agent-core/`
Scope: `eos-engine`, `eos-sandbox-host`, `eos-db`, `eos-obs-collector`,
`eos-plugin-catalog`, `eos-state`, `eos-workflow`.

This spec captures the **only** code changes that survived two read-only,
adversarially-verified reviews of `agent-core/crates`:

1. An **OO-pattern review** (what should be converted to idiomatic Rust
   object-oriented patterns). Result: the codebase is already heavily idiomatic
   (ports as `Arc<dyn>` + `#[async_trait]`, typed-ID newtypes, closed-set enums,
   parse-don't-validate at every boundary, RAII `Drop` guards). Of all
   candidates, **5 survived** — every one a **net-negative deletion or
   de-duplication**, not new abstraction.
2. A **refactor review** (cohesion-splits + cross-module duplication). Result:
   of 10 large files, **0 are split-worthy** (all mechanically cohesive single
   adapters / state-machines / parsers, all below the 800–1000+ LOC review-smell
   band). **2 optional micro-cleanups** survived.

The honest baseline is that there is **very little to do**, and doing nothing is
a defensible outcome. Every item below is a small, behavior-preserving cleanup
that reduces a concrete current cost (dead surface, leaked concrete types,
counted duplication). **No item adds an abstraction, config, or extension
point** — consistent with the repo's surgical-scope / net-negative bar.

This spec exists so these changes can be executed deliberately (not
opportunistically) and so the **rejected** candidates are recorded and not
re-litigated.

---

## Provenance & method

Findings were produced by per-crate / per-file discovery agents under a
consequence-gated contract (a finding requires a concrete current consequence,
not a pattern-match opportunity), then each was independently
adversarially-verified against the repo's simplicity rules and against Rust
object-safety / async-trait discriminators. All code anchors below were
re-verified against the working tree on 2026-06-05; line numbers may drift under
parallel agent edits — anchor on the symbol, not the line.

---

## Progress

| Phase | Status | Notes |
|---|---|---|
| Phase 0: Spec | Complete | This document. |
| Phase 1: Unconditional net-negative cleanups (zero risk) | Not started | Items 1–6. Independent; landable in one sweep. |
| Phase 2: Optional judgment-dependent cleanups | Not started | Items 7–8. Low value; do only when already in the file / module. |

Progress update rules:

- Mark a phase `Complete` only after every item's acceptance check passes.
- Each item is independent; land them in separate commits where practical.
- If an item's anchor no longer matches (parallel edits), re-verify the symbol
  before editing; update this spec if the finding no longer holds.
- Do **not** expand scope: these are the *only* sanctioned changes. Anything in
  the "Considered and rejected" table must stay as-is.

---

## Phase 1 — Unconditional net-negative cleanups (zero risk)

All six are behavior-preserving deletions / narrowings / de-duplications. Each
verification uses the repo ladder from the owning workspace
(`cd agent-core && cargo …`).

### Item 1 — Delete the dead streaming-deferral seam (`eos-engine`)

- **Lens:** port/composition — over-abstraction to delete.
- **Anchors:**
  - `eos-engine/src/tool_call/streaming.rs` — `struct StreamingToolExecutor`
    (:9), `fn should_defer_tool` (:34). Only callers are this module's own
    `#[cfg(test)]` block (:81, :82, :88).
  - `eos-engine/src/tool_call/mod.rs:4` — `mod streaming;`
  - `eos-engine/src/tool_call/mod.rs:7` —
    `pub use streaming::{should_defer_tool, StreamingToolExecutor};`
- **Current problem:** an exported deferral policy with **zero live consumers**;
  the real query loop handles `AssistantMessageComplete` directly.
  `should_defer_tool` ignores its `_name` argument. The export advertises a
  streaming-deferral seam that does not exist, misleading future maintainers.
- **Change:** delete `streaming.rs` entirely; remove the `mod streaming;` line
  and the `pub use streaming::{…};` re-export in `tool_call/mod.rs`.
- **Verification:**
  - `grep -rn "StreamingToolExecutor\|should_defer_tool" agent-core/crates`
    returns nothing after the edit.
  - `cargo check -p eos-engine --all-targets`
  - `cargo test -p eos-engine`
- **Risk:** none (dead code + own tests).

### Item 2 — Delete the dead `notification_state` field (`eos-engine`)

- **Lens:** encapsulation — dead state.
- **Anchor:** `eos-engine/src/query/context.rs:87` —
  `pub notification_state: JsonObject` (doc: "Per-rule scratchpad").
- **Current problem:** written by all `QueryContext` constructors, **read
  nowhere**. `NotificationRule` only receives `&QueryContext` (immutable), so the
  "per-rule scratchpad" the field documents is structurally impossible. The
  field is cloned on every `QueryContext` clone for no consumer.
- **Change:** delete the field, its doc comment, and its initializer in every
  constructor. Drop the `JsonObject` import if it becomes unused.
- **Conditions:** confirm it is **not** part of a persisted/serde contract — it
  is `Clone`-only state, not serialized (verify no `Serialize`/`Deserialize`
  path touches it).
- **Verification:**
  - `grep -rn "notification_state" agent-core/crates` returns nothing.
  - `cargo check -p eos-engine --all-targets` and `cargo test -p eos-engine`.
- **Risk:** none, contingent on the not-a-serde-contract check.

### Item 3 — Collapse the duplicated `ensure_plugin_package` public symbol (`eos-sandbox-host`)

- **Lens:** encapsulation — dead duplicated public surface.
- **Anchors (host crate only — do NOT touch the same-named `eos-sandbox-api`
  function, which is a separate live API):**
  - `eos-sandbox-host/src/plugin_package.rs:26` — `pub async fn
    ensure_plugin_package(..)`, a pass-through that calls
    `ensure_plugin_package_inner` (:31).
  - `eos-sandbox-host/src/plugin_package.rs:34` — `pub(crate) async fn
    ensure_plugin_package_inner(..)` — the real body.
  - `eos-sandbox-host/src/lib.rs:47` —
    `pub use plugin_package::ensure_plugin_package;`
  - **Live caller of the inner fn:** `eos-sandbox-host/src/daemon_client.rs:519`
    — `crate::plugin_package::ensure_plugin_package_inner(self, …)`.
- **Current problem:** the host's public `ensure_plugin_package` wrapper has
  **no caller** (the live path is the `DaemonClient` impl → `_inner`); its
  docstring falsely claims it is "the normal public host setup API." Counted
  duplication + leaked public surface + a misleading doc.
- **Change:** delete the `pub` wrapper (`plugin_package.rs:26–32`) and the
  `lib.rs:47` re-export; rename `ensure_plugin_package_inner` →
  `ensure_plugin_package`, keep it `pub(crate)`, and move the (corrected) doc
  onto it. Update the single caller at `daemon_client.rs:519` to the new name.
- **Verification:**
  - `grep -rn "ensure_plugin_package_inner" agent-core/crates` returns nothing;
    the host's `ensure_plugin_package` is `pub(crate)` with exactly one caller.
  - `cargo check -p eos-sandbox-host --all-targets` and
    `cargo test -p eos-sandbox-host`.
- **Risk:** none. Note the published-library "keep public" lens does not apply to
  this internal workspace.

### Item 4 — Narrow leaked sqlx repository concretes to `pub(crate)` (`eos-db`)

- **Lens:** encapsulation — leaked concrete types behind a sealed port.
- **Anchors:**
  - `eos-db/src/lib.rs:24` — re-exports `SqlAgentRunStore, SqlAttemptStore,
    SqlIterationStore, SqlRequestTaskStore, SqlWorkflowStore`.
  - `eos-db/src/repositories/mod.rs:9–13` — `pub use …::Sql*Store;` (×5).
  - Internal consumer: `eos-db/src/composition.rs:17` uses them via
    `use crate::repositories::{…}` (crate-internal path).
- **Current problem:** five concrete sqlx store structs are exported as public
  API with **zero consumers outside `eos-db`** (verified). Every accessor hands
  out `Arc<dyn …Store>` and the Store traits are `Sealed`, so leaking the
  concretes invites a downstream crate to bind `SqlAttemptStore` directly and
  bypass the `Arc<dyn>` port seam.
- **Change:** delete the `lib.rs:24` re-export block; change
  `repositories/mod.rs:9–13` from `pub use` → `pub(crate) use`. Keep
  `ModelRegistry` / `ResolvedModel` exports (real external consumers).
- **Verification:**
  - `grep -rn "Sql\(AgentRun\|Attempt\|Iteration\|RequestTask\|Workflow\)Store"
    agent-core/crates | grep -v eos-db/src` returns nothing.
  - `cargo check -p eos-db --all-targets`, then
    `cargo check -p eos-workflow -p eos-runtime --all-targets` (the
    composition-root consumers) to prove the narrowing broke no build.
- **Risk:** none (zero external consumers).

### Item 5 — Replace the duplicate `counted_loss` free fn with the existing method (`eos-obs-collector`)

- **Lens:** duplication — duplicated predicate (single source of truth).
- **Anchors:**
  - `eos-obs-collector/src/gates.rs:417` — `fn counted_loss(loss:
    &SandboxAuditLoss) -> bool { … }`, called once at `gates.rs:237`.
  - `eos-obs-collector/src/normalization.rs:28` —
    `SandboxAuditLoss::has_counted_loss(&self) -> bool`, **byte-identical** body.
  - `SandboxAuditLoss` is already imported into `gates.rs` (line 9).
- **Current problem:** "what counts as audit loss" is defined twice (free fn vs
  method) over the same fields; the two can silently diverge.
- **Change:** delete `counted_loss` (gates.rs:417); change the call site at
  `gates.rs:237` from `counted_loss(loss)` to `loss.has_counted_loss()`.
- **Verification (behavior-preserving — bodies are identical):**
  - `grep -rn "fn counted_loss\|counted_loss(" agent-core/crates` returns nothing.
  - `cargo test -p eos-obs-collector` (the gate tests exercise this path).
- **Risk:** none (verified byte-identical predicate).

### Item 6 — Delete the dead `PluginKind::as_wire` encoder (`eos-plugin-catalog`)

- **Lens:** dead code (surfaced by the refactor sweep).
- **Anchors:**
  - `eos-plugin-catalog/src/manifest.rs:61` — `pub fn as_wire(self) ->
    &'static str`. **Zero callers** across both workspaces (the many live
    `as_wire` hits belong to unrelated enums — `DaemonOp`, `Intent`,
    `MessageRole`).
  - Keep: `PluginKind::parse` (:46) and the serde `Serialize` path.
- **Current problem:** a dead encoder whose doc **falsely** claims it stamps the
  audit `plugin_kind` field — but that field is a daemon-side `String`
  (`eos-protocol/src/audit.rs`), so `as_wire` feeds nothing. Dead surface + a
  misleading doc; the kind string tables drop from 3 → 2 once removed.
- **Change:** remove `as_wire` (and its doc). **Do not** replace
  `PluginKind::parse` with `Deserialize`: the granular
  `UnknownKind` / `KindNotString` error variants are a documented design choice
  and `Deserialize` is not even derived on the type.
- **Conditions:** the repo's standing rule is "mention dead code, don't delete
  unasked." This spec is the explicit ask; still, only land it as part of a
  commit that already touches `manifest.rs`, or as a standalone clearly-scoped
  "remove dead `PluginKind::as_wire`" commit.
- **Verification:**
  - `grep -rn "as_wire" agent-core/crates sandbox/crates | grep -i pluginkind`
    returns nothing; `grep -rn "\.as_wire()" …` shows only the unrelated enums.
  - `cargo check -p eos-plugin-catalog --all-targets` and
    `cargo test -p eos-plugin-catalog`.
- **Risk:** none (verified zero callers).

---

## Phase 2 — Optional judgment-dependent cleanups (low value)

Both are low-impact and require a judgment call; **do them only when already
working in the relevant file/module**, not as standalone churn.

### Item 7 — Hoist the `NO_OUTCOME` sentinel into `eos-state` (`eos-db`, `eos-workflow`, `eos-state`)

- **Lens:** duplication — shared prompt-facing constant across two prod crates.
- **Anchors:**
  - `eos-db/src/rows.rs:158` — `const NO_OUTCOME: &str = "(no outcome
    recorded)";` (used at `rows.rs:201`).
  - `eos-workflow/src/context/engine.rs:266` — the literal
    `"(no outcome recorded)".to_owned()`.
  - Target owner: `eos-state/src/outcomes.rs` (already defines
    `ExecutionTaskOutcome`; both `eos-db` and `eos-workflow` already depend on
    `eos-state` — **no new dependency edge**).
- **Current problem:** two production crates materialize the same literal into
  the same prompt-facing `ExecutionTaskOutcome.outcome` field; both render
  verbatim into the agent prompt, so rewording one silently strands the other.
- **Change:** add `pub const NO_OUTCOME: &str = "(no outcome recorded)";` to
  `eos-state/src/outcomes.rs` (export via `eos-state` lib). Replace the `eos-db`
  `const` and the `eos-workflow` literal with the shared const.
- **Conditions / nuance (state explicitly, this is why it is optional):**
  - The two uses are **semantically distinct** — `eos-db` means "empty outcome
    field"; `eos-workflow` synthesizes a success outcome for a Done task with no
    records. They could legitimately *want* different text someday, which
    weakens the "must stay in sync" argument. Sharing is a judgment call, not a
    correctness fix.
  - **Leave the two test literals** at `rows.rs:420` and `rows.rs:453`
    hardcoded — they act as a drift net that fails if the shared const changes.
- **Verification:**
  - `grep -rn '"(no outcome recorded)"' agent-core/crates` shows only the two
    intentional test literals.
  - `cargo check -p eos-state -p eos-db -p eos-workflow --all-targets`;
    `cargo test -p eos-db -p eos-workflow`.
- **Risk:** low (judgment about whether sharing is desirable at all).

### Item 8 — `ContextScope` struct → role-carrying enum (`eos-workflow`)

- **Lens:** type-safety — make-illegal-states-unrepresentable (remove dead
  defensive code). **Verdict: needs-nuance.**
- **Anchors:**
  - `eos-workflow/src/context/scope.rs:9` — `pub struct ContextScope { … }` with
    `Option`-wrapped ids and four fallible accessors returning
    `WorkflowError::MissingContextField` (:76, :82, :88, :94).
  - Construction is only via the three `for_*` constructors (no external
    consumers).
  - Consumers: `build()` delegates to `build_planner_context(&ContextScope)` and
    `build_execution_context(&ContextScope, role)`;
    `validate_context_recipe(recipe_id, scope.role)` reads `role`.
- **Current problem:** the role→field invariant (which ids are present for
  Planner vs Generator vs Reducer) is held only by convention; the four fallible
  accessors + `MissingContextField` variant re-validate states that `build()`
  has already pinned — dead defensive code the repo prefers to remove.
- **Change:** replace the struct with a role-carrying enum:
  ```rust
  enum ContextScope {
      Planner   { workflow_id, iteration_id, attempt_id },
      Generator { workflow_id, iteration_id, attempt_id, task_id },
      Reducer   { workflow_id, iteration_id, attempt_id, task_id },
  }
  ```
  This deletes the four fallible accessors, the `MissingContextField` variant,
  and the `Option` wrappers, making the invalid role/field combination
  unrepresentable.
- **Conditions / nuance (MUST be applied or the change is mis-scoped):**
  - The original "another crate can build an invalid scope" premise is **false**
    — there are **zero external consumers**; all construction goes through the
    three `for_*` constructors. The real win is deleting *dead defensive code*
    guarding by-construction-impossible states, **not** closing a live hole.
    Keep impact framed as **low**.
  - `build()` does **not** destructure directly; realizing the enum requires
    changing `build_planner_context` / `build_execution_context` to take
    explicit ids (preferred, net-negative) **and** re-adding a small `role()`
    accessor because `validate_context_recipe(recipe_id, scope.role)` reads the
    dropped `role` field.
  - Do **not** introduce an `unreachable!()` arm inside the helpers.
  - Only undertake as part of deliberate `eos-workflow/src/context/` work.
- **Verification:**
  - `cargo check -p eos-workflow --all-targets`;
    `cargo test -p eos-workflow`;
    `cargo clippy -p eos-workflow --all-targets -- -D warnings` (this one touches
    a state model — run clippy).
- **Risk:** low, with mechanical nuance. Net-negative if the helper signatures
  are updated rather than worked around.

---

## Considered and rejected (do NOT re-open)

Recorded so these are not re-litigated. Each was adversarially verified and
rejected against the repo's rules or Rust object-safety / async-trait facts.

| Candidate | Why rejected |
|---|---|
| Split any of the 10 largest files (`docker.rs` 908, `gates.rs` 815, `parse.rs` 665, `orchestrator.rs` 656, `lifecycle.rs` 603, `anthropic.rs` 594, `run_stage.rs` 593, `manifest.rs` 569, `app_state.rs` 557, `hooks/mod.rs` 555) | **Mechanically cohesive** — each is one adapter / state-machine / two-stage parser whose helpers are single-use and co-evolving; all below the 800–1000+ LOC review-smell band. The repo explicitly forbids hard size caps. |
| `async_trait` → native `async fn` in trait | **Invalid** for every flagged trait — all are erased to `Arc<dyn>`; `async_trait` *is* the boxing that makes them object-safe. Correct as-is. |
| Add `-> impl Trait` returns (the "0 found" observation) | **Not a smell** — `Arc<dyn>` struct fields for shared cross-task ownership cannot hold `impl Trait`. |
| Convert sqlx-row `status: String` / `role: String` to enums (`eos-db/rows.rs`) | **Correct parse-don't-validate** — the row is the raw SQL projection; the repository maps to the typed enum before domain use. |
| `ExecCommandResult.status: String` → enum (`eos-sandbox-api`) | Daemon vocabulary is open/inconsistent and re-serialized verbatim downstream; the proposed substitution is impossible at one site and behavior-changing at another. Single-use helper otherwise. |
| `DaemonOp` serde-rename vs `as_wire` vs test table (`eos-sandbox-api/ops.rs`) | **Sanctioned invariant** — a test pins all 18 variants across serialize/deserialize/`as_wire`; `as_wire` is an infallible `const fn`; a serde swap would add fallibility + alloc at live callers. |
| `Intent` / `ToolIntent` enum-string tables; merging the two enums | **Sanctioned / single-use / forbidden** — inverse `from_payload` is test-only; merging the enums is explicitly forbidden by the CLAUDE.md design contract; round-trip is test-pinned. |
| `PluginKind::parse` → `Deserialize` | Granular error variants are documented intent; `Deserialize` not derived. (Only the dead `as_wire` is removed — Item 6.) |
| Narrow `eos-sandbox-host` 0-usage `lib.rs` re-exports | **Expected pre-wiring** (Phase 6 wiring pending per crate doc), not dead code. |
| Narrow `eos-sandbox-api` broad `pub` surface (plugin descriptors, `*ResultBase`) | **Real external consumers** in `eos-tools`, `eos-sandbox-host`, `eos-plugin-catalog`. |
| Replace explicit registry deregister with `Drop` (`eos-workflow`) | The self-referential `Drop` is impossible to fire; explicit deregister is the correct idiom. |
| Blanket-`Deserialize` the `eos-sandbox-api` daemon DTOs | Violates wire-contract invariant 9; hand-written decoders are intended discipline. |
| `unwrap`/`expect` raw counts (e.g. 80 in `eos-workflow`) | **Sanctioned** — the repo allows `.expect()` for programming errors / true invariants. No `unwrap` on a reachable runtime error path was found. |
| Lock-across-`await` concerns | None — all locks are `tokio::sync::Mutex` (async-aware), correct to hold across `.await`. |
| Add a port/seam anywhere one does not already exist | Existing seams already have prod + test-fake impls; no missing seam found. |

---

## Acceptance criteria

Phase 1 complete when, for items 1–6:

- Each item's `grep` check confirms the dead/duplicated symbol is gone.
- `cargo check -p <crate> --all-targets` passes for every touched crate, plus
  `eos-workflow` + `eos-runtime` for Item 4's narrowing.
- `cargo test -p <crate>` passes for every touched crate.
- No new `#[allow(...)]` was added; net line count is negative.

Phase 2 complete when, for items 7–8 (if undertaken):

- Item 7: the shared const is the single source; the two test literals remain as
  the drift net; the three crates build and test green.
- Item 8: `ContextScope` is the role enum; the four fallible accessors and
  `MissingContextField` are gone; `build_*` helper signatures updated (no
  `unreachable!()`); `eos-workflow` check/test/clippy green.

If any phase uncovers that an anchor no longer holds (parallel agent edits) or
that a finding's premise changed, update this spec before coding around it.
