# agent-core Test Architecture Spec

Status: draft · Scope: `agent-core/` workspace only · Companion: `integration-test/SPEC.md`

## 1. Purpose

Define how `agent-core` is tested. After this spec lands, `agent-core` contains
**unit / behavior tests only** — no Docker, no live `eosd`, no real network, no
real model. Every test verifies the correctness of a functionality or feature
with the three external I/O edges mocked. All shared test setup lives in one
crate, `eos-testkit`; no production crate carries test-support code in `src/`.

Tests target a **functionality**, not a full run: they may terminate an agent
loop, a request, a workflow, or a runner loop **early**, at the nearest
checkpoint, and assert there. Driving to final closure is allowed but never
required (§4).

Live end-to-end coverage (real sandbox + real `agent-core`) is **out of scope
here** and lives in the root `integration-test/` module (see its spec).

## 2. Invariants

- **I1 — Mock-only.** No `agent-core` test starts a container, dials `eosd`, or
  makes a network call to a model provider. CI runs the full suite with no
  Docker and no API keys.
- **I2 — No test-support in production `src/`.** No `testsupport/`,
  `test_support`, `testutil`, `fakes`, doubles, fixtures, builders, or harness
  code under any production crate's `src/`. Each lands in **`eos-testkit/src/`**
  if shared/duplicated, or the **crate's own `tests/` via `#[path]`** if
  crate-specific or sealed (full inventory + disposition in §7.1). (Small inline
  `#[cfg(test)] mod tests` that assert a single module's *pure* logic and define
  **no reusable doubles** are permitted — e.g. `eos-llm-client`'s SSE/retry
  tests.)
- **I3 — Tests under `tests/`.** Behavior tests live in each crate's
  `tests/<topic>/mod.rs`, included via the existing
  `#[path = "../tests/<topic>/mod.rs"]` attribute from `src/` (the convention
  already used at `eos-runtime/src/lib.rs`).
- **I4 — Mock only at the real edges.** Everything else runs live: engine loop,
  tool dispatch, workflow/attempt lifecycle, state machine, and a real temporary
  SQLite database. Bugs are found by exercising real code paths, not by mocking
  internals.
- **I5 — Closure is optional.** A test asserts at the checkpoint that proves the
  functionality under test; reaching final closure (root outcome / workflow
  `Closed` / engine `ToolStop`) is **not** required. Early termination uses
  existing cooperative seams only — **never** a test-only stop branch added to
  production loop code (§4).
- **I6 — No dedup.** One builder per layer, two runner doubles, one waiter, and
  single-layer coverage per behavior (§5). The design must not grow a second
  builder, a third runner double, a parallel waiter, or the same behavior
  asserted at two layers.

## 3. Mock seams — two layers

agent-core is verified at two mock granularities. A test picks the layer that
matches what it verifies; both keep LLM and sandbox out of the loop.

### Layer A — `EventSource` (the engine/tool path)

Mocks **only the LLM**; the engine loop and **real tool dispatch** run live,
with the sandbox faked at its two edges. Use for root-agent, terminal
enforcement, tool dispatch, and tool-error tests — anything where you want the
real loop to actually fire tool calls.

| Edge | Trait | Defined in | Double (in `eos-testkit`) |
|---|---|---|---|
| Model / LLM | `EventSource` | `eos-engine` | `ScriptedSource` (+ `tool_use_turn`, script loader) |
| Daemon RPC | `SandboxTransport` | `eos-sandbox-api` | `FakeTransport` |
| Sandbox provisioning | `RequestProvisioner` | `eos-runtime` | `FakeProvisioner` |

### Layer B — `AgentRunner` (the workflow path)

Mocks the **entire agent run** as a unit: the script runner injects terminal
submissions directly (`ScriptedSubmission::{Planner,Generator,Reducer}`),
skipping the engine loop, LLM, tools, and sandbox. The workflow lifecycle,
attempt orchestration, plan DAG, and state machine run live. Use for
delegate_workflow, PLAN→RUN→CLOSED, stage-advance, and reducer-gate tests, where
driving a full scripted loop per sub-agent would be wasteful and irrelevant.

| Seam | Trait | Defined in | Double (in `eos-testkit`) |
|---|---|---|---|
| Agent run | `AgentRunner` | `eos-workflow` | `ScriptedRunner`, `QueueRunner` |

