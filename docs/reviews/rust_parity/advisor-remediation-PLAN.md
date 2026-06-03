# Advisor gate — Rust parity remediation plan (PLAN ONLY)

Status: **plan only, do not implement.** Scope: the `agent-core / advisor` HIGH
findings in `docs/reviews/rust_parity/REPORT.html` (areas/advisor: D1 runner+decision
stub, D2 root-gating, D3 metadata passthrough).

This is the round-3 design. It supersedes the earlier draft that used an
`AdvisorPort::review` runner and a `RuntimeAdvisor`/`AppState` adapter — both are
**dropped** per user direction.

---

## 0. Locked constraints (authoritative)

1. **`ask_advisor` is the only launch path, and it calls `run_ephemeral_agent` → the
   engine loop** — the same primitive root / workflow agents use. No advisor-specific
   runner abstraction.
2. **The advisor pre-hook is a verbatim port of Python `advisor_approval.py`** — the
   conversation scan + `_classify` live *inside the hook*, not behind a port.
3. **Wire the pre-hook through the per-tool prehooks list** (`meta::tool_hooks`, the
   Rust port of `@tool(pre_hooks=…)`), as a normal `Hook::AdvisorApproval` entry.
4. **Exactly four gated terminals:** `submit_root_outcome`, `submit_planner_outcome`,
   `submit_generator_outcome`, `submit_reducer_outcome` (root **kept**). Helper /
   explorer terminals carry no advisor hook.
5. **Drop `review`** — no `AdvisorPort::review`, no message-building behind a port.
6. **Drop `AppState`** from the advisor path — no `RuntimeAdvisor`, no AppState-coupled
   runner; `run_ephemeral_agent` stops taking `&AppState`.
7. **Drop the "additional guardrail"** — the Rust-invented `AdvisorPort::approval_status`
   decision port and the `AdvisorApproval` result type are removed; the hook decides.
8. **Remove the advisor from `notifications`** — delete `AdvisorService` from
   `eos-engine/src/notifications/`.
9. **No advisor agent/engine state — the hook infers the verdict from the transcript.**
   The gate is stateless: it derives the verdict by scanning the conversation (the single
   source of truth), never from a cached/derived verdict held on `QueryContext` or any
   engine side-channel. `ExecutionMetadata.conversation` is *transcript access*, not
   advisor state. (Rejected: caching a typed `AdvisorOutcome` on `QueryContext` — §9.)

Net: the entire `AdvisorPort` trait is deleted. The advisor becomes (a) an
engine-driven `run_ephemeral_agent` call for `ask_advisor`, and (b) a self-contained,
**stateless** pre-hook that infers the verdict by scanning the transcript.

---

## 1. Root cause (why it is a stub today)

Python's mechanism (ground truth):

- `ask_advisor` (`backend/src/tools/ask_helper/ask_advisor/ask_advisor.py:159-211`)
  builds the advisor's two messages from `context.conversation_messages` and calls
  `run_ephemeral_agent(..., persist_agent_run=False)` — a **direct late import** from
  `engine.api`. It forwards the advisor terminal's `output` + `is_error` + `metadata`
  (`{helper_role:"advisor", verdict}`) back as its `ToolResult`.
- The gate is the pre-hook `AdvisorApprovalPreHook`
  (`backend/src/tools/_hooks/advisor_approval.py`): it reverse-walks
  `context.conversation_messages` for the latest advisor result, pairs it to the
  originating `ask_advisor` block by `tool_use_id`, and `_classify`-blocks unless the
  verdict is `approve` for *this* terminal. **No port — the scan is in the hook.**

Rust diverged twice and both pieces are inert:

- **R1 — conversation removed from the tool context.** `eos-tools/src/metadata.rs:16-17`
  drops `conversation_messages` ("not tool-facing"); `eos-engine/.../query/loop_.rs:192`
  calls `dispatch_assistant_tools(ctx, &tool_uses)` without `messages`. The hook can't
  scan.
- **R2 — the decision was relocated behind an invented port.** `ports.rs:284-294` adds
  `AdvisorPort::{review,approval_status}`; the only impl
  (`eos-engine/src/notifications/mod.rs:156-174` `AdvisorService`) denies everything and
  `review` returns a canned string. The only approver is `#[cfg(test)] ApprovingAdvisor`.

