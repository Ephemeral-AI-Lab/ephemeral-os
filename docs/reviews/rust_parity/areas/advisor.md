# Advisor pass-verdict gate before terminal submission — Rust parity audit

Area: Advisor (`ask_advisor` → advisor-approval gate before terminal submission). Domain: agent-core.

Bottom line: the gate **mechanism** (per-terminal pre-hook that calls the advisor port and short-circuits terminal stamping on denial) is correctly ported. But the gate's **decision logic** and the **advisor runner** itself are NOT implemented in production Rust — both are deny/inert stubs that the code self-documents as an "engine-only phase" placeholder. The only approving `AdvisorPort` is a `#[cfg(test)]` fake that approves unconditionally. Consequently, in a default production build, `ask_advisor` never runs an advisor agent, and every advisor-gated terminal is permanently blocked. There is also an intentional, documented divergence from Python ground truth: Rust gates `submit_root_outcome`; Python does not.

---

## Ground truth

Docs (corroboration):
- `docs/architecture/tools/ask-helper.html` §3 "Advisor Flow" (lines 88-98), §4 "Connections" (lines 100-109). Frames the advisor as advisory evidence: "Helper advice is evidence for the parent. It does not directly close the parent Workflow attempt or bypass the parent's terminal submission contract" (line 105). The doc does NOT mention the enforcing pre-hook gate — it is stale on the actual hard-gate behavior.
- `docs/architecture/workflow/terminal-tools.html` §5 "Advisor Before Terminal" (lines 183-200): "Parent decision: retry, fix, or submit parent terminal" (line 191) — again frames it as advisory, omits the `AdvisorApprovalPreHook` gate.

Python (behavioral ground truth):
- `backend/src/tools/ask_helper/ask_advisor/ask_advisor.py` — the `ask_advisor` tool. Blocking read-only helper launch via `run_ephemeral_agent` (lines 184-195). Returns the advisor terminal output + metadata (verdict) to the parent (lines 206-211). Returns an explicit error `ToolResult` on advisor crash / missing terminal (lines 196-205). It does NOT itself block any terminal.
- `backend/src/tools/submission/advisor/submit_advisor_feedback/submit_advisor_feedback.py` — the advisor's own terminal. Input `verdict: Literal["approve","reject"]` + `summary` (lines 18-22). Emits `metadata={"helper_role":"advisor","verdict":verdict}` (lines 41-46). `is_terminal_tool=True`, no advisor pre-hook on itself.
- `backend/src/tools/_hooks/advisor_approval.py` — **THE GATE**. `AdvisorApprovalPreHook(target_tool)` is a per-terminal submission pre-hook (lines 39-89). `run()` scans `context.conversation_messages` for the latest advisor result, classifies, and `HookResult.fail(...)` blocks unless approved (lines 51-64). `_classify` (lines 66-89) pass condition requires ALL of: result present (`else "missing"`), not error (`else "advisor_failed"`), `verdict in {"approve","reject"}` (`else "structural"`), `verdict != "reject"` (`else "rejected"`), originating `ask_advisor` block paired by `tool_use_id` (`else "unpaired"`), and `originating.input["tool_name"] == self.target_tool` (`else "wrong_tool"`). Valid verdicts `_VALID_VERDICTS = frozenset({"approve","reject"})` (line 24). Block message `_MSG_BLOCKED` (lines 32-36).
- Gate wiring (which terminals carry the hook):
  - `backend/src/tools/submission/planner/submit_planner_outcome/submit_planner_outcome.py:66` — `AdvisorApprovalPreHook("submit_planner_outcome")`.
  - `backend/src/tools/submission/generator/submit_generator_outcome/submit_generator_outcome.py:49` — `AdvisorApprovalPreHook("submit_generator_outcome")`.
  - `backend/src/tools/submission/reducer/submit_reducer_outcome/submit_reducer_outcome.py:49` — `AdvisorApprovalPreHook("submit_reducer_outcome")`.
  - `backend/src/tools/submission/root/submit_root_outcome/submit_root_outcome.py:42` — `pre_hooks=(RequireNoInflightBackgroundTasks("submit_root_outcome"),)` — **NO advisor hook**. Root is NOT advisor-gated in Python.
  - Explorer terminal (`submit_exploration_result`) and `submit_advisor_feedback` — no advisor hook (helper/subagent terminals intentionally omit it; `advisor_approval.py` docstring lines 8-11).
