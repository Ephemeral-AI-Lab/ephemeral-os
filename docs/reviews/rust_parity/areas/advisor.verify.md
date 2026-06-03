# Advisor pass-verdict gate — INDEPENDENT VERIFICATION

Verifier scope: re-derived every anchor from source (Python = ground truth, Rust = port). The
investigation (`advisor.md`) is substantively accurate: no false match was found in its claims, and
no `investigator_missed` (it never called a broken thing a match — it correctly flagged the inert
stub as HIGH). I sharpen two verdict framings: invariant 2 is a `confirmed_disparity` (the
investigation hedged "partial"), and invariant 4's match is scoped to helper/subagent *exclusion*
only (the gated-*inclusion* set diverges on root and is counted under invariant 1 / D2).

## Invariant verdict table

| # | Invariant | independent_status | severity | decisive bilateral anchor |
| --- | --- | --- | --- | --- |
| 1 | Root AND workflow agents must call `ask_advisor` to choose the terminal payload | confirmed_disparity | medium (intentional) | Python gates workflow only — `submit_planner_outcome.py:63-67`, `submit_generator_outcome.py:49`, `submit_reducer_outcome.py:49`; root carries ONLY `RequireNoInflightBackgroundTasks` (`submit_root_outcome.py:42`). Rust gates root TOO (`meta.rs:72-75` adds `Hook::AdvisorApproval` to `SubmitRootOutcome`). Checklist's "root+workflow" matches **Rust**, not Python ground truth → logged as Python-parity disparity. See D2. |
| 2 | A PASS verdict must be received before terminal submission is allowed | confirmed_disparity | high | Mechanism present + test-proven: `hooks.rs:574-597` `run_advisor_approval` → `approval.approved` ⇒ `Pass`. But NO production code path returns `approved:true`: the only production impl `AdvisorService::approval_status` returns `approved:false, reason:"missing"` unconditionally (`notifications.rs:226-231`); the only approver is `#[cfg(test)] ApprovingAdvisor` (`app_state.rs:599-622`). Python's real check is `verdict=="approve"` + tool-pair (`advisor_approval.py:80-89`). Default build ⇒ PASS unachievable. See D1. |
| 3 | A non-pass verdict BLOCKS terminal submission (enforced, not advisory) | confirmed_match | (info) | Python `HookResult.fail` (`advisor_approval.py:61-64`) → framework converts to in-band error, not stamped terminal. Rust `HookOutcome::Deny` (`hooks.rs:588-596`) short-circuits at `execution.rs:58-60` BEFORE `tool.executor().execute()` (`:65`) and `stamp_terminal` (`:71,106-114`). Executor-never-runs is tested (`execution.rs:227-270`). Mechanism is a real MATCH; the decision feeding it is stubbed (cross-ref D1), so the block fires unconditionally in production. |
| 4 | Which roles are gated is correct (subagents/helpers excluded) | confirmed_match (scoped to helper/subagent EXCLUSION) | (info) | `submit_advisor_feedback.py:25-32` has `is_terminal_tool=True` and NO `pre_hooks`; `submit_exploration_result.py:28` `is_terminal_tool=True` NO `pre_hooks`. Rust: neither `SubmitAdvisorFeedback` nor `SubmitExplorationResult` appears in an explicit `tool_hooks` arm → both fall to `_ => Vec::new()` (`meta.rs:60-87`). Clean bilateral exclusion match. NOTE: the *inclusion* half (which roles ARE gated) diverges on root — see invariant 1 / D2; this verdict covers exclusion only. |

Constant/operator extraction: Python compares `verdict == "approve"` against
`_VALID_VERDICTS = frozenset({"approve","reject"})` (`advisor_approval.py:24,81,83`). Rust has **no
verdict literal** in the production gate path — the comparison is collapsed to
`AdvisorApproval{approved: bool}` decided behind the port (`ports.rs:234-240,253`); no
`"approve"`/`"reject"` string is compared anywhere in production Rust gate code. The reason-tag
taxonomy (`missing/advisor_failed/structural/rejected/unpaired/wrong_tool`) is documented at
`ports.rs:230-233` but only `"missing"` is ever emitted (`notifications.rs:229`). The
literal-verdict comparison the checklist asks to compare has no production Rust analog. Block-message
text matches verbatim (`advisor_approval.py:32-36` vs `hooks.rs:451-454`). Hook ORDER matches:
Python planner tuple `RequireNoInflight → DisallowNestedPlannerDeferral → AdvisorApproval`
(`submit_planner_outcome.py:63-67`) == Rust `meta.rs:80-84`.

## Disparity adjudication

- **D1 (advisor logic + runner are non-functional production stubs, HIGH): CONFIRMED.**
  Independently re-derived every anchor. `AdvisorService::approval_status` returns
  `approved:false, reason:"missing"` for every tool (`notifications.rs:226-231`).
  `AdvisorService::review` returns the literal `"Advisor runner is not wired for ... engine-only
  phase."` (`notifications.rs:216-224`). Default wiring is the stub:
  `advisor.unwrap_or_else(|| Arc::new(AdvisorService))` (`app_state.rs:482-484`). Exhaustive
  `grep` for `impl AdvisorPort` returned EXACTLY two: the deny stub (`notifications.rs:215`) and
  `#[cfg(test)] ApprovingAdvisor` (`app_state.rs:604`). No production conversation-scanning impl
  exists. The investigation's claim is exact. Intentional-phase framing is accurate
  (`notifications.rs:207-208,222`; `app_state.rs:260-262`) but does NOT change the parity verdict —
  a documented gap is still a gap.

