# SPEC: Rust `test_runner` — bridging `sandbox` and `agent-core`

Status: **draft v2 (post adversarial review; code-verified)**
Date: 2026-06-03
Owner doc: this file (`docs/plans/test_runner_rust_SPEC.md`)
Supersedes (for the harness tier): `docs/plans/test_runner_migration_PLAN.md`
(that plan renamed the **Python** `task_center_runner -> test_runner` and kept the
harness + host/API boundary in Python; this spec moves the harness itself to
**Rust**, now that `sandbox/` and `agent-core/` are migrated).

> v2 incorporates a multi-agent adversarial review that verified every
> load-bearing claim against the live code. Corrections from review are marked
> **[rev]**. The net effect is **smaller and more correct**: one crate (not six),
> consumer-side audit normalization (no shared contract crate), one mock surface,
> a one-direction correlation fix, and no changes to production config or
> `eos-runtime::main`.

---

## 0. TL;DR

Build **one new top-level Rust crate, `test_runner/`** (peer to `sandbox/` and
`agent-core/`) that drives a **real** Rust sandbox and the **real** Rust
agent-core engine to test agent execution end-to-end. Modules:

| Module | Job | Primary upstream surface |
|---|---|---|
| `config` | api-client creds via `.env`; sandbox + multi-node sizing in a per-run handle | `eos-config` (+ `providers.active` only) |
| `audit` | **Unified, human-readable, correlated** trace — **bridged consumer-side** | `eos-audit::AuditSink` (in-proc) + `api.audit.pull` (sandbox ring) |
| `agent-core` (`src/agent_core/`) | one `MockedLlmClient` injected into the real loop; run→completion/partial; trivial live api smoke | `EventSource` seam; `eos-runtime::start_request` |
| `sandbox` | fast, reusable dask container; single + multi-node; **never mocked** | `eos-sandbox-host` + `/sandbox` wire protocol |

**The two things the user explicitly asked, answered up front:**

1. **"How do I design an audit interface where each module handles its own, then
   bridge them?"** → **Each module keeps its own native audit** (agent-core
   `eos-audit::AuditEvent`; sandbox `eos-protocol::audit` `*Section`). They are
   bridged **only in the `test_runner` collector**, via consumer-side
   normalization into one `TraceEvent` with four facets. **No shared cross-repo
   contract.** The only cross-repo coupling is **one correlation key** (the engine
   `tool_use_id`, which the daemon already echoes). This mirrors the proven Python
   `daemon_event_normalizer.py` + `performance_report.py` shape. **[rev: was a
   shared `eos-audit-contract` crate — deleted as over-engineering.]**
2. **"Reform the audits to be collected nicely, human-readable, reflecting
   semantics / performance / resource usage / correctness."** → A **four-facet
   model** (`Semantics / Performance / Resource / Correctness`) + a tree/summary
   **renderer** in the collector (§7). The four facets map 1:1 to the user's ask
   and to fields both sides already emit (timings→perf, bytes/peak→resource,
   status/conflict→correctness, op→semantics).

The hardest fact — **is the LLM client injectable?** — is **YES, no new seam**: the
loop consumes `Arc<dyn EventSource>`; concrete clients are built only in
`eos-runtime::default_llm_client`.

---

## 1. Goals / Non-Goals

### Goals
1. Run `user request -> root Task -> root agent -> optional delegate_workflow ->
   submit_root_outcome` under test, with the ability to **terminate early**
   (partial result) when a test condition is met.
2. **Mock LLM** tier: scripted thinking/text/tool-call turns injected into the
   **real** engine loop. Sandbox is **never** mocked.
3. **Live api-client** tier: a *trivial* smoke proving `anthropic.rs` and
   `openai.rs` produce well-shaped tool calls + honor the system reminder.
4. **Sandbox** tier: fast reusable dask container; configurable multi-node.
5. **Unified audit**: one correlated, human-readable timeline across both sides.
6. **Centralized config**: api-client (`.env`), sandbox, run params.
7. Preserve the Python harness's test **rigor** (difficulty / complexity /
   load-bearing) while discarding its bad layout.

### Non-Goals (`CLAUDE.md` simplicity rules — over-engineering is a defect equal to under-coverage)
- No new orchestration layer; no peer-to-peer agent comms; no fake agent loop.
- No exhaustive provider matrix for the api-client test (intentionally trivial).
- No port of the over-engineered Python scenarios as-is (`full_stack_adversarial`,
  `full_system_capacity_matrix`, `pack_catalog`, the ~120-file
  `isolated_workspace` explosion). Port **invariant categories**, not file count.
- No Daytona/Minimax client wiring.
- **[rev]** No shared audit-contract crate; no second mock surface; no changes to
  production `CentralConfig` or `eos-runtime::main`.

---

## 2. Key decisions & assumptions

- **A1 — One crate `test_runner/`** (a top-level peer to `sandbox/`, `agent-core/`)
  with internal modules, mirroring the Python package
  (`backend/src/test_runner/{core,audit,agent,scenarios}`). It path-depends on
  agent-core crates (`eos-runtime`, `eos-engine`, `eos-audit`, `eos-sandbox-host`,
  `eos-sandbox-api`, `eos-config`, `eos-workflow`, `eos-state`) and one sandbox
  crate (`eos-protocol`, for typed audit `*Section` deserialization). Direction is
  always `test_runner -> {agent-core, sandbox}`. **[rev: was six crates — premature
  granularity for an internal harness with no external consumer.]**
- **A2 — LLM seam already injectable.** No seam introduction. One mock surface
  (`MockedLlmClient: EventSource`) replaces the client in the loop.
- **A3 — "use sandbox api/commands in `/sandbox`"** = drive the sandbox through its
  **wire protocol** (`eos-protocol` envelope; `api.v1.*` / `api.audit.*` daemon
  ops) via the `eos-sandbox-host` transport. Never reach into LayerStack / OCC /
  overlay internals.
- **A4 — Sandbox is real; the LLM is the only thing ever mocked.**
- **A5 — Container reuse is the default.** A session keeps one warm dask container
  per (instance × node). **[rev]** Per-test reset is the **real** SWE-EVO reset
  (git reset/clean/checkout + `build_workspace_base{reset}`), not overlay-only — see §8.2.
- **A6 — Audit bridge is consumer-side.** Each module keeps native emission; the
  collector normalizes. The only cross-repo change is the correlation key and a
  daemon-side emission *widening* (not a wire change).

---

## 3. Source-side prerequisites & dependencies

> **[rev]** The review found that some bridge work is **agent-core/sandbox feature
> work**, not test-harness work. This section separates the two so the scope
> expansion is explicit, not buried. **Decision needed from the user** on the
> out-of-scope items (they gate specific scenario tiers, not the baseline).

