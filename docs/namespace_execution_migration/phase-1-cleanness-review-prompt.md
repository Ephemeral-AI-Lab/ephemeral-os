# Multi-Agent Adversarial Review: Phase 1 — Cleanness, Minimalism & Round-Trip Discipline

Use this prompt to run a **read-only, multi-agent** review of the **implemented**
Phase 1 of the Namespace Execution Engine migration, hunting for **unused/legacy
code, redundant types/fields/methods/derives/dependencies, dead remnants left by
the move, and any choice that re-bakes the legacy round-trip multiplicity** the
design exists to remove. Target = live code as committed:

```text
crates/sandbox-runtime/namespace-execution/                 # the new crate under review
crates/sandbox-runtime/operation/src/namespace_execution.rs # the move's residue + shim
```

Contract and design intent:

```text
docs/namespace_execution_migration/phase-1-spec.md          # what Phase 1 must contain (§5) and why each thing is kept/deferred (§2)
docs/namespace_execution_migration/migration-phases.md      # § "Phase 1" and the Phase 2-6 deferral map
docs/namespace-execution.md                                 # the design: NamespaceTarget shape, the round-trip/hop reductions
docs/namespace-execution-adversarial-review-results.md      # the simplification scorecard (F1–F28): 27 hops → few; 3 poll loops → 0; 8 request fields → 2
```

## Mission & Premise

The premise is **not** that Phase 1 should carry more, or that the migration's
direction is wrong. Treat as fixed: Phase 1 is a **skeleton + a move**, behavior
unchanged, dependency set = `namespace-process` only.

But **"skeleton" is not a license to carry weight earlier than the phase that
first needs it.** The spec itself sets the standard by *deferring* `serde`,
`serde_json`, `thiserror`, `PtyMaster`, `on_terminal`, `RunnerOutcome::{status,
payload}`, the registry maps, and `From<WorkspaceEntry>` to the phase that first
consumes each (§2 Decisions 1, 5, 6; §5.5–5.11). Apply that same discipline to
everything Phase 1 **kept**: every type, field, method, derive, trait, dependency,
and `allow(dead_code)` site must be either

- **(a) load-bearing** — required for Phase 1 to compile and pass its own tests; or
- **(b) a contract-named seam** — explicitly required present by `migration-phases.md`
  § "Phase 1" / spec §5.

Anything that is neither is **premature** and the finding is: *defer it to the
phase that first references it* (delete-from-Phase-1, not implement-more).

Your job, across four lenses, is to make the Phase 1 skeleton the **leanest** one
that still satisfies the contract — and to verify the skeleton's *shape* commits
to the design's round-trip reductions rather than re-introducing a poll/hop. Every
finding ends in a **concrete edit** (delete / collapse / defer / drop-a-derive)
with the saving **quantified** (types, fields, methods, derives, deps,
`allow(dead_code)` sites, or eventual per-exec hops removed).

"Looks clean" is not acceptable. If a lens is genuinely minimal, prove it with the
before→after counts and name the single biggest remaining reduction.

This is **review-only**. Do **not** implement. Do **not** pull Phase 2+ behavior
forward to "use up" a premature type — the remedy for premature surface is
**deferral**, never early implementation. **Live code is the source of truth.**

## How To Run (multi-agent)

```text
1. Orchestrator: run the bootstrap once, then spawn the 4 reviewer agents IN
   PARALLEL. Each is blind to the others and owns one lens. Give each only: this
   prompt, its own section, the shared reading, and the targets.
2. Each reviewer returns findings in the per-reviewer output contract.
3. Orchestrator: spawn the Synthesis agent with all four reviewers' findings; it
   merges, resolves conflicts, and produces one lean-skeleton proposal + edit list.
4. Orchestrator: return the synthesis verbatim, plus the raw findings appendix.
```

Simplify by **deletion/deferral before abstraction**. Do not propose a new
module/indirection to "tidy up" unless it removes strictly more than it adds.