All doubles in both layers are built on **public** APIs (`AppState::builder()`
and the trait surfaces are already `pub`), so no production crate needs a
`test-util` feature gate.

## 4. Partial execution & early termination

Three loops can each be stopped early; each has a **different** pre-existing
cooperative seam. None requires a test-only branch in production (I5). The
mechanisms are not uniform — in particular, the engine loop is *pulled*, the
runtime request is *spawned + aborted*, and the workflow/runner is *gated*.

| Closure named by the user | Driver (evidence) | Early-stop seam | Inspect at checkpoint |
|---|---|---|---|
| Engine **agent loop** (Layer A) | `run_query(&mut ctx, &mut msgs) -> QueryStream` (`eos-engine/src/query/loop_.rs`) | pull `stream.next()` to a chosen `StreamEvent`, then `drop(stream)` — releases the `&mut ctx` borrow (proven by `hard_ceiling_exit_*`) | `ctx` (`exit_reason`, `terminal_result`, `tool_calls_used`, `text_only_no_terminal_turns`) + `messages`; or the collected events while still streaming |
| Runtime **request → completion** (Layer A) | `tokio::spawn(run_root_agent)` → `JoinHandle` in the request handle (`eos-runtime/src/entry.rs:297,43`) | park with a blocking/non-closure source (`BlockingSource`, `factory_root_blocks_after`), then `handle.shutdown(reason, grace)` / drop-abort (`entry.rs:68,134`); or run a non-closure script and `handle.join()` | persisted `Request`/`Task` rows via the real-SQLite stores |
| **Workflow lifecycle** + **runner loop** (Layer B) | background tasks gated by `AgentRunner`; `QueueRunner::run()` blocks awaiting a pushed submission | don't push → loop parks at the next role's `run()`; `cancel_workflow` for a hard stop | `Workflow`/`Iteration`/`Attempt`/`Task` store rows while parked |

### 4.1 Checkpoint granularity per loop

- **Engine loop** — per `StreamEvent`: `AssistantMessageComplete` (after the
  model turn, before dispatch), `ToolExecutionStarted`/`ToolExecutionCompleted`
  (around each tool), `SystemNotification`. Enables "stop after N tool calls",
  "stop after tool X", "stop before terminal".
- **Runtime request** — "started but unfinished" (request row exists, root
  `Task` is `Running`), or any persisted state reachable before closure. The
  blocking source holds the root agent open while the test asserts.
- **Workflow loop's OWN checkpoints** (distinct from runner role boundaries):
  the **iteration** boundary (halt after iteration-1 `Succeeded`, before
  iteration-2 spawns) and the **attempt** boundary (halt after attempt-1
  `Failed`, before retry). Both are reached through the **same** `QueueRunner`
  gate, because each new attempt/iteration re-enters at its planner's `run()`
  and parks awaiting a push.
- **Runner loop** — role boundaries: after planner (before generators), after
  generators (before reducer). The `QueueRunner` is parked at the next role's
  `run()`.

### 4.2 Termination taxonomy (pick the lightest that proves the functionality)

1. **Bounded** — stop after N events / N roles.
2. **Targeted** — stop at the first event matching a predicate / after a named
   role.
3. **Park-and-inspect** — hold at a cooperative pause point, inspect, discard;
   no closure. **Preferred for clean intermediate assertions.**
4. **Cancel** — `cancel_workflow` / `handle.shutdown` hard stop.
5. **Fail-halt** — `ScriptedSubmission::NoSubmission` triggers the production
   exhaustion guard (`run_exhausted`, `run_stage.rs`), forcing a `Failed`
   closure. **Reserved for exhaustion/failure-path tests** — do **not** use it
   merely to obtain a pause; it dirties the asserted state.

### 4.3 Two-double / NoSubmission split (locked, I6)

- **`QueueRunner` = manual gate.** Clean park-and-inspect, injects no failure.
  Use for partial / checkpoint tests.
- **`ScriptedRunner` = auto-pilot.** Synthesizes per-role submissions and runs
  to closure. Use for end-to-end workflow tests.
- **`NoSubmission` = fail-halt.** The failure path itself; see taxonomy #5.
- **No third runner double.**

### 4.4 The one new piece of infra: `wait_until`

