# Remove `ask_resolver` / resolver role; grant write tools to verifier and evaluator

## RALPLAN-DR Summary (Short Mode)

### Principles
1. **Role purity over capability sprawl** — verifier/evaluator stay scoped to verification; write tools are a small accommodation for trivial fixes, not a license to implement. *This is a deliberate writer/judge collapse: the agent that grades success can now also nudge its own subject. We accept that trade because the advisor pre-terminal gate sees the full edit-input transcript and can reject scope-creep, and downstream verification catches bad approvals (see §7 Risk 1 + Risk 2).*
2. **Prose + visibility is the boundary, not code** — the "typos only" intent is enforced by (a) profile prose, (b) the advisor seeing full edit inputs in the parent transcript, and (c) the advisor approval gate already required for terminals. No new runtime guardrail.
3. **Delete fearlessly when behavior collapses to a subset of an existing path** — resolver was a thin specialization of "verifier with edit tools." Removing it is net negative-LOC and removes one helper-spawn variant.
4. **Single-mode helper transcript** — collapse `TranscriptMode` to advisor-only; do not preserve resolver-mode plumbing for hypothetical reuse.
5. **Tool-call budget is already the loop cap** — `tool_call_limit: 50` plus advisor-rejection feedback is sufficient; no separate edit-iteration counter.

### Decision Drivers
1. **Complexity reduction**: one less helper agent kind, one less helper spawn variant, one less transcript mode, one less soft-reminder notification trigger, one less terminal submission. Net ~6 deleted files plus ~12 file simplifications.
2. **Latency/cost**: every `ask_resolver` call is a full ephemeral agent spawn with its own LLM round-trips. Direct verifier/evaluator edits skip this — typo fixes that took 1 spawn + 2-3 LLM turns now take a single `edit_file` call.
3. **Advisor gate already exists**: terminal submissions from verifier/evaluator are already advisor-gated, and the advisor sees the parent's full tool-use transcript including any `edit_file`/`write_file` calls (the `_ADVISOR_STRIP_INPUT_TOOLS` set strips Claude Code's `Edit`/`Write` literal names, NOT EphemeralOS's `edit_file`/`write_file`). The safety net is already wired.

### Viable Options

