# impl-eos-runtime — composition root: DI graph, request entry, sandbox provisioning, root-agent lifecycle

> Owning crate in the agent-core workspace. Conforms to ./spec-conventions.md.
> Plan section: ../backend_agent_core_rust_migration_PLAN.md §10 (and the
> cross-cutting Migration Phases / Cutover sections it points to).

## 1. Purpose & Responsibility (SRP)

`eos-runtime` is the **composition root** of agent-core. Its single
responsibility is *wiring*: it owns the typed dependency graph (`AppState`) that
constructs every concrete store (from `eos-db`) and every concrete seam
implementation (`LlmClient`, `ProviderAdapter`, `AuditSink`, `Clock`,
`AgentRegistry`, `SkillRegistry`, `PluginCatalog`), then injects those concretes
into the trait seams that `eos-engine` and `eos-workflow` depend on (DIP). It
creates the single Tokio multi-thread runtime, mints the root `Task` for a
top-level request, and runs the root agent **directly through `eos-engine`** —
there is no root workflow. It provisions one sandbox binding per request.

What this crate must **not** do: define any domain type, store trait, or seam
trait (those are owned upstream — see §5); implement query-loop, tool dispatch,
or workflow scheduling logic; introduce a global agent orchestrator; or mutate
the parent Task at workflow close. It is the only crate that may use `anyhow`
(`err-anyhow-app`) and the only crate that constructs/owns the async runtime.

## 2. Dependencies

**Upstream crates (depends on):** every other agent-core crate, because it wires
all of them. Direct construction edges: `eos-types`, `eos-state`, `eos-config`,
`eos-db`, `eos-audit`, `eos-llm-client`, `eos-tools`, `eos-agent-def`,
`eos-skills`, `eos-plugin-catalog`, `eos-sandbox-api`, `eos-sandbox-host`,
`eos-engine`, `eos-workflow`.

**Downstream consumers (used by):** none (top of the DAG). The thin `main.rs`
binary depends on the `eos_runtime` library (`proj-lib-main-split`).

**Implements / supplies at the composition root (anchor §6a/§6b):** the concrete
`AgentRunner` adapter (an `eos-workflow`-owned trait, §6a) over
`eos-engine::run_ephemeral_agent`, and the `IsolatedWorkspacePort` impl (an
`eos-tools`-owned trait, §6b) over the `eos-sandbox-host` isolated-workspace
lifecycle. It also injects the remaining downstream-state ports —
`SubagentSupervisorPort` / `AdvisorPort` / `NotificationSink` (implemented by
`eos-engine`) and `WorkflowControlPort` / `PlanSubmissionPort` (implemented by
`eos-workflow`) — into tool `ExecutionMetadata`. This wiring is the
composition-root half of the DIP seams (anchor §6).

**External crates** (pinned via `[workspace.dependencies]` inheritance,
`proj-workspace-deps`; declared with `{ workspace = true }`):

| Crate | Why | rust-skills |
|---|---|---|
| `tokio` (rt-multi-thread, macros) | owns the single multi-thread runtime + `JoinHandle` for the root-agent task | `async-tokio-runtime` |
| `anyhow` | top-level wiring errors with context chaining at the app boundary only | `err-anyhow-app` |
| `tokio-util` | `CancellationToken` propagated into `AppState`/root-agent for graceful shutdown | `async-cancellation-token` |
| `tracing` | structured startup/shutdown logging (replaces `logging` in `app_factory.py`) | — |
| `uuid` (v4) | mint `request_id` and `root-<hex16>` root-task ids (parity with `entry.py`) | — |

No `serde`/`schemars` is needed here directly: this crate owns no wire DTOs (all
serialized types are owned upstream). No `sqlx` (the pool/builder is owned by
`eos-db`; this crate only calls its builder).

## 3. Scope & Source Mapping