The crate DAG is why Python's "just import run_ephemeral_agent" became a port:
`eos-engine` **depends on** `eos-tools` (e.g. `notifications/mod.rs` imports
`eos_tools::ports`), so `eos-tools` cannot call back into the engine loop. The fix
below resolves that without a port by **driving the advisor run from the engine**.

---

## 2. Design

### 2a. `ask_advisor` runs in the engine (no port, no AppState)

`ask_advisor` stays a registered, model-facing tool, but its *execution* is the engine
running an ephemeral advisor agent — this is the faithful Rust form of "the tool calls
`run_ephemeral_agent` → engine loop":

- **Relocate `run_ephemeral_agent` from `eos-runtime` to `eos-engine`** (it already
  wraps `eos-engine`'s `build_query_context` + `run_query`; it is an engine primitive).
  Drop the `&AppState` parameter — pass the explicit handles it uses
  (`agent_run_store`, `model_store`, `llm_client`, `event_source_factory`,
  `agent_registry`, `cwd`). `eos-runtime`'s `root_agent` / `agent_runner` call the
  relocated function with those handles (behavior unchanged).
- **Module responsibility for Flow A (precise):**
  - `eos-engine/src/tool_call/dispatch.rs` — **interception only**: a single match arm in
    `dispatch_assistant_tools` routes `ToolName::AskAdvisor` to `advisor::run_advisor(...)`
    instead of the generic `execute_tool_once`. No advisor logic lives here.
  - `eos-engine/src/advisor.rs` — **owns prompt building + run orchestration**:
    `run_advisor(handles, ctx, messages, tool_name, tool_payload)` calls
    `build_advisor_user_msg_1` / `build_advisor_user_msg_2` / `build_parent_transcript`
    (and `eos_tools::terminal::render_terminal_catalog`), resolves the advisor
    `AgentDefinition` from `handles.agent_registry`, invokes `run_ephemeral_agent(...,
    sandbox_id = ctx.sandbox_id, persist_agent_run=false)`, and maps the advisor's
    `terminal_result` → the `ask_advisor` `ToolResult`.
  - `eos-engine/src/agent_loop.rs` — **the loop primitive only**: `run_ephemeral_agent`
    builds the advisor's own `QueryContext` and drives `run_query` to completion.
  - `eos-tools/src/model_tools/advisor.rs` — keeps **only** the model-facing `ask_advisor`
    spec/registration; no executor body.
  - The advisor `AgentDefinition` is resolved from the `AgentRegistry` threaded into the
    engine context (not AppState).
  - **Cohesion cost (deliberate):** Python keeps prompt building inside `ask_advisor.py`
    next to the tool; the crate DAG forces Rust to host it in `eos-engine/src/advisor.rs`
    (next to the run, its only caller), reducing the `eos-tools` tool file to a spec stub.
    Alternative (rejected for now): keep the builders in `eos-tools` as pure functions and
    call them from the engine — splits Flow A across two crates for marginal gain.
- Because the engine builds the result directly from the advisor terminal, the
  `helper_role`/`verdict` metadata is carried natively into the transcript — **D3 is
  resolved by construction** (no projection layer).

`submit_advisor_feedback` is **not** advisor-gated (`meta.rs` `_ => Vec::new()`), so the
advisor run can always submit its verdict — no self-gate / deadlock. (State this in code.)

### 2b. The pre-hook is self-contained (verbatim Python port)

- **Restore the conversation to the tool context.** Add `conversation: Arc<[Message]>`
  to `ExecutionMetadata`; thread the live `messages` into `dispatch_assistant_tools` and
  stamp a per-turn snapshot in `metadata_for_call`. This is the port of Python
  `context.conversation_messages` and is required by the hook scan. Update the
  `metadata.rs` "not tool-facing" doc-comment (it *is* tool-facing now).
- **Rewrite `run_advisor_approval`** (`eos-tools/src/hooks.rs:574-597`) to read
  `ctx.conversation` and reproduce `advisor_approval.py` exactly: `find_latest_advisor_pair`
  (reverse-walk `User` messages for a `ToolResult` with `metadata["helper_role"]=="advisor"`),
  `find_originating_ask_advisor` (forward-walk `Assistant` messages for the `ToolUse` with
  the same `tool_use_id` and `name=="ask_advisor"`), `classify` → reason tag in order:
  `missing` → `advisor_failed` → `structural` → `rejected` → `unpaired` → `wrong_tool` →
  pass. Keep the existing `_MSG_BLOCKED` text (`hooks.rs:451-454`) and `policy:"advisor_approval"`
  + `reason:<tag>` metadata. The hook calls no port.

### 2c. Deletions

- `eos-tools/src/ports.rs`: delete the entire `AdvisorPort` trait + `AdvisorApproval` +
  `AdvisorReview` types.
- `eos-tools/src/metadata.rs`: delete the `advisor` field + `require_advisor()`.
- `eos-tools/src/model_tools/advisor.rs`: delete the `AskAdvisor` executor body that
  called the port; keep `ask_advisor`'s name/spec/description registration (the engine
  intercepts execution).
