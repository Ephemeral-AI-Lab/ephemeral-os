# Initial-Messages Cases

One markdown file per agent launch position the test contract cares about.
Each file shows the initial rows `AgentMessageJsonlRecorder.record_initial_messages`
writes to `message.jsonl`:

* **system** — agent profile body, prepended with
  `agents/profile/main/_main_role_contract.md` for the seven main-role
  profiles (excludes `entry_executor`).
* **user_msg_1** — `<context>...</context>` envelope around the rendered
  packet (`XmlPromptRenderer`).
* **user_msg_2** — `<Task Guidance>...</Task Guidance>` envelope around
  role-specific prose from `task_center/task_guidance/builders.py`. Ends
  with a single `<terminal_tool_selection>` block (omitted for
  `entry_executor`).
* **user_msg_3 — row 4** (planner only) — `Load skill: planner` header +
  `<skill>` body + a byte-equal `<terminal_tool_selection>` block (AC #15).

## Index

| # | Case | Source | Notes |
|---|---|---|---|
| 01 | `entry_executor` — root delegation | `pipeline.initial_messages_capture` | single-user-message launch |
| 02 | planner — iter1 attempt1, fresh | `pipeline.initial_messages_capture` | `planner_instruction` branch: `iter==1`, no failed attempts |
| 03 | planner — iter1 attempt2, after evaluator failure | `pipeline.initial_messages_capture` | `<attempt status="failed">` body is **fully populated** — real `<plan_spec>`, `<generator_outcomes>` with per-task summary, and `<evaluator_judgment status="ran" verdict="fail">` with `<evaluation_criteria>` / `<evaluator_summary>` / `<failed_criteria>` (scenario submits a valid plan that the evaluator rejects, so all downstream stages produce real evidence) |
| 04 | planner — iter2 attempt1, continuation | `pipeline.initial_messages_capture` | `<iteration status="prior">` + `<iteration status="current">` group |
| 05 | executor — iter1 attempt2 (partial plan, handoff variant) | `pipeline.initial_messages_capture` | `<next_iteration_handoff_goal>` present |
| 06 | executor — iter2 attempt1 (full plan, handoff variant) | `pipeline.initial_messages_capture` | no `<next_iteration_handoff_goal>` |
| 07 | evaluator — iter1 attempt2 (partial attempt) | `pipeline.initial_messages_capture` | passing path |
| 08 | evaluator — iter2 attempt1 (complete attempt) | `pipeline.initial_messages_capture` | passing path |
| 09 | advisor — invoked by executor pre-submission | programmatic via `tools/ask_helper/_lib/_compose.py` | mock runner does not invoke helpers today |
| 10 | resolver — invoked by verifier/evaluator on issues | programmatic via `tools/ask_helper/_lib/_compose.py` + `ask_resolver._build_resolver_user_msg_2` | mock runner does not invoke helpers today |
| 11 | explorer subagent — invoked via `run_subagent` | programmatic via `explorer_instruction().text` | mock runner does not invoke subagents today |
| 12 | planner_full_only — child goal, delegated from partial-parent | `pipeline.partial_parent_planner_full_only` | terminal catalog has `submit_plan_closes_goal` only |
| 13 | planner — iter1 attempt2, after evaluator failure (cross-reference from focused-scenario suite) | `pipeline.attempt_retry_evaluator_failure` | Same shape as case 03; kept as a focused-reference example from a single-purpose scenario. Case 03 is the canonical reference. |
| 14 | executor — `has_deps=True` branch, `<dependency_results>` block | `pipeline.dependency_dag_serial` | task `b` of serial chain `a → b → c`; deps: `[a]` |
| 15 | evaluator — pre `submit_evaluation_failure` | `pipeline.attempt_retry_evaluator_failure` | input shape matches a passing evaluator; the failure path is the agent's decision, not a renderer branch |

## Gap coverage (vs. the v1 gap report)

Closed:

* **Gap 1** — `planner_full_only` variant terminal catalog (case 12)
* **Gap 2** — rich `<attempt status="failed">` body. Closed natively in case 03 (the main scenario was updated to submit a valid plan + evaluator failure rather than an invalid plan rejected by validation). Case 13 keeps the same shape from a focused-reference scenario.
* **Gap 3** — `<dependency_results>` block in executor user_msg_1 (case 14)
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

# Captures case 12 from pipeline.partial_parent_planner_full_only
.venv/bin/pytest backend/src/task_center_runner/tests/sweevo/test_partial_parent_planner_full_only.py

# Captures cases 13..15 from the focused-reference scenarios
.venv/bin/pytest 'backend/src/task_center_runner/tests/sweevo/test_focused_scenarios.py::test_focused_reference_scenario_runs[pipeline.attempt_retry_evaluator_failure]' \
                 'backend/src/task_center_runner/tests/sweevo/test_focused_scenarios.py::test_focused_reference_scenario_runs[pipeline.dependency_dag_serial]'
.venv/bin/python scripts/regen_initial_messages_cases_gaps.py
```

Cases 12 was authored by hand from a single capture; if the
`partial_parent_planner_full_only` scenario's prompts shift, regenerate
case 12 manually by adapting the case-12 file from that run's
`goal_02/.../01_planner_*/message.jsonl`.
