# Independent verification — Budget notifications (75/100/125 notify, 150 fail) + premature non-terminal reminder retry

Area: agent-core. Verifier opened every Python and Rust anchor below; constants/operators re-derived from source, not from the investigator's report.

## Constant cross-walk (re-derived)

| Quantity | Python source | Rust source | Equal? |
|---|---|---|---|
| Budget tiers | `_TOOL_CALL_BUDGET_TIERS = (("75%",3,4),("100%",1,1),("125%",5,4))` — `terminal_tool_call_count_reminder.py:14-18` | `ToolCallBudget{label,numerator,denominator}` with `75%→(3,4)`, `100%→(1,1)`, `125%→(5,4)` — `notifications.rs:131-145` | yes |
| Budget threshold test | `used >= _ceil_ratio(limit,num,den)` = `used >= (limit*num+den-1)//den` — `terminal_tool_call_count_reminder.py:21-22,34-35` | `used*den >= limit*num` — `notifications.rs:83-84` | yes — `used >= ceil(limit*num/den)` ⟺ `used*den >= limit*num` for integers |
| Hard-fail ceiling | `ceil(1.5*limit)` — `loop.py:46` | `(3*limit+1)/2` int-div (`mul(3).add(1)/2`) — `loop_.rs:25` | yes — `ceil(3n/2)=floor((3n+1)/2)` ∀ n≥0 (checked n=1,2,3,4,5,7) |
| Hard-fail operator | `>=` — `loop.py:44-47` | `>=` — `loop_.rs:26-28` | yes |
| Hard-fail base | `used + text_only_no_terminal_turns` — `loop.py:45` | `used + text_only_no_terminal_turns` — `loop_.rs:26-28` | yes |
| Body ceiling (display) | `ceil(1.5*limit)` — `terminal_tool_call_count_reminder.py:41`, `must_submit_terminal_tool.py:28` | `limit*3 div_ceil 2` — `notifications.rs:98` | yes |
| Body `turns_remaining` | `max(0, ceiling - used)` using **tool_calls_used only** — `terminal_tool_call_count_reminder.py:42`, `must_submit_terminal_tool.py:29` | `ceiling.saturating_sub(used)` using **tool_calls_used only** — `notifications.rs:99` | yes (both intentionally exclude text-only turns from the *display*, while the *gate* includes them) |
| Budget fire-once | default `fire_once=True` (constructor omits it) — `terminal_tool_call_count_reminder.py:50-54` + `model.py:42` | `fire_once()==true` for `ToolCallBudget` — `notifications.rs:57-59` | yes |
| Terminal reminder fire-once | `fire_once=False` — `must_submit_terminal_tool.py:41` | `fire_once()==false` for `TerminalCallReminder` — `notifications.rs:57-59` | yes |

## Invariant verdicts

### 1. Notifications fire at 75/100/125% of tool_call_limit — **confirmed_match**
- Python `terminal_tool_call_count_reminder.py:14-18,21-22,32-35`: trigger `terminal_result is None and tool_calls_used >= _ceil_ratio(tool_call_limit, num, den)`.
- Rust `notifications.rs:75-85`: same tiers; trigger `tool_calls_used*denominator >= tool_call_limit*numerator` after the terminal-result short-circuit at `:64`. Base is the configured `tool_call_limit` on both sides. The cross-multiplied form is exactly equivalent to Python's ceil form. Labels/names match (`tool_call_budget_75_percent` etc., `name()` at `notifications.rs:49-52`).

### 2. Hard failure at ceil(1.5*limit) — **confirmed_match**
- Python `loop.py:41-47` (`terminal_submission_failed`) + `:313-327` raise `ToolExecutionCompletedEvent(tool_name="", is_error=True)` and set `exit_reason=TERMINAL_NOT_SUBMITTED`. Operator `>=`.
- Rust `loop_.rs:24-29` (`terminal_submission_failed`) + `:84-96`/`:103-106`/`:180-183` emit `ToolExecutionCompleted{tool_name:"", is_error:true, is_terminal:false}` and set `exit_reason=TerminalNotSubmitted`. Operator `>=`. Constant `(3*limit+1)/2` proven identical to `ceil(1.5*limit)`. Crossing FAILS (loop `break`), not merely warns.