## Repo

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os
```

Bootstrap (orchestrator runs once; `export PATH="$PWD/bin:$PATH"` first):

```sh
git status --short && git log --oneline -6
# The whole surface to audit — every public type + every pub(crate) item:
rg -n "pub struct|pub enum|pub trait|pub fn|pub\(crate\)|pub use" crates/sandbox-runtime/namespace-execution/src
# The premature-surface map: every place that exists before its first non-test use:
rg -n "allow\(dead_code\)" crates/sandbox-runtime/namespace-execution/src
# The duplication candidate: NamespaceTarget(5 fields) vs NamespaceRunnerRequest(8 fields):
sed -n '1,15p' crates/sandbox-runtime/namespace-execution/src/target.rs
sed -n '21,41p' crates/sandbox-runtime/namespace-process/src/runner/protocol.rs
# The move's residue — type now in new crate, allocator still in operation:
sed -n '143,147p' crates/sandbox-runtime/operation/src/namespace_execution.rs
# Confirm everything still builds/tests lean (cleanness must not break green):
cargo test -p sandbox-runtime-namespace-execution
cargo clippy -p sandbox-runtime-namespace-execution --all-targets --no-deps -- -D warnings
```

## Shared Required Reading

```text
crates/sandbox-runtime/namespace-execution/src/{lib,id,error,target,promise,execution,shell,observer,registry}.rs
crates/sandbox-runtime/namespace-execution/Cargo.toml
crates/sandbox-runtime/operation/src/namespace_execution.rs            # :8 shim; :143-145 allocate→format! (stayed in operation)
crates/sandbox-runtime/namespace-process/src/runner/protocol.rs        # :14 NsFds; :21-35 NamespaceRunnerRequest; :37-41 RunResult
docs/namespace_execution_migration/phase-1-spec.md                     # §2 (why kept/deferred), §4 (LOC ledger), §5 (per-file), §8 (anchors)
docs/namespace-execution.md                                            # the eventual engine: which type is first consumed where
docs/namespace-execution-adversarial-review-results.md                 # round-trip scorecard the skeleton must not regress
```

Use `rg` to find the **first non-test consumer** of each type across the whole
repo and the migration phases. A type whose only references are its own definition
+ its inline test is, by definition, declared-for-later — classify it.

## Shared Ground Rules

- Live code wins over the spec; the design doc states intent, the spec states the
  Phase-1 cut.
- Every finding cites `file:line` and ends in a concrete edit + a quantified saving.
- The remedy for a premature item is **defer to its first-use phase** (name the
  phase), not "implement it now."
- You may challenge any kept type/field/method/derive/dep/indirection. You may
  **not** propose breaking the id re-export, the behavior-unchanged guarantee, or
  re-adding a deferred dependency to "simplify."
- Separate live-code facts from inferred advice.

## Quantitative Targets

Each reviewer reports before→after. Synthesis drives each toward its leanest
**defensible** value (defensible = still satisfies the contract).

```text
Public types/traits in the crate (currently 8):         classify each load-bearing / contract-seam / PREMATURE→defer
pub(crate) items (CompletionPromise, ExecutionRegistry): same classification
allow(dead_code) sites (currently 8):                    each = a premature-surface marker; minimize, justify survivors
Fields duplicated NamespaceTarget↔NamespaceRunnerRequest: 5 of 8 overlap — collapse-to-one or justify two
Derives per type carried with no Phase-1 consumer:       minimize (e.g. NamespaceTarget PartialEq+Eq — is there a test?)
Slot<T> states (currently 3: Pending/Ready/Taken):       minimize — can write-once/single-consumer be 2 states?
Premature indirections (InteractiveExecution over Handle adds 0 capability; RunnerOutcome over RunResult exposes 1 method): collapse or justify
Move residue lines in operation (stranded import / split responsibility): minimize
Eventual per-exec hops the skeleton commits to (design F1–F13): 0 regressions — prove the promise/handle shape stays poll-free
```

---

## Reviewer 1 — Premature Surface (defer-to-first-use)

**Lens:** every type, trait, and method that exists in Phase 1 but has **no
non-test consumer until a later phase**. The 8 `allow(dead_code)` sites are the
map; the public re-exports are the rest.

**Mandate:** for each item, find its first real consumer in the migration; if that
is Phase 2/3/4, decide whether `migration-phases.md` § "Phase 1" **names it as a
required seam** — if not, the finding is *move its declaration to that phase*.

**Seed hunt list (find more):**

- `ShellOperation` (first `impl` is `ExecCommand`, **Phase 3**) — does Phase 1
  need the trait declared, or only `RunnerOutcome`? Is a trait with zero impls and
  zero callers earning its place now?
- `ExecutionObserver` (first `impl` = `NamespaceExecutionLedger`, **Phase 3**) —
  same question; one method, no implementor, no caller.
- `RunnerOutcome` (first **produced** by the launcher, **Phase 2**) — a newtype
  with no constructor and a single `exit_code()` reader, unconstructable in Phase
  1. Declared-for-later?
- `InteractiveExecution` (PTY capability lands **Phase 2**) — in Phase 1 it is a
  pure pass-through over `ExecutionHandle` (forwards `id`/`is_finished`/`wait`,
  adds nothing). Is the species type required now, or does it arrive with the PTY?
- `ExecutionRegistry` (maps/admission **Phase 2/3**) — a struct holding only
  `max_active`. Is the placeholder a contract-named seam or deferrable whole?
- `CompletionPromise::{new,resolve,wait_timeout}` — used only by inline tests in
  Phase 1 (hence the `allow(dead_code)`). Is the primitive required to *exist*
  now (it is the one thing Phase 1 can unit-test), or are individual methods
  (`wait_timeout`) ahead of need?
- Cross-check each survivor against the spec's stated justification (§2/§5): where
  the spec says a type is "included in Phase 1," confirm the contract actually
  requires it vs the spec choosing to front-load it.

**Output:** a surface table (item → first non-test consumer + phase → contract-named
seam? Y/N → **keep / defer-to-PhaseN**), the count of items deferrable, and the
single biggest chunk of surface that should arrive with the phase that uses it.

## Reviewer 2 — Redundancy & Duplication

**Lens:** types/fields/derives/states that duplicate something that already exists
or that a leaner construct expresses.

**Mandate:** collapse each duplicate or justify why two are load-bearing in Phase 1.

**Seed hunt list (find more):**

- **`NamespaceTarget` vs `NamespaceRunnerRequest`:** 5 of the request's 8 fields
  (`workspace_root, layer_paths, upperdir, workdir, ns_fds`) are re-declared on
  the target (with `ns_fds: NsFds` vs the request's `Option<NsFds>`). The design
  (review-results **F12**) targets request fields *authored per exec* 8→2 by
  building the request **from** the target. Is `NamespaceTarget` the right
  workspace-free boundary that *removes* per-exec authoring, or a second struct
  that just restates the wire shape? State the leanest boundary.
- **`RunnerOutcome(RunResult)` newtype** exposing only `exit_code()` (an `i32`
  widened to `i64`): does the newtype earn its keep in Phase 1, or is it wrapping
  `RunResult` before any behavior distinguishes them? (Note the spec defers
  `status()`/`payload()` to Phase 2 — so today it adds one widening accessor.)
- **`Slot<T>` three states** (`Pending`/`Ready`/`Taken`): a write-once,
  single-consumer cell — can it be two states (or `Option<Result<…>>`) without
  losing the take-once guarantee? Quantify the simplification vs the safety it buys.
- **The genus/species split** (`ExecutionHandle` + `InteractiveExecution`): in
  Phase 1 the species forwards every method and adds no field. Is the split
  redundant until the PTY field exists (Phase 2), i.e. could Phase 1 carry only
  `ExecutionHandle`?
- **Derives with no Phase-1 consumer:** `NamespaceTarget` derives
  `PartialEq, Eq` "useful for tests" (spec §5.6) — but is there a `target` test?
  If not, the justification is stale; drop or test. Sweep every type for a derive
  no Phase-1 path or test exercises.

**Output:** a duplication ledger (pair/construct → overlap count → collapse-to-one
proposal or load-bearing justification), with the field/derive/state counts
before→after.

## Reviewer 3 — Legacy / Dead Remnants & the Move's Residue

**Lens:** what the id move and the skeleton **left behind** — stranded imports,
split responsibilities, and `allow(dead_code)` that hides a genuinely *removable*
(not merely deferrable) item.

**Mandate:** find every remnant and either delete it or flag the split for the
phase that should consolidate it.

**Seed hunt list (find more):**

- **The shim minimality:** `operation/src/namespace_execution.rs:8` should be one
  `pub use` and nothing else from the move — no leftover `use` that the deleted
  struct needed, no now-unused import, no orphaned doc comment. Confirm.
- **The split responsibility:** the id **type** now lives in the new crate, but
  `allocate_namespace_execution_id` + `format!("namespace_execution_{n}")` stays
  on `NamespaceExecutionStore` in `operation` (`:143-145`). Is that a clean Phase-1
  boundary (allocation is observability-store concern) or a smell where one
  concept (id identity + minting) is split across two crates? If a smell, name the
  phase that should reunite them and the eventual shape (engine mints? store
  mints?) — do **not** move it now if the contract forbids it; flag it.
- **`allow(dead_code)`: deferrable vs removable.** For each of the 8 sites, decide
  whether the item is *deferrable* (real Phase-2 API, keep + annotate — fine) or
  *removable* (exists only to satisfy a test that itself tests nothing the
  contract needs). A removable item + its test should both go. Cross-check with
  the correctness review's test-adequacy findings.
- **Dead variants/fields inside kept types:** `error.rs`'s three variants are
  unconstructed in Phase 1 (suppressed by being `pub`+re-exported) — that is
  contract-named (named in signatures), keep. But sweep for any field/variant that
  is neither constructed nor named in a Phase-2 signature the spec cites.

**Output:** a remnant list (remnant → `delete now` / `flag for PhaseN
consolidation` → `file:line`), the shim-minimality verdict, and the
deferrable-vs-removable split of the 8 `allow(dead_code)` sites.

## Reviewer 4 — Round-Trip / Hop Discipline the Skeleton Commits To

**Lens:** forward-looking but concrete — the migration's *reason to exist* is
collapsing the legacy per-exec path (review-results: **27 hops, 3 daemon poll
loops, 1 start-ack RT, 3 pipes, ~7 locks**) to a blocking, promise-driven path
(**F1** blocking `wait`, **F2** condvar, **F4** drop start-ack, **F9** one registry
transition). Phase 1 lays the primitives that decide whether that is reachable.

**Mandate:** verify each Phase-1 primitive **commits to** the reduction rather than
re-baking a poll/hop, and flag any shape that will force an extra round-trip later.

**Seed hunt list (find more):**

- **`CompletionPromise` is the poll-killer.** Confirm its `wait` is genuinely
  *blocking on a condvar* (no internal sleep/spin), so the eventual command path
  has **0 poll loops** (F1/F2) — not a timed `wait_timeout` loop in disguise. Does
  exposing `wait_timeout` invite a future caller to poll? If so, note the risk and
  the guard (single blocking `wait` is the intended consumer).
- **One state transition, one store.** The design folds `FinalizationState` +
  `CommandLifecycleState` into the promise + a single registry transition (F8/F9).
  Does the Phase-1 `ExecutionHandle{ id, promise }` + `ExecutionRegistry` shape
  commit to **one** live/completed store, or does carrying both a promise *and* a
  registry hint at two state homes? (Registry is a placeholder now — judge the
  *shape it commits to*, not the absent maps.)
- **`NamespaceTarget` "built once, reused per exec"** (spec §5.6): does this shape
  actually remove per-exec request authoring (F12: 8 authored fields → 2), or does
  a second struct just relocate the authoring? Trace how Phase 2 would build the
  request from the target and confirm the field set makes that a *copy*, not a
  re-derivation.
- **No start-ack / no extra ser-deser baked in:** nothing in the Phase-1 types
  presupposes a start-acknowledgement round-trip (F4) or a second
  serialize/deserialize of the outcome. Confirm `RunnerOutcome` wraps the wire
  `RunResult` directly (one deser), not a re-encoded form.

**Output:** a hop-commitment table (design reduction F#→ does the Phase-1 primitive
commit to it? → evidence), each shape that risks a future round-trip with the
guard that prevents it, and a verdict on whether Phase 1 lays a poll-free,
single-store base.

---

## Synthesis Agent — Lean Skeleton & Edit List

**Input:** all four reviewers' raw findings.

**Method:**

1. De-duplicate; keep the strongest evidence and the clearest first-use phase.
2. Resolve cross-lens conflicts (e.g. R1 says defer `RunnerOutcome`; R4 says its
   one-deser shape is load-bearing for the hop target — decide and state why).
3. Drive every Quantitative Target to its leanest **defensible** value; show
   before→after and the contract clause that protects each survivor.
4. Produce one lean-skeleton proposal and the precise edits (delete / collapse /
   defer / drop-derive) that reach it — **without** crossing the contract or the
   behavior-unchanged guarantee.

**Output:**

```text
Lean Scorecard
  <each quantitative target: before → after, with the change and the contract clause that bounds it>

