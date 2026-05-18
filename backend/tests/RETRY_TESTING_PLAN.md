# Terminal-tool Retry Testing Plan

Coverage plan for the new engine-layer retry in `run_ephemeral_agent`
(`max_terminal_retries`, default `1`). This plan is structured around the
**three test layers** that already exist in the repo, plus what each can
and **cannot** prove about the retry path.

## Critical layering note (read first)

`MockSquadRunner` (`task_center_runner/agent/mock/runner.py:242`)
constructs `EphemeralRunResult` directly via `return EphemeralRunResult(...)`
— it does **not** go through `run_ephemeral_agent`. Therefore:

- **The existing scenario suite under `task_center_runner/scenarios/pipeline/`** —
  including the four `pipeline.attempt_retry_*` scenarios and
  `pipeline.attempt_budget_exhausted` — exercises the **attempt-harness retry**
  (DB-backed new `Attempt` rows), not the engine retry. Adding new
  "engine retry" pipeline scenarios there is a no-op for the new code path.
- **The engine retry only fires in three places**: (1) direct
  `run_ephemeral_agent` calls, (2) `task_center/attempt/launch.py:122` which
  is the production main-agent path, (3) `tools/subagent/`,
  `tools/ask_helper/`, `task_center_runner/benchmarks/sweevo/csv_runner.py`
  which wrap `run_ephemeral_agent`.
- **To exercise engine retry through the scenario suite would require**
  routing `MockSquadRunner` through `run_ephemeral_agent` with a scripted
  `SupportsStreamingMessages` fake client. That is a significant infra
  change — discussed below in §5 (P3 infra option).

This means: **most of the testing happens at the engine layer with fake
provider clients, not in the scenario suite**.

## What the feature does (refresher)

`run_ephemeral_agent` retries up to `max_terminal_retries` times when:
- `QueryExitReason.RESOURCE_LIMIT` — `tool_calls_used >= tool_call_limit`
- `QueryExitReason.TEXT_RESPONSE` — assistant replied with plain text

Each retry resets `tool_calls_used=0`, clears
`notification_state["budget_warning"]`, resets `exit_reason=None`, appends a
nudge prompt naming the terminal tools (merged into the existing user
message on RESOURCE_LIMIT, appended as a new user message on TEXT_RESPONSE),
and re-enters the query loop without prepending a fresh user prompt.

**Never retries**: crashes (status=failed), agents with no terminal tools,
`max_terminal_retries=0`.

## Layer map

| Layer | Touches engine retry? | Where tests live | Speed |
|---|---|---|---|
| L1 — Direct `run_ephemeral_agent` + fake provider | **Yes — exercises real code path** | `backend/tests/unit_test/test_engine/` | <1s |
| L2 — Caller wrappers (`run_subagent`, `ask_advisor`, `ask_resolver`, attempt-launcher) + `spawn_agent` monkeypatch | Indirectly (via scripted agent) | `backend/tests/unit_test/test_tools/`, `test_task_center/` | <1s |
| L3 — `MockSquadRunner` scenario suite | **No (bypasses engine)** | `backend/src/task_center_runner/tests/` | seconds |
| L4 — Sweevo benchmark with real LLM | **Yes** | `backend/src/task_center_runner/tests/sweevo/` | minutes, gated |

L3 is included only to make the "what we can't test here" boundary
explicit — see §5.

---

## §1 — L1: Engine-layer tests with fake provider client (P0)

This is the **only** place the new retry code path is fully exercised under
fast unit tests. The existing 6 tests in `test_lifecycle.py` and 1 in
`test_loop_resource_limit_transcript.py` already cover the basic happy
paths. The missing coverage:

### 1a. Required infra: `_ScriptedProviderClient`

The current `_ScriptedRetryAgent` scripts the agent **above** the engine —
it bypasses `run_query`, so the engine retry logic runs but the provider
streaming layer does not. To exercise the full path (provider stream →
tool dispatch → budget check → transcript fix → retry), add a fake provider
client that implements `providers.types.SupportsStreamingMessages`.