### 3. Premature non-terminal end fires reminder — **confirmed_match**
- Python `must_submit_terminal_tool.py:14-42`: trigger `terminal_result is None and any(m.role=="assistant")`; body lists sorted terminal tool names and budget.
- Rust `notifications.rs:71-74` + `:64`: trigger `terminal_result not terminal && !terminal_tools.is_empty() && any assistant message`; body `:101-113` lists sorted terminal names + budget. The extra `!terminal_tools.is_empty()` guard only changes the degenerate empty-terminals case (every real agent has terminals by construction — `AgentDefinition` requires non-empty `terminals`, `model.rs:227-229`). Behaviorally identical for all real agents.

### 4. Retries until valid terminal OR 150% fail — **confirmed_match**
- Python `loop.py:225-328`: `while True`; on a non-terminal turn it falls through to `:328` "Otherwise: loop. terminal_call_reminder fires next iteration." The reminder is `fire_once=False`, so it re-emits every iteration until terminal_result is set (`:303-310` break TOOL_STOP) or the ceiling is hit (`:313-327` break).
- Rust `loop_.rs:100-207`: `loop {}`; top fail-gate `:103-106`, dispatch reminders `:108`, text-only path `:177-184` (increment + re-check), terminal break `:199-206`. `TerminalCallReminder.fire_once()==false` so it re-fires each pass. Same retry-until-terminal-or-fail dynamic.