The background-driven loops (runtime, workflow, runner) park **concurrently**,
so a test must wait until a checkpoint is reached before inspecting. There is
already a terminal-status waiter (`wait_for_workflow_status`). Do **not** add a
parallel waiter — **generalize** it:

- `wait_until(predicate)` — the same spin-poll, predicate-driven.
- `wait_for_workflow_status(status)` becomes a thin call to `wait_until`.
- Mid-flight predicates: `runner.launches()` contains role X (`QueueRunner`
  records-then-blocks, so the order is correct), `attempt.stage() == Run`, or
  `request_store.get(id).is_some()`.
- The engine loop needs no waiter (synchronous pull-and-drop).

This generalization is the **only** net-new test mechanism; everything else in
§4 is an existing production seam.

## 5. Dedup discipline (I6)

### 5.1 Builder ownership — one fixture per layer

| Fixture | Owns | Backing | Must never |
|---|---|---|---|
| `build_test_state` | runtime + full-stack Layer A | real temp SQLite | grow a hand-rolled workflow driver |
| `MemoryStores + deps + QueueRunner`/`ScriptedRunner` | workflow / attempt Layer B | in-memory stores | grow an engine-loop driver |

Two fixtures because two layers. A workflow behavior is driven by exactly one of
them, never re-built in the other.

### 5.2 Coverage matrix — one owning layer + checkpoint per behavior

| Functionality | Layer | Checkpoint (taxonomy) | Owning test |
|---|---|---|---|
| terminal enforcement (ceiling / not-submitted / alone-call) | A engine | targeted: drop at synthetic-failure event | `eos-engine/tests/terminal` |
| tool dispatch → result block | A engine | targeted: after `ToolExecutionCompleted` | `eos-engine/tests/tool_call/dispatch` |
| tool error surface | A engine | targeted: after errored `ToolExecutionCompleted` | `eos-engine/tests/tool_errors` |
| root request → outcome | A runtime | closure: `handle.join()` | `eos-runtime/tests/root_agent` |
| request started, not finished | A runtime | park: blocking source + inspect persisted | `eos-runtime/tests/state_persistence` |
| plan DAG materialization | B runner | park after planner (before generators) | `eos-workflow/tests/plan_dag` |
| stage advance PLAN→RUN | B workflow | park at first generator `run()` | `eos-workflow/tests/plan_dag` |
| reducer exit gate | B runner | park after generators (before reducer) | `eos-workflow/tests/delegate` |
| attempt retry boundary | B workflow | fail-halt attempt-1 → park at attempt-2 planner | `eos-workflow/tests/iteration` |
| iteration continuation | B workflow | park: iteration-1 `Succeeded`, don't spawn iteration-2 | `eos-workflow/tests/iteration` |
| delegate wiring (root→handle→outcome) | A full-stack | closure; **wiring only** | `eos-runtime/tests/full_stack` |

Rule: the full-stack test asserts integration/wiring only — it never
re-validates DAG internals already owned by a Layer-B test. Note (§14.1): it is
**Layer A only** — root and delegated sub-agents both run scripted `EventSource`s
(keyed per agent name); a `ScriptedRunner` cannot be injected at this altitude.

## 6. `eos-testkit` — the test-setup crate

New workspace member: `agent-core/crates/eos-testkit`. A dev-dependency library
whose `src/` *is* test infrastructure (a dedicated test crate, satisfying I2).
It is the **single** home for every builder, double, and helper named in §3–§5.

```
crates/eos-testkit/
├── Cargo.toml
└── src/
    ├── lib.rs        # thin re-export surface
    ├── llm.rs        # ScriptedSource (the single copy), tool_use_turn, text_turn
    ├── script.rs     # .ron/.json script -> per-agent turns -> tool_use_turn lowering
    ├── engine.rs     # Layer A stepping: pull a QueryStream to a checkpoint, drop, inspect
    ├── sandbox.rs    # FakeTransport, FakeProvisioner
    ├── agents.rs     # agent_def() builder, common allowed/terminal tool sets
    ├── state.rs      # build_test_state(llm_tier, mock-sandbox) over temp SQLite
    └── workflow.rs   # [feature=workflow] MemoryStores, deps, QueueRunner,
                      #   ScriptedRunner, ScriptedSubmission, wait_until, role-gate sugar
```