**File**: `backend/tests/unit_test/test_engine/_fake_provider.py`
**Pattern**: takes a list of `ScriptedTurn` dataclasses, each describing one
streaming response with `(text_deltas, tool_uses, usage)`. Yields
`ApiTextDeltaEvent`, `ApiToolUseDeltaEvent`, `ApiMessageCompleteEvent` per
turn. Real `run_query` consumes this and dispatches real tools against a
real `ToolRegistry`. The `_TextClient` inside
`test_lifecycle.py::test_ephemeral_agent_run_preserves_initial_messages`
shows the minimum pattern; expand to support multiple turns and tool_uses.

### 1b. Tests to add — `test_engine_retry_end_to_end.py` (NEW, P0)

| # | Test                                                                       | What it proves |
|---|----------------------------------------------------------------------------|----------------|
| 1 | `test_full_provider_stream_resource_limit_then_terminal_success`           | Turn 1: model emits N tool_uses → budget hits limit during dispatch → transcript gets paired tool_results (loop fix verified end-to-end) → nudge injected → Turn 2: model emits terminal tool_use → terminal_result delivered. **This is the ship-blocking integration test.** |
| 2 | `test_full_provider_stream_text_response_then_terminal_success`            | Turn 1: model emits plain text only → TEXT_RESPONSE exit → nudge appended → Turn 2: terminal tool succeeds. |
| 3 | `test_provider_stream_terminal_reserved_slot_reapplies_on_retry`           | Turn 1 budget=2; model uses 1 non-terminal + tries 1 non-terminal at boundary (rejected by reserved-slot rule) → RESOURCE_LIMIT. Retry resets `tool_calls_used=0` → reserved-slot rule re-arms → Turn 2 succeeds with terminal. |
| 4 | `test_provider_stream_orphan_tool_uses_paired_on_resource_limit`           | Inspect transcript after Turn 1: assert last message is `user` with `ToolResultBlock` content, every tool_use has a matching tool_use_id. Prevents the malformed-transcript regression. |
| 5 | `test_provider_stream_does_not_retry_when_terminal_tools_empty`            | Same as #1 but `terminal_tools=set()`. Verify only 1 provider stream invocation occurred. |
| 6 | `test_provider_stream_budget_warning_notification_re_fires_on_retry`       | Attach a `make_budget_warning(thresholds=(0.5,))` rule. Turn 1 pushes used past 50% → rule fires → RESOURCE_LIMIT. Turn 2: assert the rule fires again (cleared state proven) by checking the next user-message content. |
| 7 | `test_provider_stream_assistant_tool_uses_preserved_across_retry_boundary` | The original assistant message with tool_uses must remain in the transcript on retry — only the next user message changes. Asserts the historical reasoning is visible to attempt 2. |

These are slow-ish (~10-50ms each) but execute real `run_query` machinery.

### 1c. Tests to add — `test_retry_state_integrity.py` (NEW, P0)

These can stay at the `_ScriptedRetryAgent` level (no real provider needed)
because they target lifecycle bookkeeping, not loop semantics.

| # | Test                                                                | Asserts |
|---|---------------------------------------------------------------------|---------|
| 1 | `test_total_usage_accumulates_across_retries`                       | `agent.total_usage` sums tokens from all attempts; not reset between |
| 2 | `test_close_called_exactly_once_after_all_attempts`                 | Confirmed in current tests — promote to its own assertion |
| 3 | `test_run_id_stable_across_retries`                                 | `query_context.run_id` unchanged across attempts |
| 4 | `test_exit_reason_reset_before_retry`                               | At start of attempt N+1, `query_context.exit_reason is None` |
| 5 | `test_budget_warning_state_cleared_per_retry`                       | Already implicit in existing test — make it explicit with multi-attempt |
| 6 | `test_other_notification_state_keys_preserved_across_retry`         | Custom keys in `notification_state` other than `budget_warning` survive |
| 7 | `test_tracker_finish_records_final_terminal_only`                   | `agent_run_store.finish_run` called once with the final attempt's payload |
| 8 | `test_event_count_aggregates_across_attempts`                       | `EphemeralRunResult.event_count` is sum across all attempts |

