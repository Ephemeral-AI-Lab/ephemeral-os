# Advisor-Gated Terminal Tools — Testing Plan

**Status:** draft
**Owner:** TBD
**Last updated:** 2026-05-27
**Companion to:** `docs/plans/advisor-gated-terminal-tools-implementation-plan.md`

## 1. Goal

Verify that `AdvisorApprovalPreHook` (the gate from the companion
implementation plan) holds across the test pyramid:

- **Unit** — the hook's decision table in isolation.
- **Submission integration** — gated terminals fail-closed when the
  approval pair is absent, fail-with-the-right-reason when it's
  wrong, and accept the call when it's right.
- **Live e2e (mock squad)** — the deterministic mock squad still drives a
  full planner → executor → evaluator scenario end-to-end through the gate.
- **Live e2e (real LLM, optional tier-7)** — the production engine loop
  invokes `ask_advisor` and a real advisor verdict, threaded through the
  hook, lets the terminal submit.

The unit and submission-integration layers are already covered by the
implementation plan and pass green (`2078 + 7 + 12` tests). This plan
focuses on what's outstanding: the **mock-squad live e2e** layer, plus a
small set of negative-path additions to lock the gate's behavior in
production paths.

## 2. Scope

### 2.1 In scope

- Update the `MockSquadRunner` (`backend/src/task_center_runner/agent/mock/runner.py`)
  so it synthesizes the advisor-approval transcript pair before every
  gated terminal call. The mock squad does not run the engine loop, so
  the engine never threads a real `ask_advisor` result through it; without
  this shim every existing live e2e scenario will trip the gate.
- A negative-path mock scenario or addition to existing scenarios that
  asserts the gate *does* block a terminal when the synthesized approval
  is intentionally for the wrong tool.
- One end-to-end run of `test_correctness_testing_via_live_e2e` (the
  generic-runner variant at
  `backend/src/task_center_runner/tests/mock/task_center/test_correctness_via_live_e2e.py`)
  against a live sandbox via `run_tiered.py --tier 7` (the
  existing tier-7 entry-point), with the hook enabled.
- A tier-7 production path test that exercises the real `ask_advisor`
  tool (no mock squad) for one terminal — proves the production
  `ask_advisor` → advisor agent → `submit_advisor_feedback` → gate path
  works.

### 2.2 Out of scope