### 6.1 Feature slicing

So a low-level crate's test build does not compile the whole stack:

- `llm` → pulls only `eos-engine` + `eos-llm-client` (`ScriptedSource`,
  `engine.rs` stepping).
- `mock-state` → adds `eos-runtime` (`build_test_state`, `FakeProvisioner`).
- `workflow` → adds `eos-workflow` + `eos-state` (the relocated `AgentRunner`
  doubles + `wait_until`, see §7).

Each production crate enables only the feature it needs. The resulting dev-dep
cycle (e.g. `eos-engine[dev] → eos-testkit[llm] → eos-engine`) is permitted by
Cargo because the back-edge is dev-only.

### 6.2 Public surface (illustrative)

```rust
// eos-testkit
pub use llm::{ScriptedSource, tool_use_turn, text_turn};
pub use script::{Script, AgentBackend};        // AgentBackend::{Scripted(path), Real}
pub use engine::run_until;                      // Layer A: pull a QueryStream to a checkpoint
pub use sandbox::{FakeTransport, FakeProvisioner};
pub use agents::agent_def;
pub use state::build_test_state;
#[cfg(feature = "workflow")]
pub use workflow::{MemoryStores, QueueRunner, ScriptedRunner, ScriptedSubmission, wait_until};
```

`AgentBackend`/`Script` also serve the root `integration-test` module, which
reuses `ScriptedSource` for its scripted-LLM tier.

## 7. Relocations (this spec performs them)

