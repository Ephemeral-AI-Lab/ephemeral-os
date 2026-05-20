# RALPLAN — Unified Soft-Limit Termination for the Agent Loop

## Goal
Replace the three-mechanism termination machinery (per-tool reject gate, loop `RESOURCE_LIMIT` exit, lifecycle retry-with-fresh-budget) with a single, simpler model:

1. `tool_call_limit` becomes a **soft warning threshold** — exceeding it does NOT abort the run.
2. Every 5 tool calls past the soft threshold, the loop injects a system reminder telling the model the budget is exhausted and to wrap up via a terminal tool.
3. If the model returns plain text without calling any terminal tool, a system reminder asks it to terminate via a terminal tool; the loop continues.
4. The run is hard-failed only when the agent has burned through `max_tolerance_after_max_tool_call` units of "overshoot" without delivering a terminal tool result, where overshoot is the **sum** of (tool calls past `tool_call_limit`) + (text-only turns without terminal). Tool-overflow exits via `QueryExitReason.RESOURCE_LIMIT`; text-only-ceiling exits via a new `QueryExitReason.TERMINAL_REFUSED`. Two failure modes, two exit codes — one shared tolerance budget.

This subsumes the lifecycle retry path (`max_terminal_retries`, `_build_retry_nudge`, `_prepare_retry_transcript`) — it becomes dead code that this RALPLAN deletes.

---

## RALPLAN-DR Summary

### Principles
1. **Single overshoot budget.** One number — `max_tolerance_after_max_tool_call` — governs total "extra work" the agent is allowed after exhausting `tool_call_limit` *or* returning text without a terminal call. Overshoot is the sum of (calls past limit) + (text-only-no-terminal turns); hard-fail when that sum exceeds tolerance. No retry-with-fresh-budget reset path.
2. **Soft signal inside the live transcript over hard exit + retry transcripts.** The model sees reminders in real time and gets the chance to terminate gracefully without losing partial work.
3. **Reuse the existing `NotificationRule` machinery, extending the attachment pathway explicitly.** `_run_query_loop` already evaluates `dispatch_rules` at the top of every turn (`loop.py:307-318`); the two new rules use that same evaluation slot. The rule *attachment* path is extended — today profiles opt in via `notification_triggers:` frontmatter, the new rules are auto-attached in `spawn_agent` when an agent has both a `tool_call_limit` and at least one terminal tool. This is a documented extension of the pathway, not a parallel one.
4. **Transcript well-formedness is non-negotiable.** The recently-added invariant from `test_loop_resource_limit_transcript.py` — orphan `tool_use` blocks must be paired with `tool_result` blocks — is preserved at the new hard-cap exit.
5. **"Good terminal tool call" is unambiguous and each failure mode has its own exit code.** Good terminal call = a `ToolResultBlock` with `does_terminate=True` and `is_error=False` (matches `tools/_framework/execution/tool_call.py:211-212`). Tool-overflow hard-fail exits via `QueryExitReason.RESOURCE_LIMIT`; text-only ceiling exits via a new `QueryExitReason.TERMINAL_REFUSED`. Sharing one exit code across two distinct failures would make post-mortem audit harder, so we add the new enum member up-front in Phase 2 — not as a follow-up.