- Test fixture confirming the gate's transcript shape: `backend/src/test_runner/agent/mock/_advisor_approval.py` (`build_advisor_approval_messages`, default `verdict="approve"`).

---

## Rust mapping

| Concept | Python anchor | Rust anchor |
| --- | --- | --- |
| `ask_advisor` tool body | `ask_advisor.py:159-211` | `eos-tools/src/model_tools/advisor.rs:33-53` (`AskAdvisor::execute` → `ctx.require_advisor()?.review(...)`) |
| Advisor verdict terminal | `submit_advisor_feedback.py` | `ToolName::SubmitAdvisorFeedback` (registered terminal; `meta.rs:42`) |
| The gate (per-terminal pre-hook) | `advisor_approval.py` `AdvisorApprovalPreHook` | `eos-tools/src/hooks.rs:39` `Hook::AdvisorApproval{tool}`; logic `hooks.rs:574-597` `run_advisor_approval` |
| Gate wiring table | each tool's `@tool(pre_hooks=...)` | `eos-tools/src/meta.rs:58-88` `tool_hooks()` |
| Gate decision logic (`_classify`) | `advisor_approval.py:66-119` (in-hook conversation scan) | **relocated behind the port** → `AdvisorPort::approval_status` (`ports.rs:253`); NO production impl |
| Approval result type | `verdict` string + `_classify` reason tags | `eos-tools/src/ports.rs:234-240` `AdvisorApproval{approved: bool, reason: Option<String>}` |
| Advisor port trait | (implicit) | `eos-tools/src/ports.rs:244-254` `AdvisorPort{review, approval_status}` |
| Production advisor impl | `run_ephemeral_agent` helper run | `eos-engine/src/notifications.rs:210-232` `AdvisorService` — **deny/inert stub** |
| Test advisor impl | `_advisor_approval.py` fixture | `eos-runtime/src/app_state.rs:599-622` `ApprovingAdvisor` — `#[cfg(test)]`, approves unconditionally |
| Default wiring | n/a | `eos-runtime/src/app_state.rs:482-484` `advisor.unwrap_or_else(|| Arc::new(AdvisorService))` |
| Block message text | `advisor_approval.py:32-36` | `eos-tools/src/hooks.rs:451-454` `ADVISOR_APPROVAL_MESSAGE_PREFIX/SUFFIX` |
| Pre-hook before terminal stamp | framework execution pipeline | `eos-tools/src/execution.rs:58` (Deny short-circuits) → `:71` (`stamp_terminal`) |

Key structural divergence: Python performs the verdict check + reverse-walk conversation pairing **inside the hook** (`advisor_approval.py`). Rust's `run_advisor_approval` does NOT scan the conversation — it delegates the entire decision to `AdvisorPort::approval_status(tool)` (`hooks.rs:587`). The classification logic was moved *behind the port contract*; `ports.rs:230-233` documents the six reason tags the impl is meant to produce (`missing/advisor_failed/structural/rejected/unpaired/wrong_tool`), but the only production impl emits `"missing"` unconditionally.