### 3a. Test-enabling bridge changes — IN SCOPE (small, harness-prerequisite)
| # | Change | Where | Why |
|---|---|---|---|
| P1 | Stamp engine `tool_use_id` onto `sandbox_invocation_id` **upstream**; verify every mint/fallback site honors a present id | `eos-engine::tool_call::dispatch` (`metadata_for_call`), `eos-tools::model_tools::sandbox` (drop the `new_v4` fallback when present), `eos-sandbox-host::daemon_client` (`new_invocation_id` — reuse present id) | Per-call audit join. The daemon **already echoes** the wire id as `ToolCallSection.tool_use_id` (`emit_tool_call_event`); the gap is upstream that the id is never populated. **[rev: not a daemon change.]** |
| P2 | `pub testsupport` feature exposing `ScriptedTurn`/`TurnScript`/`MockedLlmClient` (an `EventSource`) | `eos-engine` (model + emit helpers); reference impls `ScriptedSource`/`MockLlmClient` are `#[cfg(test)]` today | Let the external harness build scripted runs without re-implementing the trait (mirrors `eos-workflow/src/testsupport.rs`). **No loop change.** |
| P3 | Add `AppStateBuilder::advisor(Arc<dyn AdvisorPort>)` setter + a `pub testsupport` **auto-approve** `AdvisorPort` stub (**bootstrap scaffold only**) | `eos-runtime::app_state` (setter), `eos-engine`/`eos-tools` (`AdvisorPort` is `Sealed`, stub ships in-tree) | Lets **non-advisor** tiers run gated terminals before the real advisor helper runner (D1) lands. **[rev]** It **bypasses** the real `ask_advisor`→sub-agent→`submit_advisor_feedback` path, so advisor-branch scenarios and the §14 bundle use the real sub-agent path (D1), **not** this stub. The advisor is a mocked **sub-agent**, not a verdict service (§6.3). |
| P4 | Wire the **dead** `engine.tool.*` audit path to publish on the injected sink; enrich `AuditNode` from `QueryContext`/`ExecutionMetadata` (request/task/attempt ids) | `eos-engine::query::loop_` + `eos-engine::audit::stream` (`audit_events_from_stream_event` has **zero** callers) | Without this the collector sees only `plugin.*`. Enrichment lets tool rows self-group into the §7 tree without an `eos-state` join. |
| P5 | Daemon-side **audit emission widening** (a *family*, all emission-only, no wire change): (a) `ToolCallSection` carries the breadcrumb ids the daemon already receives (`SandboxCaller::identity_block`: workflow/attempt/task); (b) emit distinct `overlay_workspace.mounted`/`.published` (not one `cleanup`) populating mount_ms/lease_id/manifest_root_hash/committed_layer_id/publish_layer_ms/upperdir_bytes; (c) `OccSection` sets `changeset_id` + real base/current manifest_version (not `manifest_depth`) + `occ.conflict` on `Lane::Critical`; (d) propagate real `OverlayPathChangeKind` (delete/opaque_dir/symlink) into `changed_path_kinds` instead of hardcoded `"write"` | `eos-daemon::dispatcher` (`emit_workspace_lifecycle_audit`, `emit_occ_audit`, `command_exec` response) + `eos-protocol::audit` | Each field/event exists in the `*Section` schema but is never set, so the §14 sandbox assertions (overlay mounted↔cleaned pair, occ changeset causal-chain, delete/opaque_dir kinds) have no host. Consumer-side normalize cannot synthesize what the daemon never emits. |
| P6 | Add `providers.active: ProviderKind` to `eos-config` (keys stay **env-only**, matching Python) | `eos-config::providers` | **Bugfix**: `default_llm_client`'s `if/else-if` makes OpenAI unreachable whenever `ANTHROPIC_API_KEY` is set. Update the `ProvidersConfig` parity case + insta snapshot. |
| P7 | **Notification/reminder capture surface**: the run driver records the loop's appended notification/system-reminder user messages (off the `LlmRequest` message stream the mock already scans) into a queryable list | `test_runner` run driver (harness-side; the rules already exist in `eos-engine::notifications`) | The 75/100/125% tier reminders + `terminal_call_reminder` ride the **message stream**, not the audit `*Section`s, so §7's collector cannot see them. Assert **structurally** (rule fired, tier, order, once-vs-repeat, `exit_reason`); exact frozen text would need a separate engine change (`body()` is generic today) — out of scope. |

### 3b. Agent-core / sandbox migration work the harness DEPENDS ON — OUT OF SCOPE (flagged)
> The **baseline** mock tier (non-gated correctness, no explorer subagents)
> delivers value with only 3a. These gate **specific** tiers and should be carved
> out as explicit dependencies, owned by the agent-core/sandbox migration:

| # | Dependency | Gates which tier | Workaround until done |
|---|---|---|---|
| D1 | **Sub-agent helper runner** (unified): BOTH `SubagentSupervisorPort::spawn` (explorer) AND `AdvisorPort::review` (advisor) must drive `run_ephemeral_agent` for the spawned profile, so the mock scripts them by name (§6.3). Today both are stubbed (`supervisor.rs` only `register_running`; `AdvisorService` is a placeholder "until eos-runtime wires a helper runner around the engine loop") | Real advisor gate (every gated terminal), explorer/`run_subagent` turns — **MANDATORY for the §14 bundle** (iter1 explorer-spawn + advisor branches are P0) | Non-advisor tiers use the P3 auto-approve stub + `delegate_workflow`; advisor/explorer/bundle cannot run until this lands |
| D2 | Lifecycle audit emitters (`request/workflow/iteration/attempt started/completed`) in `eos-workflow` (it holds an unused `audit_sink`) | The **full** request→workflow→attempt timeline tree (§7.5) | Tool-level + sandbox-level timeline works; lifecycle nodes joined from `eos-state` instead |
| D3 | **Cooperative cancellation** in tool dispatch (poll `shutdown.is_cancelled()` at await boundaries / thread the token to the daemon client) | Clean **mid-tool** early-abort (§6.4) | Baseline terminates via natural loop exits / between turns; mid-tool abort is best-effort (see §6.4 consistency contract) |
| D4 | Per-test **OCC publish idempotency/atomicity across abort** (verify daemon-side) | Early-abort during a sandbox write | Drive early-abort at turn boundaries, not mid-write |
| D5 | **Depth-aware planner terminal gating**: carry an `is_recursive`/depth flag through `WorkflowStarter::start` → planner launch and drop `submit_plan_defers_goal` at depth>1 (today `eos-workflow::attempt::launch` builds one static `AgentDef`, no depth filtering) | The depth-2 child "close-only terminals" assertion (`tcco:TCCO9`) | Either land it as an in-scope `eos-workflow` change or **drop** that one sub-assertion (state which in §14) |

---

## 4. System architecture

```
                          test_runner/  (ONE new top-level crate)
   ┌─────────────────────────────────────────────────────────────────────────┐
   │  config        audit            agent-core (mock|api)       sandbox       │
   │  ──────        ─────            ────────────────            ───────       │
   │  RunConfig     Collector        MockedLlmClient             SandboxPool   │
   │  (.env→env)    Timeline+Render  (EventSource)               FastReset     │
   │  run params    normalize.rs     run→completion/partial      MultiNode     │
   │                (TraceEvent)     mock subagents(advisor/expl) RawExec+commit│
   └───────┬───────────┬──────────────────┬───────────────────────┬──────────┘
           │           │                  │                        │
   reads   │  in-proc  │ AuditSink   inject EventSource +    wire: eos-protocol
   .env+yaml│  capture │             scripted subagents    api.v1.* / api.audit.*
           ▼           ▼                  ▼                        ▼
   ┌──────────────┐  ┌──────────────────────────────┐   ┌─────────────────────┐
   │  eos-config  │  │          agent-core            │   │   eos-sandbox-host   │
   │ (+providers. │  │  eos-runtime  eos-engine       │   │   (host transport)   │
   │   active)    │  │  eos-audit    eos-llm-client   │   └──────────┬──────────┘
   └──────────────┘  │  eos-workflow eos-state        │              │ TCP/UDS
                     └───────────────┬────────────────┘              ▼
                                     │ api.v1.* (tool_use_id stamped)┌─────────────────┐
                                     └──────────────────────────────▶│  eosd (Rust)    │
                                              api.audit.pull          │  /sandbox crates │
                                     ◀────────────────────────────────│  daemon + ring   │
                                                                      └─────────────────┘
   Bridge (←): engine.* (in-proc AuditSink)  +  sandbox.* (pull, native Sections)
               → collector normalize → ONE TraceEvent timeline, joined on tool_use_id
```

---

## 5. Module: `config`

### 5.1 Current state (`eos-config`)
`CentralConfig { database, sandbox, providers, attempt }`, layered
`defaults < ephemeralos.yaml < env < init`, `#[serde(deny_unknown_fields)]`. Gaps:
no active-provider selector; api keys read inline via `std::env::var` + hardcoded
base_urls in `default_llm_client`; **no `.env` loading** (deliberately removed).

### 5.2 Design — minimal, env-keyed, harness-scoped `.env`
**[rev]** Three corrections from review:
1. **Do NOT re-add a `runner` section to production `CentralConfig`** (it was
   removed as `GC-eos-config-05`; re-adding reverses a recorded decision and breaks
   the schema-parity test). Runner/multi-node knobs live in the harness's own
   `RunConfig`.