### 1d. Tests to add — `test_retry_multi_attempt.py` (NEW, P1)

| # | Test                                                                | Asserts |
|---|---------------------------------------------------------------------|---------|
| 1 | `test_three_attempts_succeeds_on_second_retry`                      | `max_terminal_retries=2`; attempt 3 succeeds |
| 2 | `test_three_attempts_all_fail_returns_none`                         | All three RESOURCE_LIMIT → `terminal_result=None`, `status="completed"` |
| 3 | `test_alternating_exit_reasons_across_retries`                      | RESOURCE_LIMIT → TEXT_RESPONSE → success. Each nudge differs. |
| 4 | `test_crash_on_retry_attempt_short_circuits`                        | RESOURCE_LIMIT → retry crashes → `status="failed"` |
| 5 | `test_large_max_retries_does_not_run_forever`                       | `max_terminal_retries=100` always fails → exactly 101 attempts, no infinite loop |

### 1e. Tests to add — `test_retry_profile_matrix.py` (NEW, P1)

Profile-by-failure-mode coverage. The 8 profiles in
`agents/profile/main/` and `agents/profile/subagent/` each have distinct
terminal tools. Parametrize over the registry so future profiles are
auto-covered.

```python
PROFILES_AND_TERMINALS = [
    ("planner",              {"submit_plan_closes_goal", "submit_plan_defers_goal"}),
    ("planner_full_only",    {"submit_plan_closes_goal"}),
    ("executor_success_failure",  {"submit_execution_success", "submit_execution_failure"}),
    ("executor_success_handoff",  {"submit_execution_success", "submit_execution_handoff"}),
    ("executor",             {...}),                # thin entry-point, terminals depend on routing
    ("evaluator",            {"submit_evaluation_success", "submit_evaluation_failure"}),
    ("generator_verifier",   {"submit_verification_success", "submit_verification_failure"}),
    ("entry_executor",       {"submit_execution_handoff"}),  # verify exact set
    ("explorer",             {...}),                # subagent profile
]

@pytest.mark.parametrize("profile_name,expected_terminals", PROFILES_AND_TERMINALS)
@pytest.mark.parametrize("exit_reason", [QueryExitReason.RESOURCE_LIMIT, QueryExitReason.TEXT_RESPONSE])
async def test_nudge_mentions_profile_terminal_tools(profile_name, expected_terminals, exit_reason):
    ...
```

| # | Test                                                                       | Asserts |
|---|----------------------------------------------------------------------------|---------|
| 1 | `test_nudge_mentions_profile_terminal_tools[<profile>×<exit_reason>]`      | Each profile's terminal tool name appears verbatim in the nudge |
| 2 | `test_retry_uses_profile_tool_call_limit_unchanged_on_retry`               | Retry doesn't increase or decrease the limit; only `tool_calls_used` is reset |
| 3 | `test_handoff_profile_with_only_success_or_handoff_terminals`              | `executor_success_handoff` has no `submit_execution_failure` — confirm the nudge lists exactly success + handoff |
| 4 | `test_planner_full_only_nudges_single_terminal`                            | `planner_full_only` has only `submit_plan_closes_goal` — nudge contains exactly that name |

### 1f. Tests to add — `test_retry_side_effects.py` (NEW, P1)