- **D2 (Rust gates `submit_root_outcome`; Python does not, MEDIUM/intentional): CONFIRMED.**
  Python root carries only `RequireNoInflightBackgroundTasks` (`submit_root_outcome.py:42`); only
  planner/generator/reducer import `AdvisorApprovalPreHook`. Rust adds `Hook::AdvisorApproval` to
  `SubmitRootOutcome` with the explicit "EOS decision ... diverges from the Python backend ...
  intentionally omits root" comment (`meta.rs:68-71`). Blast radius re-confirmed by the runtime
  tests: `root_terminal_blocked_without_advisor_approval` (`tests.rs:208-238`) asserts a well-formed
  `submit_root_outcome` under the default `AdvisorService` fails with `fail_reason="root_run_exhausted"`
  and request status `failed`; `successful_root_keeps_engine_terminal` (`tests.rs:166-200`) passes
  ONLY by injecting `ApprovingAdvisor`. The Python ground-truth behavior (root completes with no
  advisor) is unreachable in Rust's default build.

- **D3 (`ask_advisor` result drops `is_error`/verdict metadata, LOW/latent): CONFIRMED, and
  STRENGTHENED.** Python `ask_advisor.py:206-211` forwards `output` + `is_error` + `metadata`
  (carrying `helper_role`/`verdict`) — the exact fields `_classify` later reads off the conversation
  (`advisor_approval.py:80,102`). Rust `advisor.rs:47-51` returns `ToolResult::ok(output)` over the
  bare `String` from `review()`: no `is_error` projection, empty metadata. Strengthening note: Rust
  `AdvisorPort::approval_status(target_tool: &str)` (`ports.rs:253`) receives NO conversation
  argument, and `ExecutionMetadata` (`metadata.rs`) carries no `conversation_messages` field — so
  Python's "the gate scans the transcript" design (`ports.rs:251-252` doc) is not directly portable
  to the current seam. A real impl needs another channel for the verdict, which makes D3 a hard
  prerequisite for any conversation-scanning `approval_status`. Reinforces open-question #2.

## New findings

- **N1 — invariant-4 framing correction (verdict-level, not a code finding).** The investigation
  called invariant 4 a "clean bilateral match." That is only true for the *exclusion* sub-claim
  (helper/subagent terminals omit the hook). The *inclusion* sub-claim (which roles ARE gated)
  diverges on root and is the same fact as D2. I scope invariant 4's match to exclusion and
  cross-reference invariant 1 so the reader does not read "the gated role set is correct" — it is
  not (root differs). No new code anchor; this prevents a false "match" in the audit's own ledger.
- **N2 — invariant-2 status correction.** The investigation hedged "partial." The discriminating
  test (does any production path return `approved:true`?) answers no, so the PASS-before-submit
  dynamic is unachievable in a default build ⇒ `confirmed_disparity`. The mechanism is present and
  test-proven via the `#[cfg(test)]` fake, but a passing self-test on the fake is not evidence the
  production dynamic works. Precise shape: wiring/impl gap, not a broken mechanism.
- **N3 (confirm).** E2 (block-message verbatim), E3 (hook ordering, now positively checked against
  the planner tuple `submit_planner_outcome.py:63-67`), E4 (`BlockInIsolatedMode` on `ask_advisor`,
  `meta.rs:85`), E5 (`ask_advisor` `ReadOnly`, `meta.rs:34,43`) all independently confirmed. E6
  doc inconsistency (`ports.rs:242` "Implemented by eos-engine" vs `notifications.rs:207-208` "real
  runner ... eos-runtime") confirmed; cosmetic. E7 (architecture docs omit the hard gate) is a docs
  gap on both sides, not a Rust gap — confirmed.

## Overall verdict

The gate **mechanism** is a faithful, test-backed port (invariant 3 confirmed_match; pre-hook Deny
short-circuits terminal stamping; block-message and hook-order parity hold; helper/subagent
exclusion is a clean bilateral match). The gate **decision logic and the advisor runner are
non-functional in production** (D1/N2): in a default build `ask_advisor` returns a canned
"not wired" string, `approval_status` denies every tool, and every advisor-gated terminal —
`submit_planner_outcome`, `submit_generator_outcome`, `submit_reducer_outcome`, AND (Rust-only)
`submit_root_outcome` — is permanently blocked, so a root request can never complete (proven by
`root_terminal_blocked_without_advisor_approval`). This is a documented, intentional "engine-only
phase" gap, not a regression in already-ported code, but it is a real Python-parity disparity until
the real `eos-runtime` `AdvisorPort` lands (with D3 resolved so the transcript carries the verdict
metadata a conversation-scanning `approval_status` would need). The Rust root-gating (D2) is an
intentional, code-documented divergence from Python ground truth and is undocumented in the
architecture bundle. The investigation's anchors, severities, and disparity adjudications are
accurate; I adjusted only the two invariant *statuses* above (2: partial→disparity, 4: scope the
match to exclusion). No false match in the investigation; no `investigator_missed`.