| Move | From | To |
|---|---|---|
| `ScriptedSource`, `tool_use_turn` | `eos-engine/src/query/loop_.rs` (cfg(test)) **and** `eos-runtime/tests/unit/app_state_test_seams.rs` | `eos-testkit/src/llm.rs` (one copy; delete both originals) |
| `FakeTransport`, `FakeProvisioner`, `build_test_state` | `eos-runtime/tests/unit/app_state_test_seams.rs` — plus a **duplicate** `FakeTransport` in `eos-engine/src/support/test_support.rs` | `eos-testkit/src/{sandbox,state}.rs` (one canonical `FakeTransport`; delete the engine duplicate, engine tests consume testkit's) |
| `AgentRunner` doubles (`ScriptedRunner`, `QueueRunner`), `MemoryStores`, `wait_for_workflow_status` | `eos-workflow/src/testsupport/` | `eos-testkit/src/workflow.rs` (under `workflow` feature); the waiter is generalized to `wait_until` (§4.4) |

After these moves, `eos-runtime/tests/unit/app_state_test_seams.rs` shrinks to
its remaining crate-local seams (or retires), and
`eos-workflow/src/testsupport/` is deleted.

Prerequisites for these moves (Broad scope, §15):
- `build_test_state` move requires the three `RequestProvisioner` exposures in
  §14.4.
- The workflow-doubles move requires rewriting `MemoryStores::deps()` to the
  `pub` `with_*` builders (§15) so no `AttemptDeps` field is exposed, and accepts
  the dev-dep cycle.
- Import paths shift from `crate::…` to `eos_workflow::…` / `eos_engine::…` (the
  symbols are already `pub` at those crate roots; this is a path rewrite, not a
  visibility change).

### 7.1 Full `src/` test-support inventory & disposition

A wider audit found **seven** `#[cfg(test)]` test-support modules, not one. Two
facts govern their disposition:

- A `#[cfg(test)]` module is invisible to other crates, so "move to `eos-testkit`"
  means **rebuild on the owning crate's public API** — worth it only for genuinely
  shared or duplicated doubles.
- Everything else satisfies I2 by relocating the **file** under the crate's
  `tests/` and referencing it via `#[path = "../tests/support/mod.rs"]` (the idiom
  `eos-tools` already uses) — zero exposure, no dev-dep cycle.

**Rule of thumb: shared/duplicated → `eos-testkit`; crate-specific or sealed →
the crate's own `tests/` via `#[path]`.**

| Module (in `src/`) | Contents | Disposition |
|---|---|---|
| `eos-workflow/src/testsupport/` | `ScriptedRunner`, `QueueRunner`, `MemoryStores`, waiter | → **`eos-testkit`** (shared Layer-B doubles; §7) |
| `eos-engine/src/support/test_support.rs` — `FakeTransport` | `SandboxTransport` fake (duplicate of runtime's) | → **`eos-testkit/sandbox.rs`** (delete duplicate; dedup) |
| `eos-engine/.../test_support.rs` + `eos-state/src/fakes.rs` — store fakes (`FakeTaskStore`, `FakeRequestStore`) | in-memory Store fakes (`eos-state` Store `Sealed` is `pub`) | engine's → reuse `eos-testkit::MemoryStores`; `eos-state`'s contract fake → **relocate to `eos-state/tests/`** (keep local; moving it would make eos-state's own tests depend on a crate that depends on eos-state) |
| `eos-sandbox-host/src/testutil.rs` — `MockAdapter` | `ProviderAdapter` mock | **stays in `eos-sandbox-host`** → relocate to `tests/`. **Cannot move:** `ProviderAdapter`'s seal is `pub(crate) mod sealed` (`provider.rs:277`) — unimplementable from another crate |
| `eos-plugin-catalog/src/test_support.rs` — `temp_root`, `make_plugin` | plugin fixtures | crate-specific → **relocate to `eos-plugin-catalog/tests/`** |
| `eos-skills/src/test_support.rs` — `Scratch` | temp-dir RAII | crate-specific → **relocate to `eos-skills/tests/`** |
| `eos-tools` testsupport | tools fixtures | **already compliant** (`#[path]` → `tests/support/`) |

Only the first three rows touch `eos-testkit`; the other four relocate to their
own crate's `tests/`. The `metadata()` helper in engine `test_support.rs` is
engine-local — it relocates with the engine file, it does not go to `eos-testkit`.

## 8. Removals (this spec performs them)

- **Delete `eos-sandbox-host/tests/write_stdin_live.rs`.** It is the only live
  Docker/`eosd` test in `agent-core` (`#[ignore]`, `EOS_LIVE_E2E_IMAGE`). Its
  scenario (write-stdin empty-response not replayed) moves to the root
  `integration-test/` module as a live test. No live test remains in
  `agent-core`.

## 9. Per-crate test layout (target)

Layer per §3: [A] = `EventSource` (real loop + dispatch), [B] = `AgentRunner`
(script runner). Checkpoint per §4.2.

```
eos-engine/tests/
  notifications/mod.rs          (exists)
  tool_call/dispatch/mod.rs     (exists)
  terminal/mod.rs           [A] NEW  ceiling / not-submitted / alone-call  (targeted)
  tool_errors/mod.rs        [A] NEW  dispatch error surfaces via FakeTransport (targeted)
  background/mod.rs         [A] NEW  background subagent supervisor

eos-workflow/tests/
  iteration/mod.rs              (exists)  retry + continuation boundaries (park / fail-halt)
  attempt/orchestrator/mod.rs   (exists)
  context/engine/mod.rs         (exists)
  delegate/mod.rs           [B] NEW  reducer exit gate (park after generators)
  plan_dag/mod.rs           [B] NEW  DAG materialization + PLAN->RUN (park after planner)

eos-runtime/tests/
  unit/mod.rs                   (exists; thin after §7 moves)
  root_agent/mod.rs         [A] NEW  request -> submit_root_outcome (closure)
  full_stack/mod.rs         [A] NEW  wiring only: root + delegated sub-agents via ScriptedSource (per agent name)
  state_persistence/mod.rs  [A] NEW  started-but-unfinished persisted rows (park via blocking source)

eos-llm-client/                 inline #[cfg(test)] mod tests stay (pure, mocked)
```

Each `tests/<topic>/mod.rs` is included from `src/` via `#[path]` and pulls the
doubles it needs from `eos-testkit` under `[dev-dependencies]`.

## 10. LLM-client testing rule

`eos-llm-client` tests **must never** reach a real provider. They:

- Parse provider streams from **SSE fixtures** (the existing `sse.rs` approach),
  and
- Drive retry/projection with the **scripted `Attempt` factory** (`retry.rs`),
  never `reqwest` against a live endpoint.

This is already the state of the crate; the spec freezes it as a requirement. No
`wiremock`/live HTTP in `agent-core` model-client tests.

## 11. Cargo wiring

- `agent-core/Cargo.toml`: add `"crates/eos-testkit"` to `members`; add
  `eos-testkit = { path = "crates/eos-testkit" }` to `workspace.dependencies`.
- `eos-engine`, `eos-runtime`, `eos-workflow`: add `eos-testkit = { workspace =
  true, features = [...] }` under `[dev-dependencies]` with the minimal feature.

## 12. Acceptance criteria

- **AC1** `cargo test --workspace` in `agent-core` passes with **no Docker and
  no network** available.
- **AC2** No test-support code under any production crate's `src/`:
  `grep -rEn 'mod (testsupport|test_support|testutil|fakes)' crates/*/src` returns
  nothing. All seven §7.1 modules are relocated — three to `eos-testkit`, four to
  their own crate's `tests/` via `#[path]` (`eos-tools` already compliant). Inline
  pure-logic `mod tests` excepted per I2.
- **AC3** `ScriptedSource` is defined exactly once (in `eos-testkit`); both
  prior copies are gone. Exactly two `AgentRunner` doubles exist; one
  `wait_until` waiter (no parallel waiter).
- **AC4** `eos-sandbox-host/tests/write_stdin_live.rs` no longer exists in
  `agent-core`.
- **AC5** No `agent-core` test references `DockerProviderAdapter::connect`,
  `EOS_LIVE_E2E_IMAGE`, or a real provider URL.
- **AC6** At least one Layer-A and one Layer-B test assert at a **non-closure**
  checkpoint (I5): an engine test that drops the stream mid-loop and an
  `eos-workflow` test that parks via `QueueRunner` and inspects RUN-stage rows
  without reaching `Closed`.
- **AC7** No behavior appears in the §5.2 matrix under two layers; the
  full-stack test asserts wiring only.
- **AC8** `cargo clippy --workspace --all-targets -- -D warnings` is clean for
  touched crates.

## 13. Migration order

0. Apply the §14.4 exposures (`RequestProvisioner` → `pub`, un-gate + `pub`
   `.provisioner(...)`, re-export). Verify `eos-runtime` still builds.
1. Create `eos-testkit` (`llm.rs` + `script.rs` + `engine.rs` + `sandbox.rs` +
   `state.rs`); move `ScriptedSource`/`build_test_state`/fakes; wire `eos-runtime`
   dev-dep. Verify existing `eos-runtime` tests still pass (proves the moved
   harness).
2. Delete the `eos-engine` cfg(test) `ScriptedSource` **and its duplicate
   `FakeTransport`**; repoint engine tests at `eos-testkit` (`ScriptedSource`,
   `FakeTransport`, and `MemoryStores` for the engine store fakes). Add
   `engine::run_until`.
3. Move `eos-workflow/src/testsupport/` into `eos-testkit/src/workflow.rs`
   (`workflow` feature); rewrite `deps()` to the `with_*` builders; generalize
   the waiter to `wait_until`; delete the `src/testsupport/` tree.
4. Relocate the four crate-specific/sealed §7.1 modules out of `src/` to their
   own `tests/` via `#[path]` (NOT `eos-testkit`): `eos-sandbox-host`
   `MockAdapter` (sealed — must stay local), `eos-plugin-catalog`
   `temp_root`/`make_plugin`, `eos-skills` `Scratch`, `eos-state` `FakeTaskStore`.
   Carry the engine-local `metadata()` with whatever remains of the engine test
   file. Verify each crate's tests still pass.
5. Delete `eos-sandbox-host/tests/write_stdin_live.rs` (its scenario lands in
   `integration-test`).
6. Add the NEW per-crate test modules (§9) incrementally, each owning exactly
   one row of the §5.2 coverage matrix.

## 14. Deep-investigation findings & corrections

A read-only feasibility audit (4 parallel investigations) verified the claims
above against the code. Three corrections supersede earlier sections; the rest
is confirmed.

### 14.1 CORRECTION — the two layers are two *altitudes*, never combined

`AppState`/`start_request` binds the runner unconditionally to the production
`RuntimeAgentRunner` (`eos-runtime/src/entry.rs:222`); there is **no**
`AppStateBuilder` setter for a runner. Therefore:

- **Layer A** (scripted `EventSource`) is the *only* scripting seam reachable
  through `AppState`. Root **and** delegated sub-agents both run Layer A, keyed
  per agent name via `factory_by_agent` / `factory_root_blocks_after`.
- **Layer B** (`ScriptedRunner`/`QueueRunner`) is reachable **only** at the
  `eos-workflow` altitude by constructing `AttemptDeps::new(stores…, runner)`
  directly — it bypasses request bootstrap, provisioning, and `root_agent`.

So **`full_stack` is Layer A only.** §5.2 and §9 are corrected from `[A+B]` to
`[A]`: `eos-runtime/tests/full_stack` drives root + delegated sub-agents through scripted
`EventSource`s and asserts wiring + persisted state; it never injects a
`ScriptedRunner`. The DAG-internals rows in §5.2 stay owned by Layer-B tests that
live in `eos-workflow/tests/` and use `AttemptDeps` directly.

### 14.2 CORRECTION — most setup items are single-consumer (see §15 open decision)

- Workflow doubles (`ScriptedRunner`, `QueueRunner`, `MemoryStores`, the waiter):
  **only `eos-workflow` consumes them.** `eos-runtime` does not.
- `build_test_state` + `FakeProvisioner` + `FakeTransport`: **only `eos-runtime`
  consumes them**, and the file already lives under `tests/` (pulled via
  `#[path]`), so it already satisfies I2.
- The **only** genuinely cross-crate double is `ScriptedSource` (engine tests +
  runtime tests + integration-test).

This reshapes what `eos-testkit` should hold — resolved in §15.

### 14.3 Verified-clean (no blockers)

| Claim | Verdict | Evidence |
|---|---|---|
| Merge the two `ScriptedSource` copies into one | clean (superset w/ `block_when_empty`) | `loop_.rs:258` vs `app_state_test_seams.rs:45`; all referenced types `pub` |
| `engine::run_until` on public APIs | clean | `run_query`, `QueryStream`, `QueryContext` (+ inspect fields), `QueryExitReason` all `pub` (`eos-engine/src/lib.rs:23`) |
| `wait_for_workflow_status` → `wait_until(predicate)` | trivial refactor | `runners.rs:390`; polls `pub(crate)` stores, so it stays beside them |
| Relocating workflow doubles out of `src/` | clean | every symbol `pub` except 3 `pub(crate)` `AttemptDeps` fields, each with a `pub` `with_*` builder (`launch.rs:282,292,316`) → ~3-line `deps()` rewrite, zero new `pub` |

### 14.4 Required exposures for `build_test_state` in `eos-testkit` (Broad, §15)

Three edits widen a private port (accepted per §15):

1. `app_state.rs:51` `pub(crate) trait RequestProvisioner` → `pub`.
2. `app_state.rs:302` drop `#[cfg(test)]` and `pub(crate)` → `pub` on
   `.provisioner(...)` (the `#[cfg(test)]` gate is a hard blocker: external crates
   see `eos-runtime` built without `--test`).
3. `eos-runtime/src/lib.rs:37` re-export `RequestProvisioner`.

## 15. RESOLVED — `eos-testkit` scope = Broad (one shared home)

Decision (owner): **`eos-testkit` holds everything shared** — the scripted-LLM
bridge **plus** `build_test_state`+fakes **plus** the workflow doubles. §6/§7
stand as written. The audit's narrower alternative was declined in favor of one
discoverable test-kit crate; the costs below are accepted as part of the plan:

- **Required production exposures (§14.4)** to let `build_test_state` live in an
  external crate: `RequestProvisioner` → `pub`, un-gate + `pub` the
  `.provisioner(...)` setter, and re-export `RequestProvisioner`. These are the
  one deliberate public-API widening this plan accepts.
- **`deps()` builder rewrite** so `MemoryStores::deps()` sets the three
  `AttemptDeps` fields via the existing `pub` `with_*` builders
  (`launch.rs:282,292,316`) instead of direct field writes — keeps the move
  zero-extra-`pub` on the `eos-workflow` side.
- **Dev-dep cycles** `eos-engine[dev] → eos-testkit → eos-engine` and
  `eos-workflow[dev] → eos-testkit → eos-workflow` (Cargo-legal; back-edge is
  dev-only).

Even under Broad, the §14.1 altitude correction stands: Layer B doubles live in
`eos-testkit` but are still only *reachable* by constructing `AttemptDeps`
directly in `eos-workflow/tests/`; they cannot be injected through `AppState`.
