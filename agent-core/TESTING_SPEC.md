# agent-core Test Architecture Spec

Status: draft · Scope: `agent-core/` workspace only · Companion: `integration-test/SPEC.md`

## 1. Purpose

Define how `agent-core` is tested. After this spec lands, `agent-core` contains
**unit / behavior tests only** — no Docker, no live `eosd`, no real network, no
real model. Every test verifies the correctness of a functionality or feature
with the three external I/O edges mocked. All shared test setup lives in one
crate, `eos-testkit`; no production crate carries test-support code in `src/`.

Live end-to-end coverage (real sandbox + real `agent-core`) is **out of scope
here** and lives in the root `integration-test/` module (see its spec).

## 2. Invariants

- **I1 — Mock-only.** No `agent-core` test starts a container, dials `eosd`, or
  makes a network call to a model provider. CI can run the full `agent-core`
  suite with no Docker and no API keys.
- **I2 — No test-support in production `src/`.** No `testsupport/`, doubles,
  fakes, fixtures, builders, or harness code under any production crate's
  `src/`. They live in `eos-testkit/src/`. (Small inline `#[cfg(test)] mod
  tests` that assert a single module's *pure* logic and define **no reusable
  doubles** are permitted — e.g. `eos-llm-client`'s SSE/retry tests.)
- **I3 — Tests under `tests/`.** Behavior tests live in each crate's
  `tests/<topic>/mod.rs` and are included via the existing
  `#[path = "../tests/<topic>/mod.rs"]` attribute from `src/` (the convention
  already used at `eos-runtime/src/lib.rs`).
- **I4 — Mock only at the three real edges.** Everything else runs live:
  engine loop, tool dispatch, workflow/attempt lifecycle, state machine, and a
  real temporary SQLite database. Bugs are found by exercising real code paths,
  not by mocking internals.

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

### Choosing a layer

- Verifying **how the loop reacts to model output** (tool calls, terminals,
  ceilings, errors) → **Layer A**.
- Verifying **how the workflow advances given agent outcomes** (DAG, stages,
  reducer) → **Layer B**.
- A few `eos-runtime` full-stack tests may combine both: Layer A for the root
  agent, Layer B for delegated sub-agents.

All doubles in both layers are built on **public** APIs (`AppState::builder()`
and the trait surfaces are already `pub`), so no production crate needs a
`test-util` feature gate.

## 4. `eos-testkit` — the test-setup crate

New workspace member: `agent-core/crates/eos-testkit`. A dev-dependency library
whose `src/` *is* test infrastructure (this is not "test code in a production
crate" — it is a dedicated test crate, which satisfies I2).

```
crates/eos-testkit/
├── Cargo.toml
└── src/
    ├── lib.rs        # thin re-export surface
    ├── llm.rs        # ScriptedSource (the single copy), tool_use_turn, text_turn
    ├── script.rs     # .ron/.json script -> per-agent turns -> tool_use_turn lowering
    ├── sandbox.rs    # FakeTransport, FakeProvisioner
    ├── agents.rs     # agent_def() builder, common allowed/terminal tool sets
    └── state.rs      # build_test_state(llm_tier, mock-sandbox) over temp SQLite
```

### 4.1 Feature slicing

So a low-level crate's test build does not compile the whole stack:

- `llm` → pulls only `eos-engine` + `eos-llm-client` (the `ScriptedSource` path).
- `mock-state` → adds `eos-runtime` (`build_test_state`, `FakeProvisioner`).
- `workflow` → adds `eos-workflow` + `eos-state` (the relocated `AgentRunner`
  doubles, see §5).

Each production crate enables only the feature it needs. The resulting dev-dep
cycle (e.g. `eos-engine[dev] → eos-testkit[llm] → eos-engine`) is permitted by
Cargo because the back-edge is dev-only.

### 4.2 Public surface (illustrative)

```rust
// eos-testkit
pub use llm::{ScriptedSource, tool_use_turn, text_turn};
pub use script::{Script, AgentBackend};   // AgentBackend::{Scripted(path), Real}
pub use sandbox::{FakeTransport, FakeProvisioner};
pub use agents::agent_def;
pub use state::build_test_state;
```

`AgentBackend`/`Script` also serve the root `integration-test` module, which
reuses `ScriptedSource` for its scripted-LLM tier.

## 5. Relocations (this spec performs them)

| Move | From | To |
|---|---|---|
| `ScriptedSource`, `tool_use_turn` | `eos-engine/src/query/loop_.rs` (cfg(test)) **and** `eos-runtime/tests/unit/app_state_test_seams.rs` | `eos-testkit/src/llm.rs` (one copy; delete both originals) |
| `FakeTransport`, `FakeProvisioner`, `build_test_state` | `eos-runtime/tests/unit/app_state_test_seams.rs` | `eos-testkit/src/{sandbox,state}.rs` |
| `AgentRunner` doubles (`ScriptedRunner`, `QueueRunner`), test stores | `eos-workflow/src/testsupport/` | `eos-testkit/src/` (under `workflow` feature) — **removes the last `testsupport/` from a production `src/`** |

After these moves, `eos-runtime/tests/unit/app_state_test_seams.rs` shrinks to
only its remaining crate-local seams (or retires), and
`eos-workflow/src/testsupport/` is deleted.

## 6. Removals (this spec performs them)

- **Delete `eos-sandbox-host/tests/write_stdin_live.rs`.** It is the only live
  Docker/`eosd` test in `agent-core` (`#[ignore]`, `EOS_LIVE_E2E_IMAGE`). Its
  scenario (write-stdin empty-response not replayed) moves to the root
  `integration-test/` module as a live test. No live test remains in
  `agent-core`.

## 7. Per-crate test layout (target)

Layer per §3: [A] = `EventSource` (real loop + dispatch), [B] = `AgentRunner`
(script runner, injected submissions).

```
eos-engine/tests/
  notifications/mod.rs          (exists)
  tool_call/dispatch/mod.rs     (exists)
  terminal/mod.rs           [A] NEW  hard-ceiling, not-submitted, terminal-alone
  tool_errors/mod.rs        [A] NEW  dispatch error surfaces via FakeTransport errors
  background/mod.rs         [A] NEW  background subagent supervisor

eos-workflow/tests/
  iteration/mod.rs              (exists)
  attempt/orchestrator/mod.rs   (exists)
  context/engine/mod.rs         (exists)
  delegate/mod.rs           [B] NEW  delegate_workflow -> Workflow/Iteration/Attempt
  plan_dag/mod.rs           [B] NEW  PLAN->RUN->CLOSED, reducer exit gate

eos-runtime/tests/
  unit/mod.rs                   (exists; thin after §5 moves)
  root_agent/mod.rs         [A] NEW  request -> submit_root_outcome
  full_stack/mod.rs       [A+B] NEW  root agent via ScriptedSource, sub-agents via ScriptedRunner
  state_persistence/mod.rs  [B] NEW  Request/Task/Workflow rows in temp SQLite

eos-llm-client/                 inline #[cfg(test)] mod tests stay (pure, mocked)
```

Each `tests/<topic>/mod.rs` is included from `src/` via `#[path]` and pulls the
doubles it needs from `eos-testkit` under `[dev-dependencies]`.

## 8. LLM-client testing rule

`eos-llm-client` tests **must never** reach a real provider. They:

- Parse provider streams from **SSE fixtures** (the existing `sse.rs` approach),
  and
- Drive retry/projection with the **scripted `Attempt` factory**
  (`retry.rs`), never `reqwest` against a live endpoint.

This is already the state of the crate; the spec freezes it as a requirement. No
`wiremock`/live HTTP in `agent-core` model-client tests.

## 9. Cargo wiring

- `agent-core/Cargo.toml`: add `"crates/eos-testkit"` to `members`; add
  `eos-testkit = { path = "crates/eos-testkit" }` to `workspace.dependencies`.
- `eos-engine`, `eos-runtime`, `eos-workflow`: add `eos-testkit = { workspace =
  true, features = [...] }` under `[dev-dependencies]` with the minimal feature.

## 10. Acceptance criteria

- **AC1** `cargo test --workspace` in `agent-core` passes with **no Docker and
  no network** available.
- **AC2** `grep -r testsupport crates/*/src` returns nothing; no doubles/fakes/
  fixtures under any production crate's `src/` (inline pure-logic `mod tests`
  excepted per I2).
- **AC3** `ScriptedSource` is defined exactly once (in `eos-testkit`); the two
  prior copies are gone.
- **AC4** `eos-sandbox-host/tests/write_stdin_live.rs` no longer exists in
  `agent-core`.
- **AC5** No `agent-core` test references `DockerProviderAdapter::connect`,
  `EOS_LIVE_E2E_IMAGE`, or a real provider URL.
- **AC6** `cargo clippy --workspace --all-targets -- -D warnings` is clean for
  touched crates.

## 11. Migration order

1. Create `eos-testkit` (`llm.rs` + `script.rs` + `sandbox.rs` + `state.rs`);
   move `ScriptedSource`/`build_test_state`; wire `eos-runtime` dev-dep. Verify
   existing `eos-runtime` tests still pass (proves the moved harness).
2. Delete the `eos-engine` cfg(test) `ScriptedSource`; repoint engine tests at
   `eos-testkit`.
3. Move `eos-workflow/src/testsupport/` into `eos-testkit` (`workflow`
   feature); delete the `src/testsupport/` tree.
4. Delete `eos-sandbox-host/tests/write_stdin_live.rs` (its scenario lands in
   `integration-test`).
5. Add the NEW per-crate test modules (§7) incrementally.