- `eos-engine/src/notifications/mod.rs` + `lib.rs`: delete `AdvisorService` and its
  re-export.
- `eos-runtime/src/app_state.rs`: delete the `advisor` field, the builder `.advisor(...)`,
  the default `AdvisorService` wiring, and the `#[cfg(test)] ApprovingAdvisor` fake;
  `tool_context.rs` stops setting `advisor`.

### 2d. What the advisor carries + context validation (port of `_compose.py` + `_transcript.py`)

**Not ported in Rust today** (the stub builds no messages). The engine's `run_advisor`
must reproduce it faithfully.

Carried into the advisor run:
- **Same sandbox as the caller:** the advisor's `ExecutionMetadata.sandbox_id =
  ctx.sandbox_id` (`ask_advisor.py:189`), so it can independently read files to verify
  the caller's claims. (Also `persist_agent_run=false`.)
- **`initial_messages = [user_msg_1, user_msg_2]`** (two user messages = the standard
  contract; Python passes `user_msg_1` as `initial_messages` and `user_msg_2` as the run
  prompt — in Rust both are seeded into `initial_messages`):
  - `user_msg_1` = prompt-injection guard + caller's **verbatim `user_msg_1`**
    (`messages[0]`) + verbatim **`user_msg_2`** (`messages[1]`) + the **filtered caller
    transcript** (`messages[2:]`).
  - `user_msg_2` = terminal catalog (advisor-review focus) + pending submission +
    task / calibration / how-to-submit.

Filtered caller transcript (`build_parent_transcript`):
- Drop `system`-role messages; the first non-system message must be `user`, else omit the
  whole transcript (warn, not error).
- Drop the first two user messages (already shown verbatim above).
- Keep the last `MAX_TRANSCRIPT_MESSAGES = 40`; then byte-cap to
  `MAX_TRANSCRIPT_BYTES = 24576` with a head-trim "(N earlier messages elided)" marker.
- Per-block: `text` kept; `thinking`/reasoning **dropped**; `tool_use` inputs **stripped**
  for `{Edit, Write, NotebookEdit}` (Claude-Code names — EOS's own
  `write_file`/`edit_file`/`multi_edit` are deliberately NOT stripped so the advisor can
  audit write scope), `Bash` keeps only `command` capped at `MAX_BASH_COMMAND_CHARS = 500`,
  others full JSON; `tool_result` truncated to `MAX_TOOL_RESULT_CHARS = 4096` with an
  `[error]` marker; `system_notification` rendered inline.

Context validation (in-band tool errors, not framework faults):
- advisor `AgentDefinition` registered, else `"…agent definition 'advisor' not registered"`.
- caller conversation ≥ 2 messages and `messages[0]`/`messages[1]` non-empty, else
  `"…fewer than two user messages"` / `"…first two messages are empty"`.
- parent `AgentDefinition` may be absent → catalog degrades to a stub line; advisor still runs.
- `tool_name` nonblank (already checked in the registration).

Conversation source: the pre-hook scan (2b) reads `ctx.conversation`; `run_advisor` (2a)
builds the transcript from the live `messages` it holds at dispatch — same window.

### 2e. Agent-state removal (explicit)

The gate today reads **agent state** — a derived approval value queried through a port —
instead of inferring from the transcript. Remove all of it; the stateless hook (§2b) is the
only decision path. (`ExecutionMetadata.conversation` is transcript *access*, not state.)

Agent state to delete:
- `AdvisorPort::approval_status(target_tool) -> AdvisorApproval` (`ports.rs:293`; called at
  `hooks.rs:587`) — the gate's "what is the approval state?" query.
- `struct AdvisorApproval { approved, reason }` (`ports.rs:274-280`) — the approval-state
  value type.
- `AdvisorService::approval_status` (`notifications/mod.rs:168`) — the deny-all state impl.
- `#[cfg(test)] ApprovingAdvisor::approval_status` (`app_state.rs:609`) — the test
  approval-state fake.