2. **Keep api keys env-only** (matching Python `providers.py`: "API keys remain
   env-only"). `eos-config` gains only `providers.active: ProviderKind`. This both
   fixes the OpenAI-unreachable bug **and** keeps secrets out of the serialized
   config — no `env:VAR` placeholder machinery, no `SecretString` plumbing needed.
3. **`dotenvy::dotenv()` is called only in the `test_runner` harness entrypoint**,
   never in `eos-runtime::main`. The harness process hydrates `.env` into the
   process env *before* building `AppState`; `default_llm_client` then reads the
   keys via the existing `std::env::var` path. Real exported env still overrides
   `.env` (dotenvy default → preserves `env > yaml`).

```
.env  (user / Python writes ANTHROPIC_API_KEY=… , OPENAI_API_KEY=…  — manual/external)
   │  test_runner main: dotenvy::dotenv()   (FIRST, harness-only)
   ▼
process env ──▶ default_llm_client: std::env::var(ANTHROPIC_API_KEY|OPENAI_API_KEY)
                CentralConfig.providers.active → picks which client to build
                AnthropicClient::new(base_url, Auth::ApiKey(secret_from_env), retry)
```

### 5.3 `RunConfig` (harness per-run handle — decoupled from `CentralConfig`)
**[rev]** Match the Python shape: `RunConfig` holds only run-scoped fields and does
**not** embed `CentralConfig`; the resolved provider client/config is passed
separately to the runner.

```rust
pub struct RunConfig {
    pub entry_prompt: String,
    pub instance_id: SweevoInstanceId,         // EOS_SWEEVO_INSTANCE
    pub fidelity: Fidelity,                    // Mock | Live
    pub subject: Subject,                       // AgentExecution | SandboxTools | SandboxRpc
    pub load: Load,                             // Single | Multi { nodes: u32 }
    pub reuse_mode: ReuseMode,                  // Fresh | Reuse | ForceFresh
    pub audit_dir: PathBuf,
    pub run_label: String,
    pub max_duration_s: Option<u64>,            // wall-clock cap → early abort
    // live_e2e knobs (concurrent_sandbox_runners, real_agent_max_duration_s,
    // heavy_enabled, capacity_enabled) live here, NOT in production CentralConfig.
    pub live_e2e: LiveE2eParams,
}
```

### 5.4 Files & source-side change
```
test_runner/src/config/
  mod.rs
  run_config.rs     // RunConfig, Fidelity/Subject/Load/ReuseMode
  env_bootstrap.rs  // load_dotenv() + load_central() (harness entrypoint only)
```
Source change: P6 (`providers.active` + parity-case/snapshot update). **[rev: no
`runner` section, no `dotenv_writer.rs`, no credential-resolver plumbing.]**

---

## 6. Module: `agent-core` (`src/agent_core/`) (mock + api)

> Module dir is `agent_core/` (Rust module names cannot contain `-`); it is named
> after the upstream `agent-core` crates it drives. **[rev c]**

### 6.1 The seam (one mock surface)
```
run_query (eos-engine::query::loop_)
   └─ source: Arc<dyn EventSource> = ctx.event_source         ← INJECT MockedLlmClient HERE
        ├─ ProviderEventSource (prod) ── wraps ── Arc<dyn LlmClient> (Anthropic/OpenAI)
        └─ MockedLlmClient (the only mock) ── holds ── Box<dyn TurnScript>
```
**[rev]** Ship **one** scripted surface — the engine-level `MockedLlmClient`
(`impl EventSource`). It is named per the user's request ("rename to mocked llm
client"). The provider-level `LlmClient` mock and the precedence-footgun guard are
**dropped**; the real encode/adapt path is covered by the §6.5 live smoke.

### 6.2 Scripted-turn model (the primary type is the **branching** one)
```rust
pub struct ScriptedTurn { pub thinking: Option<String>, pub text: Option<String>, pub calls: Vec<ScriptedCall> }
pub struct ScriptedCall { pub name: String, pub input: JsonObject }

/// PRIMARY: result-reading, stateful (interior-mutable). Needed for branching
/// scenarios (root delegation polls check_workflow_status up to ~90 turns).
pub trait TurnScript: Send + Sync {
    fn next_turn(&self, prior: &[ToolResult]) -> Option<ScriptedTurn>;
}
/// Convenience for NON-branching fixtures ONLY — never for delegation/polling.
impl TurnScript for Mutex<std::vec::IntoIter<ScriptedTurn>> { /* ignores prior */ }

pub struct MockedLlmClient { script: Box<dyn TurnScript> }
impl EventSource for MockedLlmClient {
    async fn stream(&self, req: &LlmRequest) -> Result<EngineStream, EngineError> {
        let prior = trailing_tool_results(req);   // scan BACKWARD past appended notifications
        let turn = self.script.next_turn(prior).unwrap_or_else(ScriptedTurn::text_only_eos);
        Ok(emit_stream(turn))   // [ReasoningDelta?, TextDelta?, ToolUseDelta×N, AssistantMessageComplete]
    }
}
```

**[rev] Loop invariants — corrected against `run_query`:**
1. **Always end each turn with `AssistantMessageComplete`** carrying the tool_use
   blocks. Absent → `EngineError("provider stream ended without assistant
   completion")`. *(This — not deltas — is the load-bearing requirement.)*
2. Budget is counted via `streamed_tool_use_ids` de-dup **plus** a second pass over
   the message's tool_use blocks. Deltas are **optional for budget**, but when
   present their `ToolUseId`s **must match** the complete-message block ids
   (mismatch double-counts). Emit deltas-before-complete for production-faithful order.
3. There is **no dispatch-time budget gate**. The only hard ceiling is
   `terminal_submission_failed`: `tool_calls_used + text_only_no_terminal_turns >=
   (tool_call_limit*3 + 1)/2`. `attempt_budget_exhausted` scenarios must be driven
   by that ceiling or by attempt-level orchestration, not a per-call gate.
4. A terminal tool must be the **only** call in its turn (`debug_assert!` in the mock).
5. On script exhaustion, yield a **valid text-only `AssistantMessageComplete`
   every subsequent turn** (never an empty stream) so the `*3/2` ceiling terminates.
6. Leave `agent_name`/`agent_run_id` empty on **all** emitted events; the loop's
   `stamp_identity` fills them.
7. `trailing_tool_results` scans `req.messages` **backward** for the most recent
   user message carrying `ToolResult` blocks (the loop appends a notification/reminder
   user message *after* the results — a naive "last message" read returns the reminder).

### 6.3 Mocked sub-agents — the advisor and the explorer are the **same mechanism** [rev]
**The advisor is a sub-agent, not a verdict service.** `ask_advisor(tool_name,
tool_payload)` is a tool that calls `AdvisorPort::review` — documented in
`eos-tools::ports` as *"the advisor helper-agent **runner**"* — which is meant to
**spawn an advisor agent and run it through the real loop**, whose terminal is
`submit_advisor_feedback`; the `AdvisorApproval` pre-hook
(`eos-tools::hooks::run_advisor_approval`) later calls `approval_status`, which
reads that feedback from the conversation. `AdvisorService` today is explicitly a
placeholder *"until `eos-runtime` wires a helper runner around the engine loop."*

So **every mocked sub-agent — advisor, explorer (`run_subagent`), and any helper —
is driven by one mechanism**: the loop spawns the sub-agent → it runs through
`run_ephemeral_agent` → the harness's `event_source_factory` returns a
`MockedLlmClient` whose `TurnScript` is selected by **profile name**
(`planner`/`executor`/`reducer`/`advisor`/`explorer`). This is exactly the Python
mock's shape (`registered_mock_agents` = main+helper+subagent profiles;
`scenario_script_for` dispatches by `agent_def.name`).

**The advisor verdict branches are produced by scripting the advisor sub-agent,
NOT by a `ScriptableAdvisor` Port** (that abstraction is dropped):
| Branch | How the mock produces it |
|---|---|
| approve | advisor run scripts `submit_advisor_feedback` with an approving verdict |
| reject | advisor feedback rejects → `approval_status` → `approved:false, reason:rejected` |
| wrong_tool | advisor feedback reviews a different `tool_name` → `reason:wrong_tool` |
| missing | the agent submits the terminal **without** calling `ask_advisor` → `reason:missing` |
| advisor_failed | the advisor run is scripted to crash / yield no terminal → `review` errors |

