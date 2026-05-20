# Initial-Messages Cases

One markdown file per agent launch position the test contract cares about.
Each file shows the initial rows `AgentMessageJsonlRecorder.record_initial_messages`
writes to `message.jsonl`:

* **system** — agent profile body, prepended with
  `agents/profile/main/_main_role_contract.md` for the seven main-role
  profiles (excludes `entry_executor`).
* **user_msg_1** — `<context>...</context>` envelope around the rendered
  packet (`XmlPromptRenderer`).
* **user_msg_2** — `<Task Guidance>...</Task Guidance>` envelope around a
  deterministic outline (`What's in context:` from
  `task_center/context_engine/what_in_context.py:render_what_in_context`)
  plus a single role directive (`What to do:` from
  `task_center/context_engine/role_directives.py:ROLE_DIRECTIVES`) and a
  single `<terminal_tool_selection>` block. Omitted for `entry_executor`.
* **user_msg_3 — row 4** (planner, executor, evaluator) — `Load skill:
  <role>` header + `<skill>` body + a byte-equal
  `<terminal_tool_selection>` block (AC #15). Operational heuristics
  (criteria-as-authority, dependency-as-fixed-input, etc.) live in the
  skill body.

## Index

| # | Case | Source | Notes |
|---|---|---|---|
| 01 | `entry_executor` — root delegation | `pipeline.initial_messages_capture` | single-user-message launch |
| 02 | planner — iter1 attempt1, fresh | `pipeline.initial_messages_capture` | iter1, no failed attempts — minimal frame |
| 03 | planner — iter1 attempt2, after evaluator failure | `pipeline.initial_messages_capture` | `<attempt status="prior" verdict="fail">` body is **fully populated** — real `<plan_spec>`, `<status_summary>`, per-task `<task>` summaries, `<evaluation_criteria>`, `<evaluator_summary>`, and `<failed_criteria>` (all flat children — no wrappers); scenario submits a valid plan that the evaluator rejects, so all downstream stages produce real evidence |
| 04 | planner — iter2 attempt1, deferred-goal follow-up | `pipeline.initial_messages_capture` | `<iteration status="prior">` + `<iteration status="current">` group |
| 05 | executor — iter1 attempt2 (attempt with deferred goal, handoff variant) | `pipeline.initial_messages_capture` | flat `<plan_spec>` + `<assigned_task>`; the `<deferred_goal_for_next_iteration>` is intentionally dropped from executor packets |
| 06 | executor — iter2 attempt1 (complete plan, handoff variant) | `pipeline.initial_messages_capture` | flat `<plan_spec>` + `<assigned_task>` |
| 07 | evaluator — iter1 attempt2 (attempt with a deferred goal) | `pipeline.initial_messages_capture` | `<attempt status="prior" verdict="fail">` plus `<attempt status="current">` (with `<deferred_goal_for_next_iteration>` inline) nested under `<iteration status="current">` |
| 08 | evaluator — iter2 attempt1 (complete attempt) | `pipeline.initial_messages_capture` | `<iteration status="prior">` + `<iteration status="current">` with the active `<attempt status="current">` inside |
| 09 | advisor — invoked by executor pre-submission | programmatic via `tools/ask_helper/_lib/_compose.py` | mock runner does not invoke helpers today |
| 10 | resolver — invoked by verifier/evaluator on issues | programmatic via `tools/ask_helper/_lib/_compose.py` + `ask_resolver._build_resolver_user_msg_2` | mock runner does not invoke helpers today |
| 11 | explorer subagent — invoked via `run_subagent` | programmatic via `explorer_instruction().text` | mock runner does not invoke subagents today |
| 12 | planner_closes_goal — child goal, delegated from deferring parent | `pipeline.deferred_parent_planner_closes_goal` | terminal catalog has `submit_plan_closes_goal` only |
| 13 | planner — iter1 attempt2, after evaluator failure (cross-reference from focused-scenario suite) | `pipeline.attempt_retry_evaluator_failure` | Same shape as case 03; kept as a focused-reference example from a single-purpose scenario. Case 03 is the canonical reference. |
| 14 | executor — `has_deps=True` branch with flat `<dependency>` siblings | `pipeline.dependency_dag_serial` | task `b` of serial chain `a → b → c`; deps: `[a]`. No `<dependency_results>` wrapper — each upstream task becomes a flat `<dependency id="...">` block between `<plan_spec>` and `<assigned_task>`. |
| 15 | evaluator — pre `submit_evaluation_failure` | `pipeline.attempt_retry_evaluator_failure` | input shape matches a passing evaluator; the failure path is the agent's decision, not a renderer branch |

## Gap coverage (vs. the v1 gap report)

Closed:

* **Gap 1** — `planner_closes_goal` variant terminal catalog (case 12)
* **Gap 2** — rich `<attempt status="prior" verdict="fail">` body. Closed natively in case 03 (the main scenario was updated to submit a valid plan + evaluator failure rather than an invalid plan rejected by validation). Case 13 keeps the same shape from a focused-reference scenario.
* **Gap 3** — flat `<dependency>` siblings in executor user_msg_1 (case 14)
* **Gap 5** — evaluator that proceeds to `submit_evaluation_failure` (case 15; iter1 attempt 1 of the main scenario also exercises this path now)

Open / documented as structural limits:

* **Gap 4** — `executor_success_failure` routing variant. Requires
  `nested_goal_depth > MAX_HANDOFF_DEPTH` (=3 in
  `task_center/_core/agent_routing.py`). No scenario in the current suite
  exercises depth > 3. Reaching it from a test is feasible (chain
  `request_recursive_goal` 4+ levels deep) but invasive enough that no
  case is captured here; the leaf-executor agent profile is in
  `agents/profile/main/executor_success_failure.md` for direct review.

* **Gaps 6 + 7** — live helper / subagent captures. Cases 09–11 use the
  real builder code in `tools/ask_helper/_lib/_compose.py`,
  `ask_advisor._build_advisor_user_msg_2`,
  `ask_resolver._build_resolver_user_msg_2`, and
  `explorer_instruction().text` — so the prompt shape is faithful.
  The only difference from a live capture is the parent transcript
  content. Extending `MockSquadRunner` to dispatch `ask_advisor` /
  `ask_resolver` / `run_subagent` inline (the scenario already exposes a
  `call_helpers_in_executor` flag for that future extension) would
  replace these with live `message.jsonl` rows; until then the
  programmatic construction is the authoritative reference.

## Regenerating

After future renderer / recipe changes, re-run the source tests and
re-emit the case files:

```sh
# Captures cases 01..11 from pipeline.initial_messages_capture
.venv/bin/pytest backend/src/task_center_runner/tests/sweevo/test_initial_messages_capture.py
.venv/bin/python scripts/regen_initial_messages_cases.py

# Captures case 12 from pipeline.deferred_parent_planner_closes_goal
.venv/bin/pytest backend/src/task_center_runner/tests/sweevo/test_deferred_parent_planner_closes_goal.py

# Captures cases 13..15 from the focused-reference scenarios
.venv/bin/pytest 'backend/src/task_center_runner/tests/sweevo/test_focused_scenarios.py::test_focused_reference_scenario_runs[pipeline.attempt_retry_evaluator_failure]' \
                 'backend/src/task_center_runner/tests/sweevo/test_focused_scenarios.py::test_focused_reference_scenario_runs[pipeline.dependency_dag_serial]'
.venv/bin/python scripts/regen_initial_messages_cases_gaps.py
```

Case 12 is captured by `regen_initial_messages_cases_gaps.py` from the
`pipeline.deferred_parent_planner_closes_goal` scenario run; if the scenario's
prompts shift, re-run the test and rerun the gaps script.
