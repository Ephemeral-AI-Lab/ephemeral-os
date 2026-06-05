# Rust parity audit — Budget notifications (75/100/125 notify, 150 fail) + premature non-terminal reminder retry

Area key: `budget_notifications` · Domain: `agent-core`
Audited against Python ground truth at HEAD on 2026-06-03.

## Ground truth

### Notification rules (Python)
- `backend/src/notification/rules/terminal_tool_call_count_reminder.py`
  - `_TOOL_CALL_BUDGET_TIERS` (lines 14-18): `(("75%", 3, 4), ("100%", 1, 1), ("125%", 5, 4))`.
  - `_ceil_ratio(value, num, den) = (value*num + den - 1)//den` (lines 21-22) — integer ceiling of `value*num/den`.
  - Trigger (lines 32-35): `threshold = _ceil_ratio(tool_call_limit, num, den)`; fires iff `terminal_result is None and tool_calls_used >= threshold`. Comparison is `>=`.
  - Body (lines 37-48): `ceiling = math.ceil(1.5 * limit)`, `turns_remaining = max(0, ceiling - used)`; rule name `tool_call_budget_{label_without_%}_percent` (line 30).
  - Default `fire_once=True` (from `NotificationRule` dataclass default, `model.py:42`) — budget tiers fire once.
- `backend/src/notification/rules/must_submit_terminal_tool.py` (`make_terminal_call_reminder`)
  - Trigger (lines 17-21): `terminal_result is None and any(m.role == "assistant" for m in messages)`.
  - Body (lines 23-35): same `ceiling = math.ceil(1.5*limit)` / `turns_remaining = max(0, ceiling - used)`; sorted terminal-tool names.
  - `fire_once=False` (line 41) — repeats every turn until a terminal submission.
- `backend/src/notification/rules/dispatch.py:29-38` — evaluate rules in list order; skip `fire_once` rules already in `notification_fired`; skip empty body; `notify_system(text)`; `fired.add(rule.name)`.
- Default rule assembly: `backend/src/engine/agent/factory.py:266-287` (`_attach_default_notification_rules`) appends the three budget tiers then the terminal-call reminder, **deduped by `rule.name`**, after profile-resolved `notification_triggers` (factory.py:382-387). The whole list lands on `QueryContext.notification_rules` (factory.py:400).

### Loop budget enforcement (Python)
- `backend/src/engine/query/loop.py`
  - `terminal_submission_failed` (lines 41-47): `tool_calls_used + text_only_no_terminal_turns >= math.ceil(1.5 * tool_call_limit)`. Note the gate base is the **sum** of tool calls and text-only no-terminal turns; comparison `>=`.
  - `_terminal_not_submitted_message` (lines 50-57): `"Agent stopped: terminal tool not submitted. tool_calls_used=…, text_only_no_terminal_turns=…, tool_call_limit=…, hard_ceiling=…."`.
  - Per-iteration order (lines 232-328): (1) drain background completion notifications, (2) `dispatch_rules` → `pop_pending_notifications` appended as a user `Message` (lines 242-251) **before** building the provider request, (3) stream + append assistant, (4) dispatch tools / set `terminal_result`, (5) if `terminal_result` → `TOOL_STOP`, break (303-310), (6) if no `tool_uses` → `text_only_no_terminal_turns += 1` (311-312), (7) if `terminal_submission_failed` → emit `ToolExecutionCompletedEvent(tool_name="", is_error=True)`, set `exit_reason = TERMINAL_NOT_SUBMITTED`, break (313-327). Otherwise loop; the terminal-call reminder fires on the next iteration (line 328 comment).
- Tool-call counting (each tool counted exactly once):
  - `backend/src/engine/query/loop.py:199` counts on `ToolUseDeltaEvent` via `_count_tool_dispatch`.
  - `backend/src/tools/_framework/execution/tool_call.py:34-41,80-81` counts again at execution when `consume_budget=True`.
  - `backend/src/engine/tool_call/dispatch.py:479,522`: `consume_budget = tool_use_id not in streamed_tool_use_ids` — so streamed tools count at stream time, non-streamed tools count at execution; never double-counted.

### Docs corroboration
- `docs/architecture/agent_loops/notifications-messages.html` §Notification Runtime (lines 104-113): rules evaluated "at the top of each provider turn, before the next request is built"; "one-shot tool-call budget warnings at 75%, 100%, and 125% of `tool_call_limit`, plus the repeating terminal-call reminder … before the 150% hard ceiling"; pending blocks appended as a user message; list-order dispatch where a later rule cannot see an earlier rule's reminder in the same pass.