| # | Test                                                                | Asserts |
|---|---------------------------------------------------------------------|---------|
| 1 | `test_background_tasks_cancelled_before_retry`                      | Mock `BackgroundTaskManager`: `cancel_all` invoked before retry begins; no in-flight bleed |
| 2 | `test_on_event_receives_events_from_every_attempt`                  | `on_event` captures events from each attempt |
| 3 | `test_audit_events_carry_consistent_run_id_across_attempts`         | All events share `run_id` so the audit row stays singular |
| 4 | `test_persist_agent_run_records_only_final_outcome`                 | `agent_run_store.finish_run` called once total |
| 5 | `test_extra_tool_metadata_preserved_across_retries`                 | Mutations from attempt 1 visible in attempt 2 (same `query_context`) |
| 6 | `test_stream_events_for_synthetic_resource_limit_emitted_once_per_attempt` | "Agent stopped: tool_call_limit exceeded" event fires per failing attempt, not duplicated |

---

## §2 — L2: Caller-propagation tests (P0/P1)

These verify each `run_ephemeral_agent` caller wraps the result correctly.
They monkeypatch `spawn_agent` to return a scripted agent and assert on
the caller's `ToolResult` / `EphemeralRunResult`.

### 2a. `test_subagent_retry.py` (NEW, P0)

`tools/subagent/run_subagent.py:238-246` returns an error string when
`terminal_result is None`. With retry default `1`, the error fires only
after both attempts fail.

| # | Test                                                                | Asserts |
|---|---------------------------------------------------------------------|---------|
| 1 | `test_subagent_retry_succeeds_then_returns_terminal_to_parent`      | Subagent attempt 1 TEXT_RESPONSE → engine retry → terminal delivered; parent's `ToolResult.metadata["subagent_terminal_called"] = True` |
| 2 | `test_subagent_retry_exhausted_returns_existing_error_to_parent`    | Both attempts fail → existing error string ("subagent exited without calling a terminal tool. The findings were not delivered.") |
| 3 | `test_subagent_internal_retries_invisible_to_parent_budget`         | Parent's `tool_calls_used += 1` for the `run_subagent` call regardless of retry count |
| 4 | `test_parallel_subagents_retry_independently`                       | Two parallel run_subagent calls each retry once; transcripts don't cross-contaminate |
| 5 | `test_subagent_crash_does_not_trigger_retry`                        | Subagent raises → existing crash error returned ("run_subagent: subagent crashed: ...") without retry |

### 2b. `test_ask_advisor_retry.py` / `test_ask_resolver_retry.py` (NEW, P1)

Mirror the subagent tests for advisor and resolver. Specific error strings
to lock in:

| Caller | Error string when terminal not called |
|---|---|
| `ask_advisor` | `"ask_advisor: advisor exited without submit_advisor_feedback."` |
| `ask_resolver` | `"ask_resolver: resolver exited without submit_resolver_result."` |

| # | Test                                                                    | Asserts |
|---|-------------------------------------------------------------------------|---------|
| 1 | `test_advisor_retry_delivers_submit_advisor_feedback`                   | Attempt 1 fails → retry → success → parent receives terminal output |
| 2 | `test_advisor_retry_exhausted_returns_pinned_error_string`              | Pinned error after retry exhausted |
| 3 | `test_resolver_retry_delivers_submit_resolver_result`                   | Analogous |
| 4 | `test_advisor_internal_retries_invisible_to_parent_budget`              | Parent budget +1 regardless of internal retries |

### 2c. `test_attempt_launcher_retry.py` (NEW, P0)