---

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | Root AND workflow agents must call `ask_advisor` to choose the terminal payload | divergent | medium | root NOT gated (`submit_root_outcome.py:42`); planner/generator/reducer gated (`submit_planner_outcome.py:66`, `submit_generator_outcome.py:49`, `submit_reducer_outcome.py:49`) | root gated + workflow gated (`meta.rs:72-84`) | Checklist expects root gated; Python ground truth does NOT gate root. Rust DOES gate root (intentional, documented divergence `meta.rs:68-71`). Three-way disagreement → counted as divergence from ground truth. See D2. |
| 2 | A PASS verdict must be received before terminal submission is allowed | partial | high | `advisor_approval.py:80-89` (`verdict=="approve"` + tool-pair) | mechanism `hooks.rs:574-597`; decision via `approval_status` (`ports.rs:253`) — production impl always denies (`notifications.rs:226-231`) | Gate plumbing present; production decision logic is a deny-all stub. No production path can ever return approved. See D1. |
| 3 | A non-pass verdict BLOCKS terminal submission (enforced, not advisory) | match | (info) | `advisor_approval.py:61-64` `HookResult.fail`; framework converts to error → not stamped terminal | `hooks.rs:588-596` `HookOutcome::Deny`; `execution.rs:58` Deny short-circuits before `stamp_terminal` (`:71`) | Gate IS enforced (pre-hook short-circuits terminal stamping). Mechanism is a real MATCH. |
| 4 | Which roles are gated is correct (subagents/helpers excluded) | match | (info) | helper/subagent terminals omit hook (`advisor_approval.py:8-11`; `submit_advisor_feedback.py` has none; explorer has none) | `SubmitAdvisorFeedback` / `SubmitExplorationResult` fall into `_ => Vec::new()` (`meta.rs:86`) | Helper + explorer terminals are not advisor-gated on either side. Clean bilateral match. |

Extra constant comparison (task: ">= vs >" / literal values): Python compares `verdict == "approve"` against `_VALID_VERDICTS = frozenset({"approve","reject"})` (`advisor_approval.py:24,83`). Rust has **no verdict literal** — the verdict comparison is collapsed into `AdvisorApproval{approved: bool}` decided behind the port; there is no `"approve"`/`"reject"` string compared anywhere in production Rust gate code. The reason-tag taxonomy is documented in `ports.rs:230-233` but only `"missing"` is ever emitted (`notifications.rs:229`). So the literal-verdict comparison the checklist asks to compare has no production Rust analog.

---

## Disparities

### D1 — Advisor decision logic + advisor runner are non-functional stubs in production (severity: HIGH)
Evidence:
- `eos-runtime/src/app_state.rs:482-484` — default wiring: `advisor: self.advisor.unwrap_or_else(|| Arc::new(AdvisorService))`.
- `eos-engine/src/notifications.rs:226-231` — `AdvisorService::approval_status` returns `AdvisorApproval{approved: false, reason: Some("missing")}` for EVERY tool, unconditionally.
- `eos-engine/src/notifications.rs:216-224` — `AdvisorService::review` returns the literal string `"Advisor runner is not wired for `{tool_name}` in this engine-only phase."` — `ask_advisor` never launches an advisor agent.
- Only approving impl: `eos-runtime/src/app_state.rs:599-622` `ApprovingAdvisor`, gated by `#[cfg(test)]` (module header `app_state.rs:558-560` "Shared test fakes ... `#[cfg(test)]` only"). It returns `approved: true` with no verdict check, no conversation scan, no tool-name pairing.
- Exhaustive impl search returned exactly two `impl AdvisorPort`: the deny stub and the test fake. No production conversation-scanning impl exists.

Why it matters: The core advisor dynamic is inert in production. (a) `ask_advisor` cannot produce a real audit — it returns a fixed "not wired" string. (b) `approval_status` denies everything, so under the default build EVERY advisor-gated terminal (`submit_planner_outcome`, `submit_generator_outcome`, `submit_reducer_outcome`, and — see D2 — `submit_root_outcome`) is permanently blocked; no agent can ever submit a gated terminal. This is the Python `_classify` logic (`advisor_approval.py:66-119`: latest-advisor reverse-walk, verdict check, `tool_use_id` pairing, `tool_name` match) with NO production Rust counterpart — the logic was relocated behind `AdvisorPort::approval_status` and left unimplemented.