Not introduced (rejected simplification, §9):
- any `QueryContext.advisor_outcome` / cached verdict. **No advisor field is added to
  `QueryContext`** — the loop holds no advisor state.

After removal, the only advisor inputs are (1) the durable transcript (read by the hook and
by `run_advisor`) and (2) the live `ask_advisor` run. No engine/agent object holds an
advisor verdict; the verdict exists only as a `submit_advisor_feedback` result block in the
transcript, re-derived on demand by the hook.

---

## 3. What stays exactly as-is

- **Gated set / wiring.** `meta::tool_hooks` already wires `Hook::AdvisorApproval` on the
  four `submit_*_outcome` arms (root `:72-75`, generator+reducer `:76-79`, planner
  `:80-84`) and nowhere else (`:86`). Keep root (constraint 4 / resolves D2). No rewiring;
  this is the per-tool prehooks list (constraint 3).
- **Per-terminal advisor hint already exists.** `TerminalDescriptor.advisor_review_focus`
  (`terminal.rs:80`, full text `:91-118`) is ported for all six terminals — the parity of
  Python `TerminalToolDescriptor.advisor_review_focus`. The only gap is the *consumer*:
  no Rust `render_terminal_catalog(focus="advisor_review_focus")` exists yet;
  `build_advisor_user_msg_2` (§2d/§8) renders it into the advisor's `user_msg_2`.
- Hook ordering (`RequireNoInflightBackgroundTasks` first; `DisallowNestedPlannerDeferral`
  on planner; then `AdvisorApproval`).
- `submit_advisor_feedback` already emits `{helper_role:"advisor", verdict}`
  (`submission.rs:529-530`).
- Document the root-gating divergence in the architecture bundle
  (`docs/architecture/workflow/terminal-tools.html`, `.../tools/ask-helper.html`) — both
  currently call the advisor purely advisory (advisor.md E7).

---

## 4. Verification

- **End-to-end, non-injected root run:** root calls `ask_advisor` → the engine runs a
  real advisor → `approve` lands in the transcript → `submit_root_outcome` passes, with
  **no** `#[cfg(test)]` fake. (The deletion of `ApprovingAdvisor` is part of this.)
- **Negative paths (hook-local):** each reason tag blocks — `missing`, `rejected`,
  `wrong_tool`, `unpaired`, `advisor_failed`, `structural`. Port the intent of
  `test_advisor_gate_negative_path.py`.
- **Wiring contract:** port `test_advisor_gate_wiring.py` — the four terminals carry
  exactly one `AdvisorApproval` hook; helper/explorer carry none. (Rust adds root vs.
  Python; the ported test reflects that intended difference.)
- **ask_advisor result:** carries `helper_role`/`verdict`/`is_error` from the advisor
  terminal (built by the engine).
- Reconcile `eos-runtime/src/tests.rs` `successful_root_keeps_engine_terminal` and
  `root_terminal_blocked_without_advisor_approval`: the "blocked" case now blocks via the
  hook's `missing` classify (no advisor exchange in the transcript); the "success" case
  drives a real advisor turn that returns `approve` (no injected port).

---

## 5. Coordination / sequencing

- **Concurrent refactor:** `AdvisorService` is in `eos-engine/src/notifications/mod.rs`,
  under active split (`notifications/rules/`). Deleting it is compatible; rebase, don't
  stomp the `rules/` work.
