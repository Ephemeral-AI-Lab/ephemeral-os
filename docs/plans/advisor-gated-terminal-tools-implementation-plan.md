# Advisor-Gated Terminal Tools — Implementation Plan

**Status:** implemented
**Owner:** TBD
**Last updated:** 2026-05-27

## 1. Goal

Block a main agent's terminal submission unless the most-recent advisor result in
its transcript:

- has `metadata["helper_role"] == "advisor"` and `metadata["verdict"] == "approve"`, **and**
- the originating `ask_advisor(tool_name=...)` call requested approval for the
  **exact terminal** being submitted now.

Approval for tool *X* never authorizes tool *Y*. Helper / subagent terminals
(`submit_advisor_feedback`, `submit_resolver_result`, `submit_exploration_result`)
are exempt by design — they don't carry main-role responsibilities.

### 1.1 Wording clarification

The original requirement said "tool result of `submit_advisor_feedback`," but in
the parent transcript there is no direct `submit_advisor_feedback` call — only
the `ask_advisor` `ToolResultBlock`, which inherits
`metadata={"helper_role":"advisor","verdict":...}` from the nested advisor's
`submit_advisor_feedback` (see `backend/src/tools/ask_helper/ask_advisor/ask_advisor.py:214-218`).
The hook treats them as the same thing because the metadata flows through verbatim.

## 2. Scope

### 2.1 Tools that get the gate

| Tool                              | Owner       | Gated? |
| --------------------------------- | ----------- | ------ |
| `submit_plan_closes_goal`         | planner     | yes    |
| `submit_plan_defers_goal`         | planner     | yes    |
| `submit_execution_success`        | executor    | yes    |
| `submit_execution_blocker`        | executor    | yes    |
| `submit_execution_handoff`        | executor    | yes    |
| `submit_evaluation_success`       | evaluator   | yes    |
| `submit_evaluation_failure`       | evaluator   | yes    |
| `submit_verification_success`     | verifier    | yes    |
| `submit_verification_failure`     | verifier    | yes    |
| `submit_advisor_feedback`         | advisor     | no — exempt |
| `submit_resolver_result`          | resolver    | no — exempt |
| `submit_exploration_result`       | explorer    | no — exempt |

### 2.2 Explicit non-goals

- **Payload-equivalence check.** Today an agent can do
  `ask_advisor(tool_name=X, tool_payload={stub})` → approve → submit `X(real_payload)`.
  The original spec doesn't mandate payload matching, and a payload hash would
  force re-consultation on whitespace changes. Filed as a known limitation; revisit
  in a follow-up phase if abuse is observed.
- **Role-based bypass inside the hook.** Exemption is at the *terminal-tool*
  level (only main terminals get the hook), not the *agent* level. If a
  subagent is misconfigured with a main-role terminal in `allowed_tools`, the
  gate fires — that's the correct defensive behavior.
- **Residual-risk callout parsing.** The advisor's job ends at the verdict.
  `executor.md:42` already tells the agent to read residual risks even on
  approve. Parsing summary prose would couple the hook to advisor formatting.

## 3. Design

### 3.1 The hook class

Single class, instance-per-terminal. Lives at
`backend/src/tools/submission/_advisor_approval_prehook.py`.

```python
class AdvisorApprovalPreHook:
    name: str           # "advisor_approval:<target_tool>"
    target_tool: str    # instance attribute — passes validate_hook_targets

    def __init__(self, target_tool: str) -> None:
        self.target_tool = target_tool
        self.name = f"advisor_approval:{target_tool}"

    async def run(self, tool_input, context) -> HookResult: ...
```

Hook validation (`backend/src/tools/_framework/core/hooks.py:113`) reads
`target_tool` via `getattr`, so an instance attribute is sufficient and avoids
class explosion.

### 3.2 Decision table

1. Read `context.conversation_messages` (always populated by
   `backend/src/tools/_framework/execution/tool_call.py:99-100` when the engine
   dispatches).
2. **Reverse-walk** to find the most recent `ToolResultBlock` with
   `metadata.get("helper_role") == "advisor"`.
3. **Pair** it: forward-walk for an assistant message containing a
   `ToolUseBlock` with `id == tool_use_id` and `name == "ask_advisor"`. Read
   `tool_use.input["tool_name"]`.