Mitigating context (intentional vs. silent gap): this is a **documented, intentional phase gap, not a silent miss**. The code self-documents it: `notifications.rs:208-209` "Minimal advisor port implementation used until `eos-runtime` wires a helper runner around the engine loop"; `notifications.rs:222` "engine-only phase"; `app_state.rs:260-262` "The stub denies every terminal, so a real `AdvisorPort` is required for any advisor-gated terminal ... to pass". So the missing piece is the in-progress real `AdvisorPort` implementation, not a regression in already-ported code.

Suggested fix: implement a production `AdvisorPort` in `eos-runtime` that (1) `review` launches the advisor ephemeral agent via the existing `run_ephemeral_agent` (`eos-runtime/src/agent_loop.rs:49`) with `role="advisor"`, and (2) `approval_status` reproduces Python `_classify`: reverse-walk the engine-owned conversation for the latest `helper_role=="advisor"` result block, require not-error + `verdict=="approve"` + originating `ask_advisor` block paired by `tool_use_id` + `originating.input["tool_name"] == target_tool`, returning the matching reason tag otherwise. Until then, treat invariants #1/#2 as not-yet-met in production.

### D2 — Rust gates `submit_root_outcome`; Python does not (severity: MEDIUM, intentional)
Evidence:
- Python: `submit_root_outcome.py:42` carries ONLY `RequireNoInflightBackgroundTasks` — no advisor hook. Only planner/generator/reducer import `AdvisorApprovalPreHook`.
- Rust: `meta.rs:72-75` wires `Hook::AdvisorApproval{tool}` onto `T::SubmitRootOutcome`, with an explicit comment (`meta.rs:68-71`): "EOS decision: the root terminal is advisor-gated too. This diverges from the Python backend, which gates only the planner/generator/reducer main-role terminals and intentionally omits root."

Why it matters: This is a deliberate behavioral change to the gating set, not a port bug — but it diverges from the stated ground truth (Python). Blast radius: combined with D1's deny-all stub, root can NEVER complete in a default production build. `eos-runtime/src/tests.rs:208-238` (`root_terminal_blocked_without_advisor_approval`) asserts exactly this: a well-formed `submit_root_outcome` under the default `AdvisorService` fails the request with `fail_reason="root_run_exhausted"`. The companion test `successful_root_keeps_engine_terminal` (`tests.rs:166-200`) only passes because it injects the `ApprovingAdvisor` test fake. The Python ground-truth behavior (root completes without any advisor) is unreachable in Rust.

Suggested fix: confirm with owners whether root-gating is the intended final behavior. If yes, the divergence is acceptable but should be reflected in the architecture docs (currently silent) and the Python-vs-Rust parity ledger. If parity with Python is required, drop `Hook::AdvisorApproval` from the `SubmitRootOutcome` arm in `meta.rs:72-75`.

### D3 — `ask_advisor` tool result drops `is_error` / verdict-metadata passthrough (severity: LOW, latent until D1 fixed)
Evidence:
- Python `ask_advisor.py:206-211` forwards the advisor terminal's `output`, `is_error`, and `metadata` (carrying `helper_role`/`verdict`) to the parent tool result. This metadata is what the gate's `_classify` later reads off the conversation.
- Rust `advisor.rs:47-51`: `let output = ctx.require_advisor()?.review(...).await?; Ok(ToolResult::ok(output))` — wraps the bare `String` return of `review()` as a non-error text result with EMPTY metadata. No `is_error` projection, no `helper_role`/`verdict` metadata.