**Source-side prerequisite (D1, unified):** both `SubagentSupervisorPort::spawn`
(explorer) and the real `AdvisorPort::review` (advisor helper runner) must
actually drive `run_ephemeral_agent` for the spawned profile — today both are
stubbed. This single "sub-agent helper runner" is what unblocks advisor gates,
explorer turns, and the §14 bundle. The harness registers `main`+`helper`+
`subagent` profiles into the **immutable** `AgentRegistry` (`AgentRegistryBuilder`
→ `AppStateBuilder::agent_registry`).

*(Optional bootstrap only:* an injected **auto-approve** `AdvisorPort` stub via
`AppStateBuilder::advisor()` can unblock **non-advisor** tiers' gated terminals
before the helper runner lands — but it **bypasses** the real
`ask_advisor`→`submit_advisor_feedback` path, so the bundle and any advisor-branch
scenario use the real sub-agent path, not the stub.)*

### 6.4 Run→completion + early-terminate (with the consistency contract)
```
start_request(state, prompt) ─▶ RequestEntryHandle { request_id, root_task_id,
                                  root_agent_task: JoinHandle<()>, state(AppState{shutdown: CancellationToken}) }
  ├ full finish : handle.join().await            (root submits submit_root_outcome)
  └ PARTIAL / TIMEOUT:
      tokio::select! {
        _ = handle.join()       => Completed,
        _ = condition_watcher   => stop(),    // fed by audit events (a tool completed / conflict / N calls)
        _ = sleep(max_duration) => stop(),
      }
      stop() = handle.shutdown(grace)  // cancel token, parent-exit supervisor, await within grace, abort on timeout
```
**[rev] Consistency contract (the abort path is NOT lossless):** the
`CancellationToken` is **not** observed inside tool execution today (D3), so
`JoinHandle::abort()` cuts at the next `.await` — possibly mid-daemon-roundtrip.
Therefore:
- `Partial`/`AbortedByTimeout` outcomes carry **best-effort, possibly-truncated**
  audit (the in-proc `CapturingSink` "0 dropped" guarantee holds only for runs that
  end via normal loop exit, **not** mid-tool abort).
- Prefer terminating at **turn boundaries** (drive the condition off completed-tool
  audit events) over mid-tool abort.
- Clean mid-tool abort requires D3 (cooperative cancellation) + D4 (idempotent OCC
  publish) — flagged dependencies, not baseline.

### 6.5 Live api-client smoke (trivial — one test per provider)
```
api_smoke(provider):
  load .env → build real Anthropic/OpenAI client →
  send 1-turn LlmRequest {system reminder, one tool `echo{message}`, "respond by calling echo"} →
  assert: ToolUseDelta name=="echo" with well-formed input,
          terminated by AssistantMessageComplete{stop_reason: ToolUse}  (system reminder honored → tool, not free text)
```
Gated by key presence (`#[ignore]` + env preflight). Not a matrix.

### 6.6 Files & source-side change
```
test_runner/src/agent_core/
  mod.rs
  script.rs         // ScriptedTurn, ScriptedCall, TurnScript, emit_stream, trailing_tool_results
  mocked_llm.rs     // MockedLlmClient (impl EventSource)
  subagents.rs      // mocked sub-agents (advisor/explorer) scripted by profile name; registers main/helper/subagent profiles; optional auto-approve AdvisorPort bootstrap stub
  run.rs            // RequestEntryHandle driver + early-terminate (select!)
  api_smoke.rs      // trivial live anthropic/openai test
  // see §14 for the bundle-scenario additions (notification capture, spike companion)
```
Source changes: P2, P3 (+ D1 flagged for explorer scenarios).

---

## 7. Module: `audit` (the reform — consumer-side bridge, four facets, human-readable)

### 7.1 The bridge answer (the user's confusion-point #1)
**Each module keeps its native audit; the bridge is consumer-side.** No shared
contract crate. The collector defines `TraceEvent` and writes `From<AuditEvent>`
(agent-core) and `From<&Section>` (sandbox) — exactly as Python's
`daemon_event_normalizer.py` + `performance_report.py` do. The **only** cross-repo
coupling is the correlation key (P1) and the daemon emission widening (P5).

### 7.2 The four-facet `TraceEvent` (defined ONLY in `test_runner::audit::normalize`)
```rust
pub struct TraceEvent {
    pub ord: u64,                 // collector-assigned merged-timeline ordinal (the total order)
    pub ts: UtcDateTime,
    pub source: TraceSource,      // Engine | Workflow | Sandbox | Plugin
    pub kind: String,             // "engine.tool.completed", "sandbox.occ.publish", …
    pub node: CorrelationNode,    // join keys (superset of eos-audit AuditNode + agent_role + ordinals)
    pub facets: Facets,
    pub raw: Option<JsonObject>,  // forensic, gated by EOS_AUDIT_FORENSIC_RAW_ENABLED (Python parity)
}
pub struct Facets {
    pub semantics:  Option<Semantics>,   // headline sentence + op + detail
    pub performance: Option<Performance>,// duration_ms, phase_ms map
    pub resource:   Option<Resource>,    // bytes_in/out, peak_resident, changed_path_count, tokens_in/out
    pub correctness: Option<Correctness>,// status, error_kind, conflict, is_terminal
}
```
**[rev] Facet sourcing — honesty about "no new measurement code":** performance,
resource (bytes/peak/paths), and correctness map onto fields **both sides already
emit**. The **one** genuinely-new projection is **token usage** (`UsageSnapshot`
exists on the engine turn event but is never audited) — if the `resource` facet
includes tokens, P4's change list must add an `engine.turn.completed` usage emitter;
otherwise drop the tokens line. *(Recommended: include it — the user explicitly
named "resource usage"; it is a tiny projection of an existing struct.)*

**[rev] Ordering:** the **collector assigns `ord`** at ingest (the only place that
sees both streams). The daemon ring's own `seq`/`lost_before_seq` are kept strictly
for **drop detection**, not cross-source order. No sink-boundary seq is added to
`eos-audit` (it could not total-order across the boot-scoped ring anyway).

### 7.3 Correlation (P1) — direction, not an exact diff
```
engine dispatch: metadata.tool_use_id = Some(tu-…)   (already set)
   │  STAMP upstream onto sandbox_invocation_id  (verify all mint/fallback sites honor a present id)
   ▼
SandboxRequestBase.invocation_id = tu-…
   ▼  (daemon ALREADY echoes wire id)
eosd ToolCallSection.tool_use_id = tu-…   ==   engine.tool node.tool_use_id
   ▼
collector joins both streams on tool_use_id  → per-call correlation (fallback: agent_run_id)
```
The daemon needs **no** tool_use_id change. Mint sites to audit: `eos-tools::
model_tools::sandbox` (`new_v4` fallback) and `eos-sandbox-host::daemon_client`
(`new_invocation_id`) — both must reuse a present id.

### 7.4 Collector files
```
test_runner/src/audit/
  mod.rs
  capturing_sink.rs  // impl AuditSink: in-proc, lossless Vec (agent-core side)
  daemon_puller.rs   // port of DaemonAuditPuller: api.audit.pull cursor/cadence/boot_epoch_id
  normalize.rs       // TraceEvent + From<AuditEvent> + From<&eos_protocol::Section>
  timeline.rs        // merge both streams, assign ord, group by node
  render.rs          // tree + summary  (semantics/performance/resource/correctness)
  jsonl_sink.rs      // RotatingJsonlSink port (canonical sandbox_events.jsonl artifact)
  query.rs           // assertion helpers for Expectation (by kind/node/facet)
```

### 7.5 Human-readable output (sample — the deliverable)
```
REQUEST req-9f3a  "fix dask groupby regression"            [PASS]  42.1s
└─ workflow wf-21 (delegated)  iter 2 / attempt 2          ✔ reducer gate
   └─ task t-7 (executor)  agent_run ar-55
      ├─ engine.tool.completed  write_file  src/groupby.py
      │     semantics : wrote file (overlay capture)
      │     perf      : 12.4ms      resource: +1 path, 3.1 KiB out      correct: ok
      │     └─ sandbox.occ.publish  tool_use=tu-7f… (JOINED)
      │           perf: prepare 1.1 / apply 2.0 / commit 0.8 / publish 0.4 ms
      │           resource: 1 changed path                              correct: ok (no conflict)
      ├─ engine.tool.completed  exec_command  "pytest -q"   correct: error (exit 1)  perf: 8.7s
      └─ engine.tool.completed  submit_generator_outcome    correct: ok (terminal)
SUMMARY  tools 31 (write 8/read 6/exec 4/search 9/terminal 4) · sandbox occ.publish 8 conflict 0 squash 1
         perf agent 38.0s sandbox 4.1s · resource tokens 18.2k/2.1k* · correctness 0 unexpected errors, reducer PASS
         audit: in-proc 0 dropped · ring lost_before_seq 0     (* tokens require P4 usage emitter)
```

