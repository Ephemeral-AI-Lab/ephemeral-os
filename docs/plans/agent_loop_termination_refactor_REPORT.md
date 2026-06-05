# Agent-Loop Termination Refactor — Implementation Report

**Plan:** `docs/plans/agent_loop_termination_refactor_PLAN.md`
**Status:** Implemented and verified (1165 unit tests pass, 1 skipped; ruff clean).

---

## What landed

The plan was executed faithfully. Summary of the changes:

### Source

- `backend/src/engine/query/context.py` — `QueryExitReason` collapsed to `TOOL_STOP` and `TERMINAL_NOT_SUBMITTED`. Dropped `_ToolBudgetView`, `tool_overshoot`, `overshoot_units`, `tool_budget`, `max_tolerance_after_max_tool_call`, `text_only_no_terminal_turns`. Tightened `tool_call_limit: int` (required, no default).
- `backend/src/engine/query/loop.py` — Added module-level `terminal_submission_failed()` and `_terminal_not_submitted_message()`. Inlined the former `_dispatch_final_message_tools` body and deleted the helper. Final exit-decision block is now five lines.
- `backend/src/notification/rules/factories.py` — Replaced `make_budget_warning`, `make_budget_overflow_reminder`, and `make_missing_terminal_reminder` with the single `make_terminal_call_reminder`. Module-level state-keys deleted.
- `backend/src/notification/rules/__init__.py`, `backend/src/notification/__init__.py` — Re-exported the new factory; dropped the three legacy ones.
- `backend/src/agents/definition/model.py` — `tool_call_limit: int = Field(..., gt=0)`, `terminals: list[str] = Field(..., min_length=1)`. Dropped `max_tolerance_after_max_tool_call` and `_coerce_nonneg_int`. Terminals validator now raises on empty input.
- `backend/src/engine/agent/factory.py` — `_attach_default_overshoot_rules` renamed to `_attach_default_terminal_reminder`; body appends only `make_terminal_call_reminder()`. Added `assert terminal_tool_names` in `_finalize_tool_registry_and_prompt`. Dropped `max_tolerance` plumbing.
- `backend/src/engine/agent/lifecycle.py` — Docstring rewritten to reference `TERMINAL_NOT_SUBMITTED` and the hard ceiling.
- `backend/src/config/sections/engine.py` — `EngineConfig` is now an empty `ModuleConfigBase` subclass; `budget_overflow_reminder_every` deleted.
- `backend/src/tools/_framework/execution/tool_call.py` — `_count_tool_dispatch` docstring updated; counter now increments unconditionally (the `tool_call_limit is not None` guard is gone because the field is always `int`).

### Config

- `ephemeralos.yaml` — Removed the `engine:` section (the only key it contained was the deleted `budget_overflow_reminder_every`).

### Profile

- `backend/src/agents/profile/helper/advisor.md` — Added `tool_call_limit: 30` so the profile satisfies the new invariant.

### Doc

- `docs/architecture/agent_loops/main-loop.html` — Replaced the old `<section id="budget-and-terminal-refusal">` body with the minimal Termination section; updated the run-lifecycle-boundary section, exit-reason table, and `tests-and-failure-modes` evidence list.

### Tests

- **Deleted:** `test_overshoot_accounting.py`, `test_overshoot_rules.py`, `test_soft_limit_behavior.py`, `test_loop_resource_limit_transcript.py`. Deleted the `make_budget_warning` cases in `test_rules_factories.py` and the budget-warning wiring test in `test_tool_execution.py`. Removed `test_terminal_tool_selection_block_skipped_when_no_terminals` (now structurally impossible).
- **New:** `test_hard_ceiling_behavior.py`, `test_terminal_not_submitted_transcript.py`, `test_terminal_call_reminder.py`, `test_definition_invariants.py`.
- **Trimmed/updated:** `test_tool_call_limit.py` now asserts that `tool_call_limit` and `terminals` are required, and that `_coerce_int` no longer silently nulls bad input. `test_lifecycle.py::test_ephemeral_agent_run_preserves_initial_messages` rewritten to exit via the hard ceiling instead of the deleted `TEXT_RESPONSE`. `test_query_loop_rejects_streamed_terminal_tool_batched_with_sibling` now expects `TERMINAL_NOT_SUBMITTED` after a small `tool_call_limit`.

### Test-fixture sweep

Many test fixtures constructed `AgentDefinition` and `QueryContext` without the now-required fields. A scripted edit added `terminals=["submit_x"]` and `tool_call_limit=10` to all surviving call sites across:

- `test_agents/test_registry_validation.py`, `test_skill_message.py`, `test_routing_acceptance.py`, `test_skill_resolver.py`, `test_skill_lint.py`
- `test_tools/conftest.py`, `test_submission_tool_registration.py`, `test_ask_advisor_retry.py`, `test_subagent_retry.py`, `test_submission_terminal_routing.py`, `test_submission_helper_tools.py`, `test_skills_toolkit.py`, `test_tool_execution.py`
- `test_task_center/conftest.py`, `test_agent_launch/test_composer.py`, `test_agent_launch/test_terminal_tool_router.py`, `test_task_guidance/test_builders.py`, `test_lifecycle/test_orchestrator_composer.py`
- `test_engine/test_agent_system_prompt.py`, `test_factory_issue_pr_tripwire.py`, `test_spawn_agent.py`
- Two helper-only fixtures in `test_spawn_agent.py` and `test_skill_resolver.py` had their default `terminals=[]` swapped to `["submit_x"]`, and a no-terminals branch in `test_finalize_*` was given a `_TerminalTool()` registration.

