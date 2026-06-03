# SPEC: Rust `test_runner` — bridging `sandbox` and `agent-core`

Status: **draft (pre-adversarial-review)**
Date: 2026-06-03
Owner doc: this file (`docs/plans/test_runner_rust_SPEC.md`)
Supersedes (for the harness tier): `docs/plans/test_runner_migration_PLAN.md`
(that plan renamed the **Python** `task_center_runner -> test_runner` and
deliberately kept the harness + host/API boundary in Python; this spec moves the
harness itself to **Rust**, now that `sandbox/` and `agent-core/` are migrated).

---

## 0. TL;DR

Build a new **top-level Rust workspace `test_runner/`** (peer to `sandbox/` and
`agent-core/`) that drives a **real** Rust sandbox and the **real** Rust
agent-core engine to test agent execution end-to-end. It has four modules:

| Module | Job | Primary upstream surface |
|---|---|---|
| `config` | One centralized config: api-client creds (.env-writable), sandbox setup, multi-node size, agent-core run params | `eos-config::CentralConfig` (extended) |
| `audit` | **Unified, human-readable, correlated** trace across agent-core + sandbox | `eos-audit::AuditSink` (in-proc) + `api.audit.pull` (sandbox ring) |
| `agent-core` (mock + api) | Drive a run from a user request to completion/partial; inject a **`MockedLlmClient`**; trivial live api-client smoke | `EventSource`/`LlmClient` seams; `eos-runtime::start_request` |
| `sandbox` | Provision a **fast, reusable** dask container; single- and multi-node; real (never mocked) | `eos-sandbox-host` + `/sandbox` wire protocol |

The single hardest fact — **is the LLM client injectable?** — resolved **YES,
no new seam needed**: the engine loop consumes `Arc<dyn EventSource>`; concrete
Anthropic/OpenAI clients are built only in `eos-runtime::default_llm_client`.
The only agent-core change for mock injection is **promoting an existing
`#[cfg(test)]` mock to a `pub` test-support feature**.

The audit reform (the user's explicit ask) is the largest source-side change and
gets its own module + a four-facet model: **semantics / performance / resource /
correctness**, joined per-tool-call by stamping the engine `tool_use_id` onto the
sandbox request.

---

## 1. Goals / Non-Goals

### Goals
1. Run the canonical request flow (`user request -> root Task -> root agent ->
   optional delegate_workflow -> submit_root_outcome`) under test, with the
   ability to **terminate early** when a test condition is met (partial result).
2. **Mock LLM** tier: scripted thinking/text/tool-call turns injected into the
   **real** engine loop. Sandbox is **never** mocked.
3. **Live api-client** tier: a *trivial* smoke proving `anthropic.rs` and
   `openai.rs` produce well-shaped tool calls + honor the system reminder.
4. **Sandbox** tier: fast reusable dask container; configurable multi-node.
5. **Unified audit**: collect agent-core + sandbox events into one correlated,
   human-readable timeline reflecting semantics/performance/resource/correctness.
6. **Centralized config**: api-client (.env), sandbox, agent-core run params.
7. Preserve the Python harness's test **rigor** (difficulty / complexity /
   load-bearing) while discarding its bad layout.

### Non-Goals (scope discipline — see `CLAUDE.md`)
- No new agent orchestration layer; no peer-to-peer agent comms.
- No re-implementation of a fake agent loop — tests drive the **real** loop.
- No exhaustive provider matrix for the api-client test — it is intentionally
  trivial.
- No port of the over-engineered Python scenarios (`full_stack_adversarial`,
  `full_system_capacity_matrix`, `pack_catalog`, the ~120-file
  `isolated_workspace` explosion). Port the **invariants**, not the file count.
- No Daytona/Minimax client wiring (agent-core is Docker + Anthropic/OpenAI).

---

## 2. Key decisions & assumptions

> Material assumptions are stated here rather than guessed silently.

