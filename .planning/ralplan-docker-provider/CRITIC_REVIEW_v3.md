# CRITIC REVIEW v3 — Docker as Default Sandbox Provider

**Reviewing:** `PLAN_v3.md` after `ARCHITECT_REVIEW_v3.md` (APPROVE) and the v1 9-revision list.
**Reviewer role:** Critic (deliberate-mode quality gate, ralplan iteration #3)

## Verdict: **APPROVE**

## Summary

All 9 v1 required revisions are resolved in v3, the v2-blocking `mount(8)/mount(2)` factual error is fixed with verified line references (`execution/overlay/kernel_mount.py:44-50` confirmed to use `subprocess.run(["mount", ...])` — i.e., mount(8)), and the four v3 cheap fixes landed verbatim. No new structural concerns. The two architect non-blocking observations are correctly classified as implementation-time concerns, not consensus blockers.

## 9-revision recheck

| # | v1 required revision | Resolved in v3? | Evidence |
|---|---|---|---|
| 1 | §3 Axis A — empirical pre-flight gate before A.2 as default | Yes | §3 A.2 Cons col + §6 Step 0 + decision rule (v3:30, 135-161) |
| 2 | §7.3 + §5.3 — coverage ratio ≥95% + p95 ±25% vs Daytona baseline | Yes | §5.3 (108-124), §7 criterion 3 (331-334) |
| 3 | §1 Principle 2 + §8 — process-startup flip, restart-required rollback | Yes | §1 Principle 2 (12), §8 (342-344) |
| 4 | §3 Axis B + §6 Step 6 — darwin→daytona, linux→docker per-platform default | Yes | §3 Axis B (44-53), §6 Step 6 (322), `_resolve_provider_name` (238-242) |
| 5 | Env-var precedence — `EOS_SANDBOX_PROVIDER` authoritative over `DAYTONA_API_KEY` | Yes | §3 Axis B (55), dispatcher impl (277-280), startup INFO log codified |
| 6 | §6 Step 3 — drop misleading "idempotent" or sentinel-gate dispatcher | Yes | Sentinel-gated via `_PROVIDER_BOOTSTRAPPED` (227, 252-264), framing explicitly corrected (287) |
| 7 | §6 Step 2 — decide `workspace.py` reuse vs duplication NOW | Yes | Path (b) chosen with verified evidence from `daytona/workspace.py:13-22, 32-51, 54-76` (177-182), §9 scope guard reaffirmed (357) |
| 8 | §6 Step 5 — explicit `else: raise NotImplementedError(...)` in snapshot branch | Yes | §6 Step 5 (310), ADR Consequences (394) |
| 9 | §5.1 — unit test asserting Docker `get_signed_preview_url` returns `{"url": None, ...}` shape | Yes | §5.1 test_docker_adapter.py bullet (99-100) |

All nine remain resolved. No regression.

## v3-incremental changes recheck (architect-v2 asks)

mount(8) vs mount(2) correction (v3:144-146), Cons-col scope tightening (v3:30), ADR follow-up entry for kernel_mount.py migration (v3:404), ±25% rationale (v3:122), fixture-reset note for parametrized tests (v3:289), 180-day baseline staleness assertion (v3:124), and halt-artifact commit path (v3:161) — all present and accurate.

## New concerns

None of structural significance. The plan preserves all v2 structure outside the targeted edits.

## Non-blocking observations (architect-raised, Critic concurs)

1. **`tests/integration_test/test_benchmarks/data/daytona_baseline_p95.json` path** — confirmed: the codebase has `backend/tests/unit_test/test_benchmarks/` but no `tests/integration_test/test_benchmarks/`. This is an implementation-time path-alignment task. Plan-level intent (a checked-in baseline file with a stale-mtime guard) is sound. Not blocking.

2. **`get_registered_provider_names()` existence** — confirmed: `provider/registry.py` exposes `set_default_provider`/`get_default_provider`/`register_adapter`/`get_adapter`/`has_registered_adapter` but no name-enumeration helper. The ADR Consequences line is "linter rule OR test enforces"; the executor can iterate `_ADAPTERS` or add a one-liner helper. Not blocking.

Both are healthy hand-offs to executor judgment.

## Final decision rationale

This is iteration 3 of a max-5 loop. The plan satisfies every gate:

- **Principle/Option Consistency:** PASS — Principle 2 matches sentinel reality; Principle 4 honored via darwin→daytona default.
- **Alternatives Depth:** PASS — A.1/A.2/A.3/A.4 each have real Pros/Cons; B.1/B.2/B.3 likewise; A.2 sufficiency gated by Step 0 preflight.
- **Risk/Verification Rigor:** PASS — coverage ratio + perf delta falsifiable; baseline staleness enforced; halt artifact checked in for review.
- **Deliberate Additions (pre-mortem + expanded test plan):** PASS — 3 detectable scenarios with codified mitigations; §5 covers unit/integration/e2e/observability with named tests, env-var gates, and parametrized-fixture reset pattern.

**APPROVE for shipping.** Executor proceeds to Step 0 (preflight CI experiment). Consensus loop terminates at iteration 3.

## Ralplan summary row

- **Principle/Option Consistency:** PASS
- **Alternatives Depth:** PASS
- **Risk/Verification Rigor:** PASS
- **Deliberate Additions (pre-mortem + expanded test plan):** PASS