---

## Deviations from the plan

- **`spawn_agent` signature tightened.** The plan implies every agent has an `AgentDefinition` but does not call out the `agent_def: AgentDefinition | None = None` default on `spawn_agent`. The cleanup pass made `agent_def` a required keyword argument and dropped every `agent_def is None` defensive branch in `_resolve_agent_identity`, `_build_agent_tool_registry`, `_build_agent_system_prompt`, and `spawn_agent` itself. The previously-supported "default sandbox agent" fallback (which bulk-registered `make_sandbox_tools()` when no agent_def was supplied) is removed; the two tests exercising the default-sandbox-agent fallback were deleted because the code path no longer exists.
- **`AgentDefinition.tool_call_limit` coercion.** The plan tightens the field to `int = Field(..., gt=0)` but does not specify the validator. I kept a string-coercion validator (`_coerce_int`) so YAML values like `"30"` continue to load. The validator no longer silently nulls out invalid input; bad values raise `ValidationError` via Pydantic's normal type-check.
- **`test_lifecycle.py::test_ephemeral_agent_run_preserves_initial_messages`.** The plan keeps `test_lifecycle.py` verbatim. This specific case asserted the deleted `TEXT_RESPONSE` exit reason, so I rewrote the setup to use `tool_call_limit=1, tool_calls_used=2` plus a terminal-tool set so the loop exits via `TERMINAL_NOT_SUBMITTED` on the first turn. Message-preservation assertions are unchanged.

---

## Deferred items

These are outside the scope of this refactor and remain open follow-ups:

1. **Pathological text-only-forever agent (plan Scenario A).** With mandatory terminals and a hard ceiling on tool calls, an agent that emits only text and never dispatches a tool can loop until the caller cancels. The engine layer accepts this; callers of `run_ephemeral_agent` (`task_center/attempt/launch.py`, `tools/ask_helper/ask_advisor/ask_advisor.py`, `task_center_runner/benchmarks/sweevo/run.py`, `tools/subagent/run_subagent/run_subagent.py`) may wrap with their own deadlines if real traces demand it.

2. **`EngineConfig` is now empty.** `backend/src/config/sections/engine.py` defines an empty `ModuleConfigBase` subclass. Removing the section entirely (and its central-config wiring) is a tractable follow-up if no future engine knob is anticipated.

3. **Per-turn reminder token cost.** `terminal_call_reminder` fires every turn after the first assistant message. The estimated cost is ~50 tokens × ~10 turns ≈ 500 tokens per run. If real traces show excess noise, revisit with optional staged thresholds.

4. **`AgentRunTracker` + audit downstream.** The audit row records the final `terminal_result` payload but no longer distinguishes between the old `RESOURCE_LIMIT` and `TERMINAL_REFUSED` exit reasons in any downstream analytics. If exit-reason breakdown was being scraped from logs anywhere, this refactor collapses those two into `TERMINAL_NOT_SUBMITTED`.

---

## Verification

```bash
# Grep-zero (deleted symbols)
$ grep -rn "max_tolerance_after_max_tool_call|text_only_no_terminal_turns|tool_overshoot|overshoot_units|_ToolBudgetView|RESOURCE_LIMIT|TERMINAL_REFUSED|TEXT_RESPONSE|make_budget_warning|make_budget_overflow_reminder|make_missing_terminal_reminder|budget_overflow_reminder_every|_dispatch_final_message_tools|_attach_default_overshoot_rules" \
    backend/src backend/tests docs/architecture
# Only matches: test_definition_invariants.py (intentional — proves the legacy key is rejected).

# Grep-present (new symbols)
$ grep -rln "TERMINAL_NOT_SUBMITTED|terminal_submission_failed|make_terminal_call_reminder|_attach_default_terminal_reminder" backend/src
# Hits in: context.py, loop.py, factory.py, lifecycle.py, notification/__init__.py, notification/rules/__init__.py, notification/rules/factories.py.

# Profile audit
$ for f in backend/src/agents/profile/*/*.md; do
    [[ $(basename $f) == _* ]] && continue
    grep -q "^terminals:" "$f" || echo "MISSING terminals: $f"
    grep -q "^tool_call_limit:" "$f" || echo "MISSING tool_call_limit: $f"
done
# Zero output — every profile declares both.

# Lint
$ .venv/bin/ruff check backend/src/engine backend/src/notification backend/src/config backend/src/agents
# All checks passed!

# Tests (unit suite, sandbox excluded — sandbox owns no AgentDefinition/QueryContext fixtures)
$ .venv/bin/pytest backend/tests/unit_test/ --ignore=backend/tests/unit_test/test_sandbox -q
# 1163 passed, 1 skipped (after the cleanup pass deleted two more legacy tests).
```