## Rust mapping

- `agent-core/crates/eos-engine/src/notifications.rs`
  - `NotificationRule` enum: `TerminalCallReminder` and `ToolCallBudget { label, numerator, denominator }` (lines 28-41).
  - `name()` (46-53), `fire_once()` → true only for `ToolCallBudget` (57-59).
  - `trigger()` (63-87): global gate `if terminal_result.is_some_and(|r| r.is_terminal) { return false }`; `TerminalCallReminder` needs a non-empty `terminal_tools` and an assistant message; `ToolCallBudget` fires iff `tool_calls_used * denominator >= tool_call_limit * numerator` (and guards `limit==0`/`den==0`).
  - `body()` (91-124): `ceiling = limit.saturating_mul(3).div_ceil(2)`; `turns_remaining = ceiling.saturating_sub(used)`; matching body strings.
  - `make_default_notification_rules()` (129-153): `[75% (3,4), 100% (1,1), 125% (5,4), TerminalCallReminder]`, deduped by `name()` via a `BTreeSet` filter.
  - `dispatch_rules()` (157-176): list order; skip `fire_once` rules already in `notification_fired`; record fired; build `SystemNotification`.
- `agent-core/crates/eos-engine/src/query/loop_.rs`
  - `terminal_submission_failed` (24-29): `ceiling = (limit*3 + 1)/2`; `tool_calls_used + text_only_no_terminal_turns >= ceiling`.
  - `terminal_not_submitted_message` (31-37): `"The agent used {N} tool calls/text-only turns without submitting a terminal tool. Submit one of the terminal tools to finish the run."`.
  - `terminal_not_submitted_event` (84-96): sets `exit_reason = TerminalNotSubmitted`; `ToolExecutionCompleted { tool_name: "", is_error: true, is_terminal: false }`.
  - Loop (101-208): (1) **top-of-loop** `terminal_submission_failed` → break (103-106), (2) `dispatch_rules` → emit stream `SystemNotification` events and append a user `Message` with `SystemNotification` blocks (108-117), (3) build request + stream; count `tool_calls_used` on `ToolUseDelta` (139-143); (4) append assistant; reconcile non-streamed tool_uses into the counter (170-174); (5) if no tool_uses → `text_only_no_terminal_turns += 1`, then `terminal_submission_failed` → break (177-185); (6) dispatch tools; if `terminal_result.is_terminal` → `exit_reason = ToolStop`, break (199-206).
- `agent-core/crates/eos-engine/src/agent/factory.rs:144`: `notification_rules: make_default_notification_rules()` (unconditional). Test at 248 asserts `len() == 4`.
- `agent-core/crates/eos-tools/src/registry.rs`: thin insertion-ordered registry; terminal flag lives on `RegisteredTool.is_terminal` (`executor.rs:44`). Terminal set derived in factory.rs:87-95 from `agent.terminals`, validated against registry `is_terminal`. No budget logic in registry.rs.

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | Budget warnings at 75/100/125% of `tool_call_limit`; base = configured limit | **match** | none | `terminal_tool_call_count_reminder.py:14-18,21-22,32-35` | `notifications.rs:130-145,83-84` | Tiers `(3,4)/(1,1)/(5,4)`. Rust `used*den >= limit*num` ≡ Python `used >= ceil(limit*num/den)` for all three tiers (proven below). Base is `tool_call_limit` on both. |
| 2 | Hard FAILURE at `ceil(1.5*limit)`, `>=`, fails (not warns) | **match** | none | `loop.py:41-47` | `loop_.rs:24-29,84-96` | `(3L+1)/2 == ceil(3L/2) == ceil(1.5L)`; both `>=`; both base on `tool_calls_used + text_only_no_terminal_turns`; both set `exit_reason=TerminalNotSubmitted` + `is_error=true` and break. |
| 3 | Premature non-terminal end → system reminder to submit terminal tool | **match** | none | `must_submit_terminal_tool.py:14-42`; `loop.py:328` | `notifications.rs:71-74,101-114`; `loop_.rs:108-117` | Rust reminder gated on non-empty `terminal_tools` + an assistant message in transcript; same body; `fire_once()==false` so it repeats. |
| 4 | Keep re-prompting until valid terminal submission OR 150% failure | **match** | none | `loop.py:303-328` | `loop_.rs:177-206,103-106,180-183` | Loop continues; exits only on `ToolStop` (terminal) or `TerminalNotSubmitted` (ceiling). Reminder re-appended each turn. |
| 5 | Default rule set + dedupe-by-name matches Python (budget tiers + terminal reminder) | **match** (in-area); see #disparity D1 for the broader merge path | low | `factory.py:266-287` | `notifications.rs:129-153` | For an empty trigger list the produced set is identical: 3 budget tiers (fire-once) + 1 repeating reminder, deduped by name. Rust dedupes its own 4 rules. The Python *profile-trigger merge* path is absent in Rust (D1) but is out of the budget/terminal scope. |

