# ARCHITECT REVIEW v3 — Docker as Default Sandbox Provider

**Reviewing:** `PLAN_v3.md` against `ARCHITECT_REVIEW_v2.md` (one blocking issue + 4 cheap fixes)
**Reviewer role:** Architect (deliberate consensus iteration #3)

## Verdict: **APPROVE**

All 5 v2 revisions landed cleanly in v3. No new structural concerns introduced. Plan is ready for Critic adjudication.

## 5-item check table

| # | v2 ask | Resolved? | Notes |
|---|---|---|---|
| 1 (BLOCKING) | §6 Step 0 item 3 — fix `mount(8)` vs `mount(2)` factual error | **Yes** | v3:144-146 explicitly cites `mount(8) / mount(8), NOT mount(2) syscall`; references `kernel_mount.py:44-50`; depths 1/5/10/15/16 enumerated; expected rc=32 cliff documented. |
| 1 cont. | §6 Step 0 Decision rule — match new scope | **Yes** | v3:156 sets gate at "mount(8) succeeds at depth ≥10". |
| 1 cont. | §3 Axis A A.2 Cons | **Yes** | v3:30 scopes sufficiency to live `mount(8)` path; defers `mount(2)` 199-layer regime to ADR Follow-ups. |
| 1 cont. | ADR §Follow-ups new bullet | **Yes** | v3:404 captures `kernel_mount.py` mount(8)→mount(2) migration as follow-up. |
| 2 (cheap) | §5.3 ±25% rationale | **Yes** | v3:122 documents why ±25% is deliberately loose; warns against tightening without platform-relative baseline. |
| 3 (cheap) | §6 Step 3 pytest fixture-reset note | **Yes** | v3:289 calls out `_PROVIDER_BOOTSTRAPPED = False` reset requirement for parametrized provider tests. |
| 4 (cheap) | §5.3 baseline freshness | **Yes** | v3:124 adds 180-day mtime check with explicit fail-loud assertion message. |
| 5 (cheap) | §6 Step 0 halt artifact | **Yes** | v3:161 requires committing `preflight_docker_a2_caps.log` under `.planning/ralplan-docker-provider/preflight-logs/` before halting. |

## New issues introduced in v3

None of structural significance. The Planner preserved v2 line-by-line outside the 5 targeted revisions, so the rest of the design (provider seam, dispatcher, sentinel, snapshot branch, scope guard) is unchanged.

## Recommendation

**APPROVE for Critic adjudication.** v3 satisfies the v2 blocking concern with a factually-accurate Step 0 spec, and the 4 cheap fixes all landed verbatim. The plan is shippable.

Two non-blocking observations for the Critic to consider:

1. **Daytona baseline file path** (`tests/integration_test/test_benchmarks/data/daytona_baseline_p95.json`): the path is plausible but uses a `tests/integration_test/` prefix that doesn't appear elsewhere in this codebase's tree (the live tree has `backend/tests/integration_test/` etc.). Implementation may need to align this path to wherever the project's integration-test data files live; not a plan-level issue.

2. **§6 Step 5 lint enforcement**: ADR §Consequences says "linter rule or test enforces the branch covers every registered provider name." The plan doesn't yet specify whether `get_registered_provider_names()` is a function that exists or one we'd need to add. Implementation detail; doesn't block consensus.

Neither of these requires a v4. Pass to Critic.