---

## 8. Module: `sandbox` (real, fast, reusable, multi-node)

### 8.1 Drive via the `/sandbox` wire (A3)
Orchestrate **only** through `eos-sandbox-host`: provision
(`RequestSandboxProvisioner::prepare_for_run`), lifecycle (`SandboxLifecycle`),
transport (`DaemonClient: SandboxTransport`), typed tool ops (`tool_api::*` →
`api.v1.*`), raw daemon ops (`DaemonClient::call_daemon_api(op: &str, …)` — the
escape hatch for `api.build_workspace_base` / `api.commit_to_workspace` /
`api.runtime.ready` / `api.isolated_workspace.*` / `plugin.*` that have no typed
`DaemonOp` variant), audit pull (`api.audit.*`), one-time eosd push
(`runtime_artifact::ensure_eosd_uploaded`, marker-skip `/eos/daemon/.eosd-sha256`).

**`RawExec` (host docker exec — NOT a daemon op).** `eos-sandbox-host::
ProviderAdapter::exec → RawExecResult{exit_code,stdout,stderr}` runs a command in
the container **outside** the overlay, so it is the only way to (a) empty
`/testbed` keeping `.git`, (b) read the **base disk** after commit-back
(`api.v1.exec_command` is overlay-bound and cannot see materialized files), and
(c) run host-side `git add -f` / `git diff --cached` / `test -d /testbed/.git`.
The §14 commit-back capstone depends on it; `ops.rs` exposes it as `raw_exec(cmd)`.

### 8.2 Fast reuse — the bench `bench_rust_daemon_phase3.py` setup (task b) + corrected reset
**Canonical fast-setup sequence** (the authoritative reference, ported from
`backend/scripts/bench_rust_daemon_phase3.py` → its Rust `eos-sandbox-host`
equivalents):

```
1. SandboxLifecycle::create / prepare_for_run(reuse by sandbox_id|name)   # DockerBench.create(container_id, name_prefix)
2. (verify arch)                                                           # collect_environment
3. reset_runtime                                                          # kill pid, rm sock/pid/env/log + clear layer-stack root
4. runtime_artifact::ensure_eosd_uploaded                                # upload_artifact (marker-skip on reuse)
5. DaemonClient::ensure_daemon_current(sandbox_id)                       # invalidate endpoint → spawn (skip if pid+socket live)
6. DaemonClient resolve TCP endpoint
7. call_daemon_api("api.build_workspace_base", {workspace_root:"/testbed", reset:true}, timeout=180)   # version-1 base binding
8. call_daemon_api("api.runtime.ready", {}, timeout=30)                  # readiness gate (control/data plane + mutation gate)
```
Steps 1–6 are **one-time/cacheable per warm container** (skip on reuse: cached
image tag, eosd sha-marker, daemon pid+socket liveness). Steps 7–8 are the
**per-test** binding. `WORKSPACE_ROOT=/testbed`, `LAYER_STACK_ROOT` are the bench
constants.

**From-scratch base variant (§14 bundle, `cap:SAND1`/`ls:SAND1`).** Before step 7,
empty the repo keeping only `.git`:
`raw_exec("find /testbed -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +")`,
then `build_workspace_base{workspace_root:"/testbed", reset:true}` yields the
version-1 base over the emptied repo. (Confirmed supported: `eos-layerstack::
build_workspace_base` walks whatever exists and always emits `Manifest::new(1)`.)

**Corrected per-test reset.** Overlay-only reset **cannot** prevent contamination:
the SWE-EVO reset does `git reset --hard / clean -fd / checkout -f base_commit` +
`build_workspace_base{reset}` (+ `pip install -e .` + daemon rebind), and
`commit_to_workspace` materializes overlays into the repo's `.git`.

```
                       FIRST container in session         EVERY reused test
  docker pull+tag image (snapshot)   ████ one-time         ░ skip (cached tag)
  create + map daemon port           ████ one-time         ░ skip (resume start)
  ensure_eosd_uploaded               ████ one-time         ░ skip (.eosd-sha256 marker)
  ensure_daemon_current (spawn)      ████ one-time         ░ skip (pid+socket liveness)
  ─────────────────────────────── REAL per-test reset ───────────────────────────────
  git reset/clean/checkout base      ████ first use        ████ per-test
  build_workspace_base{reset}        ████ first use        ████ per-test
  pip install -e . / daemon rebind   ████ first use        ████ per-test IF the test mutates
                                                                  site-packages or rebinds /eos/mount
```
**Skip everything cacheable** (image/eosd/daemon/snapshot/base-layer). The
**"active-overlay-only reset" fast path is a precondition, not the default**: it is
safe **only** for tests that never materialize the overlay (`commit_to_workspace`)
and never mutate site-packages. The default per-test path is the full SWE-EVO reset.

### 8.3 Configurable multi-node — **[rev] with partial-failure rollback**
```
  RunConfig.live_e2e.concurrent_sandbox_runners = N  (semaphore cap, pre-acquire quota check)
  SandboxPool::provision_n(N):
     shared:   ONE image/snapshot pull+tag, ONE host artifact_dir (sandbox/dist)
     per-node: distinct SandboxId; label node_index (via set_labels post-create —
               fresh_create_spec only mints a random request-<hex> name)  [rev]
     ROLLBACK: SandboxLifecycle::create registers EAGERLY → if node k<N fails,
               delete+dispose nodes 0..k-1 (else lease/quota leak)        [rev]
  reuse/attach: requires a list()+name-filter discovery step              [rev]
               (prepare_for_run takes an explicit id only; no name path — mirror
                Python _find_existing_sandbox_by_name over ProviderAdapter::list())
  teardown:    release() deletes/disposes all N (no-op all under Reuse/Attach)
```
Verified: `ProviderRegistry.bindings` + `DaemonClient.tcp_cache`/`tcp_locks` are
already per-`SandboxId` → **no new isolation primitive**. The new work is the
N-provisioner, the rollback, the reuse discovery, and per-node labelling.

### 8.4 Files & source-side change
```
test_runner/src/sandbox/
  mod.rs
  pool.rs        // SandboxPool: provision / provision_n (+rollback) / reuse / teardown
  fast_reset.rs  // bench fast-setup + real per-test reset + from-scratch empty-/testbed bootstrap
  instance.rs    // SweevoInstance resolve (EOS_SWEEVO_INSTANCE → image/base_commit)
  ops.rs         // facade: typed tool_api (api.v1.*) + raw_exec (host docker exec)
                 //   + call_daemon_api raw ops: build_workspace_base, commit_to_workspace,
                 //     runtime.ready, isolated_workspace.{enter,exit,status}, plugin.{ensure,status}, plugin.lsp.*
```
Source change: N-provisioner + rollback + reuse discovery (host-side). **[rev]**
**Plugin/LSP needs no new typed surface to be reachable** — the daemon already
serves `plugin.lsp.{hover,diagnostics,apply_workspace_edit}` / `api.plugin.{ensure,
status}`, and the harness reaches them via the existing **raw-op**
`DaemonClient::call_daemon_api(op: &str, …)`. Adding typed `DaemonOp::{PluginEnsure,
PluginStatus, PluginLspOp, IsolatedWorkspace*}` variants is an optional ergonomic
nicety, not a prerequisite (§14 LSP/plugin rows ride the raw-op path).

---

## 9. Test taxonomy (preserve rigor; drop the bad layout)

