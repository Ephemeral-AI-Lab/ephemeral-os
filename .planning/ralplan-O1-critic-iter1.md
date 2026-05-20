# Critic — RALPLAN-DR O(1) overlay mount (iter1)

**Verdict:** **ITERATE**

**Mode:** DELIBERATE (per plan §1.4). Review escalated to **ADVERSARIAL** after finding 1 CRITICAL + 4 MAJOR; systemic pattern in measurement/concurrency rigor.

**Independent from Architect.** Architect already redlined Steps 1/3/4/9/10 and §5.2 averaging; below are issues Architect did **not** flag.

---

## Pre-commitment predictions (made before re-reading the plan)

1. Audit harness measurement validity will be hand-waved.
2. The "Open Questions" section will contain something that should block, not defer.
3. SQLite live_e2e setup will skip a schema/migration step.
4. Pre-mortem will have a missing 4th scenario about the harness itself.

All four hit.

---

## CRITICAL findings

### C1 — §10 Q1 is an unresolved correctness invariant masquerading as an "Open Question"

Plan line 662: *"Manifest order vs `lowerdir+=` priority... Confirm `lowerdir+=` semantics: is the first call the highest-priority (top) or lowest-priority (bottom)? ... semantics for ordering depend on kernel version. Action: add an explicit unit test in Step 2..."*

A plan whose author cannot state which layer wins on file conflict cannot ship. Step 3 (line 190) already hardcodes `manifest.layers` newest-first and Step 6 (line 261) bakes the same ordering into the payload schema — but if `lowerdir+=` priority is the opposite of what the plan assumes, every lease serves wrong file contents. This is silent data corruption on the hot path, not a tunable.

**Confidence:** HIGH. The plan ships ordering decisions before resolving the ordering question.

**Why this matters:** Wrong-priority bug is not caught by depth/disk-delta harness; only a content-correctness test sees it. The plan's §8.1 row 4 says "returns layer_paths newest-first" — that's about list order, not mount priority.

**Fix:** Promote Q1 out of §10 into Step 2 as a blocking pre-implementation experiment. The unit test must (a) write distinct markers `A`/`B`/`C` into 3 real overlay layers, (b) mount via the new API in both orders, (c) read back the merged path, (d) assert which marker wins. **Until this passes, the plan is not implementable.** Verified against kernel docs in Open Questions — plan needs to cite a primary source, not "depends on kernel version."

---

## MAJOR findings

### M1 — §5.1 harness conflates per-lease and per-filesystem measurements

Plan line 446: *"`tmpfs_used_delta` | `df -B1 /eos-mount-scratch \| awk 'NR==2{print $3}'` (post − pre)"*

`df` reports the entire tmpfs, shared by all N concurrent leases. Under Bound A's concurrent-N procedure (§5.2, N up to 200), per-lease attribution is undefined: lease 47's "post" snapshot includes leases 1-46's upperdir bytes. The acceptance criterion `avg(tmpfs_used_delta - upperdir_bytes) ≤ 64 KiB` (line 461) is **only well-defined for N=1**.

**Confidence:** HIGH.

**Fix:** Replace whole-tmpfs `df` with per-lease `du -sb <run_dir>/upper` + `du -sb <run_dir>/work` measured inside each lease's own teardown. Drop `tmpfs_used_delta` for Bound A (or keep it only as a sanity reading at outer N=1). Run an outer-loop assertion that **sum of per-lease upperdir+workdir ≤ N × budget** rather than averaging.

### M2 — §5.2 averaging hides single-lease regressions (independent angle from Architect)

Architect already flagged averaging. **New angle:** a single 800 KiB regression averaged over N=200 = 4 KiB exactly at threshold; one full re-materialize (workspace_bytes / N) under heavy fan-out can pass the gate.

**Fix:** Replace `avg(lower_bytes_delta) ≤ 4 KiB` with `max(lower_bytes_delta) ≤ 4 KiB` AND emit p99 + distribution on failure. Same for Bound B mount-time slope: require **per-M slope** not aggregate.

### M3 — §7.3 mitigation references a registration shim that doesn't exist

Plan line 584: *"the current `_unreferenced_layers` filter at `stack.py:344-351` already excludes pinned layers — verify this invariant survives"*

Verified at `stack.py:194-195`: `pinned_layers()` returns `tuple[LayerRef, ...]` from `LeaseRegistry`. But §3 Step 3 creates `materialize=False` leases that carry `layer_paths: tuple[str, ...]` — paths, not LayerRefs. The plan does not show **how** these new leases register their pins with `LeaseRegistry` such that `pinned_layers()` keeps returning correct refs. If the existing registration already covers it (because `LeaseRegistry.acquire(manifest, ...)` is unchanged), the plan needs to **state that explicitly** as the invariant being relied on; otherwise the eviction race in §7.3 is unmitigated.

**Confidence:** MEDIUM-HIGH (the registry call at line 135 looks unchanged in plan Step 3, but the plan never says "lease pinning is unchanged — verified at LeaseRegistry.acquire" which it needs to).

**Fix:** Add to Step 3 an explicit invariant statement: *"`LeaseRegistry.acquire(manifest, ...)` remains the sole pinning entry; `materialize=False` does not bypass registration."* Add an integration test that holds a `materialize=False` lease and queries `pinned_layers()` for the expected refs before §7.3's eviction test runs.

### M4 — §4 runbook will fail on first run due to missing SQLite schema bootstrap

Plan line 358-360: *`export EPHEMERALOS_DATABASE_URL="sqlite:////tmp/eos-validation.db"`*

Brand-new file at `/tmp/eos-validation.db`. `backend/docs/migrations` exists, so there IS a migration story, but the runbook (§4.1 pre-flight) doesn't invoke it. First task_center_runner write will hit "no such table". The plan acknowledges this by citing `core/stores.py:148` ("sqlite bundle creation works!") but bundle creation ≠ full task_center schema.