Why it matters: Moot while `review` is stubbed (D1). Once a real advisor runner is wired, the Rust gate's `approval_status` must recover the verdict from the conversation; if the `ask_advisor` tool result carries no `helper_role`/`verdict` metadata, a conversation-scanning `approval_status` (the Python design) would have nothing to match against. The contract divergence (`review` returns `String`, not a structured result with metadata) needs resolving alongside D1.

Suggested fix: when wiring the real `review`, return a structured result (output + is_error + verdict metadata) and have `AskAdvisor::execute` project `helper_role="advisor"` + `verdict` into the tool result metadata, mirroring `ask_advisor.py:206-211`, so the engine-owned transcript carries what `approval_status` needs.

---

## Extra findings

- E1 (match, positive): The Rust `ask_advisor` description (`eos-tools/src/model_tools/descriptions/ask_advisor.md`) is a faithful port of the Python prompt (`backend/src/tools/ask_helper/ask_advisor/prompt.py`) — including the lenient approve-bar wording and "Because every terminal requires a prior advisor approval, terminal submission is impossible while isolated". Note: that prompt sentence is slightly inaccurate for Python (root is not gated), but it is actually MORE accurate for Rust (root IS gated, D2).
- E2 (match): Block message text parity. Rust `hooks.rs:451-454` reconstructs Python's `_MSG_BLOCKED` (`advisor_approval.py:32-36`) verbatim ("BLOCKED: You must get approval from advisor before submitting this terminal. Call ask_advisor(tool_name=\"...\", tool_payload=...) and resubmit only after the advisor returns verdict=\"approve\".").
- E3 (match): Hook ordering preserved. Python keeps `RequireNoInflightBackgroundTasks` before the advisor gate on root; Rust `meta.rs:55-56,72-84` documents and preserves "RequireNoInflight ... first so a background rejection surfaces before the advisor gate". Planner also keeps `DisallowNestedPlannerDeferral` between them (`meta.rs:80-84`).
- E4 (match): `BlockInIsolatedMode` on `ask_advisor`. Python `ask_advisor.py:157` `pre_hooks=(BlockInIsolatedMode("ask_advisor"),)`; Rust `meta.rs:85` `T::AskAdvisor => vec![Hook::BlockInIsolatedMode{tool: name}]`.
- E5 (match): `ask_advisor` intent `READ_ONLY` on both sides — Python `ask_advisor.py:156` `intent=Intent.READ_ONLY`; Rust `meta.rs:34,43` `T::AskAdvisor` → `ToolIntent::ReadOnly`.
- E6 (doc inconsistency, trivial): `ports.rs:242` says the advisor runner is "Implemented by `eos-engine`", but `notifications.rs:207-208` (the eos-engine stub) says the real runner will live in `eos-runtime`. Cosmetic; resolve when D1 lands.
- E7 (docs stale): Both architecture pages (`ask-helper.html`, `terminal-tools.html`) describe the advisor as purely advisory ("Parent decision: retry, fix, or submit") and omit the enforcing `AdvisorApprovalPreHook` gate entirely. The hard-gate behavior is undocumented in the architecture bundle on BOTH the Python and Rust sides. This is a docs gap, not a Rust gap.

---

## Open questions

1. Is root-gating (D2) the intended final EOS behavior, or a temporary artifact? The `meta.rs` comment frames it as an "EOS decision" but the architecture docs do not reflect it. This determines whether D2 is "acceptable divergence" or "regression to fix".
2. Where is the real `AdvisorPort::approval_status` slated to live — does it scan the engine-owned conversation (Python design) or carry server-side approval state? `ports.rs:251-253` says "the implementor inspects the engine-owned conversation history", which implies the Python reverse-walk design; D3 must be resolved so the transcript carries the verdict metadata that scan needs.
3. Is there an in-flight branch/PR wiring the real advisor runner? The stubs are explicitly labeled "engine-only phase"; this audit reflects the current `main` worktree only. If the real impl lands elsewhere, D1's severity drops to "phase-gated, expected".