### 9.1 Axes — **[rev] three subjects** (sandbox invariants are daemon-RPC, not loop)
```
  fidelity ∈ {Mock, Live}
  subject  ∈ {AgentExecution,        // through the real engine loop
              SandboxTools,          // engine-driven high-volume tool calls (stability/load)
              SandboxRpc}            // DIRECT api.* daemon RPC (IWS/OCC/overlay/layerstack invariants)
  load     ∈ {Single, Multi{nodes}}  // replaces smoke-vs-full + the capacity mega-scenario
```
**[rev]** The `isolated_workspace`/OCC/overlay/layer-stack invariants are driven by
**direct `api.isolated_workspace.*` / `api.v1.*` daemon RPC**, NOT the engine loop —
hence the explicit `SandboxRpc` subject. Mis-routing them through "scripted tool
calls" would silently shrink coverage to a handful of phrases.

### 9.2 Ported architecture (the good parts)
- `run_pipeline` 5-seam spine; `LifecycleHooks` (`before_run/on_event/after_run/
  on_aborted`); dual report (`PipelineReport` + mode views from typed audit events);
  Scenario-as-data + **real loop**; `_graph_summary` real-state walk on `eos-state`.
- The single most important property: **mock tests drive the REAL loop** (real tool
  dispatch, terminal-alone, budget, real ContextEngine XML envelopes); **graph shape
  is read from persisted `eos-state` rows, never scenario self-report.**

### 9.3 `Expectation` — **[rev] expanded to cover what `FocusedScenarioCase` +
`_assert_tool_and_event_capacity` assert** (each field maps to a real Python assertion)
```rust
pub struct Expectation {
    pub request_status: RequestStatus,
    pub role_task_floors:  BTreeMap<(AgentRole, TaskStatus), u32>,  // status-scoped (done/failed) [rev]
    pub absent_done_roles: BTreeSet<AgentRole>,                     // [rev]
    pub required_event_kinds: Vec<String>,
    pub attempt_count: Option<u32>, pub iteration_count: Option<u32>,
    pub deferred_attempt_bounds: Option<(u32,u32)>,
    pub recursive_workflow_count: Option<u32>,                      // multi-workflow/delegation [rev]
    pub tool_count_floors: BTreeMap<String, u32>,                   // write>=30, read>=20, …
    pub tool_error_floor: Option<u32>,                             // tool_errors_total>=1 [rev]
    pub required_sandbox_events: Vec<String>,                       // occ.publish, squash, conflict, …
    pub dependency_prompt_xml: bool,                               // the real-XML-envelope gate [rev]
    pub sandbox_checks_pass: bool,                                 // [rev]
    pub forbidden_substrings: Vec<String>,    // no-internal-error gate: "internal_error","stale lowerdir",… [rev]
    // ── §14 bundle-driven additions ───────────────────────────────────────────
    pub exit_reason: Option<QueryExitReason>,        // assert TERMINAL_NOT_SUBMITTED (150% ceiling)
    pub notifications: Vec<NotificationProbe>,        // {rule, tier, occurrence: Once|Repeats(n), order_index} — structural, not exact text
    pub advisor_denials: Vec<String>,                 // rejected/wrong_tool/missing/advisor_failed observed
    pub hook_denials: Vec<String>,                    // nested_workflow, destructive_git/shell, forbidden_in_isolated_workspace, …
    pub iteration_axes: Vec<(IterationCreationReason, Option<String>)>, // (Initial|DeferredGoalContinuation, iteration_goal) — deferral-vs-retry axis
    pub registry_profile_checks: bool,                // role==role.value, limits 100/100/50, executor triggers, no SkillLintError
    pub commit_back: Option<CommitBackAssertion>,     // §14 capstone: {manifest_version==1, timings keys, host-git lists paths, .git survives}
}
```
`QueryExitReason` and `IterationCreationReason` are real `eos-engine`/`eos-state`
types. `notifications`/`advisor_denials`/`hook_denials` are populated from the
run driver's captured notification/system-reminder message stream (P7) + the
audit timeline; `commit_back` from the `api.commit_to_workspace` RPC result + a
`RawExec` post-check.

### 9.4 Scenario catalog (small, orthogonal, high-signal)
- **Mock×AgentExecution**: `initial_workflow`, `initial_messages_capture` (root-request
  envelope) **[rev]**, `dependency_dag_{serial,parallel,diamond,mixed}` **[rev: +mixed]**,
  `dependency_blocked_descendants`, `attempt_retry_{planner,generator,reducer}_failure`,
  `iterative_deferral`, `nested_workflow(_failure)`, `attempt_budget_exhausted`,
  `generator_failure_quiescence`, + 6 `planner_validation` negatives.
- **Mock×SandboxTools**: high-volume scripted tool calls → write/read/exec/search floors.
- **SandboxRpc** (Mock or Live) **[rev]** — enumerate invariant **categories**, table-driven
  (not 120 files): OCC conflict round-trip; overlay capture/publish; auto-squash;
  lease-non-leak; read-only-plugin-no-publish vs write-plugin-publish; finite-command
  vs command-session lifecycle; **IWS**: enter-rejects-active-bg, exit-drains+releases,
  no-OCC-publish, audit-only-writes, daemon-restart orphan-GC (cgroup/netns/scratch/
  veth/lease), quota-one-per-agent / total-cap / host-RAM-gate / TTL-evict, **O(1)
  lowerdir disk (`workspace_tree_bytes==0` regression gate)**, concurrent-enter IP
  non-double-allocation, network hardening (egress masquerade / IMDS+RFC1918 drop /
  inbound reject). State explicitly which buckets are **dropped** vs ported.