### Decision Drivers
1. **Simplification (user's explicit goal).** Collapse three overlapping mechanisms into one. Removing the lifecycle retry loop alone deletes ~80 lines and removes a stateful budget-reset that resets `tool_calls_used = 0` mid-run — a counterintuitive mutation that the new design eliminates.
2. **Graceful degradation.** Today the loop cuts the agent off mid-batch on the exact tool call that hits the limit. The new model gives the agent a deterministic, observable grace window (`max_tolerance_after_max_tool_call`) to call a terminal tool and deliver partial work. Aborts only when the agent fails to use that grace.
3. **Backward compatibility for declared profiles.** All seven shipped profiles in `backend/src/agents/profile/main/*.md` and `backend/src/agents/profile/subagent/*.md` declare `tool_call_limit` (range 30–100). Their meaning shifts from hard-cap to soft-threshold; we must pick a default tolerance that keeps current behavior approximately preserved while delivering the new graceful path.

### Viable Options

**Option A — Unified soft threshold + single hard ceiling (RECOMMENDED).**
- `tool_call_limit` → soft warning threshold.
- New per-agent field `max_tolerance_after_max_tool_call: int | None` (default 10).
- Hard cap = `tool_call_limit + max_tolerance_after_max_tool_call`.
- New notification rule `make_budget_overflow_reminder(every=5)`: fires at first call past `tool_call_limit` and then every 5 calls. Body names the terminal tools and instructs the model to call one.
- Loop change: when the model returns no tool_uses AND no terminal has fired, inject a reminder (also as a `NotificationRule` triggered by `final_message.tool_uses == []` AND `terminal_result is None`) and continue. This "text-no-terminal" turn consumes one unit against the shared tolerance budget.
- Hard exit at `tool_calls_used > limit + tolerance` OR (`text-no-terminal turns > tolerance` if `tool_call_limit is None`).
- `_consume_tool_budget_or_reject` keeps the counter but never returns a rejection block.
- Lifecycle retry path deleted in full.
- **Pros:** matches user request literally; one ceiling; reuses notification rules; loop becomes simpler (no retry orchestration in `run_ephemeral_agent`).
- **Cons:** mid-grace work the agent does still counts tokens/cost; deleting `max_terminal_retries` is a public API change for any external caller passing `max_terminal_retries=0`.

**Option B — Two separate tolerances.**
- Same as A but split into `tool_overflow_tolerance` (calls past soft limit) and `text_only_terminal_tolerance` (turns of plain text without terminal).
- **Pros:** cleaner separation; lets operators tune the two failure modes independently.
- **Cons:** two knobs to reason about; doesn't match the user's single-tolerance phrasing; the reminder pathway is still one notification rule, so the extra knob has limited semantic payoff.

**Option C — Keep the per-tool reject gate; add reminders only.**
- Leave `_consume_tool_budget_or_reject` rejecting calls past the limit.
- Add the "every 5 calls past limit" reminder *before* the rejection takes effect.
- Keep the lifecycle retry path for the no-terminal case.
- **Pros:** smallest diff.
- **Cons:** does not actually simplify — still three mechanisms; the user explicitly asked "do not set max tool call limit as a hard failure", which this option violates.

**Invalidation rationale:** B is rejected because the user phrased the requirement as a single ceiling: *"after [max_tool_call] + [max_tolerance_after_max_tool_call] exhausted, then it is a hard failure of the run"* — singular tolerance. C is rejected because it retains the hard-failure-at-limit behavior the user wants removed (item 1 of their spec).

### Chosen: Option A.

---

## Architecture (current → target)

### Current (3 mechanisms)
```
┌────────────────────────────────────────────────────────────────────┐
│ tool_call_limit enforcement today                                  │
├────────────────────────────────────────────────────────────────────┤
│ 1. tools/_framework/execution/tool_call.py:_consume_tool_budget…   │
│    └─ at-limit  → return budget-exceeded error ToolResultBlock     │
│    └─ at limit-1 + non-terminal → return reserved error            │
│                                                                    │
│ 2. engine/query/loop.py:_handle_tool_dispatch_branch (l. 259-287)  │
│    └─ used >= limit → exit_reason = RESOURCE_LIMIT, break loop     │
│                                                                    │
│ 3. engine/agent/lifecycle.py:run_ephemeral_agent (l. 207-260)      │
│    └─ on RESOURCE_LIMIT or TEXT_RESPONSE → reset tool_calls_used   │
│       = 0, append nudge, re-enter loop (max_terminal_retries=1)    │
└────────────────────────────────────────────────────────────────────┘
```

### Target (1 mechanism, 1 budget, 2 distinct exit codes)
```
┌─────────────────────────────────────────────────────────────────────┐
│ Unified soft-limit + shared overshoot budget                        │
├─────────────────────────────────────────────────────────────────────┤
│  Overshoot accounting (QueryContext)                                │
│    tool_overshoot         = max(0, tool_calls_used - tool_call_limit)│
│    text_only_no_terminal_turns                                       │
│    overshoot_units        = tool_overshoot + text_only_…_turns       │
│                                                                     │
│  A. NotificationRule make_budget_overflow_reminder(every=5)         │
│     └─ trigger: tool_overshoot > 0 AND (first crossing OR           │
│        over - last_emitted >= every) — monotonic-crossing-safe      │
│     └─ body: "Budget exhausted. Call terminal tool {names} now."    │
│                                                                     │
│  B. NotificationRule make_missing_terminal_reminder                 │
│     └─ trigger: last assistant message had no tool_uses AND no      │
│        terminal_result yet AND terminal tools are registered        │
│     └─ body: "You returned text without a terminal call. Call …"    │
│                                                                     │
│  C. engine/query/loop.py:_handle_tool_dispatch_branch               │
│     └─ overshoot_units > tolerance → RESOURCE_LIMIT                 │
│        (after dispatch, transcript well-formed)                     │
│                                                                     │
│  D. engine/query/loop.py: text-only path                            │
│     └─ overshoot_units > tolerance → TERMINAL_REFUSED               │
│        (new enum member)                                            │
│                                                                     │
│  E. _consume_tool_budget_or_reject                                  │
│     └─ counts calls; never rejects                                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Detailed changes

### 1. `AgentDefinition` — new field
**File:** `backend/src/agents/definition/model.py`

Add:
```python
# Grace allowance past `tool_call_limit`. Hard ceiling is
# `tool_call_limit + max_tolerance_after_max_tool_call`. Reached only after
# the model ignores the system reminders. Default 10 (≈ 2 reminder cycles).
# None when `tool_call_limit is None` — no soft threshold means no tolerance.
max_tolerance_after_max_tool_call: int | None = 10
```

Add the same `@field_validator` pattern as `tool_call_limit` to coerce string ints and reject negatives.

Update the seven shipped profiles **only if** the existing tool_call_limit value should change. **Default decision: keep all current `tool_call_limit` values unchanged; default tolerance=10 globally so no profile MD edits.** This means today's executors (limit=75) effectively get a hard cap of 85 — slightly more headroom than today (75 hard). Stated explicitly in the ADR as a behavior change.

### 2. `QueryContext` — new fields + overshoot accounting
**File:** `backend/src/engine/query/context.py`

Add to `QueryContext`:
```python
max_tolerance_after_max_tool_call: int | None = None
text_only_no_terminal_turns: int = 0
```

`text_only_no_terminal_turns` is the per-run counter for "agent returned text without terminal" turns. Reset only at run start.

Expose two read-only views: `tool_overshoot` (calls past `tool_call_limit`) and `overshoot_units` (the sum that the hard cap is checked against):

```python
@property
def tool_overshoot(self) -> int:
    """Tool calls executed past the soft `tool_call_limit`. 0 below limit."""
    if self.tool_call_limit is None:
        return 0
    return max(0, self.tool_calls_used - self.tool_call_limit)

@property
def overshoot_units(self) -> int:
    """Total 'extra work' the agent has spent beyond its soft budget.

    Sum of (calls past `tool_call_limit`) + (text-only turns without
    terminal). The hard ceiling is `max_tolerance_after_max_tool_call`,
    compared against this single number. Returning text without a terminal
    call burns the same budget as making one extra tool call — so the
    agent cannot game the cap by alternating between modes.
    """
    return self.tool_overshoot + self.text_only_no_terminal_turns
```

Note: there is no `hard_ceiling` property tied to an integer of `limit + tolerance`. The cap is on `overshoot_units`, not on `tool_calls_used`. This is the mechanical realization of Principle 1.

### 3. `_consume_tool_budget_or_reject` — counter only
**File:** `backend/src/tools/_framework/execution/tool_call.py`

Delete both rejection branches:
- Delete `_build_budget_exceeded_error` (lines 26-39).
- Delete `_build_terminal_budget_reserved_error` (lines 42-56).
- Rewrite the body to simply increment when `tool_call_limit is not None`:

```python
async def _consume_tool_budget_or_reject(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
) -> ToolResultBlock | None:
    """Increment the per-run tool-call counter. Never rejects.

    Soft-limit signaling is delivered as a system reminder via the
    `budget_overflow_reminder` notification rule; hard-failure is the
    loop's responsibility when `overshoot_units > tolerance`.
    """
    del tool_name, tool_use_id  # signature preserved for call-site stability
    if context.tool_call_limit is not None:
        context.tool_calls_used += 1
    return None
```

**Why keep the `Optional[ToolResultBlock]` return type:** this PR is large enough already; keeping the type means call sites in `dispatch.py:_consume_tool_budget_or_reject` and `loop.py:_consume_provider_stream` (which branch on a non-None return) stay valid without simultaneous edits. Those branches are unreachable after this PR — Phase 3 deletes them along with the `Optional`. No external Python callers exist (verified via `rg "_consume_tool_budget_or_reject" backend/src` — only the three internal callers). This is a PR-size deferral, not forward-compatibility.

### 4. Loop-level hard ceiling
**File:** `backend/src/engine/query/loop.py`

Add the new exit reason in `backend/src/engine/query/context.py`:

```python
class QueryExitReason(StrEnum):
    TEXT_RESPONSE = "text_response"      # no tool_uses in response (within tolerance)
    TOOL_STOP = "tool_stop"              # terminal tool succeeded
    RESOURCE_LIMIT = "resource_limit"    # tool-call overshoot > tolerance
    TERMINAL_REFUSED = "terminal_refused"  # text-only turns > tolerance
```

Rewrite the `_handle_tool_dispatch_branch` block at lines 259-287:

```python
tolerance = context.max_tolerance_after_max_tool_call
if (
    context.tool_call_limit is not None
    and tolerance is not None
    and context.overshoot_units > tolerance
):
    context.exit_reason = QueryExitReason.RESOURCE_LIMIT
    if background_manager is not None:
        await background_manager.cancel_all()
    if tool_results:
        messages.append(ConversationMessage(role="user", content=list(tool_results)))
    yield (
        ToolExecutionCompleted(
            tool_name="",
            output=(
                f"Agent stopped: overshoot ({context.overshoot_units}) exceeded "
                f"tolerance ({tolerance}) without a terminal tool call. "
                f"Soft limit={context.tool_call_limit}, "
                f"calls used={context.tool_calls_used}, "
                f"text-only turns={context.text_only_no_terminal_turns}."
            ),
            is_error=True,
        ),
        None,
    )
    for event in flush_system_notification_events(notification_service):
        yield event, None
    return
```

**Off-by-one semantics, documented:** `_consume_tool_budget_or_reject` increments *before* the tool body runs. With `tool_call_limit=10, tolerance=5`:
- Calls 1–10: `overshoot_units == 0`, no overflow rule firing.
- Call 11: increments to 11, `tool_overshoot == 1`, rule trigger evaluates next turn.
- Calls 11–15: `overshoot_units` ∈ [1, 5], `5 > 5` is False — loop continues.
- Call 16: increments to 16, `overshoot_units == 6`, `6 > 5` is True — hard exit fires after dispatch.

So with `tolerance=N`, the agent executes at most `limit + N` non-terminal calls, and the `(limit + N + 1)`-th non-terminal call is the one that triggers the exit branch. This is the "after [max_tool_call] + [max_tolerance_after_max_tool_call] exhausted" semantics the user specified. The exit happens after dispatching that final call so its `tool_result` lands in the transcript (preserving the well-formed-transcript invariant from `test_loop_resource_limit_transcript.py`).

The well-formed-transcript invariant is preserved: when orphan `tool_use` blocks exist (`tool_results` non-empty), they are paired into a `user` message before exit — same shape the existing `test_loop_resource_limit_transcript.py` asserts.

### 5. Text-response-without-terminal — in-loop reminder + shared tolerance
**File:** `backend/src/engine/query/loop.py`

The current break-on-no-tool-uses path (line 345) becomes:

```python
if not final_message.tool_uses:
    has_terminal = context.terminal_result is not None
    if not has_terminal and context.terminal_tools:
        context.text_only_no_terminal_turns += 1
        tolerance = context.max_tolerance_after_max_tool_call
        if (
            tolerance is not None
            and context.overshoot_units > tolerance
        ):
            # Distinguish text-only ceiling from tool-overflow ceiling so
            # post-mortem audit can separate "burned through tools" from
            # "refused to terminate after being asked." Principle 5.
            context.exit_reason = QueryExitReason.TERMINAL_REFUSED
            for event in flush_system_notification_events(notification_service):
                yield event, None
            break
        # Continue: the missing_terminal_reminder rule fires next turn,
        # injects a user message asking for the terminal call, and the
        # provider request goes out again.
        context.exit_reason = None
        continue
    for event in flush_system_notification_events(notification_service):
        yield event, None
    context.exit_reason = QueryExitReason.TEXT_RESPONSE
    break
```

**Counter composition (one budget, two contributors):**

The hard-fail check uses `context.overshoot_units` — the single sum of `tool_overshoot + text_only_no_terminal_turns`. Both tool-overflow and text-only-no-terminal contribute to the same budget. With `tool_call_limit=10, tolerance=2`, an agent that does 11 tool calls (`tool_overshoot=1`) then returns text twice (`text_only_no_terminal_turns=2`) hits `overshoot_units = 3 > 2` and fails on the second text-only turn — the agent cannot cheat the cap by alternating between modes.

The user's phrasing — "if agent failed to submit good terminal tool call after [max_tool_call + tolerance] exhausted" — is satisfied with a single, defensible meaning: exactly `tolerance` units of overshoot (in any combination) are allowed; the `(tolerance + 1)`-th unit triggers the hard exit. Which exit reason fires (`RESOURCE_LIMIT` vs `TERMINAL_REFUSED`) is determined by *which contributor* tipped overshoot past the cap — tool dispatch path vs. text-only path. The check itself is identical: `overshoot_units > tolerance`.

### 6. New notification rules
**File:** `backend/src/notification/rules/factories.py`

Add `make_budget_overflow_reminder`:

```python
_OVERFLOW_STATE_KEY = "budget_overflow"

def make_budget_overflow_reminder(every: int = 5) -> NotificationRule:
    """Emit a terminal-call nudge on the first turn whose `tool_overshoot`
    is positive, then again whenever overshoot has grown by `every` since
    the last emission.

    Monotonic-crossing-safe under batched dispatch: a turn that pushes
    `tool_calls_used` from `limit-2` to `limit+3` in one provider response
    still fires on the next `dispatch_rules` evaluation, because the
    trigger checks "have we crossed an `every` boundary since last
    emission" rather than equality against a specific count.
    """
    def _trigger(messages, context):
        del messages
        if context.tool_call_limit is None:
            return False
        over = context.tool_overshoot
        if over <= 0:
            return False
        state = context.notification_state.setdefault(
            _OVERFLOW_STATE_KEY, {"last_emitted_at": -1}
        )
        last = state["last_emitted_at"]
        # First crossing into overshoot (any positive `over`) → fire.
        # Subsequent: fire when overshoot has advanced by >= `every`.
        if last < 0 or (over - last) >= every:
            state["last_emitted_at"] = over
            return True
        return False

    def _body(messages, context):
        del messages
        names = ", ".join(sorted(context.terminal_tools)) or "<terminal tool>"
        tolerance = context.max_tolerance_after_max_tool_call
        suffix = (
            f" Hard ceiling at {tolerance} overshoot units; you have used "
            f"{context.overshoot_units}."
            if tolerance is not None
            else ""
        )
        return (
            f"Tool-call budget exhausted ({context.tool_calls_used} / "
            f"{context.tool_call_limit}). Stop exploring and call a terminal "
            f"tool now to deliver your result: {names}.{suffix}"
        )

    return NotificationRule(
        name="budget_overflow_reminder",
        body=_body,
        trigger=_trigger,
        fire_once=False,
    )
```

**Batched-overshoot test (new, in §test item 4 below):** assert that an artificial batched jump (`tool_calls_used = limit - 2 → limit + 5` in one turn) fires the reminder on the very next `dispatch_rules` evaluation. The previous-draft trigger using `over == 0` would have silently swallowed this case.

Add `make_missing_terminal_reminder`:

```python
def make_missing_terminal_reminder() -> NotificationRule:
    """Fire after a turn ends with text and no terminal call."""
    def _trigger(messages, context):
        if not context.terminal_tools:
            return False
        # Look at the most recent assistant message: if it had no tool_uses
        # and no terminal result has been recorded, fire.
        if context.terminal_result is not None:
            return False
        for msg in reversed(messages):
            if msg.role != "assistant":
                continue
            return not msg.tool_uses
        return False

    def _body(messages, context):
        del messages
        names = ", ".join(sorted(context.terminal_tools))
        return (
            f"You returned plain text without calling a terminal tool. "
            f"Deliver your result via one of: {names}. "
            f"Do this now — no further exploration."
        )

    return NotificationRule(
        name="missing_terminal_reminder",
        body=_body,
        trigger=_trigger,
        fire_once=False,
    )
```

Export both from `notification/__init__.py`.

### 7. Wire the new rules into default agent definitions
**File:** `backend/src/engine/agent/factory.py:spawn_agent` — append the two new rules to the `notification_rules` list immediately after the existing `resolve_harness_notification_triggers(...)` call (around line 371). **Do not extend `resolve_harness_notification_triggers`** — its resolver at `backend/src/tools/submission/notification_triggers/__init__.py:25` raises on unknown trigger IDs, and the harness trigger set is profile-driven by design. We are extending the *attachment* pathway in `factory.py`, not the harness-trigger vocabulary.

Auto-attach condition: the agent has `agent_def.tool_call_limit is not None` AND `tool_registry` contains at least one tool with `is_terminal_tool=True`. Both rules:
- `make_budget_overflow_reminder(every=cfg.budget_overflow_reminder_every)` (see §9 for `cfg`)
- `make_missing_terminal_reminder()`

Dedupe by `rule.name` against any rule already present in `notification_rules` (so a profile that declares its own customized overflow rule wins). Concretely:

```python
# factory.py:spawn_agent — after resolve_harness_notification_triggers
existing_names = {r.name for r in notification_rules}
has_terminal_tools = any(
    getattr(t, "is_terminal_tool", False) for t in tool_registry.list_tools()
)
if agent_def and agent_def.tool_call_limit is not None and has_terminal_tools:
    engine_cfg = get_central_config().engine
    if "budget_overflow_reminder" not in existing_names:
        notification_rules.append(
            make_budget_overflow_reminder(every=engine_cfg.budget_overflow_reminder_every)
        )
    if "missing_terminal_reminder" not in existing_names:
        notification_rules.append(make_missing_terminal_reminder())
```

Keep `make_budget_warning(thresholds=(0.50, 0.75, 0.90))` as-is — it triggers BEFORE the overflow rule (the warning state-key is `budget_warning`, the overflow state-key is `budget_overflow`; no collision). The agent gets soft 50/75/90 nudges first, then the harder "you went over" reminders once `tool_overshoot > 0`. The two rules compose; no semantic conflict.

### 8. Delete lifecycle retry path
**File:** `backend/src/engine/agent/lifecycle.py`

Remove:
- `_build_retry_nudge` (lines 49-66).
- `_prepare_retry_transcript` (lines 69-94).
- `max_terminal_retries` parameter (line 134 default) and the retry loop body at lines 207-260 — collapse to a single `async for event in agent.run(...)` invocation. The function signature loses the `max_terminal_retries` kwarg.
- Update the docstring to describe the new contract: "If the agent exits without a terminal tool, `terminal_result` is `None` and `status` is `failed`. Tool-overflow exits surface as `exit_reason == RESOURCE_LIMIT`; text-only ceiling exits surface as `exit_reason == TERMINAL_REFUSED`."

Keep `_last_terminal_tool_result` — it's still useful for the post-run scan when `terminal_result` is delivered late.

### 9. Central config knob (cadence only — not the tolerance default)
**File:** `backend/src/config/sections/` — add a new section `engine.py` (or extend an existing one).

```python
class EngineConfig(ModuleConfigBase):
    """Agent-loop tuning knobs.

    `tool_call_limit` and `max_tolerance_after_max_tool_call` are per-agent
    (declared in profile MDs) — only the global reminder cadence lives here.
    """
    budget_overflow_reminder_every: int = Field(default=5, ge=1)
```

Wire into `CentralConfig` and `ephemeralos.yaml`:
```yaml
engine:
  budget_overflow_reminder_every: 5
```

`spawn_agent` reads this value at agent construction:
```python
cfg = get_central_config().engine
notification_rules.append(make_budget_overflow_reminder(every=cfg.budget_overflow_reminder_every))
```

---

## Tests

### Unit
1. **`tests/unit_test/test_engine/test_tool_call_limit.py`** — rewrite:
   - `test_execute_tool_call_does_not_reject_at_limit` (replaces `test_execute_tool_call_rejects_when_over_budget`).
   - `test_execute_tool_call_does_not_reserve_last_call_for_terminal` (replaces `test_execute_tool_call_reserves_last_call_for_terminal_tool`).
   - Keep: counter-increments-on-unknown-tool, unlimited-budget-does-not-count, terminal-tool-counter behavior.

2. **`tests/unit_test/test_engine/test_loop_resource_limit_transcript.py`** — update to assert hard-ceiling exit instead of at-limit exit:
   - Pre-state: `tool_call_limit=L`, `max_tolerance_after_max_tool_call=T`, `tool_calls_used = L + T` (the agent has used exactly its full overshoot allowance).
   - The dispatched assistant turn produces `K` new `tool_use` blocks. After dispatch, `tool_calls_used = L + T + K`, so `overshoot_units = T + K`, and `T + K > T` triggers the exit (any `K >= 1`).
   - Choose `K=2` to mirror the existing fixture; assert `exit_reason == RESOURCE_LIMIT` and that the two `tool_result` blocks are paired with the assistant's two `tool_use` blocks in the final transcript user message. (The previous draft of this item stated `used = limit + tolerance` and asserted "one past ceiling" — that was off-by-one; the check is `> tolerance`, not `>=`, so the +K increment is what trips it.)

3. **NEW `tests/unit_test/test_engine/test_soft_limit_behavior.py`:**
   - `test_loop_continues_past_soft_limit_until_overshoot_exceeds_tolerance`
   - `test_overflow_reminder_fires_at_first_overshoot_then_every_5`
   - `test_overflow_reminder_fires_after_batched_jump` — pre-state `tool_calls_used = limit - 2`; one provider turn that emits 7 parallel `tool_use` blocks bumps to `limit + 5`. Assert the reminder fires on the next `dispatch_rules` evaluation (Architect-flagged scenario).
   - `test_text_response_without_terminal_injects_reminder_and_continues`
   - `test_text_response_tolerance_exhausted_emits_terminal_refused` — assert the new `QueryExitReason.TERMINAL_REFUSED` (not `RESOURCE_LIMIT`).
   - `test_overshoot_units_mixed_tool_and_text_share_one_budget` — limit=10 tolerance=2; 11 tool calls (tool_overshoot=1) + 2 text-only turns (text-counter=2) → `overshoot_units=3 > 2` → exit at the 2nd text-only turn with `TERMINAL_REFUSED` (since the text path tipped it past).
   - `test_terminal_tool_at_soft_limit_completes_run_normally`

4. **NEW `tests/test_notification/test_budget_overflow_reminder.py`:**
   - First call past limit (`tool_overshoot == 1`) → fires.
   - Subsequent calls within `every-1` of last fire → does not fire.
   - Call at `every` past the last fire → fires.
   - Below limit → never fires.
   - Batched jump (`tool_overshoot` goes from 0 → 5 in one turn evaluation) → fires (covers the monotonic-crossing-safe trigger).
   - Body names terminal tools and includes the `overshoot_units` / tolerance suffix.

5. **NEW `tests/test_notification/test_missing_terminal_reminder.py`:**
   - Fires after text-only assistant turn.
   - Does not fire when assistant called any tool.
   - Does not fire when `terminal_result` is already set.
   - Does not fire when `terminal_tools` is empty.

### Integration
6. **`tests/unit_test/test_agents/test_skill_message.py`** and `test_tools/test_submission_tool_registration.py` — search for assertions about `RESOURCE_LIMIT` exit and `max_terminal_retries`; update or delete.

7. **`tests/unit_test/test_engine/test_engine_retry_end_to_end.py`** and related retry tests (the `test_retry_*` family in `tests/unit_test/test_engine/`) — rewrite. The retry behavior they cover is being removed; the new tests in (3) cover the replacement.

8. **`tests/unit_test/test_engine/test_loop_resource_limit_transcript.py`** is preserved (assertion targets updated per item 2) — the transcript invariant survives.

9. **`tests/unit_test/test_task_center/test_agent_launch/test_attempt_launcher_retry.py:396-406`** contains a **static-source assertion** that greps the launcher source for the literal string `max_terminal_retries`. Deleting the kwarg breaks this test — update the assertion to check the absence of `max_terminal_retries` (negative-assertion) or delete the test outright if its sibling tests in the file no longer cover meaningful behavior.

10. `rg "max_terminal_retries|_build_retry_nudge|_prepare_retry_transcript|RESOURCE_LIMIT" backend/tests` to surface any other tests touching the removed pathways; update them in the same PR.

### Acceptance probes
9. Run `backend/src/task_center_runner/agent/mock/complex_project_build_probe.py` (already in working tree) to verify the mock runner still completes a probe against a small profile with `tool_call_limit=10` and the new tolerance default.

### Verification commands
```bash
.venv/bin/pytest backend/tests/unit_test/test_engine -q
.venv/bin/pytest backend/tests/test_notification -q
.venv/bin/pytest backend/tests/unit_test/test_tools -q
.venv/bin/pytest backend/tests -q  # full suite
.venv/bin/ruff check backend/src/engine backend/src/notification backend/src/agents
```

---

## Migration / rollout

This is a behaviour change visible to all agents. To de-risk:

### Phase 1 — Counter + notification rules (no behaviour change yet)
1. Add `EngineConfig`, `max_tolerance_after_max_tool_call` field, `text_only_no_terminal_turns`, `hard_ceiling` property.
2. Add the two new notification rule factories. Auto-attach them in `spawn_agent` *only* when `tool_call_limit` is set.
3. Keep the existing rejection branches in `_consume_tool_budget_or_reject` and the loop's hard exit at `used >= limit`.
4. **Result:** Agents now see reminders at soft limit but the hard cap is still `limit` (not `limit + tolerance`). Verifies the reminder pathway in isolation.

### Phase 2 — Flip the hard cap
1. Add `QueryExitReason.TERMINAL_REFUSED` to the enum.
2. Delete `_build_budget_exceeded_error`, `_build_terminal_budget_reserved_error`, and rewrite `_consume_tool_budget_or_reject` to count-only.
3. Change `_handle_tool_dispatch_branch` exit condition from `used >= limit` to `overshoot_units > tolerance` (exits via `RESOURCE_LIMIT`).
4. Replace text-response break with the inject-reminder-and-continue path; the text-only-ceiling exit uses `TERMINAL_REFUSED`.
5. Delete the lifecycle retry path.
6. Update tests per §test items 1-10.

### Phase 3 — Cleanup
1. Remove the (now-unreachable) rejection-branch handling in `dispatch.py:_consume_tool_budget_or_reject` and `loop.py:_consume_provider_stream`.
2. Remove `_build_retry_nudge`, `_prepare_retry_transcript`, `max_terminal_retries` everywhere.
3. Update docs (`docs/sandbox-architecture.html` and any agent-loop documentation).

Each phase is one PR. Tests stay green at every phase boundary.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Agents waste compute by burning the full tolerance grace before terminating. | Default tolerance=10 is small; reminders are firm ("call a terminal tool NOW"); cost monitored via existing token telemetry. |
| Existing transcripts that triggered retry now succeed via in-loop reminders — different runtime cost shape in production. | Phase 1 + Phase 2 are separate PRs so the cost change can be measured between them. Tolerance is per-agent configurable; high-volume profiles can set it to 0 to preserve old behavior. |
| The `notification_rules` list mutates `notification_state` — the overflow rule's `last_emitted_at` must be reset across re-runs in the same agent process. | `QueryContext.notification_state` is per-context; `spawn_agent` constructs a fresh `QueryContext` per ephemeral run, so the state is naturally per-run. |
| Removing `max_terminal_retries` is a public-API break for external callers of `run_ephemeral_agent`. | Search `backend/src` for `max_terminal_retries=` usage; today only `lifecycle.py` itself references it. External Python callers (none found in tree) get a deprecation cycle in Phase 3. |
| `make_budget_warning` (existing 50/75/90% rule) and the new overflow rule both fire near the limit. | Intentional layering: warnings at 50/75/90, overflow nudges past 100%. Different bodies, different state keys — no collision. Documented in ADR. |

---

## Acceptance criteria (testable)

1. With `tool_call_limit=10, max_tolerance_after_max_tool_call=5`, an agent that issues only single-tool turns (no parallel batches) without a terminal sees:
   - Tool calls #1–#10 succeed; no overflow reminder.
   - Tool call #11 succeeds; on the **next** `dispatch_rules` evaluation `tool_overshoot == 1`, `overshoot_units == 1`, the overflow reminder fires ("Tool-call budget exhausted (11/10)…").
   - Tool calls #12–#15 succeed; reminder does not re-fire within `every=5` of last emission.
   - Tool call #16 succeeds (`tool_overshoot == 6`, `overshoot_units == 6`, `6 > 5` triggers the loop exit after dispatch completes); `exit_reason == RESOURCE_LIMIT`; transcript ends with paired `tool_result` blocks for that final batch.

2. With `tool_call_limit=10, max_tolerance_after_max_tool_call=2`, an agent that returns text-only (no tool_uses) three turns in a row:
   - Turn 1: `text_only_no_terminal_turns == 1`, `overshoot_units == 1`, `1 > 2` is False → reminder fires next turn, loop continues.
   - Turn 2: `text_only_no_terminal_turns == 2`, `overshoot_units == 2`, `2 > 2` is False → reminder fires next turn, loop continues.
   - Turn 3: `text_only_no_terminal_turns == 3`, `overshoot_units == 3`, `3 > 2` is True → hard exit with `QueryExitReason.TERMINAL_REFUSED`.

3. With the same settings, an agent that returns text once then calls a terminal tool on the next turn → `status == completed`, `terminal_result` present.

4. Mixed mode: `tool_call_limit=10, tolerance=2`, agent makes 11 tool calls (`tool_overshoot=1`), then returns text twice (`text_only_no_terminal_turns=2`) → on the second text-only turn `overshoot_units = 1 + 2 = 3 > 2` → hard exit with `TERMINAL_REFUSED` (text-only contributor tipped the cap). Demonstrates the shared single-budget property.

5. Batched-overshoot reminder: `tool_call_limit=10, tolerance=20`, agent emits one assistant turn with 7 parallel `tool_use` blocks starting from `tool_calls_used=8` → after dispatch `tool_calls_used=15`, `tool_overshoot=5`. On the next `dispatch_rules` evaluation the overflow reminder fires (first-crossing path).

6. `rg "max_terminal_retries|_build_retry_nudge|_prepare_retry_transcript" backend/src` returns 0 references after Phase 3.

7. `rg "tool_call_limit exceeded|terminal call reserved" backend/src` returns 0 references after Phase 2.

8. `test_loop_resource_limit_transcript.py` continues to pass — orphan `tool_use` blocks are paired with `tool_result` blocks at the hard-cap exit.

9. `rg "max_terminal_retries" backend/tests` returns 0 references after Phase 3 (the static-source assertion in `test_attempt_launcher_retry.py:396-406` is updated or removed).

10. Full test suite (`.venv/bin/pytest backend/tests -q`) green at the end of each phase.

---

## ADR

- **Decision:** Replace the three-mechanism termination model (per-tool reject gate + loop budget exit + lifecycle retry) with a single shared-overshoot-budget model. `tool_call_limit` is the soft threshold; `max_tolerance_after_max_tool_call` is the single number capping total overshoot (sum of tool-overshoot + text-only-no-terminal turns). Two exit reasons distinguish the two failure modes (`RESOURCE_LIMIT` vs new `TERMINAL_REFUSED`) but the cap itself is one number. Signaled via the existing `NotificationRule` machinery.
- **Drivers:** simplification (user's explicit ask), graceful degradation in place of mid-batch cutoffs, single shared overshoot budget that the agent cannot game by alternating modes.
- **Alternatives considered:**
  - B: split tolerance into separate overflow/text-only knobs — rejected, doesn't match user's single-tolerance framing and adds tuning surface for no semantic gain.
  - C: keep rejection gate, add reminders only — rejected, doesn't satisfy "do not set max tool call limit as a hard failure".
  - A-variant rejected during Architect review: OR-semantics on two independent counters (`tool_calls_used > limit + tolerance` OR `text_only_no_terminal_turns > tolerance`). This was Option B in disguise — the agent could burn up to `2 × tolerance` units of extra work before hard-fail. The chosen sum-based approach (`overshoot_units > tolerance`) enforces a true single budget.
- **Why chosen:** delivers exactly the requested behavior; deletes more code than it adds; reuses one well-tested subsystem (notification rules) instead of growing a parallel one; preserves the well-formed-transcript invariant the codebase recently fixed; the sum-based overshoot is the only formulation that honors "single tolerance" mechanically and not just by label.
- **Consequences:**
  - All shipped agent profiles get up to `tolerance=10` units of extra work past their declared `tool_call_limit`; tolerable since the new path is observable and capped.
  - `max_terminal_retries` kwarg deleted from `run_ephemeral_agent` — minor API break (no external callers in `backend/src`).
  - One new `QueryExitReason` enum member (`TERMINAL_REFUSED`); audit consumers reading `exit_reason` see a new value.
  - Reminder cadence lives in central config (`engine.budget_overflow_reminder_every`) so operators can tune without touching agent profiles.
- **Follow-ups:**
  - After 1 week in production, audit how often `overshoot_units` is consumed past 50% of tolerance; if rare, consider lowering default tolerance to 5; if common, the soft threshold is mis-tuned, not the tolerance.
  - Consider extending `overshoot_units` and the split (`tool_overshoot`, `text_only_no_terminal_turns`) into `EphemeralRunResult` so callers can distinguish "ran out of tools" from "model refused to call terminal" without parsing `exit_reason`.
  - Phase 3 also removes the `Optional[ToolResultBlock]` return type from `_consume_tool_budget_or_reject` and the dead rejection-handling branches in `dispatch.py` and `loop.py:_consume_provider_stream`.

---

## Out of scope
- Restructuring `dispatch_rules` or the `NotificationRule` interface.
- Replacing `make_budget_warning` (50/75/90% thresholds) — it stays.
- DB-level audit changes to `agent_runs` schema.
- Provider-side request-budget enforcement (max_tokens, retry caps).
- Anything in `task_center_runner` or `benchmarks` beyond verifying probes still pass.