**Fix:** Add §4.1 step 5: *"Run schema migration: `cd backend && .venv/bin/python -m task_center_runner.migrations.apply $EPHEMERALOS_DATABASE_URL`"* (or the actual command — verify against `backend/docs/migrations/`). Provide expected stdout excerpt.

### M5 — §4.5 baseline is captured from a single run, σ unknown

Plan line 379-382 captures `/tmp/eos-baseline.log` from one run. §4.5 then asserts `≤ baseline × 1.20` (line 420). With N=1 sample, you cannot tell whether a 21% post-change reading is regression or variance. The 20% margin is arbitrary without measured noise floor.

**Fix:** Require ≥5 baseline runs, freeze the **median** as the canonical baseline constant in the runbook, and report measured `(p95 - median) / median` as the variance floor. Adjust the regression threshold to `max(baseline_median × 1.20, baseline_median + 3σ)`.

---

## Minor findings

- **§1.3 Option C is strawmanned.** Plan's C is FUSE. The real alternative is cached-materialize-per-manifest + bind-mount-per-lease (Architect's synthesis). Add this as Option D explicitly and invalidate on stated grounds (e.g., still pays first-materialize EXDEV cost), or admit it as a follow-up. Plan line 658 ("only one chosen path is in scope") is a *result*, not a justification for skipping the steelman.
- **§8.1 row 1 unit-test coverage gap.** Probe test covers only `ENOSYS → False`. §7.2 (line 568) explicitly mentions seccomp; no test for `EPERM`. Add `test_probe_supported_returns_false_on_eperm` and `test_probe_supported_returns_false_on_ebadf`.
- **§1.1 Step 1 aarch64 assertion is unproven.** Plan line 140 demands a unit test asserting equal syscall numbers x86_64 == aarch64 == 430, but the empirical N=4..500 evidence (line 64) was Docker, almost certainly x86_64. The unit test will pass without ever having run a real aarch64 mount. Either gate to x86_64-only at runtime via `os.uname().machine` until a live aarch64 mount is validated, or note this in the ADR's "Negative Consequences" column.

---

## What's missing

- **Pre-mortem scenario #4: harness measurement failure.** All three scenarios (§7.1-7.3) are about the *implementation* failing. Missing: "harness reports false-pass under concurrency because `df` aggregates" (this is M1 promoted). Without #4, deliberate-mode's "3 genuinely independent scenarios" check fails — §7.1 and §7.3 are both depth/eviction failures, leaving only §7.2 (kernel availability) and one harness gap as the truly independent axes.
- **No rollback test.** §10 Q5 suggests `EOS_OVERLAY_FORCE_MATERIALIZE=1` env kill-switch but no test that flipping it mid-flight does what's expected.
- **No `RLIMIT_NOFILE` check at daemon start.** §10 Q3 acknowledges 4500 fds at peak; plan does not raise the ulimit or validate it. A surprise EMFILE at lease 1024 in production is a slow-fuse incident.

---

## Multi-perspective notes

- **Executor:** §4.1 pre-flight is missing the migration step; first runbook attempt by anyone-not-the-author will fail before any measurement happens.
- **Stakeholder:** §5.2/5.3 acceptance can be gamed by averaging; commitment to "O(1)" is weaker than the principle (§1.1 #1) claims.
- **Skeptic:** The plan's strongest argument against itself (Architect's cached-materialize synthesis) appears nowhere in §1.3.

---

## Verdict justification

**ITERATE.** Architect's REVISE plus C1 (unresolved correctness invariant) and M1 (harness ill-defined under concurrency) make APPROVE impossible. Not REJECT because Option A's empirical foundation (N=4..500 validated this session) is solid and the implementation steps are file-anchored; the gaps are in deliberation rigor and measurement design, both fixable. Realist check: C1 stays CRITICAL — silent file-priority bugs on every shell are not minor, mitigation is "add a test before Step 3", straightforward. M1 stays MAJOR — concurrency measurement is fixable without redesign. No downgrades applied.

Escalation: entered **ADVERSARIAL** after C1; expanded to verify pin-registration claim (M3) and runbook bootstrap (M4) which weren't originally in the brief's quality criteria list.

---

## Open Questions (unscored)

- Are `LayerRef` lifetime invariants compatible with `materialize=False` leases such that `pinned_layers()` continues to return correct refs without code change? (M3 partial)
- Does §10 Q3's 4500-fd ceiling actually clear container-default `RLIMIT_NOFILE`, or is the daemon already raising it elsewhere?
- §1.3 invalidation of refcount-cached-materialize (Option B) was flagged weak by Architect — is the Architect's synthesis (Option D = cached + bind-mount) Pareto-better than A on old kernels? If yes, Step 10's fallback becomes Option D not "materialize+mount(8)".

---

*Ralplan summary row*
- **Principle/Option Consistency:** Fail — Principle 1 (no per-lease materialize) is what picks A; Principles 2-4 are post-hoc justifications equally compatible with cached-materialize+bind-mount. Re-derive principles after the steelmanned alternative set is restored.
- **Alternatives Depth:** Fail — Option C strawmanned (FUSE, not the realistic do-nothing-different cached path); Option B's invalidation already flagged by Architect.
- **Risk/Verification Rigor:** Fail — §5.2 averaging + §5.1 whole-tmpfs `df` + §4.5 single-sample baseline; the harness cannot detect what it claims to.
- **Deliberate Additions:** Fail — pre-mortem only has 2 truly independent scenarios; expanded test plan missing seccomp/EPERM probe coverage; §10 Q1 should not exist as an open question at all.