`task_center/attempt/launch.py:120` is the **main-agent path**. Test that:
1. Engine retry happens *inside* one Attempt row.
2. Engine retry does NOT create new Attempt rows (that's the attempt
   harness's job, which only kicks in after engine retry exhausts).

| # | Test                                                                       | Asserts |
|---|----------------------------------------------------------------------------|---------|
| 1 | `test_main_planner_engine_retry_keeps_attempt_sequence_no_at_one`          | Planner first attempt TEXT_RESPONSE → engine retry succeeds → `Attempt.attempt_sequence_no=1`. No new attempt row. |
| 2 | `test_main_planner_engine_retry_exhausted_marks_attempt_failed`            | All engine retries fail → that one Attempt closes failed → outer attempt harness is free to create attempt_sequence_no=2 |
| 3 | `test_attempt_harness_records_engine_retry_token_usage`                    | Token counts in `agent_run` row include both attempts' usage |
| 4 | `test_continuation_planner_attempt_inherits_default_retry`                 | Continuation planners (sequence_no>1) ALSO retry once |

These tests use real DB stores (via the existing `stores` fixture in
`task_center_runner/core/fixtures.py:77`) but a scripted-agent fake via
`monkeypatch` on `spawn_agent` — so they're still fast (~50ms).

---

## §3 — L3: Mock-runner scenario suite (NEW SCENARIOS — see §5 for caveats)

**Bottom line:** new pipeline scenarios under
`task_center_runner/scenarios/pipeline/` will NOT exercise engine retry
unless we add the infra in §5. If we add only the scenarios without that
infra, they'd duplicate the existing `pipeline.attempt_retry_*` coverage
and wouldn't test anything new.

If §5 lands (engine-routed mock), the following scenarios become valuable:

| Scenario name                                  | Failure injected                                          | Expected event sequence change                                |
|------------------------------------------------|-----------------------------------------------------------|---------------------------------------------------------------|
| `pipeline.planner_text_response_engine_retry`  | Planner attempt 1: TEXT_RESPONSE                          | PLANNER_INVOKED × 1 (one Attempt), PLANNER_COMPLETES_GOAL_PLAN once after retry succeeds; no extra Attempt row |
| `pipeline.executor_budget_engine_retry`        | Generator attempt 1: RESOURCE_LIMIT                       | EXECUTOR_INVOKED × 1, EXECUTOR_SUCCESS after retry          |
| `pipeline.evaluator_text_response_engine_retry`| Evaluator attempt 1: TEXT_RESPONSE                        | EVALUATOR_INVOKED × 1, EVALUATOR_SUCCESS after retry        |
| `pipeline.engine_retry_then_attempt_retry`     | Planner engine retry exhausts; attempt harness creates seq_no=2 | PLANNER_INVOKED × 2, second one full plan after attempt-harness retry |

Add to `CAPACITY_PACK_SPECS` with `registry_name=...` and an
`implementation_anchor` pointing to the new scenario file.

---

## §4 — L4: Real-LLM e2e (gated, P3)

Sweevo csv_runner goes through `run_ephemeral_agent` so it sees engine
retry naturally. Gate behind `EOS_E2E_RETRY=1` env flag.

**File**: `backend/src/task_center_runner/tests/sweevo/test_retry_e2e_real_agent.py`

| # | Test                                                                      | Asserts |
|---|---------------------------------------------------------------------------|---------|
| 1 | `test_real_agent_recovers_from_text_response_via_retry`                   | Hand-picked CSV row with a prompt known to lure the model into a plain-text reply. Run with `max_terminal_retries=1`; verify retry was invoked (audit trail), final terminal result delivered. |
| 2 | `test_real_agent_retry_exhausted_returns_completed_with_no_terminal`     | Pathological prompt where even retry fails — `status="completed"` with `terminal_result=None`. Distinguish from `status="failed"`. |
| 3 | `test_real_agent_no_retry_with_max_retries_zero`                          | Same prompt, `max_terminal_retries=0` — opt-out preserves single-shot. |

These tests cost real tokens and have ~minute latency. Skip in CI; run
nightly or pre-release.

---

## §5 — Infra option: route MockSquadRunner through `run_ephemeral_agent` (P3)

The biggest blind spot is that the entire scenario suite bypasses engine
retry. Two ways to close that gap:

**Option A — replace MockSquadRunner internals with a thin
`_ScriptedProviderClient`**: refactor `MockSquadRunner` to construct an
`EphemeralAgent` with a fake `api_client` whose `stream_message` is
scripted from the scenario's `planner_response` / `executor_actions` /
etc. Then call `run_ephemeral_agent` instead of synthesizing
`EphemeralRunResult` directly. This makes every existing scenario also
test engine retry (and budget enforcement, batch validation, etc.).

**Pros**: every scenario gains true engine coverage. The "engine retry +
attempt retry" interaction becomes trivially testable.

**Cons**: significant refactor (~500 LOC in `mock/runner.py`). Slower
scenarios. Some scenarios may need rebalancing.

**Option B — add three sentinel actions to MockSquadRunner**:
- `"no_terminal:reply"` → stream plain text, no submit
- `"exhaust_budget"` → emit benign tool_uses until `tool_call_limit` hits
- `"crash"` → raise `RuntimeError`

These would only work if Option A is also applied (otherwise they're
unreachable from the synthesized-result path). So Option B is a follow-on
to Option A.

**Recommendation**: defer Option A until L1 + L2 + L4 are green. Most
real-world retry bugs will surface in L1 (engine internals) and L4 (real
LLM behavior). L3 expansion is defense in depth.

---

## §6 — Priority and sequencing

**P0 — ship-blocking** (in this PR or immediate follow-up):
1. `test_engine_retry_end_to_end.py` rows 1, 2, 4 (real `run_query` with
   fake provider — these are the only true integration tests of the new
   code).
2. `test_retry_state_integrity.py` (all 8 rows — fast, important for
   correctness).
3. `test_subagent_retry.py` (the most-affected caller).
4. `test_attempt_launcher_retry.py` rows 1, 2 (main-agent path).

**P1 — same milestone**:
5. Remaining `test_engine_retry_end_to_end.py` rows (3, 5, 6, 7).
6. `test_retry_profile_matrix.py` (loop over registered profiles).
7. `test_retry_multi_attempt.py`.
8. `test_retry_side_effects.py`.
9. `test_ask_advisor_retry.py` + `test_ask_resolver_retry.py`.

**P2 — defense in depth**:
10. Add `terminal-reserved + retry` interaction test (row 3 of P0 file
    already covers this — promote if not done at P0).
11. Negative tests for misuse (retry with no terminal_tools, with
    `max_terminal_retries < 0`, etc.).

**P3 — infra-gated, run nightly**:
12. L4 sweevo real-LLM tests.
13. §5 Option A refactor + L3 scenario expansion.

---

## §7 — What this plan does NOT test (intentional)

- **Live provider behavior on retry**: how Claude/GPT actually responds to
  the nudge prompt. Only the L4 tests probe that, and only on a small
  set of curated prompts.
- **Cost & latency budgets**: doubling the worst case for callers is the
  stated tradeoff, not a regression to gate.
- **Retry behavior under partial network failures**: covered by existing
  provider-error tests; the retry loop sits above the streaming layer
  and inherits their semantics.
- **Compatibility with the legacy single-shot contract** in callers that
  haven't been audited (e.g., third-party benchmarks): if any caller is
  found that requires single-shot, thread `max_terminal_retries=0`
  through it explicitly. The user has confirmed this is intentional for
  all current callers.

---

## §8 — How to start

Two parallel work streams:

**Stream A** (1-2 days): Build `_FakeProviderClient` in
`backend/tests/unit_test/test_engine/_fake_provider.py` then write
`test_engine_retry_end_to_end.py` rows 1–4. This is the foundational
infra plus the ship-blocking integration tests.

**Stream B** (concurrent, ~1 day): Write `test_retry_state_integrity.py`,
`test_subagent_retry.py`, and `test_attempt_launcher_retry.py` using the
existing `_ScriptedRetryAgent` pattern (no fake-provider dependency).

Once Stream A's infra exists, profile-matrix and side-effect tests follow
trivially.