- **Order:** (1) relocate `run_ephemeral_agent` to `eos-engine`, signature off `AppState`;
  (2) thread conversation into `ctx` + rewrite the hook (gate becomes real, denies until a
  runner exists); (3) engine-dispatch the advisor for `ask_advisor`; (4) delete the port /
  `AdvisorService` / `RuntimeAdvisor` / `ApprovingAdvisor`. Steps 1–3 make `approve`
  reachable before 4 removes the test fake.
- Phase-1 hard-gate `advisor` lane in `REPORT.md`; parallels `subagent ⊕ query_engine`,
  `attempt_harness`, `request_completion`.

---

## 6. Advisor mechanism — workflow

```
TWO FLOWS OVER ONE SHARED PARENT TRANSCRIPT — no AdvisorPort, no AppState in the path

Flow A — ask_advisor  (the ONLY launch; engine-driven run_ephemeral_agent)
──────────────────────────────────────────────────────────────────────────
main agent (root | planner | generator | reducer)
  │ ask_advisor(tool_name="submit_X_outcome", tool_payload={...})
  ▼
[eos-engine]  dispatch_assistant_tools(ctx, calls, messages)
  │ sees ToolName::AskAdvisor → run advisor inline (NOT a normal executor call):
  │   1. resolve "advisor" AgentDefinition from the engine's AgentRegistry
  │   2. user_msg_1 = injection-guard + parent user_msg_1 + user_msg_2 + transcript[2:]
  │      user_msg_2 = terminal catalog (advisor focus) + pending submission + task/calibration
  │      (built from the live `messages`)
  │   3. run_ephemeral_agent(handles, advisor_def, [user_msg_1]+user_msg_2,
  │                          persist_agent_run = false)        ← same engine loop
  │         └─ advisor agent calls submit_advisor_feedback(verdict, summary)
  │            (NOT advisor-gated → no deadlock) → terminal_result
  │   4. tool result = { output: summary, is_error, metadata:{helper_role,verdict} }
  ▼
engine appends the ask_advisor ToolResult block to `messages`
        (carries helper_role + verdict + the ask_advisor tool_use_id)   ← D3 by construction

Flow B — terminal submission gate  (self-contained pre-hook; verbatim Python)
──────────────────────────────────────────────────────────────────────────────
main agent calls submit_X_outcome(...)        X ∈ { root | planner | generator | reducer }
  ▼
[eos-engine]  dispatch_assistant_tools(ctx, calls, messages)
  │ metadata_for_call stamps  ctx.conversation = Arc<[messages snapshot]>
  ▼
[eos-tools]  execute_tool_once → for &hook in &tool.hooks   (list from meta::tool_hooks)
  │   RequireNoInflightBackgroundTasks → [DisallowNestedPlannerDeferral, planner only] →
  ▼   Hook::AdvisorApproval { tool }
run_advisor_approval(tool, ctx)        ← reads ctx.conversation only; NO port
  │ find_latest_advisor_pair(messages) ; find_originating_ask_advisor(messages, tool_use_id)
  │ classify(result, originating, target = tool):
  │     None → "missing" | is_error → "advisor_failed" | verdict∉{approve,reject} → "structural"
  │     verdict=="reject" → "rejected" | originating None → "unpaired"
  │     originating.tool_name≠target → "wrong_tool" | else → PASS
  ▼
 PASS → run submit_X_outcome body → stamp is_terminal
 DENY → HookDenial{ message:_MSG_BLOCKED, policy:"advisor_approval", reason:<tag> }
        → in-band error, terminal NOT stamped → agent must re-ask_advisor and resubmit
```

---

## 7. Resulting file / folder structure