| Python source | Rust target | What moves / what is dropped |
|---|---|---|
| `runtime/entry.py` `RequestEntry`, `start_request`, `_create_top_level_request`, `_create_root_task`, `_schedule_root_agent`, `_run_root_agent`, `_fail_unfinished_root`, `_assert_stores_ready` | `entry.rs` | Logic ports 1:1. `_schedule_root_agent`'s `loop.create_task(...)` → `tokio::spawn` returning `JoinHandle<()>`. `RequestEntryHandle` moves to `entry.rs`. The `runner: object \| None` test-injection seam has two fates: (a) on the **root path** (`self._runner or run_ephemeral_agent`, entry.py:202) it is dropped in favor of `event_source_factory` / mock `EventSource`, which runs the real engine loop against a scripted source; (b) on the **launcher path** (`EphemeralAttemptAgentLauncher(runner=self._runner)`, entry.py:261) it is **not** dropped — it is promoted to the §6a `AgentRunner` seam (owned by eos-workflow), whose concrete adapter over `run_ephemeral_agent` this crate supplies (§2). |
| `runtime/entry.py` `_create_runtime` / `_build_composer` (builds `AttemptDeps`, `EphemeralAttemptAgentLauncher`, `ContextEngine`, registries) | `app_state.rs` (the DI graph) + a call into `eos-workflow` | The wiring of `AttemptDeps`/launcher/`ContextEngine` is **eos-workflow-owned construction** invoked here; `eos-runtime` supplies the wired stores + seams. The self-referential `runtime_ref`/`deps_provider` lambda becomes an `Arc` set once during build. |
| `runtime/app_factory.py` `RuntimeConfig` | dissolved into `AppState` + `eos-config::CentralConfig` (see below) | `cwd`/settings → `CentralConfig` (eos-config). `external_api_client` → `Arc<dyn LlmClient>` seam. `event_source_factory` → `Arc<dyn Fn(&AgentDefinition) -> Box<dyn EventSource>>` seam (production = `None` ⇒ live stream). No `system_prompt_override` (dead in Python). `_initial_messages` is **not** ported into the root path: it is resurrected-public-shape compat that no reader consumes (`config._initial_messages` is read nowhere; the engine's `initial_messages` param is fed from the launcher's computed initial messages in `workflow/attempt/launch.py`, not this field), so `run_root_agent` passes no `initial_messages`. |
| `runtime/app_factory.py` store singletons + `ensure_runtime_stores_ready` | `app_state.rs` `AppState::builder()...build()` | Module-level mutable singletons are removed; stores are constructed once inside the typed graph (single-place init). SQLAlchemy `sessionmaker` → `eos-db` `SqlitePool` builder. |
| `runtime/app_factory.py` `_model_registry_path` + `assert registry_path.is_file()` + `seed_from_json` | `app_state.rs` optional seed step | The hard `assert` is dropped: a missing/optional registry JSON is **non-fatal** at startup (GC-eos-runtime-04). |
| `runtime/sandbox_provisioning.py` `RequestSandboxBinding`, `RequestSandboxProvisioner` | `sandbox_provisioning.rs` | Ports 1:1. `create_fn`/`start_fn` injected `dict`-returning callables → calls through `eos-sandbox-host` `ProviderAdapter`. |
| (new) thin binary | `main.rs` | Parse minimal args/env, build `AppState`, call `start_request`, await the handle (`proj-lib-main-split`). |

**In scope:** runtime construction, request bootstrap, root-agent direct
execution, sandbox binding, single-place store/seam init, graceful shutdown.
**Out of scope:** the FastAPI lifespan/HTTP routers (already not restored in
Python); any domain/store/seam definition; query-loop, dispatch, or workflow
scheduling internals.

## 4. File & Module Layout

```
eos-runtime/
├── Cargo.toml
└── src/
    ├── lib.rs                 # pub use AppState, RequestEntryHandle, start_request,
    │                          #   RequestSandboxBinding, RequestSandboxProvisioner;
    │                          #   builds + owns the Tokio runtime via run() (proj-pub-use-reexport)
    ├── app_state.rs           # AppState (DI graph) + AppStateBuilder; single-place
    │                          #   store + seam construction; optional registry seed
    ├── entry.rs               # RequestEntry, RequestEntryHandle, start_request,
    │                          #   root-task minting, root-agent spawn, fail-unfinished glue
    ├── root_agent.rs          # run_root_agent: direct eos-engine call + outcome mapping
    └── sandbox_provisioning.rs# RequestSandboxBinding, RequestSandboxProvisioner

eos-runtime-bin/ (or src/bin/eos-runtime.rs)
    main.rs                    # thin entry point: AppState::builder().build()?, run()
```