### Off-by-one proof for invariant 1 (Rust vs Python trigger equivalence)
Python fires when `used >= ceil(limit*num/den)`. Rust fires when `used*den >= limit*num`, i.e. `used >= (limit*num)/den` over the rationals, i.e. `used >= ceil(limit*num/den)` over the integers (since `used` is an integer). The two are identical for every `(num,den)` in `{(3,4),(1,1),(5,4)}` and every integer `limit`. Verified per tier:
- 75%: Python `(3L+3)//4`; Rust `4*used >= 3L` ⇔ `used >= ceil(3L/4) = (3L+3)//4`. ✓
- 100%: both `used >= L`. ✓
- 125%: Python `(5L+3)//4`; Rust `4*used >= 5L` ⇔ `used >= ceil(5L/4) = (5L+3)//4`. ✓

## Disparities

### D1 — Rust drops profile `notification_triggers`; no `resolve_harness_notification_triggers` merge path (accepted deferral)
- Status: **deferred by scope decision** · Severity: **low** (for THIS area; medium as a general parity item)
- Python evidence: `backend/src/engine/agent/factory.py:382-387` extends `notification_rules` with `resolve_harness_notification_triggers(agent_def.notification_triggers)` **before** `_attach_default_notification_rules`, whose dedupe-by-name (factory.py:281-287) exists precisely so a profile-supplied rule with the same name wins over the default. The planner profile sets `notification_triggers: [nested_planner_deferral_disabled]` (`backend/src/agents/profile/main/planner.md:16-17`), which resolves to `make_nested_planner_deferral_disabled_reminder` (`backend/src/tools/submission/notification_triggers/__init__.py:11-23`).
- Rust evidence: `agent-core/crates/eos-engine/src/agent/factory.rs:144` sets `notification_rules: make_default_notification_rules()` unconditionally; `agent.notification_triggers` is parsed and stored (`eos-agent-def/src/model.rs:192,230-245`) but **never consumed** by `build_query_context` — confirmed by `grep -rn "notification_triggers" agent-core/crates/eos-engine/src` (only the test-fixture `Vec::new()` at factory.rs:222) and the absence of any `resolve_harness_notification_triggers` symbol in `agent-core/crates`.
- Why it matters: the planner agent loses its `nested_planner_deferral_disabled` reminder. This does NOT affect the budget tiers or the terminal reminder (those names don't collide with the planner trigger), so invariant 5's *default* set is still correct. But the dedupe-by-name *mechanism* the invariant references has no profile input to dedupe against in Rust — the merge path is simply missing.
- Scope decision: profile `notification_triggers` are intentionally ignored for now. If this becomes active later, thread `agent.notification_triggers` into `build_query_context`, resolve each id to a `NotificationRule`, prepend before `make_default_notification_rules()`, and dedupe by `name()` (default appended only if name absent), mirroring `_attach_default_notification_rules`.

### D2 — Failure message text diverges; diagnostic fields dropped
- Status: **divergent** · Severity: **low**
- Python evidence: `backend/src/engine/query/loop.py:50-57` — `"Agent stopped: terminal tool not submitted. tool_calls_used={…}, text_only_no_terminal_turns={…}, tool_call_limit={…}, hard_ceiling={ceil(1.5*limit)}."` (four labeled diagnostic fields).
- Rust evidence: `agent-core/crates/eos-engine/src/query/loop_.rs:31-37` — `"The agent used {tool_calls_used + text_only_no_terminal_turns} tool calls/text-only turns without submitting a terminal tool. Submit one of the terminal tools to finish the run."` (single collapsed sum, no limit/ceiling fields).
- Why it matters: behavior is equivalent (both set `exit_reason=TerminalNotSubmitted`, `is_error=true`, empty `tool_name`, and break), but the persisted/streamed failure string differs. Anything asserting on the Python message substring (e.g. `tool_call_limit=`, `hard_ceiling=`) or parsing those fields for diagnostics will not match. The Rust loop test only checks for `"without submitting a terminal tool"` (loop_.rs:323-325).
- Suggested fix: if cross-impl string parity is desired, emit the four labeled fields. Otherwise document the intentional message change.

### D3 — Failure-check placement: Rust checks at top of loop, Python at bottom
- Status: **divergent** · Severity: **low** (unreachable in normal flow)
- Python evidence: `backend/src/engine/query/loop.py:313-327` — `terminal_submission_failed` is checked at the **bottom** of each iteration, after at least one full dispatch+turn.
- Rust evidence: `agent-core/crates/eos-engine/src/query/loop_.rs:103-106` — checked at the **top** of each iteration (before `dispatch_rules`/request build), plus a redundant bottom check on the text-only path (loop_.rs:180-183).
- Why it matters: For a fresh run (factory starts `tool_calls_used=0`, `text_only_no_terminal_turns=0`), the two are behaviorally equivalent — the Rust top-of-loop check only triggers after a prior iteration already pushed the counters over the ceiling, so it adds no extra provider turn and no extra notification; the same 75/100/125 tiers and terminal reminders fire, and the failure event carries the same counts. The ONLY divergence is a seeded/resumed `QueryContext` that *enters* already at/over the ceiling: Rust fails immediately at the top; Python would grant one more `dispatch_rules`+provider turn first. That entry state is unreachable from `build_query_context` (both impls start at 0), so this is a latent edge-case divergence, not a live bug.
- Suggested fix: none required; optionally move the Rust check to the bottom to mirror Python exactly and drop the redundant top check.

## Extra findings

- **Tool-call counting parity is correct (not a divergence).** Rust counts on `ToolUseDelta` (loop_.rs:139-143) and reconciles non-streamed tool_uses from the final message (loop_.rs:170-174). Python counts on `ToolUseDeltaEvent` (loop.py:199) and at execution with `consume_budget = tool_use_id not in streamed_tool_use_ids` (dispatch.py:479,522). Both count each tool exactly once whether or not it streamed, so the budget threshold base `tool_calls_used` evolves identically. **Match.**
- **`terminal_result` gate equivalence.** Rust's trigger gate `terminal_result.is_some_and(|r| r.is_terminal)` (notifications.rs:64) collapses to `is_some()` because `ctx.terminal_result` is only ever assigned from `is_terminal` results (`tool_call/dispatch.rs:279-280,92`). Python gates per-rule on `terminal_result is None` and likewise only stores terminal results (loop.py:298-299). Equivalent.
- **Dual emission parity.** Rust `dispatch_rules` consumers emit both a stream `SystemNotification` event AND append a user `Message` carrying `ContentBlock::SystemNotification` (loop_.rs:108-117), matching Python's stream event + `SystemNotificationBlock` user message (loop.py:242-251). The provider-wire flattening to `<system-reminder>` is a serialization concern outside this area.
- **`ceiling` is computed two different ways in Rust** for the same value: `(L*3 + 1)/2` in the loop gate (loop_.rs:25) and `L.saturating_mul(3).div_ceil(2)` in the rule body (notifications.rs:98). Both equal `ceil(1.5L)`; benign, but a single helper would avoid drift.
- **`make_default_notification_rules` dedupe is effectively a no-op for the fixed set** (all four `name()` values are distinct), so the `BTreeSet` filter (notifications.rs:148-152) never drops a rule. It only matters if the function were extended; harmless today.
- **Saturating arithmetic vs Python bigints.** Rust uses `saturating_mul`/`saturating_add`/`saturating_sub` on `u32`. For realistic `tool_call_limit` and `tool_calls_used` values this never saturates; only a pathological `limit` near `u32::MAX` would clamp where Python would not. Not a practical concern.

## Open questions

1. Profile `notification_triggers` are intentionally deferred for now. The planner's `nested_planner_deferral_disabled` reminder remains the only in-tree non-empty trigger and should be the regression target if this seam is restored later.
2. Is cross-implementation parity of the terminal-not-submitted failure *string* (D2) required by any consumer (test harness, audit parser, UI), or is the collapsed Rust message acceptable?