```
agent-core/crates/
├─ eos-tools/src/
│   ├─ ports.rs            EDIT  DELETE trait AdvisorPort + structs AdvisorApproval/AdvisorReview
│   ├─ metadata.rs         EDIT  - DELETE field `advisor` + require_advisor()
│   │                            + ADD field `conversation: Arc<[Message]>`; fix doc-comment
│   ├─ hooks.rs            EDIT  module ROOT only: keep Hook enum / HookOutcome / HookDenial /
│   │                            hook_failure_result / Hook::run dispatch; add `mod advisor_approval;`
│   │                            + re-export; DELETE the old inline run_advisor_approval body
│   ├─ hooks/              (dir already exists, empty — parallel split in progress)
│   │   └─ advisor_approval.rs  NEW  the advisor gate; port of tools/_hooks/advisor_approval.py:
│   │                            run_advisor_approval + find_latest_advisor_pair +
│   │                            find_originating_ask_advisor + classify_advisor_approval + consts
│   ├─ terminal.rs         KEEP  TerminalDescriptor.advisor_review_focus (per-terminal advisor hint;
│   │                            already ported for all 6 terminals) + ADD render_terminal_catalog
│   ├─ meta.rs             KEEP  tool_hooks: Hook::AdvisorApproval on the 4 submit_*_outcome arms
│   └─ model_tools/
│       └─ advisor.rs      EDIT  keep ask_advisor name/spec/description registration;
│                                DELETE the executor body that called the port
├─ eos-engine/src/
│   ├─ agent_loop.rs       NEW   run_ephemeral_agent + EphemeralRunInput/EphemeralRun
│   │                            (RELOCATED from eos-runtime; no &AppState — explicit handles)
│   ├─ advisor.rs          NEW   build_advisor_user_msg_1/_2 + run_advisor (drives the loop)
│   ├─ query/loop_.rs      EDIT  pass &messages into dispatch_assistant_tools
│   ├─ tool_call/dispatch.rs  EDIT  intercept ToolName::AskAdvisor → run_advisor;
│   │                               stamp ctx.conversation snapshot in metadata_for_call
│   ├─ query/context.rs    EDIT  QueryContext carries the AgentRegistry + run handles
│   │                            run_ephemeral_agent needs
│   ├─ notifications/mod.rs EDIT  DELETE AdvisorService
│   └─ lib.rs              EDIT  drop AdvisorService + run_ephemeral_agent moves into engine exports
└─ eos-runtime/src/
    ├─ agent_loop.rs       DELETE  (moved to eos-engine)
    ├─ root_agent.rs       EDIT   call eos_engine::run_ephemeral_agent with explicit handles
    ├─ agent_runner.rs     EDIT   same
    ├─ app_state.rs        EDIT   DELETE advisor field/builder/default + ApprovingAdvisor fake;
    │                             drop `use eos_engine::AdvisorService`
    ├─ tool_context.rs     EDIT   stop setting `advisor`; conversation defaults empty
    └─ tests.rs            EDIT   reconcile the two advisor tests to the engine-run seam
```

---

## 8. Class / struct / field names