4. Decide:

   | Condition                                               | Verdict                                                                                        |
   | ------------------------------------------------------- | ---------------------------------------------------------------------------------------------- |
   | No advisor result found                                 | `fail` — "must call ask_advisor with this terminal first"                                      |
   | `is_error=True`                                         | `fail` — "previous advisor call failed; retry ask_advisor before submitting"                   |
   | `metadata["verdict"]` not in `{"approve","reject"}`     | `fail` — structural error                                                                       |
   | `verdict == "reject"`                                   | `fail` — echo `result.content` (the advisor's summary)                                         |
   | Matching `ToolUseBlock` not found (compaction/truncate) | `fail` — distinct reason ("approval found but originating call not in transcript")             |
   | `verdict == "approve"` but `tool_name != target_tool`   | `fail` — "advisor approved `<X>`; you are calling `<Y>`. Re-consult for `<Y>`."                 |
   | `verdict == "approve"` and `tool_name == target_tool`   | `pass`                                                                                          |

### 3.3 History-only invariant

`conversation_messages` passed to the hook is **history-only** —
`backend/src/engine/query/loop.py:308` appends `final_message` to `messages`
*after* `dispatch_assistant_tools` runs, so the current assistant turn (which
contains the terminal `ToolUseBlock`) is invisible to the prehook. The
"terminal must be called alone" rule
(`backend/src/engine/tool_call/dispatch.py:184-203`) prevents `ask_advisor` and
the terminal from sharing a turn, so the advisor result is always at least one
turn old by the time the hook fires.

## 4. Files

### 4.1 Add

- `backend/src/tools/submission/_advisor_approval_prehook.py` — hook + module docstring.
- `backend/tests/unit_test/test_tools/test_submission/test_advisor_approval_prehook.py` — 10 unit cases (see §6).
- `backend/tests/unit_test/test_tools/test_submission/_advisor_approval_fixtures.py` — shared helper `build_advisor_approval_messages(...)`. All updated existing tests import this — no test invents its own.

### 4.2 Modify — add `pre_hooks=(AdvisorApprovalPreHook("<own_name>"),)` to the `@tool(...)` decorator

1. `backend/src/tools/submission/planner/submit_plan_closes_goal/submit_plan_closes_goal.py`
2. `backend/src/tools/submission/planner/submit_plan_defers_goal/submit_plan_defers_goal.py`
3. `backend/src/tools/submission/executor/submit_execution_success/submit_execution_success.py`
4. `backend/src/tools/submission/executor/submit_execution_blocker/submit_execution_blocker.py`
5. `backend/src/tools/submission/executor/submit_execution_handoff/submit_execution_handoff.py`
6. `backend/src/tools/submission/evaluator/submit_evaluation_success/submit_evaluation_success.py`
7. `backend/src/tools/submission/evaluator/submit_evaluation_failure/submit_evaluation_failure.py`
8. `backend/src/tools/submission/verifier/submit_verification_success/submit_verification_success.py`
9. `backend/src/tools/submission/verifier/submit_verification_failure/submit_verification_failure.py`

### 4.3 Profile prompts — required, not optional

`backend/src/agents/profile/main/executor.md:22, 40-42` already documents
`ask_advisor` (allowed_tools line + 3-bullet flow). Replicate that pattern in:

- `backend/src/agents/profile/main/planner.md`
- `backend/src/agents/profile/main/evaluator.md`
- `backend/src/agents/profile/main/generator_verifier.md`

Without the prompt updates, the gate teaches via 400-style errors and the agent
burns iterations.

### 4.4 Will NOT modify

- `ask_advisor.py` — already produces the metadata the hook reads.
- `submit_advisor_feedback.py` — already stamps `helper_role`/`verdict`.
- `dispatch.py` / `loop.py` — `conversation_messages` is already threaded correctly.

## 5. Phase-by-phase execution

### Phase 0 — Pre-flight inventory (no commit)

Enumerate exact files to migrate so each later commit is small and reviewable.

```bash
grep -rn "submit_plan_closes_goal\|submit_plan_defers_goal\|submit_execution_success\|submit_execution_blocker\|submit_execution_handoff\|submit_evaluation_success\|submit_evaluation_failure\|submit_verification_success\|submit_verification_failure" backend/tests --include="*.py" > /tmp/gated_terminal_callsites.txt

grep -n "ask_advisor" backend/src/agents/profile/main/planner.md backend/src/agents/profile/main/evaluator.md backend/src/agents/profile/main/generator_verifier.md
```

Output of the first grep (initial estimate: 29 files) is the Phase 4 worksheet.

### Phase 1 — Hook module + test fixture (one commit)

**Hook (concrete sketch):**

```python
# backend/src/tools/submission/_advisor_approval_prehook.py
"""Pre-hook that gates main-agent terminal submissions on an advisor approval.

The hook scans ``context.conversation_messages`` for the most recent
``ask_advisor`` result, pairs it with its originating ``ToolUseBlock`` to
recover the ``tool_name`` argument, and rejects the terminal call unless
the advisor approved THIS specific terminal.

Wiring is per-terminal: each gated tool's ``@tool`` decorator carries
``pre_hooks=(AdvisorApprovalPreHook("<own_name>"),)``. Helper / subagent
terminals (``submit_advisor_feedback``, ``submit_resolver_result``,
``submit_exploration_result``) intentionally omit the hook.
"""

from __future__ import annotations

from pydantic import BaseModel

from message.message import Message, ToolResultBlock, ToolUseBlock
from tools._framework.core.context import ToolExecutionContextService
from tools._framework.core.hooks import HookResult


_ADVISOR_HELPER_ROLE = "advisor"
_VALID_VERDICTS = frozenset({"approve", "reject"})

_MSG_MISSING = (
    "BLOCKED: terminal submission requires an `approve` verdict from "
    "ask_advisor. Call ask_advisor(tool_name=\"{tool}\", tool_payload=...) "
    "and resubmit after the advisor approves."
)
_MSG_ADVISOR_FAILED = (
    "BLOCKED: the previous ask_advisor call errored out. Re-call "
    "ask_advisor with tool_name=\"{tool}\" before resubmitting."
)
_MSG_REJECTED = (
    "BLOCKED: advisor rejected the pending submission. Advisor summary:\n\n"
    "{summary}\n\n"
    "Address the issues, then re-call ask_advisor and obtain an approve."
)
_MSG_WRONG_TOOL = (
    "BLOCKED: advisor approved `{approved}`, but you are calling `{actual}`. "
    "Re-call ask_advisor with tool_name=\"{actual}\" for an approval specific "
    "to this terminal."
)
_MSG_STRUCTURAL = (
    "BLOCKED: advisor result is malformed (verdict={verdict!r}). Re-call "
    "ask_advisor with tool_name=\"{tool}\"."
)
_MSG_UNPAIRED = (
    "BLOCKED: found an advisor result in the transcript but its originating "
    "ask_advisor call is no longer visible (transcript compaction). Re-call "
    "ask_advisor with tool_name=\"{tool}\"."
)


class AdvisorApprovalPreHook:
    """Per-terminal hook: requires advisor approval for THIS tool."""

    def __init__(self, target_tool: str) -> None:
        self.target_tool = target_tool
        self.name = f"advisor_approval:{target_tool}"

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolExecutionContextService,
    ) -> HookResult[BaseModel]:
        messages = list(context.get("conversation_messages") or [])
        result_block, originating = _find_latest_advisor_pair(messages)

        if result_block is None:
            return HookResult.fail(_MSG_MISSING.format(tool=self.target_tool))
        if result_block.is_error:
            return HookResult.fail(_MSG_ADVISOR_FAILED.format(tool=self.target_tool))

        verdict = result_block.metadata.get("verdict")
        if verdict not in _VALID_VERDICTS:
            return HookResult.fail(
                _MSG_STRUCTURAL.format(verdict=verdict, tool=self.target_tool)
            )
        if verdict == "reject":
            summary = (result_block.content or "").strip() or "(no summary)"
            return HookResult.fail(_MSG_REJECTED.format(summary=summary))

        if originating is None:
            return HookResult.fail(_MSG_UNPAIRED.format(tool=self.target_tool))
        approved_tool = originating.input.get("tool_name")
        if approved_tool != self.target_tool:
            return HookResult.fail(
                _MSG_WRONG_TOOL.format(
                    approved=approved_tool or "(missing)",
                    actual=self.target_tool,
                )
            )
        return HookResult.pass_(tool_input)


def _find_latest_advisor_pair(
    messages: list[Message],
) -> tuple[ToolResultBlock | None, ToolUseBlock | None]:
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        for block in reversed(msg.content):
            if (
                isinstance(block, ToolResultBlock)
                and block.metadata.get("helper_role") == _ADVISOR_HELPER_ROLE
            ):
                originating = _find_originating_ask_advisor(messages, block.tool_use_id)
                return block, originating
    return None, None


def _find_originating_ask_advisor(
    messages: list[Message],
    tool_use_id: str,
) -> ToolUseBlock | None:
    for msg in messages:
        if msg.role != "assistant":
            continue
        for block in msg.tool_uses:
            if block.id == tool_use_id and block.name == "ask_advisor":
                return block
    return None


__all__ = ["AdvisorApprovalPreHook"]
```

**Fixture:**

```python
# backend/tests/unit_test/test_tools/test_submission/_advisor_approval_fixtures.py
"""Shared fixture: construct a synthetic ask_advisor approval transcript pair."""

from __future__ import annotations

from message.message import Message, ToolResultBlock, ToolUseBlock

_DEFAULT_ID = "toolu_test_advisor_approval"


def build_advisor_approval_messages(
    *,
    tool_name: str,
    verdict: str = "approve",
    summary: str = "ok",
    tool_payload: dict | None = None,
    tool_use_id: str = _DEFAULT_ID,
    is_error: bool = False,
) -> list[Message]:
    return [
        Message(
            role="assistant",
            content=[
                ToolUseBlock(
                    id=tool_use_id,
                    name="ask_advisor",
                    input={"tool_name": tool_name, "tool_payload": tool_payload or {}},
                )
            ],
        ),
        Message(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id=tool_use_id,
                    content=summary,
                    is_error=is_error,
                    metadata={"helper_role": "advisor", "verdict": verdict},
                )
            ],
        ),
    ]
```

**Verify:** `uv run python -c "from tools.submission._advisor_approval_prehook import AdvisorApprovalPreHook; AdvisorApprovalPreHook('x')"` — module imports cleanly, no circular imports.

### Phase 2 — Unit tests for the hook (one commit, TDD-style)

`backend/tests/unit_test/test_tools/test_submission/test_advisor_approval_prehook.py`

10 cases mirroring design §3.2:

| # | Setup                                                       | Expected           |
|---|-------------------------------------------------------------|--------------------|
| 1 | empty `conversation_messages`                               | fail (MISSING)     |
| 2 | approve for `target_tool`                                   | pass               |
| 3 | approve for a different tool                                | fail (WRONG_TOOL)  |
| 4 | latest is reject; summary populated                          | fail, summary echoed |
| 5 | 2 calls; latest approve for `target_tool`                   | pass               |
| 6 | 2 calls; latest reject, prior was approve-for-this           | fail (REJECTED)    |
| 7 | `is_error=True` on advisor result                           | fail (ADVISOR_FAILED) |
| 8 | `verdict == "approved"` (typo)                              | fail (STRUCTURAL)  |
| 9 | result present, originating `ToolUseBlock` absent           | fail (UNPAIRED)    |
| 10 | introspection: confirm helper terminals omit the hook       | pass (static guard) |

**Verify:** `.venv/bin/pytest backend/tests/unit_test/test_tools/test_submission/test_advisor_approval_prehook.py -v` → 10 passed.

> **Reminder:** use `.venv/bin/pytest`, not global pytest — see project memory `feedback_use_venv_pytest`.

### Phase 3 — Wire hook to 9 terminals (single commit)

Mechanical edit on each of the 9 terminal files in §4.2. Two lines per file:

```python
from tools.submission._advisor_approval_prehook import AdvisorApprovalPreHook  # NEW

@tool(
    name="submit_execution_success",
    ...
    is_terminal_tool=True,
    pre_hooks=(AdvisorApprovalPreHook("submit_execution_success"),),  # NEW
)
async def submit_execution_success(...): ...
```

**Verify (expect submission-test failures — that's the signal):**

```bash
.venv/bin/pytest backend/tests/unit_test/test_tools/test_submission/ -x
# Hook tests pass. Submission-tool tests that don't supply advisor approval
# now fail. Capture the failing list — that's the Phase 4 worksheet.
```

### Phase 4 — Migrate callsites (one commit per logical cluster)

Use the Phase 0 worksheet. Two patterns per file:

- **Pattern A — direct tool invocation in tests:** locate where the test builds
  `ToolExecutionContextService(...)` or `ExecutionMetadata(conversation_messages=...)`
  and prepend `build_advisor_approval_messages(tool_name="submit_<X>")` to that
  list.
- **Pattern B — full agent run (live e2e):** the agent will naturally call
  `ask_advisor` per the profile prompt (once Phase 5 lands). For pre-Phase-5
  scaffolds, inject a minimum-viable mock approval into the conversation prefix.

**Commit cadence:** one commit per directory cluster:

1. `backend/tests/unit_test/test_tools/test_submission/`
2. `backend/tests/unit_test/test_engine/`
3. `backend/tests/unit_test/test_task_center/`
4. live e2e dirs (one commit each — usually `backend/tests/live_e2e_test/...`)

**Verify after each cluster:** `.venv/bin/pytest <cluster_path>` green.

### Phase 5 — Profile prompt updates (one commit)

`executor.md` has the canonical 3-bullet block at lines 40-42 plus `ask_advisor`
in `allowed_tools:` at line 22. Copy that pattern into:

- `backend/src/agents/profile/main/planner.md`
- `backend/src/agents/profile/main/evaluator.md`
- `backend/src/agents/profile/main/generator_verifier.md`

**Verify:**

```bash
grep -l "ask_advisor" backend/src/agents/profile/main/*.md   # all 4 main profiles
.venv/bin/pytest backend/tests/unit_test/test_agents -v
```

### Phase 6 — Final integration check (no commit)

```bash
# Full unit + integration green
.venv/bin/pytest backend/tests/unit_test -x

# Static wiring check: exactly 9 main terminals + the hook module reference it,
# and zero helper terminals do.
grep -rn "AdvisorApprovalPreHook" backend/src/tools/submission/
# Expect: 1 hook module + 9 terminal files; submit_advisor_feedback,
# submit_resolver_result, submit_exploration_result NOT in the output.

# One live e2e: executor end-to-end through ask_advisor → submit_execution_success.
# Confirm hook_trace contains advisor_approval:submit_execution_success.
```

## 6. Test plan (`test_advisor_approval_prehook.py`)

See Phase 2 table for the 10 cases. Each test:

- Builds a `ToolExecutionContextService` with `conversation_messages` synthesized
  via `build_advisor_approval_messages(...)`.
- Instantiates `AdvisorApprovalPreHook(target_tool=<name>)`.
- Calls `.run(input_model, context)` with a dummy `BaseModel` instance.
- Asserts on `HookResult.status`, and on the failure reason substring for
  negative cases.

Case 10 is structural: import the 9 main terminals and the 3 exempt terminals,
inspect `tool.pre_hooks`, and assert presence / absence of
`AdvisorApprovalPreHook`. Guards against future drift.

## 7. Commit / rollback shape

| Commit | Scope                          | Reversible? |
|--------|--------------------------------|-------------|
| 1      | hook + fixture                 | yes — no consumers              |
| 2      | unit tests                     | yes                              |
| 3      | wire 9 terminals               | yes — revert one file = revert one terminal |
| 4a-d   | test migrations (4 clusters)   | yes                              |
| 5      | profile prompts                | yes                              |

If any commit breaks something outside the migration list, revert that single
commit and root-cause before continuing.

## 8. Risk summary

- **Highest:** Phase 4 test migrations missing a tucked-away invocation that
  doesn't surface in grep (e.g., constructed by a fixture builder). Mitigation:
  run `.venv/bin/pytest backend/tests/unit_test` after Phase 3 to surface every
  red test before Phase 4 begins.
- **Second:** profile-prompt drift — the hook teaches via errors if a profile
  doesn't mention `ask_advisor`. Phase 5 closes this.
- **Lowest:** circular imports — the hook depends only on `message.message` +
  `_framework.core.{context,hooks}`, all of which live below `tools/submission/`.

## 9. Evidence anchors

- Hook framework: `backend/src/tools/_framework/core/hooks.py:102-117`
  (validate_hook_targets), `backend/src/tools/_framework/execution/hook_pipeline.py:50-108`
  (pre-hook execution).
- Terminal-only batch rule: `backend/src/engine/tool_call/dispatch.py:177-203`.
- `conversation_messages` threading: `backend/src/tools/_framework/execution/tool_call.py:99-100`,
  `backend/src/engine/query/loop.py:130, 306-308`.
- Advisor return metadata: `backend/src/tools/submission/advisor/submit_advisor_feedback/submit_advisor_feedback.py:40-46`,
  `backend/src/tools/ask_helper/ask_advisor/ask_advisor.py:214-218`.
- Reference profile (template for prompt updates): `backend/src/agents/profile/main/executor.md:22, 40-42`.
- Existing pre-hook pattern: `backend/src/tools/sandbox/_lib/shell_policy.py:194-233`.

## 10. Implementation report (2026-05-27)

### 10.1 Phase status

| Phase | Scope                                | Status                                                       |
|-------|--------------------------------------|--------------------------------------------------------------|
| 0     | Pre-flight inventory                 | done                                                         |
| 1     | Hook module + shared fixture         | done                                                         |
| 2     | Unit tests for the hook              | done — 12 cases (10 from §6 + 2 bonus type/identity guards) |
| 3     | Wire hook to 9 terminals             | done                                                         |
| 4     | Migrate test callsites               | done — affected: 1 cluster                                   |
| 5     | Profile prompt updates               | already done before this implementation began                |
| 6     | Final integration check + cleanup    | done                                                         |

### 10.2 Files added

- `backend/src/tools/submission/_advisor_approval_prehook.py` — the
  `AdvisorApprovalPreHook` class and its history-walking helpers.
- `backend/tests/unit_test/test_tools/test_submission/__init__.py` — new
  subpackage for the gate-specific tests.
- `backend/tests/unit_test/test_tools/test_submission/_advisor_approval_fixtures.py`
  — single source of truth for the synthesized advisor-approval transcript pair
  (`build_advisor_approval_messages`).
- `backend/tests/unit_test/test_tools/test_submission/test_advisor_approval_prehook.py`
  — 12 unit cases covering the decision table and a structural guard that
  walks the registered terminals.

### 10.3 Files modified

- 9 terminal tools — added `from tools.submission._advisor_approval_prehook
  import AdvisorApprovalPreHook` and `pre_hooks=(AdvisorApprovalPreHook("<own
  name>"),)` on the `@tool(...)` decorator:
  - `submit_plan_closes_goal`, `submit_plan_defers_goal`
  - `submit_execution_success`, `submit_execution_blocker`, `submit_execution_handoff`
  - `submit_evaluation_success`, `submit_evaluation_failure`
  - `submit_verification_success`, `submit_verification_failure`
- `backend/tests/unit_test/test_tools/submission_test_utils.py` — extended
  `make_tool_context(...)` with an `advisor_approves` kwarg that prepends a
  synthetic ask_advisor approval pair to `conversation_messages`.
- `backend/tests/unit_test/test_tools/test_submission_planner_tools.py` —
  added `advisor_approves=...` at 3 call-site clusters.
- `backend/tests/unit_test/test_tools/test_submission_terminal_routing.py` —
  added `advisor_approves=...` at 8 call sites.
- `backend/tests/unit_test/test_task_center/test_lifecycle/test_phase03_submission_integration.py`
  — extended the local `_tool_context(...)` helper with `advisor_approves` and
  threaded it through three calls in the smoke test.

### 10.4 Verification

- `.venv/bin/pytest backend/tests/unit_test`: **2078 passed, 3 skipped** (3
  skips are pre-existing, unrelated to this work).
- `.venv/bin/pytest backend/tests/contracts`: **7 passed**.
- Static wiring check (`grep -rn "AdvisorApprovalPreHook"
  backend/src/tools/submission/`): exactly the hook module + 9 main terminal
  files appear; the 3 helper terminals (`submit_advisor_feedback`,
  `submit_resolver_result`, `submit_exploration_result`) are absent — matches
  the design.

### 10.5 Deferred items

1. ~~Empty `submit_execution_failure/` directory.~~ **Resolved** (post-implementation
   follow-up). The empty directory was removed; the tripwire test
   `test_tool_registry_renamed` asserts on the registry, not the filesystem,
   and continues to pass after the deletion.
2. **Payload-equivalence check** (plan §2.2). An agent can still call
   `ask_advisor(tool_name=X, tool_payload=<stub>)` → approve → submit
   `X(<real_payload>)`. Captured as a known limitation; revisit if abuse is
   observed.
3. **Residual-risk callout parsing** (plan §2.2). The advisor's verdict alone
   gates submission; summary prose is unread by the hook. The agent's profile
   prompt (`executor.md:42` and counterparts) tells the agent to read
   residual-risk bullets even on approve.
4. **Live e2e end-to-end check** (plan §6 — "executor end-to-end through
   ask_advisor → submit_execution_success"). Not run during this
   implementation — the full unit suite + contracts suffice to verify the
   gate. A live e2e pass is recommended before the next release that touches
   the engine loop.
5. **Cross-package test import.** `test_phase03_submission_integration.py`
   uses the absolute form `from tests.unit_test.test_tools.test_submission._advisor_approval_fixtures
   import build_advisor_approval_messages` (the only cross-tree import of this
   shape in the unit suite). Both forms work; the absolute form is clearer
   than `from ...test_tools.test_submission._advisor_approval_fixtures` would
   be. Left as-is.
6. **New `test_submission/` subpackage breaks the flat-file convention.**
   Existing submission tests live as siblings in
   `backend/tests/unit_test/test_tools/` (e.g.
   `test_submission_tool_registration.py`,
   `test_submission_planner_tools.py`). This implementation introduced a
   subpackage at `backend/tests/unit_test/test_tools/test_submission/` to
   host the hook tests + shared fixture, because the plan (§4.1, §6) was
   explicit about that layout. The deviation is deliberate — a future
   reviewer should not flatten it without first revisiting whether the
   shared fixture needs to live elsewhere.
7. **`ask_advisor`-wrapper crash leaks stale approvals.** When the outer
   `ask_advisor` tool itself errors (e.g. `runtime_config` missing, helper
   compose failure, or the inner advisor exits without
   `submit_advisor_feedback` — see
   `backend/src/tools/ask_helper/ask_advisor/ask_advisor.py:174-212`), the
   returned `ToolResultBlock` has `is_error=True` but no `helper_role`
   metadata. The hook's reverse-walk filters by
   `metadata["helper_role"] == "advisor"` and therefore skips that block,
   continuing past it to find any older valid approval. A prior approval for
   the same terminal would then satisfy the gate. Cost is bounded — at worst
   one extra free submission per session — and agent profiles already tell
   agents to retry on advisor failure. A one-line guard (detect a recent
   `ask_advisor` `ToolUseBlock` whose paired result lacks
   `helper_role` metadata) would close it cleanly. Deferred to a follow-up.

### 10.6 Commit / rollback shape — actual

The implementation was performed in-place across overlapping concerns; no
intermediate commits were created (parallel-agent worktree with a dirty tree
on entry). To split for review:

1. Hook module + fixture + unit test (`backend/src/tools/submission/_advisor_approval_prehook.py`,
   `backend/tests/unit_test/test_tools/test_submission/`).
2. Terminal wiring (9 `@tool(...)` decorators).
3. Shared helper change + test migrations
   (`submission_test_utils.py`, `test_submission_planner_tools.py`,
   `test_submission_terminal_routing.py`,
   `test_phase03_submission_integration.py`).
4. Plan doc status + report update (this section).

---

> _Note (2026-05-27):_ The helper-terminal exemption list in this plan
> originally included `submit_resolver_result`. That terminal and the
> `ask_resolver` helper were deleted by the
> [remove-ask-resolver](./remove-ask-resolver.md) plan. The current
> exemption list is `{submit_advisor_feedback, submit_exploration_result}`.