**Option A — Delete ask_resolver, grant write tools directly (USER'S PROPOSAL) [SELECTED]**
- Pros: Cuts ~6 files entirely, simplifies 12+ more, removes 1 transcript mode, removes 1 notification trigger, removes 1 helper-spawn variant, removes resolver_history tracking. Latency reduction for trivial fixes (no helper spawn). Advisor visibility of edits is already in place.
- Cons: Verifier/evaluator role-purity erosion risk (mitigated by prose + advisor visibility). No hard cap on edit-loop iterations (mitigated by existing `tool_call_limit: 50`). Introduces a *new* failure mode: the verifier mutates the workspace before the advisor sees the success payload; an advisor reject blocks the terminal but does not revert the edit (see §7 Risk 2).

**Option B — Keep ask_resolver but simplify (e.g., inline it, drop helper agent)**
- Pros: Preserves the typed boundary "verifiers don't write code."
- Cons: Still requires a helper-spawn variant, the `submit_resolver_result` terminal, resolver_history, transcript-mode plumbing, resolver_limit notification. Does not address the user's stated complexity concern. ~80% of the deletion work would not happen.

**Option C — Hybrid: delete ask_resolver, but add a new "edit_budget" notification trigger (max 4 inline edits)**
- Pros: Hard-coded ceiling on edit-loop divergence.
- Cons: Replaces one indirection with another. The `resolver_limit` trigger this would mimic was already a soft warning, not a hard stop. Adds runtime code where the user's explicit goal was code removal. The existing `tool_call_limit: 50` already covers pathological loops; an edit-specific budget is speculative.

**Invalidation rationale:** Options B and C both add or preserve code paths the user explicitly named as "unnecessary complexity." A passes the user's stated bar; B and C fail it.

---

## Plan

### 1. Goal
Remove the `ask_resolver` helper tool, the `resolver` agent kind, the `submit_resolver_result` terminal, the resolver-mode transcript, and the `resolver_limit` notification trigger; grant `write_file` + `edit_file` to the verifier and evaluator profiles with explicit prose-level scope guidance for "small inline fixes only."

### 2. Loop-control decision

**Choice: (a) — accept existing `tool_call_limit: 50` as the loop cap.**

Rationale:
- `resolver_limit` was a *soft* warning at 4 unresolved helper-spawn calls, specifically because each spawn was expensive. Inline edits are cheap (one tool call each), so a per-spawn budget no longer applies.
- The hard cap is and remains `tool_call_limit: 50` + `max_tolerance_after_max_tool_call: 10`. A verifier that loops more than ~5 edits is already pathological and the advisor (called pre-terminal) will reject a payload that claims success after that much churn.
- **Budget arithmetic note:** an edit-cycle ≈ 3 tool calls (`edit_file` + `read_file` + a `shell` recheck). The prose 3-4-edit cap means ≈12 tool calls per worst-case edit loop, leaving ≥38 budget for the initial verification scan plus advisor calls — well within the 50 ceiling.
- **Notification triggers go to `[]` (was `[resolver_limit]`).** No replacement reminder is introduced. Per Principle #5, the `tool_call_limit` + prose self-restraint is the loop cap. If observability shows edit-loop divergence in practice, a generic edit-budget reminder can be added later (see §8 Follow-ups).
- **Mitigation in prose, not code:** add one sentence to each updated profile body:
  > *Inline edits count against your `tool_call_limit`. If you've made more than 3-4 edits without converging, the issue is implementation work — submit the failure terminal and let the planner replan.*

### 3. Prompt-boundary replacement text

#### 3a. New body in `backend/src/agents/profile/main/generator_verifier.md` (replaces line 25 paragraph **and** the `submit_verification_failure` bullet at line 38)

Replace line 25 paragraph with:
```
Check whether assigned generator output satisfies the `Assigned Task`. Use
read-only inspection and verification commands first.

If you find a defect that is **trivial and unambiguous** — a typo, a wrong
variable name, an off-by-one, a missing import, a comment fix, formatting —
you may call `edit_file` or `write_file` to correct it inline, then re-check.

Do NOT edit inline when:
- The fix requires understanding the generator's intent.
- The fix touches control flow or branching.
- The fix needs new or updated tests.
- The fix spans more than one file.
- You are not sure whether the fix is correct.

In any of those cases, call `submit_verification_failure` with concrete
issues. The advisor will reject success submissions that include edits
exceeding this scope, so self-check before calling `ask_advisor`.

If the advisor rejects your success submission specifically because your
prior edit exceeded scope, do NOT attempt to revert via another edit.
Submit `submit_verification_failure` with the rejected scope-violation
issue echoed in your failure summary (this will require a fresh
`ask_advisor` call for the failure terminal per the Submission
discipline section; the advisor can approve a failure terminal that
admits the scope violation even when it just rejected the success
terminal for the same edit). The next iteration will inherit the
mutated workspace and plan accordingly.

Inline edits count against your `tool_call_limit`. If you've made more
than 3-4 edits without converging, the issue is implementation work —
submit the failure terminal and let the planner replan.
```

Replace the `submit_verification_failure` bullet (line 38) with:
```
- `submit_verification_failure` — unresolved issues remain after any inline-edit attempt (or no edit was safe). The attempt's failure handling reads the outcome.
```

#### 3b. New body in `backend/src/agents/profile/main/evaluator.md` (replaces line 25)
```
Run after every generator task in the attempt has passed. Evaluate the
current attempt against its `<plan_spec>`, per-task `<task>` summaries,
and `<evaluation_criteria>` — all of which appear inside the
`<attempt status="current">` body.

If an evaluation criterion fails due to a **trivial and unambiguous**
defect — a typo, wrong variable name, missing import, formatting,
single-line obvious bug — you may call `edit_file` or `write_file` to
correct it inline, then re-evaluate against the same criteria.

Do NOT edit inline when:
- The failure indicates the attempt's plan is wrong, not its execution.
- The fix requires understanding generator intent across multiple tasks.
- The fix touches control flow, schemas, or contracts.
- The fix needs new or updated tests.
- The fix spans more than one file.
- You are not sure whether the fix is correct.

In any of those cases, call `submit_evaluation_failure`. The advisor
will reject success submissions whose edits exceed this scope, so
self-check before calling `ask_advisor`.

If the advisor rejects your success submission specifically because your
prior edit exceeded scope, do NOT attempt to revert via another edit.
Submit `submit_evaluation_failure` with the rejected scope-violation
issue echoed in your failure summary (this will require a fresh
`ask_advisor` call for the failure terminal per the Submission
discipline section; the advisor can approve a failure terminal that
admits the scope violation even when it just rejected the success
terminal for the same edit). The next iteration will inherit the
mutated workspace and plan accordingly.

Inline edits count against your `tool_call_limit`. If you've made more
than 3-4 edits without converging, the issue is attempt-level rework —
submit the failure terminal and let the graph enter retry handling.
```

#### 3c. New "Do NOT call this when" body in `backend/src/tools/submission/verifier/submit_verification_failure/prompt.py:19-22`
```python
Do NOT call this when:
- Everything passed — use `{SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME}`.
- The defect is trivial and unambiguous (typo, wrong variable name,
  missing import, off-by-one, formatting, single-line obvious bug) and
  fits in one file — apply the fix inline via `{EDIT_FILE_TOOL_NAME}`
  or `{WRITE_FILE_TOOL_NAME}`, re-run the verification check, and submit
  `{SUBMIT_VERIFICATION_SUCCESS_TOOL_NAME}` if it now passes. Call this
  terminal only when the defect requires understanding intent, touches
  control flow, needs new tests, spans multiple files, or you are not
  confident the fix is correct.
```
(import statement at top of file: drop `ASK_RESOLVER_TOOL_NAME`, add `EDIT_FILE_TOOL_NAME`, `WRITE_FILE_TOOL_NAME`)

#### 3d. Update `backend/src/tools/_terminals/registry.py` (the `submit_verification_failure` descriptor)
- `selection_guidance`: replace "Call when unresolved issues remain after the resolver-edit cycle." with "Call when issues remain that the verifier could not safely fix inline (touches intent, control flow, tests, or multiple files)."
- `advisor_review_focus`: replace last sentence "...so the failure routes to the right resolver." with "...so the failure surfaces in the right scope. Also flag verifier inline edits that exceed the typo/single-line scope (a verifier doing implementation work should have failed the task instead)."

#### 3e. Update `backend/src/tools/ask_helper/ask_advisor/prompt.py` (lines 27-32)
Remove the `ASK_RESOLVER_TOOL_NAME` import. Replace the "Fixing problems — the advisor only audits and cannot edit. Use ask_resolver..." bullet with:
```
- Fixing problems — the advisor only audits and cannot edit. Verifier and
  evaluator agents may apply trivial inline fixes themselves via
  `edit_file`/`write_file` (typo, wrong variable name, single-line
  obvious bug); the advisor's job is to confirm those fixes do not exceed
  that scope before approving a success terminal.
```

### 4. Atomic file change list

**Sequencing rule:** every commit boundary must be green. Tests are moved into the same commit as the production deletion they cover, per project CLAUDE.md ("This codebase is edited across multiple agent sessions at the same time" — broken intermediate commits are a hazard for parallel sessions).

**Commit 1 — Grant write tools + rewrite role prose (no deletions yet; ask_resolver still callable as fallback)**
- `backend/src/agents/profile/main/generator_verifier.md` — add `write_file`, `edit_file` to `allowed_tools`; remove `ask_resolver` from `allowed_tools`; change `notification_triggers: [resolver_limit]` to `notification_triggers: []`; replace body per §3a (both the line-25 paragraph and the line-38 terminal-tools bullet).
- `backend/src/agents/profile/main/evaluator.md` — add `write_file`, `edit_file` to `allowed_tools`; remove `ask_resolver` from `allowed_tools`; change `notification_triggers: [resolver_limit]` to `notification_triggers: []`; replace body per §3b.
- `backend/src/tools/submission/verifier/submit_verification_failure/prompt.py` — rewrite description per §3c; fix imports.
- `backend/src/tools/_terminals/registry.py` — rewrite `submit_verification_failure` selection_guidance + advisor_review_focus per §3d.
- `backend/src/tools/ask_helper/ask_advisor/prompt.py` — drop `ASK_RESOLVER_TOOL_NAME` import; rewrite "Fixing problems" bullet per §3e.
- UPDATE: `backend/tests/unit_test/test_agents/test_agent_markdown.py:33-34` — change RHS from `["resolver_limit"]` to `[]` for both `verifier.notification_triggers` and `evaluator.notification_triggers` (mirrors the profile-MD change in this same commit; without this move, commit 1 leaves the test red). The negative assertion at line 60 (`"ask_resolver" not in executor.allowed_tools`) is about the `executor` profile (which never had `ask_resolver`) and stays true — no change needed. Any remaining resolver-MD-specific assertions in this file stay in commit 5.

**Commit 2 — Delete `ask_resolver` tool surface + coupled tests/contracts**
- DELETE: `backend/src/tools/ask_helper/ask_resolver/` (whole package: `ask_resolver.py`, `prompt.py`, `__init__.py`).
- `backend/src/tools/ask_helper/__init__.py` — drop `ask_resolver` import, `make_ask_helper_tools()` entry, and `__all__` entry.
- `backend/src/tools/_framework/core/runtime.py:44` — update the `ask_advisor / ask_resolver` parenthetical comment to `ask_advisor`.
- DELETE: `backend/tests/unit_test/test_tools/test_ask_resolver_retry.py` (verified by grep: this file is a mirror of `test_ask_advisor_retry.py`; all retry-machinery coverage is in the advisor variant. Pure resolver-specific; safe to delete here so commit 2 stays green).
- UPDATE: `backend/tests/contracts/test_tool_intent_drift.py` — drop the `"tools.ask_helper.ask_resolver.ask_resolver"` entry (line 39) **in this commit** so `importlib` does not crash on the deleted module.
- UPDATE: `backend/tests/unit_test/test_tools/test_submission_helper_tools.py` — **full resolver-scrub lands here, not in commit 4** (per Critic SEQ-FIX-3, retaining `ask_resolver` re-export until commit 4 is more error-prone). Drop both imports (line 16 `ask_resolver`, line 18 `submit_resolver_result`), the resolver fixture (`_context(role="resolver")` helper, line 118), and both resolver test functions (`test_submit_resolver_result_metadata_drives_unresolved_count` at ~line 109 and `test_ask_resolver_assembles_direct_launch` at ~line 259). After this scrub the file references only advisor/explorer machinery; the dropped `from tools.submission.resolver import submit_resolver_result` line is forward-compatible with the resolver-package deletion in commit 4 (no other code in this file uses it).

**Commit 3 — Collapse helper-transcript to advisor-only (signature-change package: every caller of `build_helper_messages` and `build_parent_transcript` updated in lock-step)**

*Caller audit (verified via `git grep -nE 'build_helper_messages|build_parent_transcript' backend/`):*
- `build_helper_messages` production callers: `ask_advisor.py:180`, `ask_resolver.py:74` (the latter is already deleted in commit 2 — moot).
- `build_helper_messages` test callers: `test_ask_advisor_retry.py` (`_fake_build` monkeypatch at line ~90, applied via `setattr` at line 103). `test_ask_resolver_retry.py:90` is already deleted in commit 2.
- `build_parent_transcript` production caller: `_compose.py:116` (internal, updated below).
- `build_parent_transcript` test callers: `test_transcript_block.py` only, with 13 advisor-mode sites and 4 resolver-mode sites.
- No further callers exist.

*Production edits:*
- `backend/src/tools/ask_helper/_lib/_transcript.py`:
  - Drop the `TranscriptMode` type alias entirely.
  - Delete `_render_tool_use_resolver` (lines 88-91).
  - Delete the resolver-mode `else` arms in `_render_block` (keep only advisor-mode behavior inline; no branching on `mode` remains).
  - Simplify `build_parent_transcript` (line 161): drop the `mode` parameter from the signature; hard-code `drop_count = 2`; drop the docstring's resolver-mode section.
  - Thinking-block handling: keep only the advisor-mode behavior (return None / drop the block).
  - Remove `"TranscriptMode"` from `__all__` (line 204).
- `backend/src/tools/ask_helper/_lib/_compose.py`:
  - Remove `mode: TranscriptMode` parameter from `build_helper_messages` (line 72) — it's now always advisor.
  - Update the internal call at line 116: change `build_parent_transcript(parent_messages, mode=mode)` to `build_parent_transcript(parent_messages)`.
  - Remove `TranscriptMode` from the imports at line 28 (no "or keep" hedge — Critic verified `TranscriptMode` has no external consumers) (COMMIT-3-FIX-4).
  - Update module docstring lines 1-16: remove "/ ask_resolver", remove "issues for the resolver" reference, simplify to "advisor-only."
- `backend/src/tools/ask_helper/ask_advisor/ask_advisor.py:180-182` — drop the `mode="advisor"` kwarg from the `build_helper_messages(...)` call so the call site matches the new signature. After the edit the call reads:
  ```python
  messages = build_helper_messages(
      helper_role="advisor", context=context
  )
  ```
  Without this fix, commit 3 raises `TypeError: build_helper_messages() got an unexpected keyword argument 'mode'` at runtime (COMMIT-3-FIX-1).

*Test edits (all land in this same commit):*
- UPDATE: `backend/tests/unit_test/test_tools/test_ask_helper/_lib/test_transcript_block.py` — two-part change (COMMIT-3-FIX-2):
  1. **Delete the 4 resolver-mode tests** at lines 63, 119, 197, 207 (and any surrounding test-function scaffolding that exists solely to exercise resolver mode — e.g., a test whose body is just lines 197+207 in sequence has no advisor-mode analog and should be removed wholesale). Verify with `grep -c "mode=\"resolver\"" backend/tests/unit_test/test_tools/test_ask_helper/_lib/test_transcript_block.py` — expected 0 after the edit.
  2. **Strip `mode="advisor"` from every remaining advisor-mode call site** — 13 sites at lines 62, 68, 87, 105, 127, 141, 158, 172, 185, 223, 237, 250, 264. After the strip, calls read e.g. `build_parent_transcript([])` and `build_parent_transcript(msgs)` with no `mode=` kwarg. Verify with `grep -c "build_parent_transcript(.*mode=" backend/tests/unit_test/test_tools/test_ask_helper/_lib/test_transcript_block.py` — expected 0 after the edit.
- UPDATE: `backend/tests/unit_test/test_tools/test_ask_advisor_retry.py:90` — change `_fake_build` signature from `(*, helper_role, mode, context)` to `(*, helper_role, context)` so the monkeypatch fake matches the new real `build_helper_messages` signature (COMMIT-3-FIX-3). Without this fix, production code calls the monkeypatched fake with no `mode` kwarg, and since the original `_fake_build` declares `mode` as a keyword-only parameter with no default, the call raises `TypeError: _fake_build() missing 1 required keyword-only argument: 'mode'`. Drop any internal references to `mode` inside the `_fake_build` body too.

**Commit 4 — Delete resolver helper agent + submission terminal + history + notification + coupled tests**
- DELETE: `backend/src/agents/profile/helper/resolver.md`.
- DELETE: `backend/src/tools/submission/resolver/` (whole package: `submit_resolver_result/`, `__init__.py`).
- `backend/src/tools/submission/_factory.py` — drop `submit_resolver_result` import + registration in `make_submission_tools()`.
- `backend/src/tools/submission/_advisor_approval_prehook.py` — update the docstring comment at line 10: drop `submit_resolver_result` from the helper-terminal exemption list.
- DELETE: `backend/src/tools/submission/notification_triggers/resolver_limit.py`.
- `backend/src/tools/submission/notification_triggers/__init__.py` — drop `make_resolver_limit_reminder` import, `factories` entry, `__all__` entry.
- DELETE: `backend/src/tools/submission/resolver_history.py`.
- UPDATE: `backend/tests/unit_test/test_tools/test_submission_soft_reminders.py` — delete `_resolver_messages` fixture + `test_resolver_limit_reminder_fires_at_four` test in this commit (depends on the deleted notification trigger).
- UPDATE: `backend/tests/unit_test/test_tools/test_submission_tool_registration.py` — drop `submit_resolver_result`.
- UPDATE: `backend/tests/contracts/test_tool_intent_drift.py:54` — drop `"tools.submission.resolver.submit_resolver_result.submit_resolver_result"` entry.
- UPDATE: `backend/tests/unit_test/test_tools/test_submission/test_advisor_approval_prehook.py:202-206, 220-226` — remove `"submit_resolver_result"` from the `helper_terminals` tuple in `test_hook_wired_to_main_terminals_and_omitted_from_helpers` so its `create_tool("submit_resolver_result", ctx)` does not raise. The assertion shape (loop over `helper_terminals`) is unchanged.
- UPDATE: `backend/src/task_center_runner/tests/mock/contracts/test_advisor_gate_wiring.py:37` — drop `submit_resolver_result` from mocked terminal list.
- UPDATE: `backend/tests/unit_test/test_agents/test_helper_profile_identity_sentences.py` — delete `test_resolver_profile_body_contains_identity_sentence` (lines 28-30); it calls `_read_profile("helper/resolver.md")` which raises `FileNotFoundError` the moment this commit deletes `helper/resolver.md`. The `test_advisor_profile_body_contains_identity_sentence` at lines 23-25 is untouched. Any other resolver edits in this file (if present) stay in commit 5.

**Commit 5 — Drop resolver from agent-kind enum + role tables + coupled test**
- `backend/src/agents/definition/model.py` — drop `AgentKind.RESOLVER` (line 41); update docstring lines 31, 90-91 (remove "/ resolver" / "RESOLVER").
- `backend/src/agents/definition/loader.py:67-68` — drop "resolver" from the validation error message.
- `backend/src/task_center/context_engine/role_directives.py:23` — drop `"resolver": …` entry from `ROLE_DIRECTIVES`.
- `backend/src/task_center/agent_launch/task_guidance_dispatch.py:8` — drop "resolver" from comment.
- `backend/src/tools/_names.py` — drop `ASK_RESOLVER_TOOL_NAME` and `SUBMIT_RESOLVER_RESULT_TOOL_NAME` constants and `__all__` entries.
- UPDATE: `backend/tests/unit_test/test_engine/test_agent_system_prompt.py:49` — remove the `("resolver", AgentKind.RESOLVER),` parametrize row in `test_main_role_base_not_injected_at_runtime` (the symbol `AgentKind.RESOLVER` is deleted in this commit). Keep advisor + explorer rows.
- UPDATE: `backend/tests/unit_test/test_agents/test_agent_markdown.py` — drop any **remaining** resolver-MD assertions tied to deletions landing in this commit (the line-33-34 `notification_triggers` change already moved to commit 1; grep for residual `resolver` mentions in this file and remove anything that references `AgentKind.RESOLVER`, the resolver helper profile, or the deleted `ask_resolver` from non-executor profiles). If grep returns empty after commits 1-4, this bullet becomes a no-op — fine to drop from the commit.
- UPDATE: `backend/tests/unit_test/test_task_center/test_context_engine/test_role_directives.py` — drop resolver directive test (tied to the `ROLE_DIRECTIVES["resolver"]` deletion in this same commit).
- UPDATE: `backend/src/task_center_runner/scenarios/pipeline/initial_messages_capture.py:100` — drop resolver from comment.
- (REMOVED: `test_helper_profile_identity_sentences.py` update — moved to commit 4 per SEQ-FIX-2, because deleting `helper/resolver.md` in commit 4 breaks the test before commit 5 runs.)

**Commit 6 — Capacity-pack catalog cleanup**
- UPDATE: `backend/src/task_center_runner/scenarios/capacity/pack_catalog.py:234-243` — delete the `CapacityPackSpec("context.helper_resolver_inheritance", ...)` entry. **Triage result (read both files):** the spec entry points at the generic `test_recipes_other.py` shared with ~10 other capacity packs, but no recipe named `helper_resolver_inheritance` exists under `backend/src/task_center/context_engine/recipes/` and no test in `test_recipes_other.py` matches the name (`grep -n "helper_resolver_inheritance|resolver_inheritance|helper_resolver" backend/src/task_center/context_engine/recipes/ backend/tests/.../test_recipes_other.py` returns empty). It is an orphan registration for a recipe that was never built. Deletion is safe; no recipe file or test removal needed.

**Commit 7 — Add NEW tests for verifier/evaluator inline-edit workflow + strip-set freeze**
- ADD: `backend/tests/unit_test/test_agents/test_verifier_evaluator_edit_tools.py` (new file) — asserts:
  - `verifier` profile's `allowed_tools` contains `edit_file` and `write_file`.
  - `verifier` profile's `allowed_tools` does NOT contain `ask_resolver`.
  - `evaluator` profile's `allowed_tools` contains `edit_file` and `write_file`.
  - `evaluator` profile's `allowed_tools` does NOT contain `ask_resolver`.
  - Neither profile lists `resolver_limit` in `notification_triggers` (both equal `[]`).
- ADD (in `test_transcript_block.py` or a new sibling): one case asserting that a parent transcript containing an `edit_file` tool_use renders the **full input**, AND a strip-set freeze assertion:
  ```python
  from tools.ask_helper._lib._transcript import _ADVISOR_STRIP_INPUT_TOOLS
  assert _ADVISOR_STRIP_INPUT_TOOLS == frozenset({"Edit", "Write", "NotebookEdit"}), (
      "Per remove-ask-resolver plan §7 Risk 1, this constant must NOT gain "
      "'edit_file' or 'write_file' — the advisor seeing edit inputs verbatim "
      "is the load-bearing scope-creep gate. If you change this, update the "
      "plan."
  )
  ```
  This codifies the load-bearing safety net (lowercase EphemeralOS names not stripped) in code, not just prose.

**Commit 8 — Documentation refresh**
Each updated page below also refreshes its `data-last-reviewed-commit` to the head of this branch and prunes deleted `data-evidence-paths` entries.

- `docs/architecture/task_center/agent-roles.html` — rewrite lines 117, 118, 126 (verifier/evaluator row: "Read-only shell/search plus ask_advisor and ask_resolver" → "Read/write shell/search plus ask_advisor; may inline-edit for trivial defects per profile prose"); delete the resolver helper-profiles subsection (lines 194-200) entirely.
- `docs/architecture/task_center/terminal-tools.html` — trim/rewrite lines 202-249 "helper results notifications" section: remove ask_resolver, resolver_history, resolver_limit references; keep advisor-related machinery only.
- `docs/architecture/tools/submission.html` — drop resolver row (lines 62, 76).
- `docs/architecture/tools/index.html:224` — update advisor/resolver helper text: remove resolver mention; note that verifier/evaluator have direct edit tools for trivial fixes.
- `docs/architecture/index.html:55` — rewrite the TaskCenter-to-tools blurb: drop "resolver" from the planner/executor/verifier/evaluator/advisor/explorer enumeration.
- `docs/architecture/tools/ask-helper.html` — substantive rewrite:
  - Drop the "Resolver Flow" TOC entry at line 50 and the entire `#resolver-flow` section (lines 100-107).
  - Rewrite the lead at line 56: "`ask_advisor` synchronously launches a helper agent and returns its terminal output to the caller."
  - Drop the `ask_resolver` row from the tool-shape table at line 64.
  - Update `data-evidence-paths` on every section that listed `ask_resolver.py`, `submit_resolver_result.py`, or `test_ask_resolver_retry.py` (sections at lines 57, 69, 100, 122).
  - Update prose at lines 76, 80, 85, 124 to drop resolver mentions.
- `docs/architecture/task_center/maintenance.html` — line 61 (drop "advisor/resolver" → "advisor"), line 127 (drop "resolver behavior"), line 144 (drop "and `ask_resolver`"), line 153 (drop "/resolver" from the changelog entry; or leave it as historical changelog text — prefer dropping since the entire module is gone).
- `docs/architecture/agent_loops/prompt-context.html:261` — replace the parenthetical mentioning `resolver_v1` with one that mentions only `advisor_v1`, or drop the parenthetical entirely (the surrounding statement is "there are no live helper recipes named X" — listing the deleted name is misleading).
- `docs/architecture/task_center/index.html:134,138` — drop "resolver" from the verifier/evaluator/advisor/resolver/explorer enumerations.
- `docs/architecture/tools/hooks.html:179` — drop `submit_resolver_result` from the `(submit_advisor_feedback, submit_resolver_result, ...)` exemption list. Keep `submit_advisor_feedback` + `submit_exploration_result`.
- `docs/architecture/assets/search-index.js` — **manually edited** (this file is hand-authored; the heading comment confirms `window.ARCHITECTURE_DOC_SEARCH = [...]` is a single literal array, no generator script lives under `scripts/`). Edits:
  - Line 152: rewrite the `ask-helper` entry text to drop `and ask_resolver`.
  - Line 156: delete the entire `'id': 'resolver-flow'` entry.
  - Line 191: rewrite the `agent-roles` entry text to drop `resolver,` from the enumeration.
  - Line 194: delete the entire `'id': 'helper-profiles'` entry (its text and tags are resolver-centric: "Advisor, resolver, and explorer helper profiles…"; after deletion the helper-profiles row only contains advisor + explorer which is no longer a distinct section worth indexing). Verify the matching anchor is removed from `agent-roles.html` first.
  - Line 196: rewrite the `role-submission-terminals` entry text to drop `resolver,` from the enumeration.
- `docs/architecture/task_center/bridges.html:201` — **NO change**. The "Submission context resolvers" phrase is a generic noun (about ContextScope resolution machinery), unrelated to the resolver agent. Confirmed by reading `backend/src/task_center/context_engine/scope.py:27`.
- `docs/architecture/index.html:55` — already covered above.
- `docs/task_center_harness_and_context_engine.html` — historical, leave untouched (per project CLAUDE.md: "Treat the older TaskCenter harness reference … as historical background and stale-claim comparison material").
- `docs/plans/advisor-gated-terminal-tools-implementation-plan.md` — historical, do NOT rewrite. Optionally append a one-line footer noting "Policy table superseded by remove-ask-resolver plan (date)."
- VERIFY (likely no-op): `scripts/regen_initial_messages_cases.py`, `scripts/build_initial_messages_report.py`, `backend/config/skills/evaluator/SKILL.md` — grep for "resolver"; update only if the matches are real (not substring noise on words like "unresolved").

### 5. Test plan

**Tests that change:**
- 1 DELETE: `test_ask_resolver_retry.py` (340 lines, mirror of advisor variant; pure resolver coverage). Lands in commit 2.
- 1 DELETE-LIKE: `test_submission_helper_tools.py` loses ~75 lines (resolver fixture + 2 resolver tests + 2 imports). Lands in commit 2 (per SEQ-FIX-3: the `ask_resolver` re-export disappears in commit 2, so the test scrub must land there or the import breaks).
- ~8 UPDATES across `test_tools/`, `test_agents/`, `test_task_center/`, `contracts/`, `task_center_runner/tests/mock/` — each landed in the same commit as the production deletion it shadows (per §4 sequencing rule).

**New tests required (commit 7):**
- `test_verifier_evaluator_edit_tools.py` — profile-level registration of `edit_file`/`write_file` and absence of `ask_resolver`/`resolver_limit` (`notification_triggers == []`). This is the **codification of the policy change in code**, not just prose.
- New `test_transcript_block.py` case: (a) assert advisor sees full `edit_file` input in parent transcript, and (b) assert `_ADVISOR_STRIP_INPUT_TOOLS == frozenset({"Edit", "Write", "NotebookEdit"})` exactly, with an in-code comment naming this plan as the reason the constant must not gain `edit_file` / `write_file`.

**Loop-control coverage:** No new test for `tool_call_limit: 50` — the existing limit-enforcement tests already cover it; no change to that behavior.

### 6. Verification commands

```bash
# Type & lint (Python 3.11 target per pyproject.toml)
uv run ruff check backend/src backend/tests

# Unit tests — narrow to changed surfaces first
uv run .venv/bin/pytest backend/tests/unit_test/test_tools/ -x
uv run .venv/bin/pytest backend/tests/unit_test/test_agents/ -x
uv run .venv/bin/pytest backend/tests/unit_test/test_task_center/test_context_engine/ -x
uv run .venv/bin/pytest backend/tests/contracts/test_tool_intent_drift.py -x

# Per-commit greenness gate (run AFTER each commit, not just at the end —
# the §4 sequencing rule depends on each boundary being green for parallel
# agent sessions):
uv run .venv/bin/pytest backend/tests/unit_test/ backend/tests/contracts/ -q

# task_center_runner mocks
uv run .venv/bin/pytest backend/src/task_center_runner/tests/mock/contracts/ -x

# Sanity grep: nothing real should remain. Use \b to catch bare "resolver"
# tokens in addition to the compound names. Allowlist applied for known
# intentional survivors.
git grep -nE '\bresolver\b|ask_resolver|submit_resolver_result|AgentKind\.RESOLVER|resolver_limit|resolver_history|ASK_RESOLVER_TOOL_NAME|SUBMIT_RESOLVER_RESULT_TOOL_NAME' backend/ docs/ \
  | grep -vE 'unresolved' \
  | grep -vE 'docs/plans/advisor-gated-terminal-tools-implementation-plan' \
  | grep -vE 'docs/task_center_harness_and_context_engine.html' \
  | grep -vE 'backend/src/task_center/context_engine/scope.py.*Submission context resolvers' \
  | grep -vE 'docs/architecture/task_center/bridges.html.*Submission context resolvers'
# Expected: empty output. If anything matches, triage manually — the grep is
# intentionally broad and the allowlist must stay narrow (per Critic SC-4).
```

(Per project MEMORY: use `.venv/bin/pytest`, not global pytest — pytest-asyncio is only registered in the uv venv. Use `ruff` from the venv too.)

### 7. Risks + mitigations

**Risk 1 — Role-purity erosion (verifier becomes mini-executor).**
- *Mitigation:* Explicit prose boundary in `generator_verifier.md` and `evaluator.md` (§3a, §3b) — concrete enumeration of "edit inline when…" and "do NOT edit when…" cases. Reinforced in `submit_verification_failure` description (§3c).
- *Mitigation:* The advisor sees the full `edit_file`/`write_file` inputs in the parent transcript (NOT stripped — `_ADVISOR_STRIP_INPUT_TOOLS` matches Claude Code's literal `Edit`/`Write`/`NotebookEdit`, not EphemeralOS's lowercase `edit_file`/`write_file`). This is the load-bearing enforcement of the policy and is **frozen by a constant-equality assertion** in commit 7's new test. **Do not add `edit_file`/`write_file` to the strip set** — the visibility is the gate.
- *Mitigation:* Updated `submit_verification_failure` registry advisor_review_focus (§3d) explicitly tells the advisor to flag verifier edits exceeding scope.

**Risk 2 — Workspace mutation precedes the advisor gate (NEW FAILURE MODE).**
- *Acceptance (honest):* This IS a new failure mode that did not exist with `ask_resolver`. The verifier/evaluator mutates the workspace via `edit_file`/`write_file` **before** the advisor sees the success-submission payload. An advisor reject blocks the terminal but **does not** revert the edit. The workspace is now corrupted relative to the agent's stated intent.
- *Containment story* (no auto-rollback; this is by design — see Principles #1, #2):
  - (a) The strip-set freeze test in commit 7 prevents accidental loss of advisor visibility, keeping the reject path effective.
  - (b) The explicit profile guidance in §3a/§3b instructs the agent: when the advisor rejects an edit for scope, submit the `*_failure` terminal with the scope-violation issue echoed in the summary — do NOT attempt an edit-revert (which would itself be a scope-creep edit).
  - (c) Downstream verification (next iteration's planning context, executor re-verification, integration tests) picks up the corrupted state and plans accordingly. This is the same containment pattern as "advisor approves a bad payload" — the bad state surfaces in the next loop.
- *Operator-facing signal:* monitor for `verification_success` / `evaluation_success` rate correlating with `edit_count > 0` for that agent. A drop in downstream verification or integration-test pass-rate following inline-edit-heavy iterations is the early warning that this risk is materializing.

**Risk 3 — Verifier/evaluator loop divergence (edit-fail-edit-fail).**
- *Mitigation:* No new code; `tool_call_limit: 50` + `max_tolerance_after_max_tool_call: 10` is the hard ceiling. The §2 budget arithmetic (3 tool calls per edit cycle × 4-edit cap = 12, leaves ≥38 budget) demonstrates the existing cap is sufficient.
- *Mitigation:* Prose-level guidance in both profiles: "If you've made more than 3-4 edits without converging, submit the failure terminal."
- *Acceptance:* If divergence happens in practice (observable via tool-call audit), the *next* iteration can add a typed edit-budget; do not speculate now.

**Risk 4 (called out but not requiring active mitigation) — Historical resolver references in `docs/task_center_harness_and_context_engine.html`.**
- *Decision:* Leave untouched. Per project CLAUDE.md, that page is "historical background and stale-claim comparison material." Updating it would violate the architecture-docs refresh boundary.

### 8. ADR

**Decision:** Delete `ask_resolver` tool, `resolver` agent kind, `submit_resolver_result` terminal, `resolver_limit` notification trigger, `resolver_history` module, resolver helper profile, and resolver-mode transcript. Grant `write_file` and `edit_file` to `verifier` and `evaluator` profiles. Enforce "trivial fixes only" via profile prose, the advisor's pre-terminal approval gate (which sees full edit inputs in the transcript), and the existing `tool_call_limit: 50`.

**Drivers:**
1. User-stated goal: remove unnecessary complexity. Resolver was a thin specialization of "verifier with write tools."
2. Latency / cost: typo fixes no longer require a full helper-agent spawn.
3. Existing safety nets (advisor visibility of edit inputs, advisor pre-terminal gate, `tool_call_limit`) already cover the scenarios `resolver_limit` and the resolver helper role were originally guarding — except for the workspace-mutation-before-gate race named in §7 Risk 2, which is accepted with explicit downstream containment.

**Alternatives considered:**
- *Option B:* Keep `ask_resolver` but simplify (inline it, drop helper agent). Rejected: still preserves ~80% of the surface the user wants gone.
- *Option C:* Delete `ask_resolver` but add an `edit_budget` notification trigger. Rejected: replaces one indirection with another; speculative; `tool_call_limit` already covers pathological loops.

**Why chosen:** Option A is the minimal change that satisfies the user's stated bar. Net deletion across ~7 files (one capacity-pack spec entry added); simplifies ~12 more. No new runtime code. Existing advisor visibility + prose boundary is sufficient because the advisor already sees `edit_file`/`write_file` inputs verbatim (the strip set targets Claude Code's literal names only).

**Consequences:**
- Verifier and evaluator now operate in a writer-grades-own-work pattern. The current resolver split provided independent re-verification by a fresh agent context with a different role-directive frame (`<issues>` vs `<assigned_task>`); deleting it replaces that separation with (1) lenient advisor audit of the parent's full edit-input transcript, (2) prose-level scope self-restraint, and (3) downstream containment in the next iteration. This is a deliberate architectural trade of separation for spawn-cost reduction; operators should monitor whether verifier/evaluator self-success after inline edit correlates with downstream regressions (see §7 Risk 2 operator signal).
- The helper-spawn machinery is now advisor-only: simpler `TranscriptMode`, simpler `_compose.py`, one less notification trigger.
- `AgentKind.RESOLVER` is gone — downstream audit consumers reading `metadata["role"]` will never see `"resolver"`. Historical audit data with that value remains valid (no migration needed).
- The advisor pre-terminal gate is now load-bearing for verifier/evaluator success submissions that include inline edits. If the advisor approves out-of-scope edits, the workspace is already mutated; downstream verification catches the bad state (same containment as existing advisor failure modes, plus the workspace-mutation caveat in §7 Risk 2).

**Follow-ups:**
- Observe in production whether verifier/evaluator edit loops actually converge in 3-4 edits. If divergence appears, add a typed `edit_budget` notification trigger then — do not pre-build it.
- Wire an ops metric: per-agent `inline_edit_count` × downstream-verification-pass-rate correlation. This is the early-warning signal for §7 Risk 2 materializing.
- Consider whether `ask_advisor` should grow a `reviewed_edits_count` field in its summary metadata for ops visibility (optional; not in this plan).
- After this change, `_compose.py` and `_transcript.py` could be inlined back into `ask_advisor.py` since they're single-caller. Defer this collapse to a separate plan if desired; it is pure simplification but not on the critical path.