### 5. Default rule set + dedupe-by-name — **confirmed_match (for the default set)**; profile-merge gap tracked as D1
- Python real assembly site is **`engine/agent/factory.py:382-387`** (NOT `factories.py`, which is re-exports only — the investigator's `factory.py:266-287` cite was for the dedup helper `_attach_default_notification_rules`). Order: profile-resolved triggers first (`:384-386`), then defaults appended with dedup-by-`name` (`:281-287`), budget tiers before `terminal_call_reminder`.
- Rust `notifications.rs:129-153` `make_default_notification_rules`: order `[75%,100%,125%,TerminalCallReminder]`, deduped by `name()` via `BTreeSet`. `factory.rs:144` installs exactly this. Order and dedup of the **defaults** match. Dispatch is list-order on both (`dispatch.py:29-38` / `notifications.rs:157-176`), so budget reminders land before the terminal reminder identically.
- The divergence is only that Rust never merges `agent.notification_triggers` (see D1); for the four default budget/terminal rules the sets are identical.

## Investigator disparity adjudication

### D1 — Rust drops profile `notification_triggers`, no merge path — **CONFIRMED** (severity low *for this area*)
- Python `factory.py:382-387` resolves `agent_def.notification_triggers` via `resolve_harness_notification_triggers` (`tools/submission/notification_triggers/__init__.py:11-23`) and merges them ahead of the defaults, deduping by name.
- Rust `factory.rs:144` hardcodes `make_default_notification_rules()` and never reads `agent.notification_triggers`. The struct field exists (`model.rs:151,192,230-245`) and is parsed, but is consumed nowhere in the engine (only test scaffolding at `factory.rs:222`). No merge path exists.
- Real-world impact: only the **planner** profile populates this field (`planner.md:16-17 → nested_planner_deferral_disabled`); root/executor/reducer are `[]`. The single dropped rule (`nested_planner_deferral_disabled`) is OUTSIDE this area's budget/terminal scope, so none of invariants 1-5 break. It IS a genuine port gap worth a tracking note. Investigator severity "low/divergent" is accurate for this area.

### D2 — Failure message text diverges, structured fields dropped — **CONFIRMED** (severity low)
- Python `loop.py:50-57`: `"Agent stopped: terminal tool not submitted. tool_calls_used=…, text_only_no_terminal_turns=…, tool_call_limit=…, hard_ceiling=…."` (four structured fields).
- Rust `loop_.rs:31-37`: `"The agent used {used+text_only} tool calls/text-only turns without submitting a terminal tool. Submit one of the terminal tools to finish the run."` — collapses the four fields into one sum, adds an imperative sentence.
- The **event shape** (is_error=true, empty tool_name, is_terminal=false, exit_reason) matches; only the human-readable string differs. Cosmetic. Confirmed low.

### D3 — Failure check at top of Rust loop vs bottom in Python — **REFUTED** (corrected severity none)
- Python checks at the **bottom** (`loop.py:313-327`), after tool dispatch/counter increments. Rust checks at the **top** (`loop_.rs:103-106`) AND at the bottom of the text-only path (`:180-183`).
- Decisive argument: between Python's bottom-check at iteration N and Rust's top-check at iteration N+1, **no state mutates and no provider request occurs** — Rust's top gate runs before `dispatch_rules` (`:108`) and before the stream. Both evaluate `used + text_only >= ceil(1.5*limit)` on identical counters.
  - Tool-dispatch ceiling-cross: Python fails at bottom of N; Rust fails at top of N+1 before any model turn — same counters, same failure event, zero extra provider turns either way.
  - Text-only ceiling-cross: Rust `:180-183` fires immediately after the increment, exactly like Python's bottom check.
- Only divergence is the degenerate `limit==0` case (non-configurable; `tool_call_limit` is `NonZeroU32` on the Rust parse path, `model.rs:179`). No observable behavioral difference for any real configuration. Investigator overstated; refuted.

## Independently confirmed extra invariants (investigator listed as match — verified true)

- **Counter counts each tool once** — confirmed_match. Python: stream-time `_count_tool_dispatch` once per `ToolUseDeltaEvent` recorded in `streamed_tool_use_ids` (`loop.py:197-199`, `tool_call.py:34-41`), finalize-time dispatch uses `consume_budget = tool_use_id not in streamed_tool_use_ids` (`dispatch.py` lines with that guard). Rust mirrors: increment on `ToolUseDelta` inserting into `streamed_tool_use_ids` (`loop_.rs:139-143`), finalize counts only `!streamed_tool_use_ids.contains(...)` (`loop_.rs:170-174`).
- **Failure event shape** — confirmed_match (`loop.py:316-327` ↔ `loop_.rs:84-96`): is_error true, empty tool_name, is_terminal false, exit_reason set.

## New findings (verifier)

- **NF1 (low/none).** Rust terminal-reminder trigger adds `!terminal_tools.is_empty()` (`notifications.rs:72`) which Python (`must_submit_terminal_tool.py:17-21`) lacks. Diverges only when an agent has no terminal tools — impossible for real agents (`terminals` non-empty by construction, `model.rs:227-229`). Python would otherwise emit a degenerate "calling one of: ." string. Harmless hardening.
- **NF2 (low/none).** Rust budget trigger guards `tool_call_limit==0 || denominator==0` (`notifications.rs:80`); Python would fire the budget tier at used=0 when limit=0. Same degenerate-only divergence; `tool_call_limit` is `NonZeroU32` on the real path so unreachable in production.
- **NF3 (scoping note).** The investigator's Python citations used non-existent / mislabeled filenames (`terminal_tool_call_count_reminder.py lines 14-18` is right, but the area's stated ground-truth path `notification/rules.py` does not exist — rules live under `notification/rules/`; the assembly site is `engine/agent/factory.py:382-387`, not `factories.py`). Verdicts unaffected; anchors corrected above.

## Overall verdict

High Rust fidelity for the five area invariants. All budget thresholds (75/100/125 via integer cross-multiplication), the 150% hard-fail ceiling (`(3n+1)/2` == `ceil(1.5n)`), the `>=` operators, the premature-terminal reminder, the retry-until-terminal-or-fail loop, the once-per-tool counting, and the failure event shape are faithfully ported. The only real divergences are peripheral: D1 (profile `notification_triggers` never merged — drops the planner's `nested_planner_deferral_disabled` reminder, outside this area's scope) and D2 (cosmetic failure-text wording). D3 is refuted as behavior-preserving. No false matches found among the area's invariants.