- **A1 — Location: new top-level workspace `test_runner/`.** The user asked for a
  module "at `…/EphemeralOS`". It path-depends on agent-core crates (`eos-runtime`,
  `eos-engine`, `eos-audit`, `eos-sandbox-host`, `eos-sandbox-api`, `eos-config`,
  `eos-workflow`, `eos-state`) and one sandbox crate (`eos-protocol`, for typed
  audit `*Section` deserialization). Dependency direction is **test_runner ->
  {agent-core, sandbox}**, never the reverse. (Alternative considered: a crate
  inside the agent-core workspace — rejected to honor the explicit root location
  and keep the harness from polluting agent-core's dependency graph.)
- **A2 — LLM seam already injectable.** No seam introduction needed. The only
  change is exposing a `pub` mock (§5). This keeps the agent-core change net-small.
- **A3 — "use sandbox api/commands in `/sandbox`"** = drive the sandbox through
  its **wire protocol** (`eos-protocol` envelope; `api.v1.*` / `api.audit.*` daemon
  ops) via the host transport. Never reach into LayerStack/OCC/overlay internals.
- **A4 — Sandbox is real; LLM is the only thing ever mocked.**
- **A5 — Container reuse is the default**; a session keeps one warm dask
  container per (instance × node), resetting only the active overlay per test.
- **A6 — Audit reform spans both repos** but the cross-repo change is kept
  **minimal**: add the missing emitters + one correlation key + facet grouping.
  The "nice/human-readable" view lives in the `test_runner` collector (consumer
  side), not baked into the daemon.

---

## 3. System architecture

```
                          test_runner/  (new top-level Rust workspace)
   ┌─────────────────────────────────────────────────────────────────────────┐
   │  config        audit          agent-core(mock|api)        sandbox        │
   │  ──────        ─────          ───────────────────         ───────        │
   │  RunnerConfig  Collector      ScenarioRunner              SandboxPool    │
   │  (.env, yaml)  Timeline       MockedLlmClient             FastProvision  │
   │  multi-node    Renderer       Expectation/Report          MultiNode      │
   └───────┬───────────┬──────────────────┬───────────────────────┬──────────┘
           │           │                  │                        │
   reads   │   in-proc │ AuditSink   inject EventSource /   wire: eos-protocol
   config  │   capture │             llm_client            api.v1.* / api.audit.*
           ▼           ▼                  ▼                        ▼
   ┌──────────────┐  ┌──────────────────────────────┐   ┌─────────────────────┐
   │  eos-config  │  │          agent-core            │   │   eos-sandbox-host   │
   │  (extended)  │  │  eos-runtime  eos-engine       │   │   (host transport)   │
   └──────────────┘  │  eos-audit    eos-llm-client   │   └──────────┬──────────┘
                     │  eos-workflow eos-state        │              │ TCP/UDS
                     └───────────────┬────────────────┘              ▼
                                     │ api.v1.* tool calls   ┌─────────────────┐
                                     └──────────────────────▶│  eosd (Rust)    │
                                              api.audit.pull  │  /sandbox crates │
                                     ◀───────────────────────│  daemon + ring   │
                                                              └─────────────────┘
   Correlation key flowing right→ : engine tool_use_id  ──stamped on the wire──▶
   Audit flowing left←            : engine.* (in-proc)  +  sandbox.* (pull)  →  one timeline
```

The bridge is two-directional:
- **Drive** (→): config selects provider + sandbox; the agent-core module injects
  a mock/real LLM and runs the engine, whose tool calls hit the real sandbox over
  the `/sandbox` wire.
- **Observe** (←): the audit module captures agent-core events in-process and
  pulls sandbox events from the daemon ring, then **correlates** them on
  `tool_use_id` into one human-readable timeline.

---

## 4. Module: `config` (centralized configuration)

### 4.1 Current state (from `eos-config`)
- `CentralConfig { database, sandbox, providers, attempt }`, layered
  `defaults < ephemeralos.yaml < env < init`, `#[serde(deny_unknown_fields)]`.
- **Gaps**: (1) no api-key / base_url / active-provider — keys read inline via
  `std::env::var` and base_urls hardcoded in `eos-runtime::default_llm_client`;
  (2) no `runner`/multi-node section (dropped as `GC-eos-config-05`); (3) **no
  `.env` loading** on either side (deliberately removed in `loader.py`).

### 4.2 Design — extend `eos-config`, add a thin `test_runner` overlay

We add the three missing surfaces to `eos-config` (so production and tests share
one loader), and a per-run handle in `test_runner`.

```
eos-config (extended, in agent-core)
  CentralConfig
    ├ database   (unchanged)
    ├ sandbox    (+ runner subsection, see below)
    ├ providers  (+ per-provider credential sections)         ◀── NEW
    │    ├ retry            RetryConfig          (existing bridge → eos-llm-client)
    │    ├ active           ProviderKind         (Anthropic|OpenAi)   ◀── NEW
    │    ├ anthropic        ProviderClientConfig { base_url, model, api_key, auth_scheme }
    │    └ openai           ProviderClientConfig { base_url, model, api_key, auth_scheme }
    ├ attempt    AttemptConfig { max_concurrent_task_runs }   (existing)
    └ runner     RunnerConfig                                  ◀── NEW (re-absorbed)
         ├ sandbox_reuse_mode   {Fresh|Reuse|ForceFresh}
         ├ sandbox_quota        u32
         ├ audit_dir            PathBuf
         ├ run_label            String
         └ live_e2e             LiveE2eConfig
               ├ concurrent_sandbox_runners  u32   (multi-node size; plan = 3)
               ├ real_agent_max_duration_s   u64
               ├ heavy_enabled               bool
               └ capacity_enabled            bool
```

`ProviderClientConfig.api_key` holds an **`env:VAR` / `${VAR}` placeholder**, not
a raw secret — resolved lazily via the existing `eos-db::resolved_kwargs`
resolver into a `secrecy::SecretString`. This **preserves** the "CentralConfig
holds no secrets" invariant.

**`.env` loading** (the user's ask: "Python writes api_client config into `.env`"):
add a single `dotenvy::dotenv()` call at runtime startup **before**
`ConfigLoader::load()` and before any `std::env::var` read, so `.env`-written
keys hydrate the process env. Real exported env still overrides `.env` (dotenvy
default), preserving `env > yaml` precedence.

```
.env  (Python writes ANTHROPIC_API_KEY=... / OPENAI_API_KEY=...)
   │  dotenvy::dotenv()  (test_runner main / eos-runtime main, FIRST)
   ▼
process env  ──read by──▶  ConfigLoader (EOS__… + legacy) ──▶ CentralConfig
                           providers.anthropic.api_key = "env:ANTHROPIC_API_KEY"
                                       │ resolved_kwargs → SecretString
                                       ▼
                           AnthropicClient::new(base_url, Auth::ApiKey(secret), retry)
```

### 4.3 `test_runner::config` files

```
test_runner/crates/runner-config/src/
  lib.rs            // re-export
  runner_config.rs  // RunConfig (per-invocation handle) — wraps CentralConfig
  env_bootstrap.rs  // load_dotenv_then_central() → CentralConfig
  dotenv_writer.rs  // write_provider_keys_to_env(path, &[(k,v)]) — the Python-side .env writer (Rust mirror, used by tests/tools)
```

```rust
/// Per-run handle. Draws defaults from CentralConfig.runner; not a second config.
pub struct RunConfig {
    pub entry_prompt: String,
    pub instance_id: SweevoInstanceId,     // EOS_SWEEVO_INSTANCE
    pub fidelity: Fidelity,                // Mock | Live
    pub subject: Subject,                  // AgentExecution | Sandbox
    pub load: Load,                        // Single | Multi { nodes: u32 }
    pub audit_dir: PathBuf,
    pub run_label: String,
    pub max_duration_s: Option<u64>,       // wall-clock cap → early abort
    pub central: Arc<CentralConfig>,
}
```

### 4.4 Source-side change list (config)
- `eos-config`: add `providers.{active,anthropic,openai}`, add `runner` section,
  port Pydantic ranges into `validation.rs`, update the schema-parity test that
  currently drops `runner`. Reuse `eos-db::resolved_kwargs` for credential resolution.
- `eos-runtime`: call `dotenvy::dotenv()` at startup; make `default_llm_client`
  read `providers.<active>.{base_url, api_key, auth_scheme}` instead of
  env+hardcode (keep env var as a fallback for compatibility).

---

## 5. Module: `agent-core` (mock + api)

### 5.1 The seam (confirmed injectable)

```
run_query (eos-engine/query/loop_.rs)
   └─ source: Arc<dyn EventSource> = ctx.event_source        ← INJECT HERE
        ├─ ProviderEventSource (prod) ── wraps ── Arc<dyn LlmClient>  ← OR INJECT HERE
        │      └─ AnthropicClient / OpenAiClient  (built ONLY in default_llm_client)
        ├─ ScenarioEventSource (mock, engine-level)   ← scripted StreamEvents
        └─ MockedLlmClient    (mock, provider-level)  ← scripted LlmStreamEvents
```

Two injection levels, both already real:

| Level | Trait | Yields | Use |
|---|---|---|---|
| Engine | `EventSource::stream(&LlmRequest) -> EngineStream` | `StreamEvent` | Branching scenarios; cheapest |
| Provider | `LlmClient::stream_message(LlmRequest) -> LlmStream` | `LlmStreamEvent` | Exercises real encode/adapt path; "closest to live" |

Runtime hook: `AppState.event_source_factory: Option<Arc<dyn Fn(&AgentDefinition)
-> Arc<dyn EventSource>>>` and `AppStateBuilder::llm_client(Arc<dyn LlmClient>)`.
**Precedence footgun**: `event_source` wins over `llm_client` — the harness guards
against setting both.

### 5.2 `MockedLlmClient` (the rename the user asked for)

The user's "mocked llm client" = the **provider-level** `LlmClient` impl that
replays scripted `LlmStreamEvent`s, so the real `ProviderEventSource`
encode/adapt path runs end-to-end. The **engine-level** `ScenarioEventSource` is
the richer, branching variant for result-driven scenarios. Both share the
scripted-turn data model (direct port of the Python `Turn`/`ToolCall`).

```rust
/// Shared scripted-turn schema (port of Python event_source.py Turn/ToolCall).
pub struct ScriptedTurn { pub thinking: Option<String>, pub text: Option<String>, pub calls: Vec<ScriptedCall> }
pub struct ScriptedCall { pub name: String, pub input: JsonObject }

/// Provider-level mock — the "MockedLlmClient".
pub struct MockedLlmClient { script: Box<dyn TurnScript> }
impl LlmClient for MockedLlmClient {
    async fn stream_message(&self, req: LlmRequest) -> Result<LlmStream, ProviderError> {
        let turn = self.script.next_turn(trailing_tool_results(&req));  // observe prior results
        Ok(emit_llm_stream(turn))   // [ReasoningDelta?, TextDelta?, ToolUseDelta×N, AssistantMessageComplete]
    }
}

/// Engine-level mock — branching/result-driven scenarios.
pub struct ScenarioEventSource { script: Box<dyn TurnScript> }
impl EventSource for ScenarioEventSource { /* same shape, emits StreamEvent */ }

/// Static = ignore results; branching = stateful machine.
pub trait TurnScript: Send + Sync {
    fn next_turn(&self, prior: &[ToolResult]) -> Option<ScriptedTurn>;
}
impl TurnScript for Vec<ScriptedTurn> { /* the simple replay case */ }
```

**Load-bearing invariants** (from the loop):
1. Emit one `ToolUseDelta` per call **before** `AssistantMessageComplete`, with
   matching `ToolUseId`s — required for budget-count parity (`tool_calls_used`).
2. A turn must terminate each model turn with `AssistantMessageComplete` or the
   loop errors `provider stream ended without assistant completion`.
3. A turn containing a **terminal** tool must contain **only** that call
   (terminal-alone). A `debug_assert!` in the mock catches port mistakes early.
4. Script exhaustion → one text-only `AssistantMessageComplete` (reproduces a
   model that stopped calling tools → loop ends).
5. Leave `agent_name`/`agent_run_id` empty; the loop's `stamp_identity` fills them.

### 5.3 Source-side change (agent-core, **the only mock change**)
Promote `MockedLlmClient` + `ScenarioEventSource` from `#[cfg(test)]`-private to a
**`pub` `testsupport` feature** on `eos-llm-client` / `eos-engine` (mirroring
`eos-workflow/src/testsupport.rs`), so the external `test_runner` crate can
construct scripted clients without re-implementing the trait. **No loop change.**

### 5.4 Run-to-completion + early-terminate

```
start_request(state, prompt) ─▶ RequestEntryHandle {
    request_id, root_task_id, attempt_deps,
    root_agent_task: JoinHandle<()>,   state(AppState w/ shutdown: CancellationToken)
}
        │
        ├── observe:  AppState.event_source_factory / EventCallback + audit CapturingSink
        │             → harness watches StreamEvent / AuditEvent per turn
        │
        ├── full finish:   handle.join().await         (root agent submits submit_root_outcome)
        │
        └── PARTIAL (test condition met):
              tokio::select! {
                  _ = handle.join()        => Completed,
                  _ = condition_watcher    => { handle.shutdown(grace).await; Partial }
                  _ = sleep(max_duration)  => { handle.shutdown(grace).await; AbortedByTimeout }
              }
              // shutdown() cancels the token, parent-exits the supervisor,
              // awaits root within grace, aborts the JoinHandle on timeout.
```

`condition_watcher` is fed by the **audit** module: when a target event appears
(e.g. a specific tool completed, a sandbox conflict observed, N tool calls
reached), it resolves and the run is terminated early with a `Partial` outcome.
This is the Rust analogue of the Python `LifecycleHooks.on_event` + `aborted_by_timeout`.

### 5.5 Live api-client smoke (trivial, per the user)

One test per provider. Not a matrix.

```
api_client_smoke(provider):
  1. load .env → CentralConfig.providers.<provider>
  2. build real AnthropicClient/OpenAiClient
  3. send a 1-turn LlmRequest: system reminder + a single tool definition
       "respond by calling tool `echo{message}`"
  4. assert the stream yields a ToolUseDelta with name=="echo" and well-formed input,
     terminated by AssistantMessageComplete{stop_reason: ToolUse}
  5. assert the system reminder was honored (tool called, not free text)
```
Gated by presence of the key (`#[ignore]` + env preflight) so it never runs in
offline CI.

### 5.6 `agent-core` module files

```
test_runner/crates/runner-agent/src/
  lib.rs
  mock/
    script.rs          // ScriptedTurn, ScriptedCall, TurnScript, emit_* helpers
    mocked_llm.rs       // MockedLlmClient (provider-level)
    scenario_source.rs  // ScenarioEventSource (engine-level, branching)
    advisor.rs          // advisor sub-agent script (gated-terminal precondition)
  run/
    request_run.rs      // start_request wrapper + RequestEntryHandle driver
    terminate.rs        // condition_watcher, early-abort (select! on shutdown/timeout)
  api/
    api_smoke.rs        // trivial live anthropic/openai tool-call + system-reminder test
```

---

## 6. Module: `audit` (the reform — unified, human-readable, correlated)

> The user: "reform the audit module in sandbox and agent-core so they can be
> collected in a nicer way, more human readable, and reflect key points,
> semantics, performance, resource usage, and correctness."

### 6.1 Current state (the problem)
- **agent-core `eos-audit`**: clean envelope (`AuditEvent{schema_version, source,
  event_type, node, payload, correlation_id, ts}`), `AuditSink` seam, sync
  `AuditEventBus` (used nowhere). **Only `plugin.*` events are actually emitted**:
  `engine.tool.*` projection (`audit_events_from_stream_event`) has **zero
  callers** (dead); no `sandbox.*`/`workflow.*`/lifecycle emitters. `node.sandbox_id`
  never set, `correlation_id` always `None` → **no cross-source correlation**.
  `ts` from an injectable Clock → not a reliable total order.
- **sandbox `eos-protocol::audit` + `eos-daemon`**: rich, pull-based ring
  (`AuditBuffer`, `api.audit.pull`, monotonic `seq` + `boot_epoch_id`), typed
  `*Section` structs (tool_call/occ/layer_stack/overlay/background/plugin/…). BUT
  the daemon stamps `tool_call.tool_use_id = host-minted uuid4`, while agent-core
  keys on the **LLM `tu-*` id** → **broken per-call join**; the rich caller
  breadcrumb the daemon receives is **dropped**.

### 6.2 The reform — a four-facet contract + one correlation key

Introduce a small **shared contract** (new crate `eos-audit-contract`, or a
`contract` module in `eos-audit`) that both repos map into. Each module keeps its
native emission; the contract is the *lingua franca* the collector consumes.

```rust
/// The one envelope both agent-core and sandbox normalize into.
pub struct TraceEvent {
    pub seq: u64,                 // monotonic, total order (NOT ts — ts unreliable under TestClock)
    pub ts: UtcDateTime,
    pub source: TraceSource,      // Engine | Workflow | Sandbox | Plugin | Runner
    pub kind: String,             // "engine.tool.completed", "sandbox.occ.publish", …
    pub node: CorrelationNode,    // the join keys
    pub facets: Facets,           // the four human dimensions
    pub raw: Option<JsonObject>,  // forensic, gated by EOS_AUDIT_FORENSIC_RAW_ENABLED
}

pub struct CorrelationNode {
    pub request_id: Option<RequestId>, pub workflow_id: Option<WorkflowId>,
    pub iteration_id: Option<IterationId>, pub attempt_id: Option<AttemptId>,
    pub task_id: Option<TaskId>, pub agent_run_id: Option<AgentRunId>,
    pub agent_name: Option<String>, pub agent_role: Option<AgentRole>,
    pub tool_use_id: Option<ToolUseId>,   // ◀── THE per-call join key (LLM id, now on BOTH sides)
    pub tool_name: Option<String>, pub sandbox_id: Option<SandboxId>,
    pub ordinals: SeqOrdinals,            // workflow_seq/iteration_seq/attempt_seq (port of NodeId)
}

pub struct Facets {
    pub semantics:  Option<Semantics>,   // WHAT happened (human sentence + structured op)
    pub performance: Option<Performance>,// durations, phase timings
    pub resource:   Option<Resource>,    // bytes in/out, peak_resident, disk, changed_paths
    pub correctness: Option<Correctness>,// status ok|error, error_kind, conflict, is_terminal
}

pub struct Semantics  { pub headline: String, pub op: String, pub detail: JsonObject }
pub struct Performance{ pub duration_ms: Option<f64>, pub phase_ms: BTreeMap<String,f64> }
pub struct Resource   { pub bytes_in: Option<u64>, pub bytes_out: Option<u64>,
                        pub peak_resident_bytes: Option<u64>, pub changed_path_count: Option<u32> }
pub struct Correctness{ pub status: Status, pub error_kind: Option<String>,
                        pub conflict: Option<ConflictInfo>, pub is_terminal: bool }
```

**Why facets and not free JSON**: the four dimensions are exactly the user's ask
and map cleanly onto data **both sides already produce** — the daemon already has
`*_ms` timings (→ performance), `bytes_in/out`/`peak_resident_bytes` (→ resource),
`status`/`conflict_kind` (→ correctness), op name (→ semantics). The reform is
**organizing existing fields**, not inventing telemetry.

#### The correlation fix (load-bearing, smallest possible change)
```
engine dispatch (eos-engine/tool_call/dispatch.rs)
   metadata.tool_use_id = Some(LLM "tu-…")          ← already set
        │  COPY (the one missing wire)
        ▼
ExecutionMetadata.sandbox_invocation_id = Some(tu-…)
        ▼  request_base (eos-tools/model_tools/sandbox.rs)
SandboxRequestBase.invocation_id = tu-…
        ▼  daemon_client: reuse present id, DO NOT mint uuid4
eosd ToolCallSection.tool_use_id = tu-…   ==   engine.tool node.tool_use_id
        ▼
collector joins both streams on tool_use_id  → per-call correlation, zero in-proc coupling
```

### 6.3 Source-side reform change list

**agent-core (`eos-audit` + emitters):**
1. **Wire the dead engine path**: call `audit_events_from_stream_event` in the
   query loop (or have the engine hold the `Arc<dyn AuditSink>` and publish per
   `StreamEvent`), so `engine.tool.started/completed/failed` actually fire.
2. Add **lifecycle emitters** in `eos-workflow` (it already holds an unused
   `audit_sink`): `request.started/completed`, `workflow.started/completed`,
   `iteration.started/completed`, `attempt.started/passed/failed`.
3. Add a **monotonic `seq`** (atomic) at the sink/bus boundary for total order.
4. Map `AuditEvent` → `TraceEvent` facets (a `From`/`to_trace()` in the contract).
5. Populate `node.tool_use_id` consistently; set `node.sandbox_id` on
   sandbox-bound tool events.

**sandbox (`eos-protocol::audit` + `eos-daemon`):**
6. **Stamp the caller `tool_use_id`** (the LLM id from the wire) into every
   emitted `*Section` (today `tool_use_id` = minted uuid). Add an explicit
   caller-supplied field distinct from the daemon invocation_id.
7. **Echo the caller breadcrumb** (`agent_run_id`, `workflow_id`, `attempt_id`)
   the daemon already receives into each section (a shared `node` sub-object) so a
   pulled event self-describes its place in the run — restores what `node_id.py`
   provided.
8. Provide `Section -> TraceEvent` facet mapping (timings→performance,
   bytes/peak→resource, status/conflict→correctness, op→semantics).
9. Add an **audit-pull schema** entry to `sandbox/CONTRACT.md` as a coordinated
   cross-repo surface (it is now a depended-upon contract).

> Guardrail: we do **not** invent a new universal telemetry bus, do **not** add
> push from daemon, do **not** force both sides onto one struct. Each side keeps
> its native event type; the contract is a thin mapping + the correlation key.

### 6.4 `test_runner::audit` (the collector — consumer side, where "nice" lives)

```
test_runner/crates/runner-audit/src/
  lib.rs
  capturing_sink.rs   // impl AuditSink: in-proc, lossless Vec<AuditEvent> (agent-core side)
  daemon_puller.rs    // port of DaemonAuditPuller: api.audit.pull cursor/cadence/epoch
  normalize.rs        // AuditEvent→TraceEvent  &  eos-protocol::Section→TraceEvent
  timeline.rs         // merge both streams, total-order by seq, group by node
  correlate.rs        // join engine↔sandbox on tool_use_id (fallback: agent_run_id)
  render.rs           // human-readable tree + summary  (the "nicer way" deliverable)
  jsonl_sink.rs       // RotatingJsonlSink port (canonical sandbox_events.jsonl artifact)
  query.rs            // assertion helpers: by kind, by node, by facet (for Expectation)
```

```
Collection pipeline:
  ┌─ agent-core run ─┐                       ┌─ sandbox daemon ─┐
  │ CapturingSink    │  AuditEvent           │ AuditBuffer ring │
  │ (injected via    │ ───────────────┐      │ api.audit.pull   │
  │  AppStateBuilder │                │      └────────┬─────────┘
  │  .audit(sink))   │                │   Section     │ (cursor, seq, boot_epoch_id)
  └──────────────────┘                ▼               ▼
                              normalize::to_trace()  normalize::to_trace()
                                        └──────┬──────┘
                                               ▼
                                     Timeline (Vec<TraceEvent>, sorted by seq)
                                               │  correlate on tool_use_id
                                               ▼
                          render::tree()  +  render::summary()  +  jsonl artifact
```

### 6.5 Human-readable output (sample)

```
REQUEST req-9f3a  "fix dask groupby regression"            [PASS]  42.1s  1 workflow
└─ workflow wf-21  (delegated)                                       3 iters, 2 attempts
   └─ iteration 2  attempt 2                                         ✔ reducer gate
      ├─ task t-7 (executor)  agent_run ar-55
      │  ├─ engine.tool.completed  write_file  src/groupby.py
      │  │     semantics : wrote file (overlay capture)
      │  │     perf      : 12.4ms
      │  │     resource  : +1 path, 3.1 KiB out
      │  │     correct   : ok
      │  │     └─sandbox.occ.publish   tool_use=tu-7f… (JOINED)
      │  │           perf: prepare 1.1 / apply 2.0 / commit 0.8 / publish 0.4 ms
      │  │           resource: 1 changed path
      │  │           correct: ok (no conflict)
      │  ├─ engine.tool.completed  exec_command  "pytest -q"
      │  │     correct: error (exit 1)   perf: 8.7s   resource: 240 KiB out
      │  └─ engine.tool.completed  submit_generator_outcome   correct: ok (terminal)
      └─ reducer  submit_reducer_outcome   correct: ok (terminal)

SUMMARY  tools: 31 (write 8 / read 6 / exec 4 / search 9 / terminal 4)
         sandbox: occ.publish 8, conflict 0, squash 1, lease ok
         perf: agent 38.0s, sandbox 4.1s   tokens: 18.2k in / 2.1k out
         correctness: 0 unexpected errors, terminal submitted, reducer gate PASS
         dropped audit events: 0 (lossless in-proc) ; ring lost_before_seq: 0
```

`render::summary()` reflects exactly the four facets the user named, per run.

---

## 7. Module: `sandbox` (real, fast, reusable, multi-node)

### 7.1 Drive via the `/sandbox` wire (A3)
The module orchestrates the sandbox **only** through `eos-sandbox-host`
(provision/transport) speaking the `/sandbox` `eos-protocol` envelope and daemon
ops. No LayerStack/OCC/overlay internals.

```
SandboxPool ──▶ eos-sandbox-host
  provision   : RequestSandboxProvisioner::prepare_for_run → RequestSandboxBinding{sandbox_id}
  lifecycle   : SandboxLifecycle::{create,start,ensure_running,set_labels,stop,delete}
  transport   : DaemonClient (impl SandboxTransport) — TCP-first/UDS-fallback
  tool ops    : tool_api::{read_file,write_file,edit_file,exec_command,glob,grep,…}  (api.v1.*)
  audit pull  : api.audit.pull / api.audit.snapshot
  one-time    : runtime_artifact::ensure_eosd_uploaded  (marker-skip /eos/daemon/.eosd-sha256)
```

### 7.2 Fast reusable setup — what is one-time vs per-test

```
                       FIRST container in a session            EVERY reused test
  docker pull+tag image (snapshot)   ████ one-time              ░ skip (cached tag)
  create container + map daemon port ████ one-time              ░ skip (resume start)
  ensure_eosd_uploaded (push eosd)   ████ one-time              ░ skip (.eosd-sha256 marker)
  ensure_daemon_current (spawn)      ████ one-time              ░ skip (pid+socket liveness)
  api.build_workspace_base{reset}    ████ first use             ████ per-test (active overlay only)
  git reset/clean/checkout base      ████ first use             ████ per-test
  base LayerStack layer (B…-base)    ████ one-time              ░ reuse base manifest
```

**Ultrafast reuse rule**: skip image/eosd/daemon/snapshot/base-layer; pay only
`api.build_workspace_base{reset}` + git checkout for the **active overlay** per
test. First-use of a fresh container skips the per-test reset (Python `workspace`
fixture `first_use` guard). This keeps reuse correct (no cross-test contamination)
while skipping everything cacheable.

### 7.3 Configurable multi-node

```
  RunnerConfig.runner.live_e2e.concurrent_sandbox_runners = N   (semaphore cap)
        │
        ▼
  SandboxPool::provision_n(N):
        shared:  ONE image/snapshot pull+tag, ONE host artifact_dir (sandbox/dist)
        per-node: distinct name+label (instance × node_index), own SandboxId
        ┌───────────┐ ┌───────────┐        ┌───────────┐
        │ node 0    │ │ node 1    │  ...   │ node N-1  │   each: own container,
        │ SandboxId │ │ SandboxId │        │ SandboxId │         own eosd, own TCP port
        └─────┬─────┘ └─────┬─────┘        └─────┬─────┘
              └─ ProviderRegistry.bindings keyed by SandboxId (already per-id)
                 DaemonClient.tcp_cache keyed by SandboxId (already per-id)
  teardown: release() deletes/disposes all N (or no-op all under Attach/Reuse)
```
Multi-node needs **no new isolation primitive** — `ProviderRegistry.bindings` and
`DaemonClient.tcp_cache` are already per-`SandboxId` maps; we add an N-binding
provisioner + a concurrency semaphore + per-node naming.

### 7.4 Source-side change list (sandbox)
- Add an **N-sandbox provisioner** (or call `prepare_for_run` N times) returning
  `Vec<RequestSandboxBinding>`; add a reuse/attach mode with no-op release.
- Thread the **real host artifact dir** (`sandbox/dist`) through the composition
  root instead of `DEFAULT_LAYER_STACK_ROOT` (a latent bug flagged by exploration;
  fix only if it blocks the harness — otherwise leave to parallel agents).
- **Plugin gap**: add `plugin.generic.*` variants to the typed `DaemonOp` enum +
  `tool_api` plugin helpers, so plugin tests don't fall back to string-prefix
  routing. (Needed only for the plugin sandbox tier.)

### 7.5 `sandbox` module files

```
test_runner/crates/runner-sandbox/src/
  lib.rs
  pool.rs           // SandboxPool: provision/provision_n, reuse modes, teardown
  fast_setup.rs     // one-time-vs-per-test setup sequence (build_workspace_base, git reset)
  instance.rs       // SweevoInstance resolve (EOS_SWEEVO_INSTANCE → image/base_commit)
  multinode.rs      // semaphore + per-node naming + Vec<binding> lifecycle
  ops.rs            // thin typed facade over eos-sandbox-api tool_api (+ plugin if enabled)
```

---

## 8. Test taxonomy (preserve rigor, drop the bad layout)

### 8.1 Clean axes (replace scattered pytest markers)
```
  fidelity ∈ {Mock, Live}          Mock = scripted EventSource through REAL loop
  subject  ∈ {AgentExecution, Sandbox}   (Live = real LLM / real provider)
  load     ∈ {Single, Multi{nodes}}      (replaces smoke-vs-full + capacity mega-scenario)

  Mock × AgentExecution : DAG/retry/deferral/planner-validation/root-request CORRECTNESS
  Mock × Sandbox        : connection/stability/load via scripted high-volume tool calls
  Live × Sandbox        : real daemon parity (the bench-script flow, asserted)
  Live × AgentExecution : real-agent SWE-EVO F2P/P2P scoring (trivial api smoke is its floor)
```

### 8.2 Ported architecture (the good parts)
- **`run_pipeline` 5-seam spine** → one Rust entrypoint; mode = (runner_factory,
  bootstrap, lifecycle, sandbox, run_label).
- **`LifecycleHooks`** trait (`before_run/on_event/after_run/on_aborted`) — the
  per-mode observation + early-terminate seam; `Noop` default.
- **Dual report**: narrow `PipelineReport` (core) + mode views (`RunReport`,
  `RealAgentRunReport`) rebuilt from typed audit events.
- **Scenario-as-data + real loop**: `Scenario` returns `ScriptedTurn`s; a Rust
  `ScenarioRunner` injects them into the **real** `eos-runtime` loop (real tool
  dispatch, terminal-alone, budget, real ContextEngine XML envelopes).
- **`_graph_summary` real-state walk**: assert Workflow→Iteration→Attempt→Task
  from persisted `eos-state` rows, never from scenario self-reporting.
- **Declarative `Expectation`** (port of `FocusedScenarioCase`): one struct →
  `assert_report(report, expectation)`.

```rust
pub struct Expectation {
    pub request_status: RequestStatus,
    pub role_task_floors: BTreeMap<AgentRole, u32>,
    pub role_task_absent: BTreeSet<AgentRole>,
    pub required_event_kinds: Vec<String>,
    pub attempt_count: Option<u32>, pub iteration_count: Option<u32>,
    pub deferred_attempt_bounds: Option<(u32,u32)>,
    pub tool_count_floors: BTreeMap<String, u32>,   // write>=30, read>=20, …
    pub required_sandbox_events: Vec<String>,       // occ.publish, squash, conflict, …
}
```

### 8.3 Scenario catalog to port (small, orthogonal, high-signal)
- **Mock×AgentExecution**: `initial_workflow`, `dependency_dag_{serial,parallel,
  diamond,mixed}`, `dependency_blocked_descendants`, `attempt_retry_{planner,
  generator,reducer}_failure`, `iterative_deferral`, `nested_workflow(_failure)`,
  `attempt_budget_exhausted`, `generator_failure_quiescence`, + 6
  `planner_validation` negatives (cycle/dup-id/unknown-dep/unknown-agent/
  empty-tasks/blank-deferred-goal).
- **Mock×Sandbox**: OCC conflict round-trip, overlay capture/publish, auto-squash,
  lease-non-leak, read-only-plugin-no-publish vs write-plugin-publish, finite
  command vs command-session lifecycle, isolated-workspace invariants
  (enter-rejects-active-bg, exit-drains+releases, no-OCC-publish, audit-only
  writes) — as a **compact table-driven suite**, not file-per-case.
- **Live×Sandbox**: the bench-script setup flow, asserted (parity).
- **Live×AgentExecution**: api-client smoke (§5.5) + SWE-EVO real-agent.

### 8.4 Explicitly dropped (do not port)
`full_stack_adversarial`, `full_system_capacity_matrix`, `pack_catalog`
(dead pointers), `_metrics.py` percentile aggregator (→ separate bench lane),
the ~120-file `isolated_workspace` explosion (→ table-driven invariant set),
smoke-vs-full duplication (→ `load` axis parameter).

---

## 9. Resulting workspace layout

```
test_runner/                          (NEW top-level Rust workspace)
  Cargo.toml                          // workspace; path-deps → ../agent-core, ../sandbox
  rust-toolchain.toml
  crates/
    runner-config/                    // §4  centralized config + .env
    runner-audit/                     // §6  collector, timeline, renderer
    runner-agent/                     // §5  MockedLlmClient, scenario runner, api smoke
    runner-sandbox/                   // §7  SandboxPool, fast setup, multi-node
    runner-core/                      // run_pipeline spine, LifecycleHooks, reports, Expectation
    runner-scenarios/                 // §8  scenario data (mock turn scripts) + catalog
  tests/
    mock_agent/                       // Mock×AgentExecution correctness
    mock_sandbox/                     // Mock×Sandbox stability/load
    live_sandbox/                     // Live×Sandbox parity (gated)
    live_agent/                       // Live×AgentExecution: api smoke + sweevo (gated)
  benches/                            // perf lane (moved out of the test taxonomy)
```

Cross-repo source changes live in their home crates (not in `test_runner/`):
`eos-config`, `eos-runtime`, `eos-engine`, `eos-llm-client`, `eos-audit`,
`eos-workflow` (agent-core); `eos-protocol`, `eos-daemon` (sandbox).

---

## 10. SOLID / SRP mapping (and simplicity guardrails)

| Principle | Where |
|---|---|
| **SRP** | Each crate owns one job: config / audit-collection / agent-drive / sandbox-drive / spine / scenario-data. Audit *emission* stays in source modules; audit *presentation* stays in the collector. |
| **Open/Closed** | New scenarios = new data (`ScriptedTurn`s), no engine change. New audit source = new `to_trace()` mapping, no collector change. |
| **Liskov** | `MockedLlmClient`/`ScenarioEventSource` are drop-in `LlmClient`/`EventSource`; `SandboxPool` honors the same `eos-sandbox-host` contract as production. |
| **Interface Segregation** | Four narrow facets instead of one fat payload; `TurnScript` is a single method; `SandboxTransport` is one `call`. |
| **Dependency Inversion** | test_runner depends on traits (`AuditSink`, `EventSource`, `SandboxTransport`), not concretes; injection via existing factories. |

**Anti-over-engineering guardrails** (per project `CLAUDE.md` — over-engineering is
a defect equal to under-coverage):
- No new universal telemetry bus, no daemon push channel, no second config system.
- The 4-facet model maps **existing** fields; it adds no new measurement code.
- The api-client test stays trivial.
- Multi-node reuses per-`SandboxId` maps; no new isolation machinery.
- Scenarios are data; no per-scenario classes/inheritance trees.

---

## 11. Progress checker

> Phases are ordered so each is independently verifiable. `[ ]` = todo.
> Source-side reforms (agent-core/sandbox) are called out because they are the
> bridge prerequisites, not test_runner-internal work.

### Phase 0 — Workspace skeleton
- [ ] `test_runner/` workspace + 6 crates compile with path-deps to agent-core & sandbox
- [ ] `runner-core` `run_pipeline` spine + `LifecycleHooks` + `PipelineReport` stubs
- [ ] verify: `cargo build -p runner-core` green

### Phase 1 — Config (centralized)
- [ ] `eos-config`: add `providers.{active,anthropic,openai}` + `runner` section + validation
- [ ] `eos-runtime`: `dotenvy::dotenv()` startup; `default_llm_client` reads provider config
- [ ] `runner-config`: `RunConfig`, `load_dotenv_then_central`, `.env` writer
- [ ] verify: a test writes a key to `.env`, loader resolves `SecretString`, client builds

### Phase 2 — agent-core mock seam
- [ ] Promote `MockedLlmClient` + `ScenarioEventSource` to `pub testsupport` feature (no loop change)
- [ ] `runner-agent`: `ScriptedTurn`/`TurnScript`, emit helpers, advisor script
- [ ] `request_run` + `terminate` (select! on shutdown/timeout/condition)
- [ ] verify: a 1-tool mock script runs the real loop to `submit_root_outcome`; budget parity holds
- [ ] verify: a condition-watcher terminates a run early → `Partial`

### Phase 3 — Audit reform (source side)
- [ ] `eos-audit`/`eos-engine`: **wire the dead `engine.tool.*` path** (publish on the sink)
- [ ] `eos-workflow`: emit request/workflow/iteration/attempt lifecycle events
- [ ] add monotonic `seq`; populate `node.tool_use_id`/`sandbox_id`
- [ ] **correlation fix**: copy engine `tool_use_id` → `sandbox_invocation_id`; daemon stops minting uuid
- [ ] `eos-protocol::audit`/`eos-daemon`: stamp caller `tool_use_id` + breadcrumb into every section
- [ ] `eos-audit-contract`: `TraceEvent`/`CorrelationNode`/`Facets` + `to_trace()` both sides
- [ ] `sandbox/CONTRACT.md`: add audit-pull schema as a coordinated surface
- [ ] verify: a real mock run produces `engine.tool.*` events (not just `plugin.*`)
- [ ] verify: an engine tool event and its sandbox `occ.publish` share `tool_use_id`

### Phase 4 — Audit collector (consumer side)
- [ ] `runner-audit`: `CapturingSink`, `DaemonAuditPuller`, `normalize`, `timeline`, `correlate`
- [ ] `render::tree` + `render::summary` (the human-readable deliverable)
- [ ] `RotatingJsonlSink` artifact port
- [ ] verify: merged timeline renders the §6.5 sample shape; summary shows the 4 facets
- [ ] verify: lossless in-proc capture (0 dropped) for an assertion run

### Phase 5 — Sandbox (real, fast, multi-node)
- [ ] `runner-sandbox`: `SandboxPool` provision/reuse over `eos-sandbox-host`
- [ ] fast setup: one-time vs per-test (marker-skip eosd, daemon liveness, active-overlay reset)
- [ ] `provision_n` + semaphore + per-node naming; reuse/attach no-op release
- [ ] (if plugin tier) add `plugin.generic.*` to `DaemonOp` + `tool_api`
- [ ] verify: warm container reuse < target setup time; no cross-test contamination
- [ ] verify: N=3 lanes run without quota overrun or lease leak

### Phase 6 — Test taxonomy + scenarios
- [ ] `Expectation` + `assert_report`; `_graph_summary` real-state walk on `eos-state`
- [ ] Port Mock×AgentExecution correctness scenarios (§8.3) as turn-script data
- [ ] Port Mock×Sandbox invariants as a table-driven suite
- [ ] Live×Sandbox parity test (bench-flow); Live×AgentExecution api smoke + sweevo (gated)
- [ ] verify: each scenario asserts via `Expectation`; graph shape from store, not self-report

### Phase 7 — Cutover
- [ ] Architecture page under `docs/architecture/` for `test_runner`
- [ ] Retire/redirect the Python `backend/src/test_runner` once parity confirmed
- [ ] verify: `EOS_SANDBOX_RUNTIME=rust` end-to-end; no Python sandbox internals imported

---

## 12. Open questions (for review)
1. `eos-audit-contract` as a **new shared crate** vs a `contract` module inside
   `eos-audit` that `sandbox` also depends on — which crosses the repo boundary
   more cleanly? (Leaning: small new crate both workspaces path-dep.)
2. Should the correlation key be `tool_use_id` reused as `invocation_id`, or a
   **separate** explicit `caller_tool_use_id` section field (safer vs daemon
   cancel/mint paths)? (Leaning: separate explicit field.)
3. Is the `audit` seq best at the **sink boundary** (per-process) or assigned by
   the collector on ingest (per-run total order)? (Leaning: collector-assigned
   ingest order for cross-source merge; keep daemon ring `seq` for drop detection.)
```