```rust
// eos-tools/src/metadata.rs
pub struct ExecutionMetadata {
    // ... existing fields ...
    pub conversation: Arc<[eos_llm_client::Message]>, // NEW; port of context.conversation_messages
    // REMOVED: pub advisor: Option<Arc<dyn AdvisorPort>>   (+ require_advisor())
}

// eos-tools/src/ports.rs
// REMOVED entirely: trait AdvisorPort, struct AdvisorApproval, struct AdvisorReview.

// eos-tools/src/hooks.rs  (variant unchanged; logic now self-contained)
Hook::AdvisorApproval { tool: ToolName }
const ADVISOR_HELPER_ROLE: &str = "advisor";
const VALID_VERDICTS: [&str; 2] = ["approve", "reject"];
async fn run_advisor_approval(tool: ToolName, ctx: &ExecutionMetadata) -> Result<HookOutcome, ToolError>;
fn find_latest_advisor_pair(messages: &[Message]) -> (Option<&ContentBlock>, Option<&ContentBlock>);
fn find_originating_ask_advisor<'a>(messages: &'a [Message], tool_use_id: &ToolUseId) -> Option<&'a ContentBlock>;
fn classify_advisor_approval(result: Option<&ContentBlock>, originating: Option<&ContentBlock>, target_tool: ToolName) -> Option<&'static str>; // Some(tag)=deny, None=pass

// eos-engine/src/agent_loop.rs  (relocated; no AppState)
pub struct EphemeralRunInput {
    pub agent: AgentDefinition,
    pub initial_messages: Vec<Message>,
    pub task_id: Option<TaskId>,
    pub agent_run_id: AgentRunId,
    pub tool_metadata: ExecutionMetadata,
    pub persist_agent_run: bool,
}
pub struct EphemeralRun { pub terminal_result: Option<ToolResult>, pub error: Option<String> }
pub async fn run_ephemeral_agent(
    handles: &EngineRunHandles,            // explicit deps (NOT &AppState)
    input: EphemeralRunInput,
    on_event: Option<&EventCallback>,
) -> EphemeralRun;
pub struct EngineRunHandles {
    pub agent_run_store: Arc<dyn AgentRunStore>,
    pub model_store: Arc<dyn ModelStore>,
    pub llm_client: Arc<dyn LlmClient>,
    pub event_source_factory: Option<EventSourceFactory>,
    pub agent_registry: Arc<AgentRegistry>,
    pub cwd: String,
}

// eos-engine/src/advisor.rs  (NEW — drives the advisor run; no port)
const MAX_TRANSCRIPT_MESSAGES: usize = 40;
const MAX_TOOL_RESULT_CHARS: usize = 4096;
const MAX_TRANSCRIPT_BYTES: usize = 24576;
const MAX_BASH_COMMAND_CHARS: usize = 500;
const ADVISOR_STRIP_INPUT_TOOLS: [&str; 3] = ["Edit", "Write", "NotebookEdit"];
fn build_parent_transcript(messages: &[Message]) -> Option<String>;                      // _transcript.py
fn build_advisor_user_msg_1(messages: &[Message]) -> Result<String, AdvisorMessageError>; // guard + verbatim contract + transcript
fn build_advisor_user_msg_2(parent_def: Option<&AgentDefinition>, tool_name: &str, tool_payload: &JsonObject) -> String;
//   build_advisor_user_msg_2 renders the catalog via terminal::render_terminal_catalog(focus = AdvisorReviewFocus)

// eos-tools/src/terminal.rs  (descriptor already has advisor_review_focus; ADD the renderer)
pub enum CatalogFocus { SelectionGuidance, AdvisorReviewFocus }   // port of Python CatalogFocus
pub fn render_terminal_catalog(terminals: &[ToolName], focus: CatalogFocus) -> String; // port of registry.render_terminal_catalog
pub(crate) async fn run_advisor(
    handles: &EngineRunHandles,
    ctx: &ExecutionMetadata,        // parent agent_name, ids, AND sandbox_id (advisor shares the caller's sandbox)
    messages: &[Message],           // live caller transcript (source for user_msg_1 + build_parent_transcript)
    tool_name: &str,
    tool_payload: &JsonObject,
) -> ToolResult;                    // { output, is_error, metadata{helper_role,verdict} }
// run_advisor builds the advisor's ExecutionMetadata with sandbox_id = ctx.sandbox_id,
// then run_ephemeral_agent(handles, EphemeralRunInput{ agent: advisor_def,
//   initial_messages: [user_msg_1, user_msg_2], persist_agent_run: false, .. }).
```

Cycle note: there is **no** `Arc<AppState>` anywhere in the advisor path. `eos-engine`
owns `run_ephemeral_agent`; the registry + run handles ride the engine's own context;
`eos-runtime` only supplies the concrete handles at composition time.

---

## 9. Alternatives considered (rejected)

- **Keep a generic ephemeral-runner port in `ExecutionMetadata`** that `ask_advisor`
  (an `eos-tools` executor) calls. Rejected: it is a port by another name, and the user
  asked to drop the port and call `run_ephemeral_agent` → loop directly. Engine-driven
  dispatch removes the seam entirely.
- **Keep `AdvisorPort::review` / `RuntimeAdvisor` / `AppState`** (the round-2 draft and
  the report's literal D1 fix). Rejected by constraints 1, 5, 6.
- **Literal "no seam" (eos-tools calls the loop directly).** Impossible: `eos-engine`
  depends on `eos-tools`, so the reverse edge is a crate cycle. Driving the run from the
  engine is the faithful equivalent of Python's late `engine.api` import.
- **Cache a typed verdict on `QueryContext` (`advisor_outcome`) instead of scanning the
  transcript.** Considered as a simplification — the engine sets the verdict when it runs
  the advisor, and the hook reads a field (no conversation threading, no reverse-walk).
  **Rejected (constraint 9):** it makes the gate's source of truth a mutable engine
  side-channel rather than the durable transcript, and adds advisor state to the agent
  loop. The stateless transcript-inferring hook is preferred even though its scan code is
  larger than a field read; the transcript stays the single source of truth.
```