`lib.rs` re-exports the public surface; everything else is `pub(crate)` unless
re-exported (`proj-pub-crate-internal`). `main.rs` holds no logic
(`proj-lib-main-split`) so `entry`/`app_state` stay integration-testable.

## 5. Contracts Owned Here

This crate sits at the top of the DAG and owns **wiring types only** — no shared
domain contract. The contracts below are the full set defined here; everything
else is referenced.

- **`AppState`** — the typed DI graph. Holds `Arc`-shared concretes + seams.
  Constructed once via `AppStateBuilder` (`api-builder-pattern`,
  `api-builder-must-use`). Not a trait; a concrete struct cloned cheaply
  (`Arc` fields, `own-arc-shared`).
- **`AppStateBuilder`** — `#[must_use]` builder; `build()` returns
  `anyhow::Result<AppState>`, validating config (fail-fast on network DB URL via
  eos-config/eos-db), constructing the `SqlitePool`, all stores, and all seams.
- **`RequestEntryHandle`** — returned by `start_request`. `#[non_exhaustive]`,
  derives `Debug` (no `Clone`: it owns a `JoinHandle`).
- **`RequestEntry`** — internal builder/runner for one request; `start()` returns
  `RequestEntryHandle`.
- **`start_request`** — free fn, the request bootstrap entry point.
- **`RequestSandboxBinding`** — `{ sandbox_id, request_id }` value object.
- **`RequestSandboxProvisioner`** — prepares the per-request sandbox binding.

**Referenced, not redefined here** (owners in anchor §5):

- Newtype IDs (`RequestId`, `TaskId`, `SandboxId`, …), `UtcDateTime`, `Clock`,
  `CoreError`, `JsonObject` — `eos-types`.
- `Task`, status/role enums, per-entity `Store` traits, terminal submission DTOs
  — `eos-state` (impls in `eos-db`).
- `SqlitePool` builder, sqlx repos, model registry — `eos-db` (see impl-eos-db.md).
- `CentralConfig` + sections, env loading, path resolution — `eos-config`.
- `AuditSink`, `AuditEventBus`, JSONL sink — `eos-audit`.
- `LlmClient`, provider-neutral `Message`, `ToolSpec` — `eos-llm-client`.
- `ToolRegistry`, `ToolExecutor`, `ExecutionMetadata` equivalent — `eos-tools`.
- `AgentDefinition`, `AgentRole`, `AgentRegistry`, `get_definition`/validation —
  `eos-agent-def`.
- `SkillRegistry` — `eos-skills`; `PluginCatalog` — `eos-plugin-catalog`.
- `ProviderAdapter` + provider registry, daemon client, lifecycle — `eos-sandbox-host`.
- `run_ephemeral_agent`, `EphemeralRunResult`, `EphemeralRunStatus`,
  `QueryContext`, `EventSource` — `eos-engine` (see impl-eos-engine.md).
- `WorkflowStarter`, `AttemptOrchestrator`, **`AttemptDeps`**,
  **`EphemeralAttemptAgentLauncher`**, `AttemptOrchestratorRegistry`,
  `OpenIterationCoordinatorRegistry`, `ContextEngine`, `AgentEntryComposer`,
  `WorkflowLifecycleConfig` — `eos-workflow` (see impl-eos-workflow.md). The
  `workflow_runtime` and `launcher` fields of `RequestEntryHandle` are these
  types; this doc references them and must not redefine their fields.

## 6. Types, Fields & Schemas

### `AppState` (DI graph)

Each field is `Arc`-shared so the graph clones cheaply for each spawned agent /
delegated workflow (`own-arc-shared`). Seam fields are `Arc<dyn Trait>`; those
traits are `#[async_trait]`-based to be `dyn`-safe behind the composition root
(anchor §6). Concrete registries that are read-only after build are
`Arc<Concrete>` (no `dyn`, `anti-type-erasure`).