Findings (merged, severity-ordered, deduped)
  N. [Lens] [Severity] Title
     Evidence:  file:line (code / spec / design)
     Edit:      delete | collapse | defer-to-PhaseN | drop-derive
     Saving:    <types | fields | methods | derives | deps | allow-sites | eventual hops>
     Bound:     <contract clause that keeps any survivor, if contested>

Conflicts Resolved
  <defer-vs-keep and collapse-vs-two decisions, with rationale>

Lean Phase-1 Skeleton
  Surface:        final type/trait set (kept seams vs deferred)
  Duplication:    final field/derive/state counts
  Residue:        shim + split-responsibility disposition
  Round-trips:    confirmation the base stays poll-free / single-store / one-deser

Deferred-to-later (explicit: item → phase that first needs it)
```

## Severity Scale

Severity = leverage on leanness and on protecting the design's round-trip win, not
runtime bug risk:

```text
L0  The skeleton commits to a shape that will FORCE a future round-trip/poll or a
    second state store (regresses F1/F2/F8/F9), or carries a duplicate that will
    diverge from the wire type. Fix the shape before it ossifies.
L1  A whole premature type/trait/indirection (or dependency-worth of surface) that
    should arrive with the phase that uses it; or a real duplication collapsible to one.
L2  A redundant field/derive/state, a non-minimal shim, or a removable item+test.
L3  A naming/justification staleness (e.g. a derive justified "for tests" with no test).
```

## Forbidden Recommendations

- Do not propose **implementing** Phase 2+ to consume a premature type — the fix
  is **defer**, not build.
- Do not propose new layers/indirection/abstraction whose only justification is
  "cleaner"; deletion and deferral come first.
- Do not propose breaking the `NamespaceExecutionId` re-export, changing observable
  behavior, or re-adding `serde`/`thiserror`/`rustix`/`nix`/`libc` to the crate.
- Do not propose moving `allocate_*`/the minting logic in this phase if the
  contract forbids it — **flag** the split for the consolidating phase instead.
- No "looks clean" verdicts; every lens reports before→after counts.

## Rules

- Lead with concrete findings, not summaries. Cite `file:line`.
- Every finding ends in a delete/collapse/defer edit with a quantified saving and,
  where contested, the contract clause that bounds it.
- Separate live-code facts from inferred advice.
- A reviewer who finds little still reports its before→after counts and names the
  single biggest remaining reduction on its axis.
- The synthesis agent outputs one lean skeleton + one edit list, not a menu.