- **Live×Sandbox**: parity asserted on **named daemon ops** (provision →
  `ensure_workspace_base` → `api.v1.*` round-trip → `api.audit.pull`) **[rev: not "the
  bench-script flow"; the bench files are churning in the worktree]**.
- **Live×AgentExecution**: api smoke (§6.5) + SWE-EVO real-agent F2P/P2P.

### 9.5 Dropped — **[rev] keep the cross-cutting invariants, drop only the scenario SIZE**
Drop `full_stack_adversarial`, `full_system_capacity_matrix`, `pack_catalog`, the
~120-file IWS layout, smoke-vs-full duplication, the percentile perf aggregator
(→ `benches/`). **But RETAIN as `Expectation`/`Correctness`-facet queries** (they are
cross-cutting correctness, not capacity-only): the **no-internal-error / forbidden-
signature** gate (8 `_invariants.py` files), the **tool_error_floor**, and the **O(1)-
overlay `workspace_tree_bytes==0`** + **`sum(phases_ms) <= total_ms`** regression gates.

---

## 10. Workspace layout

```
test_runner/                       (NEW top-level crate)
  Cargo.toml                       // path-deps → ../agent-core/*, ../sandbox/eos-protocol
  rust-toolchain.toml
  src/
    lib.rs
    config/                        // §5
    audit/                         // §7  (collector owns TraceEvent + normalize)
    agent_core/                    // §6  (MockedLlmClient, run, scripted subagents, api smoke) — named after the agent-core crates it drives
    sandbox/                       // §8
    core/                          // run_pipeline spine, LifecycleHooks, reports, Expectation, graph_summary
    scenarios/                     // §9  scenario turn-script data + catalog
  tests/
    mock_agent.rs                  // Mock×AgentExecution
    mock_sandbox.rs                // Mock×SandboxTools
    sandbox_rpc.rs                 // SandboxRpc invariant table
    live_sandbox.rs  live_agent.rs // gated
  benches/                         // perf lane (out of the test taxonomy)
```
Source changes live in their home crates (P1–P6 in agent-core/sandbox; D1–D4 flagged).

---

## 11. SOLID / SRP & simplicity guardrails

| Principle | Where |
|---|---|
| **SRP** | One module per job. Audit **emission** stays in source modules; audit **presentation/normalization** stays in the collector — the bridge is consumer-side, so there is one source of truth per side. |
| **Open/Closed** | New scenario = new `ScriptedTurn` data. New audit source = new `From<…>` in `normalize.rs`. No engine/collector change. |
| **Liskov** | `MockedLlmClient` is a drop-in `EventSource` (advisor/explorer are just profiles it scripts); the bootstrap auto-approve `AdvisorPort` is a drop-in; `SandboxPool` honors the `eos-sandbox-host` contract. |
| **ISP** | Four narrow facets; `TurnScript` is one method; `SandboxTransport` is one `call`. |
| **DIP** | Harness depends on traits (`AuditSink`, `EventSource`, `AdvisorPort`, `SandboxTransport`), injected via existing `AppStateBuilder` setters (+ the new `advisor()`). |

**Deliberately NOT built** (over-engineering = defect): shared audit-contract crate;
second mock surface + precedence guard; `runner` section in production config;
`dotenvy` in `eos-runtime::main`; `dotenv_writer` module; `env:VAR`/`SecretString`
credential plumbing (keys stay env-only); per-scenario classes; the 120-file IWS
layout; building the real advisor runtime (an injected stub suffices).

---

## 12. Progress checker

> `[ ]` = todo. **P#** = in-scope source-side prerequisite (§3a). **D#** = flagged
> external dependency (§3b). Phases are independently verifiable.

### Phase 0 — Skeleton
- [ ] `test_runner/` crate compiles with path-deps to agent-core & sandbox
- [ ] `core` `run_pipeline` spine + `LifecycleHooks` + `PipelineReport` stubs

### Phase 1 — Config
- [ ] **P6** `eos-config` `providers.active` (+ parity case + insta snapshot); fix `default_llm_client` selection bug
- [ ] `config`: `RunConfig`, harness-only `dotenvy::dotenv()` (NOT `eos-runtime::main`)
- [ ] verify: `.env` key hydrates env → correct provider client builds; env overrides `.env`

### Phase 2 — Mock agent-core (baseline)
- [ ] **P2** `pub testsupport` `ScriptedTurn`/`TurnScript`/`MockedLlmClient` (no loop change)
- [ ] **P3** `AppStateBuilder::advisor()` setter + auto-approve bootstrap stub; register main/helper/subagent profiles
- [ ] **D1** (mandatory for advisor/explorer/§14): spawn + `AdvisorPort::review` drive `run_ephemeral_agent`; advisor/explorer scripted by profile name (§6.3)
- [ ] `agent_core`: `MockedLlmClient`, `trailing_tool_results` (backward scan), run driver
- [ ] verify: 1-tool mock script reaches `submit_root_outcome`; **gated terminal passes** via stub; budget parity holds
- [ ] verify: condition-watcher terminates a run early → `Partial` (turn-boundary; audit best-effort noted)
- [ ] **D1** (flagged): explorer/`run_subagent` scenarios deferred until `spawn` drives `run_ephemeral_agent`

### Phase 3 — Audit (correlation + dead-path + collector)
- [ ] **P1** stamp engine `tool_use_id` → `sandbox_invocation_id` upstream; verify all mint/fallback sites reuse a present id
- [ ] **P4** wire dead `engine.tool.*` path on the injected sink; enrich `AuditNode` from `QueryContext`; (optional) `engine.turn.completed` usage emitter
- [ ] **P5** widen daemon `ToolCallSection` with the breadcrumb ids it already receives
- [ ] `audit`: `CapturingSink`, `DaemonAuditPuller`, `normalize` (`From<AuditEvent>` + `From<&Section>`), `timeline` (collector-assigned `ord`), `render`
- [ ] `CONTRACT.md`: add audit-pull schema as a coordinated surface
- [ ] verify: a real mock run emits `engine.tool.*` (not just `plugin.*`); engine tool event ↔ its `occ.publish` share `tool_use_id`; §7.5 tree + summary render
- [ ] **D2** (flagged): full lifecycle tree awaits `eos-workflow` lifecycle emitters

### Phase 4 — Sandbox (real, fast, multi-node)
- [ ] `sandbox`: `SandboxPool` provision/reuse over `eos-sandbox-host`
- [ ] fast reset: cacheable-skip + **real** per-test reset (git + `build_workspace_base{reset}`)
- [ ] `provision_n` + semaphore + **partial-failure rollback** + reuse discovery + per-node labels
- [ ] verify: warm reuse skips cacheable setup, no cross-test contamination; N=3 lanes, no quota overrun / lease leak (incl. mid-provision failure)
- [ ] **D3/D4** (flagged): clean mid-tool early-abort awaits cooperative cancellation + OCC idempotency

### Phase 5 — Taxonomy + scenarios
- [ ] expanded `Expectation` + `assert_report`; `graph_summary` real-state walk (all workflows)
- [ ] Mock×AgentExecution catalog (§9.4) as turn-script data
- [ ] Mock×SandboxTools floors; **SandboxRpc** invariant table (categories, ported-vs-dropped stated)
- [ ] retain cross-cutting gates (no-internal-error, tool_error_floor, O(1)-overlay, phase-sum) as facet queries
- [ ] Live×Sandbox parity (named ops); Live×AgentExecution api smoke + sweevo (gated)

### Phase 6 — Cutover
- [ ] `docs/architecture/` page for `test_runner`
- [ ] retire/redirect Python `backend/src/test_runner` after parity; no Python sandbox internals imported

---

## 13. Resolved decisions (former open questions)
- **Q1 — who owns `TraceEvent`?** The **collector** (`test_runner::audit::
  normalize`). No shared `eos-audit-contract` crate; both source repos keep native
  audit types. *(Review: a shared contract duplicates `AuditNode` + native
  `*Section`, two sources of truth.)*
- **Q2 — correlation key?** Reuse the engine `tool_use_id` as the sandbox
  `invocation_id` (it is already the daemon's registry/cancel key and is already
  echoed into `ToolCallSection.tool_use_id`). **No** new caller field —
  `SandboxCaller.tool_id` already exists.
- **Q3 — ordering?** Collector assigns the merged-timeline `ord` at ingest; the
  daemon ring `seq`/`lost_before_seq` stay for drop detection only. No sink-boundary
  seq in `eos-audit`.

### Genuinely open (need a user call)
- **§3b scope**: do D1 (**sub-agent helper runner** — spawn + advisor `review` drive
  the loop), D2 (lifecycle audit emitters), D3 (cooperative cancellation), D5
  (depth-aware planner terminals) get done as part of this effort, or are they
  tracked as agent-core migration work the corresponding tiers wait on? The
  **baseline** non-advisor mock + sandbox tiers need only 3a; the **§14 ultra-complex
  bundle** needs D1 (+ the P5 audit-emission family) before it can run.

---

## 14. Ultra-complex bundled scenario — conformance & closure

> Reviews the spec against `docs/plans/ultra_complex_bundled_scenario_CHECKLIST.html`
> (282 items / 17 areas). **That checklist is written in outdated pre-migration
> terms** (TaskCenter, `backend/src/sandbox/*.py`, `loop.py`,
> `submit_workflow_handoff`, `_advisor_script`); this section **adopts the new Rust
> mechanism/terminology** and judges hosting on the *migrated* architecture.
> A 6-cluster code-verified cross-check found the architecture **hosts the majority**
> ("partial" everywhere, never "no"); the residual gaps are tight and enumerable below.

### 14.1 Terminology translation (old checklist → new Rust)
| Old (checklist) | New (this spec / migrated code) |
|---|---|
| TaskCenter / `task_center_runner` | Task-first `test_runner`; persisted `Workflow→Iteration→Attempt` under `eos-workflow`; root `Task(role=root)` via `eos-runtime::start_request` |
| `loop.py` / `run_ephemeral_agent` | `eos-engine::query::run_query` / `eos-runtime::run_root_agent` |
| `ScenarioLoopRunner` / `ScenarioEventSource` | `MockedLlmClient` (`EventSource`) via `AppState.event_source_factory`, scripted by **profile name** |
| `submit_workflow_handoff` (terminal) | **non-terminal** `delegate_workflow(goal)` → `WorkflowStarter::start(prompt, parent_task_id)` (parent stays Running) |
| `_advisor_script` always-approve | advisor is a **mocked sub-agent** (§6.3); `AdvisorPort::review` runs it through the loop; terminal `submit_advisor_feedback` |
| `run_subagent`/explorer | same sub-agent mechanism; `SubagentSupervisorPort::spawn` → `run_ephemeral_agent` (D1) |
| `backend/src/sandbox/{overlay,occ,layerstack,ephemeral,isolated}` | `sandbox/crates/eos-{overlay,occ,layerstack,daemon,isolated}` over `api.v1.*` |
| daemon-ring audit + `sandbox.audit` events | `eos-protocol::audit` `*Section` (pull) + `eos-audit::AuditEvent` (in-proc), bridged **consumer-side** in `test_runner::audit::normalize` |
| `commit_to_workspace` / `apply_layerstack_to_repo` | `api.commit_to_workspace` → `eos-layerstack::commit_to_workspace` (reducer exit-gate) |
| `build_workspace_base` / `api.runtime.ready` | same op names, served by `eos-daemon` (`op_build_workspace_base`/`op_runtime_ready`); base layer `B000001-base` |
| notification rules (`terminal_tool_call_count_reminder.py`) | `eos-engine::notifications` `ToolCallBudget{75/100/125%}` + `TerminalCallReminder`; 150% = `query::loop_::terminal_submission_failed` |
| `_iws_rpc` in provider container | `DaemonClient::call_daemon_api(api.isolated_workspace.{enter,exit,status})` — the TCP channel terminates in-container, so no helper script |
| `AUTO_SQUASH_MAX_DEPTH` (Python) | same constant in `eos-occ::service` / `eos-layerstack` (=100) |

### 14.2 The bundle in new terms (the flagship Mock scenario)
ONE scenario `bundle.ultra_from_scratch` builds a fresh `taskflow` Python package
in an emptied `/testbed` (keep `.git`), driven through the **real loop**:

```
root request ──▶ root Task(role=root) ──▶ root agent calls delegate_workflow(build goal)
   │                                          │
   │   iter1 (PLAN+RUN, deferring): explorer sub-agent confirms Python-only image (D1);
   │       planner scripts the defer; scaffold generator builds the base; reducer closes
   │                                          ▼  (DeferredGoalContinuation)
   │   iter2 (PLAN+RUN, closing): mixed DAG — gen[scaffold|modA|modB|depthchain|bg|lsp|delegate|
   │       consumer|verbose]; same-path OCC race; auto-squash depth>100; background lease across
   │       squash; advisor-gated registry migration; delegate_workflow → depth-2 child (operators)
   │                                          ▼
   │   reducer (EXIT GATE): read-back verify → api.commit_to_workspace → /testbed/.git materialized
   │
   ├─ FOCUSED spike companion (limit=4/20): bespoke AgentDefinition through the SAME real loop
   │     for exact budget-tier text/order (the Scenario protocol has no per-task limit)
   └─ OUT-OF-BAND IWS same-port-3000 lane: api.isolated_workspace.enter + api.v1.shell via
         call_daemon_api inside the provider container (netns/unshare — Live only)
```

**Failure containment (authoring contract, no code change).** One iteration resolves
to exactly one SUCCEEDED/FAILED, and the reachability gate requires every generator
transitively needed by a reducer — so retry-then-pass / exhaust-both / drop-defer /
never-submit-`run_exhausted` lanes **MUST** each be a **separate standalone run or a
depth-2 child workflow** (`delegate_workflow`), never sibling tasks of the top-level
closing attempt. Otherwise outcome-projection + the reachability gate cross-contaminate
and the closing attempt cannot PASS.

### 14.3 17-area hosted matrix (new architecture)
| Area (id) | Hosted | Lands in | Note |
|---|---|---|---|
| overlay (ov) | ✔/▲ | SandboxRpc + audit | response-assertable; some audit fields need P5 |
| layerstack (ls) | ✔/▲ | SandboxRpc + audit | lease/squash via timings + audit |
| OCC (occ) | ✔/▲ | SandboxRpc + audit | conflict/retry via response; changeset_id/versions need P5 |
| API+squash (api) | ✔/▲ | SandboxRpc | `layer_metrics` orphan counts hardcoded 0 (D-flag) |
| ephemeral (eph) | ✔/▲ | SandboxRpc | overlay capture/publish |
| isolated workspace (iws) | ▲ | SandboxRpc (**Live**) | lifecycle JSONL not bridged (G2); netns = Live-only |
| commit-back CAPSTONE (cap) | ▲ | **new capstone scenario** | needs RawExec + commit-back assertion (G5) |
| workflow/iter/attempt (tcwo) | ✔ | AgentExecution + graph_summary | hosted |
| ContextEngine (tcco) | ✔/▲ | Expectation.dependency_prompt_xml | depth-2 close-only needs D5 |
| retry/continuation (tcre) | ✔ | Expectation (iteration_axes) | hosted |
| sandbox tools (tsx) | ✔ | Mock×SandboxTools | fully hosted |
| LSP/plugin (tlsp) | ✔/▲ | SandboxRpc (raw-op) | reachable via `call_daemon_api`; some lifecycle seams (G6) |
| enter/exit IWS (tiws) | ▲ | SandboxRpc | JSONL bridge (G2) + ops.rs facade |
| planner/gen/reducer (apgr) | ✔/▲ | AgentExecution | profile-bootstrap checks added to Expectation |
| advisor/explorer (aadv) | ▲ | AgentExecution | **mocked sub-agents** (§6.3) need D1 |
| hooks (hk) | ▲ | new Hooks assertions | denial reasons → Expectation.hook_denials |
| notifications (noti) | ▲ | new Notification capture (P7) | structural assertion; exact frozen text out of scope |

✔ hosted · ▲ partial (closure below). No area is unhostable.

### 14.4 Closure ledger (what to add — grouped, in new terms)
**(A) In-scope source-side `P5` audit-emission family** (emission-only, no wire change):
overlay `mounted`/`published` events + fields; OCC `changeset_id` + real manifest
versions + `Lane::Critical`; real `changed_path_kinds` (delete/opaque_dir). → §3a P5.

**(B) Flagged agent-core/sandbox dependencies (§3b):** D1 (sub-agent helper runner —
advisor + explorer; **bundle-mandatory**); D5 (depth-aware planner terminals for the
depth-2 close-only assertion, or drop `tcco:TCCO9`); `api.layer_metrics` orphan/missing
derivation (or re-express the lease-GC invariant via observable `leased_layers` deltas);
shell-pre-mount-squash + `EOS_SHELL_MOUNT_SQUASH_MAX_DEPTH` (absent in Rust → port or
drop `ov:SAND10`'s shell sub-claim).

**(C) Harness additions (in scope, `test_runner`-side):**
- **RawExec** (host docker exec, §8.1) for empty-`/testbed`, base-disk readback, host git.
- **Commit-back capstone scenario** (§14.2) asserting `api.commit_to_workspace`
  {`manifest_version==1`, commit timings keys} + RawExec post-checks (git lists every
  path, `.git` survives).
- **IWS lifecycle JSONL pull facet**: read `EOS_ISOLATED_WORKSPACE_AUDIT_PATH` in-container
  via `api.v1.shell`/`read_file`, add `From<IwsLifecycleRecord>` to `normalize.rs`
  (`eos-isolated` writes its own JSONL, *not* the `api.audit.pull` ring).
- **Notification/reminder capture** (P7) + `Expectation.notifications`/`exit_reason`.
- **Spike companion** lane: a bespoke `AgentDefinition` (`tool_call_limit` 4/20) via
  `AgentRegistryBuilder`, one-tool-per-turn, through the real loop — for budget-tier
  order/once/repeat (the Scenario protocol has no per-task limit).
- **`ops.rs` facade** rows for `api.isolated_workspace.{enter,exit,status}` + plugin/LSP
  raw-ops (no new typed `DaemonOp` strictly required).

**(D) Expectation deltas** (added in §9.3): `exit_reason`, `notifications`,
`advisor_denials`, `hook_denials`, `iteration_axes`, `registry_profile_checks`,
`commit_back`. Each maps to a concrete checklist assertion.

### 14.5 Bundle progress sub-checklist
- [ ] **D1** lands (sub-agent helper runner) → advisor branches + explorer turn run through the real loop
- [ ] **P5** audit-emission family lands → overlay/OCC/kind assertions have a host
- [ ] from-scratch empty-`/testbed` bootstrap + bench fast-setup (§8.2) green on a warm dask container
- [ ] commit-back capstone scenario asserts the materialization round-trip (RawExec + RPC)
- [ ] IWS lifecycle JSONL pull facet + out-of-band same-port-3000 lane (Live, in-container)
- [ ] notification/reminder capture (P7) + spike companion (limit 4/20) assert tier order/once/repeat + 150% `exit_reason`
- [ ] failure-containment discipline enforced: each fail/exhaust/drop lane is its own standalone/child workflow
- [ ] one `bundle.ultra_from_scratch` scenario wires it all through the real loop; 17/17 areas assert green (or explicitly dropped sub-claims recorded)