- Payload-equivalence verification (deferred per implementation plan §10.5 #2).
- Residual-risk callout parsing (deferred per #3).
- The `ask_advisor`-wrapper crash gap (deferred per #7).
- Performance regression budgets — the hook is a constant-time history
  walk over already-bounded message lists.

## 3. Layer-by-layer test plan

### 3.1 Unit (done — confirm only)

`backend/tests/unit_test/test_tools/test_submission/test_advisor_approval_prehook.py`
— 12 cases, all green. No changes needed.

Pass criterion: `12 passed` on
`.venv/bin/pytest backend/tests/unit_test/test_tools/test_submission/`.

### 3.2 Submission integration (done — confirm only)

The following files were migrated to thread `advisor_approves=<tool_name>`
through `make_tool_context(...)` / `_tool_context(...)`:

- `backend/tests/unit_test/test_tools/test_submission_planner_tools.py`
- `backend/tests/unit_test/test_tools/test_submission_terminal_routing.py`
- `backend/tests/unit_test/test_task_center/test_lifecycle/test_phase03_submission_integration.py`

Pass criterion: full unit suite plus contracts stay green
(`.venv/bin/pytest backend/tests/unit_test backend/tests/contracts`).

### 3.3 Mock-squad live e2e (new work)

**Problem.** The mock squad bypasses the engine loop entirely. Each
gated terminal call inside `MockSquadRunner._call_tool(...)` is sent to
`execute_tool_once`, which runs the pre-hook pipeline — but the
`conversation_messages` threaded through the mock's `metadata` are empty
(see `MockSquadRunner._metadata_for(...)` —
`backend/src/task_center_runner/agent/mock/runner.py`). Without
intervention every existing scenario starts failing at the first
planner submission.

**Fix.** Inject a synthetic advisor approval pair into
`conversation_messages` right before each `_call_tool(submit_*, …)`.
The pair must target the specific terminal about to be called. This
mirrors what the unit-test fixture
`build_advisor_approval_messages(tool_name=...)` does and reuses the
same helper to avoid drift.

#### 3.3.1 Implementation steps

1. **Centralize the approval injection.** Add a private helper to
   `MockSquadRunner`, e.g.

   ```python
   def _approve_terminal(
       self,
       metadata: ExecutionMetadata,
       tool: BaseTool,
   ) -> ExecutionMetadata:
       """Return a metadata copy with a synthesized advisor approval
       prepended to ``conversation_messages``. Idempotent: a stamped
       approval for the same tool is left alone."""
       ...
   ```

   The helper imports `build_advisor_approval_messages` from
   `backend.tests.unit_test.test_tools.test_submission._advisor_approval_fixtures`
   **only if** the fixture file moves under `src/`. Tests-only code must
   not live under `src/`, so instead **promote the helper** to a shared
   location — recommended path:
   `backend/src/task_center_runner/agent/mock/_advisor_approval.py`,
   re-exporting the same shape. The unit-test fixture then imports from
   there too (single source of truth, no test→src layering inversion).

2. **Wire it into terminal sites.** In each `_run_planner`,
   `_run_executor`, `_run_verifier`, `_run_evaluator`, wrap the
   `_call_tool(submit_*, ...)` calls so the metadata passed in is
   approved for that tool. Concretely, replace

   ```python
   result = await self._call_tool(spec.tool, dict(spec.args), metadata, emit)
   ```

   with

   ```python
   gated_metadata = self._approve_terminal(metadata, spec.tool)
   result = await self._call_tool(spec.tool, dict(spec.args), gated_metadata, emit)
   ```

   For `_run_executor`, the executor's intermediate non-terminal tool
   calls (`shell`, `write_file`, …) keep the original metadata. Only
   the final `submit_execution_success` / `submit_execution_blocker` /
   `submit_execution_handoff` get the approval shim.

3. **Audit-trail invariant.** The synthetic approval pair must NOT leak
   into the persisted message.jsonl. If the mock runner records the
   metadata-threaded messages to the audit tree, exclude approval-pair
   blocks at the recorder boundary (filter by
   `block.metadata.get("helper_role") == "advisor"` AND
   `block.tool_use_id` matching the synthesized id). Verify by reading
   `message.jsonl` in the existing e2e test (it already asserts on the
   tool-use payload — extend the existing
   `_assert_message_jsonl_contains_sandbox_tools` helper).

4. **Negative-path coverage.** Add one mock scenario (or extend
   `CorrectnessTesting`) where:
   - The synthesized approval intentionally names the wrong terminal
     (e.g. approve `submit_plan_defers_goal` while the scenario calls
     `submit_plan_closes_goal`).
   - The scenario expects an `EventType.PLANNER_INVOKED` followed by a
     gate-block (the gated tool returns `is_error=True` with the
     `BLOCKED:` prose); the attempt remains in PLAN stage and the
     planner retries.

   This proves the gate actually fires inside the mock pipeline (not
   just that the shim passes around it).

#### 3.3.2 Tests touched / added

- **Modify**:
  `backend/src/task_center_runner/tests/mock/task_center/test_correctness.py`
  and
  `backend/src/task_center_runner/tests/mock/task_center/test_correctness_via_live_e2e.py`
  — no scenario-level changes; the hook injection happens in the
  runner. Both tests should pass unchanged after step 3.3.1 (1)–(3).
- **Add**: a new focused-scenarios test under
  `backend/src/task_center_runner/tests/mock/task_center/test_focused_scenarios.py`
  (or a peer file) that drives the negative-path scenario from
  3.3.1 (4) and asserts on the resulting event stream — at minimum
  `EXECUTOR_FAILURE` or a new
  `EventType.GATE_BLOCKED_TERMINAL_SUBMISSION` (define if absent).
- **Add**: a contract test under
  `backend/src/task_center_runner/tests/mock/contracts/` that confirms
  every main terminal currently in
  `tools.submission.make_submission_tools()` has the
  `AdvisorApprovalPreHook` on its `pre_hooks` tuple. This mirrors
  Case 10 of the unit test, but at the package-level surface that the
  runner imports — guards against future drift inside the runner's
  late-import block.

#### 3.3.3 Pass criteria

1. `task_center_integration` marker green:

   ```bash
   .venv/bin/pytest -m task_center_integration backend/src/task_center_runner/tests/mock
   ```

2. The two correctness tests
   (`test_correctness_testing_scenario_runs_end_to_end` and
   `test_correctness_testing_via_live_e2e`) pass with the hook
   actively gating each terminal submission. Run the second variant
   under tier-7:

   ```bash
   uv run python backend/src/task_center_runner/scripts/run_tiered.py --tier 7
   ```

3. The new negative-path scenario produces exactly one gate-block event
   per planned terminal, no false positives on the happy path.

4. `message.jsonl` files emitted under the run dir do not contain the
   synthesized advisor approval pair.

### 3.4 Tier-7 production-path live e2e (no mock squad)

The mock-squad coverage proves the **hook reads its inputs correctly**
under realistic state. It does not prove the **engine produces those
inputs correctly** in production (i.e. that the engine's
`conversation_messages` thread actually carries the real
`ask_advisor` result when the real LLM-driven agent calls it). One
tier-7 test closes that gap.

#### 3.4.1 Scope

A single scenario that runs the real engine loop with the real
`ask_advisor` tool (not the mock squad) end-to-end through:
`planner agent → ask_advisor (real advisor agent) →
submit_plan_closes_goal`. The advisor agent must return `verdict:
approve` for the planner to clear the gate.

#### 3.4.2 Approach

- Add a thin scenario under
  `backend/tests/live_e2e_test/.../test_advisor_gate_live.py` (peer to
  existing `live_e2e_test` files) that:
  1. Boots a live sandbox.
  2. Launches a planner with a trivial goal whose only valid terminal
     is `submit_plan_closes_goal`.
  3. Asserts the planner's transcript contains exactly one
     `ask_advisor` call and one `submit_advisor_feedback` result
     before the terminal.
  4. Asserts the gate fired with `status=pass` for that terminal (i.e.
     the persisted hook_trace metadata, if recorded — otherwise
     by-construction: the terminal would not have submitted without an
     approval).

- Skip when no live sandbox is available, mirroring
  `test_correctness_testing_via_live_e2e`'s skip pattern.

#### 3.4.3 Pass criteria

1. The terminal submission succeeds (returns `is_terminal=True`,
   `is_error=False`).
2. The transcript shows an `ask_advisor` `ToolUseBlock` paired with a
   `ToolResultBlock` carrying `metadata["helper_role"] == "advisor"` and
   `metadata["verdict"] == "approve"`.
3. The hook's `hook_trace` entry (if observable in the recorded result
   metadata) shows `status=pass` and
   `hook_name=advisor_approval:submit_plan_closes_goal`.

#### 3.4.4 Cost / runtime caveat

- Tier-7 currently runs one comprehensive `correctness_testing`
  scenario. Adding a second long-running live scenario doubles
  live-sandbox cost on each tier-7 invocation. Decision required: include
  in tier-7 default suite, or behind a separate
  `--tier 7-advisor-gate` opt-in. Recommendation: opt-in initially,
  promote to default after one successful run.

## 4. Open questions

- **OQ-1**: should the synthesized approval be visible to scenarios for
  inspection (so a scenario author can intentionally vary it for
  negative tests), or completely hidden? Recommend visible — a
  `ScenarioContext.advisor_approval_override` field that defaults to
  "approve for the upcoming terminal" and can be replaced.
- **OQ-2**: do we want a dedicated `EventType.GATE_BLOCKED_TERMINAL_SUBMISSION`
  on the audit bus? Useful for §3.3.1 (4) but adds a new public event.
  Alternative: re-use `EXECUTOR_FAILURE`/`PLANNER_*_FAILURE` and
  assert on the failure payload prefix.
- **OQ-3**: tier-7 cost (§3.4.4) — opt-in or default?

## 5. Sequencing

1. Promote `build_advisor_approval_messages` from the unit-test fixture
   to a shared module under `src/` (§3.3.1 (1)).
2. Wire injection into `MockSquadRunner` terminal call sites (§3.3.1 (2)).
3. Filter synthetic blocks at the message.jsonl boundary (§3.3.1 (3))
   and confirm the existing audit-tree assertions still pass.
4. Add the negative-path mock scenario + test (§3.3.1 (4)).
5. Add the contract test (§3.3.2 bullet 3).
6. Pick OQ-2 (event vs. failure-payload) and OQ-3 (tier-7 inclusion).
7. Write the tier-7 production-path test (§3.4).
8. Run tier-7 once, baseline the new test, decide whether to promote.

## 6. Verification commands

```bash
# Unit + submission-integration (regression)
.venv/bin/pytest backend/tests/unit_test/test_tools/test_submission/
.venv/bin/pytest backend/tests/unit_test/test_tools/test_submission_planner_tools.py \
                 backend/tests/unit_test/test_tools/test_submission_terminal_routing.py \
                 backend/tests/unit_test/test_task_center/test_lifecycle/test_phase03_submission_integration.py

# Mock-squad live e2e (after §3.3 work)
.venv/bin/pytest -m task_center_integration backend/src/task_center_runner/tests/mock

# Tier-7 (real live sandbox)
uv run python backend/src/task_center_runner/scripts/run_tiered.py --tier 7
```

## 7. Evidence anchors

- Mock runner entry point: `backend/src/task_center_runner/agent/mock/runner.py:158-287`.
- Mock metadata builder (no `conversation_messages` today):
  `backend/src/task_center_runner/agent/mock/runner.py:289-312`.
- Mock terminal call site: `backend/src/task_center_runner/agent/mock/runner.py:325-345`.
- Existing live e2e (generic runner): `backend/src/task_center_runner/tests/mock/task_center/test_correctness_via_live_e2e.py`.
- Existing live e2e (SWE-EVO adapter): `backend/src/task_center_runner/tests/mock/task_center/test_correctness.py`.
- Mock conftest + integration markers: `backend/src/task_center_runner/tests/mock/conftest.py`.
- Existing fixture to reuse: `backend/tests/unit_test/test_tools/test_submission/_advisor_approval_fixtures.py:build_advisor_approval_messages`.
- Hook target the test exercises: `backend/src/tools/submission/_advisor_approval_prehook.py`.