| Field | Rust type | Notes / source-of-truth |
|---|---|---|
| `config` | `Arc<CentralConfig>` | eos-config; holds `cwd`, db, sandbox, provider sections |
| `clock` | `Arc<dyn Clock>` | eos-types seam (system clock; test clock in tests) |
| `db_pool` | `SqlitePool` | eos-db; already `Clone`/`Arc`-internal |
| `task_store` | `Arc<dyn TaskStore>` | eos-state trait, eos-db sqlx impl |
| `workflow_store` | `Arc<dyn WorkflowStore>` | eos-state trait |
| `iteration_store` | `Arc<dyn IterationStore>` | eos-state trait |
| `attempt_store` | `Arc<dyn AttemptStore>` | eos-state trait |
| `agent_run_store` | `Arc<dyn AgentRunStore>` | eos-state trait |
| `model_store` | `Arc<dyn ModelStore>` | eos-state trait; seeded optionally (GC-04) |
| `llm_client` | `Arc<dyn LlmClient>` | replaces `RuntimeConfig.external_api_client` |
| `event_source_factory` | `Option<EventSourceFactory>` = `Option<Arc<dyn Fn(&AgentDefinition) -> Box<dyn EventSource> + Send + Sync>>` | `None` ⇒ live provider stream; mock harness sets it (replaces `RuntimeConfig.event_source_factory`). Kept as a type-erased **synchronous** composition-root closure (not promoted to a named trait like §6a's `AgentRunner`): the §6a anti-type-erasure ruling targets async DI params (its `anti-type-erasure` note is about `Arc<dyn Fn -> BoxFuture>`), but the Python factory is synchronous and returns the trait object directly (`app_factory.py:61`), so there is no future to erase and a one-method trait would add no testability or type safety over this alias. |
| `audit` | `Arc<dyn AuditSink>` | eos-audit; JSONL sink in prod |
| `tool_registry` | `Arc<ToolRegistry>` | eos-tools, read-only after build |
| `agent_registry` | `Arc<dyn AgentRegistry>` | eos-agent-def |
| `skill_registry` | `Arc<dyn SkillRegistry>` | eos-skills |
| `plugin_catalog` | `Arc<dyn PluginCatalog>` | eos-plugin-catalog |
| `provider_registry` | `Arc<ProviderRegistry>` | eos-sandbox-host concrete registry (holds `ProviderAdapter` seams: Docker default / Daytona) |
| `shutdown` | `CancellationToken` | tokio-util; parent-exit / graceful cancellation |

```rust
/// Composition-root dependency graph. Cloning is cheap (Arc fields).
#[derive(Clone)]
#[non_exhaustive]
pub struct AppState {
    pub config: Arc<CentralConfig>,
    pub clock: Arc<dyn Clock>,
    pub db_pool: SqlitePool,
    pub task_store: Arc<dyn TaskStore>,
    pub workflow_store: Arc<dyn WorkflowStore>,
    pub iteration_store: Arc<dyn IterationStore>,
    pub attempt_store: Arc<dyn AttemptStore>,
    pub agent_run_store: Arc<dyn AgentRunStore>,
    pub model_store: Arc<dyn ModelStore>,
    pub llm_client: Arc<dyn LlmClient>,
    pub event_source_factory: Option<EventSourceFactory>,
    pub audit: Arc<dyn AuditSink>,
    pub tool_registry: Arc<ToolRegistry>,
    pub agent_registry: Arc<dyn AgentRegistry>,
    pub skill_registry: Arc<dyn SkillRegistry>,
    pub plugin_catalog: Arc<dyn PluginCatalog>,
    pub provider_registry: Arc<ProviderRegistry>,
    pub shutdown: CancellationToken,
}

#[must_use = "AppStateBuilder does nothing until build() is called"]
#[derive(Default)]
pub struct AppStateBuilder { /* optional overrides for tests; None ⇒ prod default */ }

impl AppStateBuilder {
    /// Construct the runtime graph: load config, build the SqlitePool (fail fast
    /// on a network DB URL), construct every store and seam, optionally seed the
    /// model registry.
    ///
    /// # Errors
    /// Returns an error if config is contradictory, the DB URL is non-local, the
    /// pool/migrations fail, or a seam fails to initialize.
    pub fn build(self) -> anyhow::Result<AppState> { /* ... */ }
}
```

### `RequestEntryHandle`

| Field | Rust type | Source-of-truth |
|---|---|---|
| `request_id` | `RequestId` | eos-types newtype (`str` in Python) |
| `root_task_id` | `TaskId` | eos-types newtype |
| `workflow_runtime` | `AttemptDeps` | eos-workflow (referenced; not redefined) |
| `launcher` | `EphemeralAttemptAgentLauncher` | eos-workflow (referenced) |
| `root_agent_task` | `tokio::task::JoinHandle<()>` | replaces `asyncio.Task[None]` |

### `RequestSandboxBinding`

| Field | Rust type | Source-of-truth |
|---|---|---|
| `sandbox_id` | `SandboxId` | eos-types newtype (`str` in Python) |
| `request_id` | `RequestId` | eos-types newtype |

Derives `Debug, Clone, PartialEq` (`api-common-traits`), `#[non_exhaustive]`.

### `RequestSandboxProvisioner`

Holds optional injected create/start closures (test seams); production routes
through `AppState.provider_registry`. `prepare_for_run(request_id, sandbox_id:
Option<SandboxId>) -> anyhow::Result<RequestSandboxBinding>`: when an explicit id
is present, `start` it and bind; otherwise `create` a sandbox named
`request-<hex8>` with labels `{ "origin": "workflow", "request_id": <id> }` and
bind the returned id (error if empty — parity with Python `create_sandbox
returned no id`).

## 7. Concurrency & State Ownership

- **Runtime:** this crate constructs the **single Tokio multi-thread runtime**
  (`async-tokio-runtime`) in `lib.rs::run` / `main.rs`. All lower crates are
  runtime-agnostic. The root-agent future is launched with `tokio::spawn`,
  yielding `root_agent_task: JoinHandle<()>` (replaces `loop.create_task`); the
  Python guard "requires an active asyncio event loop" becomes a compile/runtime
  guarantee that `start_request` is called within the runtime.
- **Shared immutable state:** `AppState` and every registry/config/seam it holds
  are `Arc<T>` cloned per spawned agent and per delegated workflow
  (`own-arc-shared`). `AppState: Clone` is the cheap-clone graph handle.
- **Self-reference fix:** the Python `runtime_ref`/`deps_provider` late-binding
  lambda is replaced by building the seams first, then constructing
  `AttemptDeps` once (no `Option`-then-mutate cell). If the launcher genuinely
  needs the wired `AttemptDeps`, supply it via `Arc<OnceLock<AttemptDeps>>` set
  exactly once during build — no `Mutex`, no mutation after build.
- **Cancellation:** `AppState.shutdown: CancellationToken` is the graceful
  shutdown / parent-exit token (`async-cancellation-token`); it is cloned into
  the engine background supervisor and into delegated-workflow handles.
- **Locks:** none owned here. There is no app-level DB mutex (SQLite
  single-writer is handled by `eos-db` via WAL + busy timeout, anchor §7). No
  lock is held across `.await` because this layer holds no shared mutable state.
- **DB:** `SqlitePool` (owned, cheaply cloneable) governs connection concurrency.
- **Background:** the JoinSet/mpsc/oneshot/watch machinery lives in `eos-engine`;
  `eos-runtime` only spawns the single root-agent task and awaits/joins it.

## 8. Behavior & Invariants

Preserve these semantics from the plan (§§Core Design Rules, Non-Goals) and the
Python source:

1. **Root is a Task, not a Workflow** (non-goal: no synthetic root workflow). A
   top-level request mints exactly one root `Task(role=root, workflow_id=None,
   iteration_id=None, attempt_id=None, status=running)` and runs the root agent
   **directly** through `eos-engine::run_ephemeral_agent` — never through the
   workflow starter. Root task id format `root-<hex16>` (parity with Python).
2. **Stores ready before start.** `start_request` asserts all required stores
   are ready before minting state (port `_assert_stores_ready`); with the builder
   model this is satisfied by construction, but the guard remains for the
   injected-store test path.
3. **Single-place store init.** Every store required by workflow/runtime is
   constructed once in `AppStateBuilder::build` (GC-eos-runtime-02); no
   module-level mutable singletons.
4. **Request bootstrap order** (port `RequestEntry.start`): mint `request_id` →
   `provisioner.prepare_for_run` → `task_store.create_request(request_id, cwd,
   sandbox_id, request_prompt)` → build workflow runtime (`AttemptDeps` +
   launcher + composer via eos-workflow) → `upsert_task(root)` +
   `set_root_task_id` → spawn root agent.
5. **Root-agent outcome mapping** (`root_agent.rs`, port `_run_root_agent`):
   resolve the `root` definition from `agent_registry`; if absent, fail-unfinished
   with summary "root agent definition 'root' is not registered". Build execution
   metadata (`request_id`, `task_id`, `active_terminals` from `root_def.terminals`,
   `task_store`, `attempt_runtime`). Call `run_ephemeral_agent(..., agent_def=root,
   sandbox_id, persist_agent_run=true, task_id=root_task_id, on_event,
   extra_tool_metadata)`. The run uses `AppState.event_source_factory` when set
   (mock harness) else the live stream.
6. **`_fail_unfinished_root` invariant** (port verbatim): runs only if the root
   task's status is **still `running`** (avoids clobbering a real terminal). On
   crash or `status == failed || terminal_result is None`, set the root task
   status `failed` with an outcome `{status: "failed", role: "root", task_id,
   outcome: <summary>}` and `terminal_tool_result = { "fail_reason":
   "root_run_exhausted" }`, then `finish_request(request_id, "failed")`. Success
   (terminal_result present, status completed) leaves the engine-stamped terminal
   as the persisted outcome — runtime does not overwrite it.
7. **No parent-Task mutation at workflow close** (non-goal). `eos-runtime` never
   touches a delegated workflow's parent Task; the parent owns its terminal
   submission. `eos-runtime` only spawns the root and wires `AttemptDeps`.
8. **Sandbox labels** must remain `origin=workflow` + `request_id` (consumed by
   sandbox lifecycle / cleanup), and explicit-id path starts (not creates) the
   sandbox.
9. **Model registry is compatibility-only** (GC-04): a missing/optional registry
   JSON is logged and skipped, never a startup `assert`/panic.

### 8a. Migration phases & cutover (integration view)

`eos-runtime` is the integration point where every prior phase converges, so the
phased rollout (plan §Migration Phases) is summarized here:

- **Phase 0 — Scaffolding/parity harness:** workspace + crate skeletons, fmt/clippy/CI, schema + SSE + SQLite snapshot fixtures.
- **Phase 1 — State/Config/DB/Audit:** `eos-types/-state/-config/-db/-audit`; store roundtrips, migrations, audit golden. *Provides every store + config + audit seam this crate wires.*
- **Phase 2 — LLM client:** `eos-llm-client`; Anthropic/OpenAI SSE, retry, error mapping. *Provides the `LlmClient` seam.*
- **Phase 3 — Tools:** `eos-tools`; specs, registry, terminal stamping, dispatch. *Provides `ToolRegistry`.*
- **Phase 4 — Engine:** `eos-engine`; query loop, dispatch, background supervisor, prompt reports. *Provides `run_ephemeral_agent`/`EphemeralRunResult`/`EventSource`.*
- **Phase 5 — Workflow & Runtime:** `eos-workflow` + **this crate**. Verifications gate here: root request creates a root Task and **no** root workflow; `delegate_workflow` creates `Workflow→Iteration→Attempt` and leaves the parent Task running; sandbox provisioning works.
- **Phase 6 — Sandbox host/plugins/skills:** `eos-sandbox-api/-host`, plugin catalog, skill registry. *Provides `ProviderAdapter`/catalog/registry seams.*
- **Phase 7 — Cutover:** Python compatibility adapters run old/new control planes side by side; the Rust control plane (rooted at `AppState` here) runs against the existing daemon + DB fixtures; Python packages retire by boundary after parity. `test_runner` is **not** migrated.

## 9. SOLID & Principles Applied

- **DIP:** this crate is the *only* place concretes are bound to seams. High-level
  crates (`eos-engine`, `eos-workflow`) depend on `Arc<dyn Trait>` seams; `eos-db`
  and the provider/registry crates implement them; `AppStateBuilder` injects. No
  high-level crate ever names a concrete store/provider type.
- **OCP:** behavior extends by *registering* into `tool_registry`,
  `agent_registry`, `skill_registry`, `plugin_catalog`, `provider_registry` — the
  builder wires the registries; adding a tool/provider does not edit a `match`
  here.
- **ISP:** `AppState` carries per-entity store seams (`TaskStore`,
  `WorkflowStore`, …) rather than one god-store, matching eos-state's split.
- **LSP:** seams are provider-neutral (`LlmClient`, `EventSource`,
  `ProviderAdapter`), so the mock harness substitutes via
  `event_source_factory` / injected provisioner closures with no behavior change.
- **SRP:** wiring only. `ContextEngine`/`AttemptDeps` construction is delegated to
  eos-workflow's constructors; this crate supplies inputs, not lifecycle policy.
- **KISS/YAGNI/DRY:** smallest concrete shape — `anyhow`-only error handling (no
  thiserror enum at the app boundary), one `AppState` struct + one builder, no
  speculative config beyond what `CentralConfig` already exposes. The only
  abstractions used are the seams already in anchor §6; this crate adds none.
- **Non-goals respected:** no global orchestrator, no peer-to-peer messaging, no
  synthetic root workflow, no parent-Task mutation at close, no provider
  `class_path` dynamic import (provider chosen by typed `sandbox_provider` /
  `llm_provider` config).

## 10. Gap Closeouts (tracked requirements)

- **GC-eos-runtime-01 — Root runs directly, no root workflow.** `start_request`
  mints a root `Task(role=root, workflow_id=None)` and dispatches it through
  `eos-engine::run_ephemeral_agent`; the workflow starter is never invoked for the
  root. *Resolution:* `entry.rs`/`root_agent.rs` call the engine directly; proven
  by AC-eos-runtime-01.
- **GC-eos-runtime-02 — Single-place store init.** Every store
  (task/workflow/iteration/attempt/agent_run/model) plus every seam is
  constructed once in `AppStateBuilder::build`. *Resolution:* remove module-level
  singletons; the typed graph is the sole init site; proven by AC-eos-runtime-04.
- **GC-eos-runtime-03 — No parent-Task mutation at workflow close.** `eos-runtime`
  performs no close-time parent mutation. *Resolution:* runtime only spawns the
  root and wires `AttemptDeps`; close/outcome flow stays in eos-workflow; proven
  by AC-eos-runtime-05 (parent Task remains `running` after delegation).
- **GC-eos-runtime-04 — Model registry JSON is compatibility-only.** A missing or
  optional registry JSON must not fail startup. *Resolution:* replace the Python
  hard `assert registry_path.is_file()` with a logged, skipped optional seed step;
  proven by AC-eos-runtime-06.

## 11. Acceptance Criteria

TDD: write each test first, confirm it fails for the right reason, then implement.
Maps to plan Phase 5/7 verifications and anchor §11.

- **AC-eos-runtime-01** — *root request creates a root Task, no root workflow.*
  Test `entry_tests::start_request_mints_root_task_no_workflow`: with a mock
  `LlmClient`/`EventSource` that immediately submits a root terminal, assert a
  `Task(role=root, workflow_id=None)` row exists, `set_root_task_id` was called,
  and **no** Workflow row was created. (Plan Phase 5: "root request creates root
  task and no root workflow".)
- **AC-eos-runtime-02** — *root-agent success persists the engine terminal.*
  Test `root_agent_tests::successful_root_keeps_engine_terminal`: when
  `run_ephemeral_agent` returns `status=completed` with a `terminal_result`,
  `_fail_unfinished_root` does nothing and the request is not force-failed.
- **AC-eos-runtime-03** — *root-agent exhaustion fails cleanly.* Test
  `root_agent_tests::unfinished_root_sets_run_exhausted`: when the run ends with
  `terminal_result=None` (or crashes) while the task is still `running`, the task
  is set `failed` with `terminal_tool_result.fail_reason == "root_run_exhausted"`
  and `finish_request(failed)` is called. If status is no longer `running`, no
  mutation occurs. (GC-01 invariant.)
- **AC-eos-runtime-04** — *single-place graph construction.* Test
  `app_state_tests::builder_constructs_all_stores_and_seams`: `AppStateBuilder::
  build()` against an in-memory SQLite (eos-db builder) yields an `AppState` whose
  stores all report ready; a network DB URL makes `build()` return an error
  (fail-fast). (Plan Phase 1 store roundtrips feed this; GC-02.)
- **AC-eos-runtime-05** — *delegation leaves the parent Task running.* Test
  `entry_tests::delegate_workflow_leaves_parent_running`: a root agent that calls
  `delegate_workflow` creates `Workflow→Iteration→Attempt`; after the delegated
  workflow completes, the parent root Task status is still `running` (no
  close-time mutation). (Plan Phase 5; GC-03.)
- **AC-eos-runtime-06** — *missing model registry is non-fatal.* Test
  `app_state_tests::missing_model_registry_does_not_fail_startup`: pointing the
  optional registry path at a nonexistent file still produces a usable
  `AppState`; the absence is logged, not asserted. (GC-04.)
- **AC-eos-runtime-07** — *sandbox provisioning binds and labels correctly.* Tests
  `sandbox_provisioning_tests::explicit_id_starts_and_binds` and
  `::auto_create_labels_origin_workflow`: explicit id path calls `start` and binds
  it; auto path calls `create` with labels `{origin: "workflow", request_id}` and
  rejects an empty returned id. (Plan Phase 5/6: "request sandbox provisioning".)
- **AC-eos-runtime-08** — *end-to-end root request with mocked LLM.* Integration
  test `tests/e2e_root_request.rs`: build `AppState` with the mock
  `event_source_factory` (test-mock-traits), `start_request`, await
  `root_agent_task`, assert the request finishes with the submitted root outcome.
  (Plan Phase 7: "end-to-end root agent request with mocked LLM".)

## 12. Implementation Checklist

1. `cargo new` the crate; declare workspace deps (§2); add workspace lints &
   `#![forbid(unsafe_code)]`. → verify: `cargo build -p eos-runtime`.
2. Port `RequestSandboxBinding` + `RequestSandboxProvisioner` to
   `sandbox_provisioning.rs` with injected closures; write AC-07 tests first. →
   verify: AC-eos-runtime-07.
3. Write `AppStateBuilder::build` skeleton: config load, `SqlitePool` builder
   call (fail-fast on network URL), construct all stores + seams; AC-04 + AC-06
   tests first. → verify: AC-eos-runtime-04, AC-eos-runtime-06.
4. Port `RequestEntry`/`start_request`/root-task minting to `entry.rs`; wire
   `AttemptDeps` via eos-workflow constructors using `AppState` inputs; AC-01
   test first. → verify: AC-eos-runtime-01.
5. Port root-agent lifecycle to `root_agent.rs` (`run_ephemeral_agent` call +
   `_fail_unfinished_root` invariant); AC-02/AC-03 tests first. → verify:
   AC-eos-runtime-02, -03.
6. Add `tests/e2e_root_request.rs` with mock `event_source_factory`; AC-05 +
   AC-08. → verify: AC-eos-runtime-05, -08.
7. Add thin `main.rs`: build runtime, build `AppState`, `start_request`, join.
   → verify: `cargo run -p eos-runtime` against a fixture prompt + mock seam.
8. `cargo fmt --check` + `cargo clippy --workspace --all-targets -- -D warnings`.

---
**On completion:** update the Progress Tracker in `./overview.md` for row
`eos-runtime` per spec-conventions.md §13. Do not edit other crates' rows.
