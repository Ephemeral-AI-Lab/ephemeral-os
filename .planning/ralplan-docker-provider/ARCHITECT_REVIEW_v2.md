# ARCHITECT REVIEW v2 — Docker as Default Sandbox Provider

**Reviewing:** `PLAN_v2.md` against `ARCHITECT_REVIEW_v1.md` (5 asks) + `CRITIC_REVIEW_v1.md` (4 independent findings + downgraded ask #5) = 9 revisions
**Reviewer role:** Architect (deliberate consensus iteration #2)

---

## Verdict: **REVISE**

8 of 9 revisions land cleanly. One revision (Step 0 pre-flight script spec) contains a primary-source factual error that makes the load-bearing gate uncodeable as written. Fix is local — three lines in §6 Step 0 — but cannot be deferred to implementation because it changes what the experiment actually measures.

---

## Revision-by-revision check

| # | Source | Ask | Resolved? | Notes |
|---|---|---|---|---|
| 1 | Architect #1 | §3 Axis A: empirical pre-flight before A.2 default | **Partially** | Step 0 framework is correct; decision rule is concrete; **but the script's third sub-step is technically wrong — see "New concerns" #1.** Cons column updated honestly. |
| 2 | Architect #2 | §7.3: mount-mode coverage ratio + perf delta | **Resolved** | ≥95% PRIVATE_NAMESPACE ratio + ±25% p95 delta with code snippet for ratio computation. See "New concerns" #2 on the ±25% threshold defensibility. |
| 3 | Architect #3 | §8 rollback: process restart required | **Resolved cleanly** | §1 Principle 2 reworded; §8 has explicit callout block citing `task_center_runner/core/bootstrap.py:16,41`. Verified against the file. |
| 4 | Architect #4 | macOS posture | **Resolved cleanly** | Per-platform default in §3 Axis B with code; documented in `provider/docker/__init__.py` docstring spec (§6 Step 2). Honors Principle 4. |
| 5 | Architect #5 (downgraded) | HTTP-tolerance unit test | **Resolved** | §5.1 explicitly asserts `{"url": None, "reason": str}` shape and no-raise for both endpoints. |
| 6 | Critic IF-1 | Env-var precedence | **Resolved cleanly** | §3 Axis B states authoritative rule; one-time INFO log codified in dispatcher snippet (§6 Step 3 lines 265-268). |
| 7 | Critic IF-2 | Idempotency false-framing | **Resolved cleanly** | Dispatcher is sentinel-gated via `_PROVIDER_BOOTSTRAPPED`; §6 Step 3 includes the full implementation; v1's misleading "idempotent" claim is explicitly corrected with the rationale. |
| 8 | Critic IF-3 | workspace.py decision | **Resolved cleanly** | §6 Step 2 commits to path (b) with file:line evidence (verified against `daytona/workspace.py:13-22,32-51,54-76`). §9 scope guard preserved. Follow-up tracked in ADR. |
| 9 | Critic IF-4 | `else: raise NotImplementedError` + lint enforcement | **Resolved cleanly** | §6 Step 5 spec + ADR §Consequences both updated; specific failure mode named. |

---

## New concerns introduced by v2

### #1 (BLOCKING) — Step 0's third probe targets the wrong code path

**Plan text (§6 Step 0, item 3, line 141):** "A minimal layer-stack overlay mount via the same `mount(2)` syscall path used by `execution/overlay/kernel_mount.py`."

**Codebase reality:** `execution/overlay/kernel_mount.py:43-50` does NOT use the `mount(2)` syscall. It shells out to the `mount` binary via `subprocess.run(["mount", "-t", "overlay", ...])` — i.e., **`mount(8)`**, the util-linux binary. Per project memory: "Overlay 16-layer cap is util-linux mount(8), not kernel — mount(2) syscall takes 199+ overlay layers; util-linux 2.41 mount binary is what fails."

**Why this matters for the gate:** the plan's intent is to verify A.2 caps unblock the perf-critical *direct-syscall* path (the >16-layer regime). Probing the same code path `kernel_mount.py` uses would only verify `mount(8)` works — which is exactly the failure mode the project already escaped. If the script literally lifts `kernel_mount.py`'s subprocess call, it will pass at low layer counts and tell us nothing about the 199-layer ceiling that justifies A.2 in the first place.

**Fix (three lines, must be in v3 not deferred):**
- Item 3 should say: "A minimal overlay mount via the `mount(2)` syscall directly (e.g., `ctypes.CDLL('libc.so.6').mount(...)` or `os.system` invocation of a tiny C helper) — NOT `subprocess.run(['mount', ...])`. The script's goal is to verify the direct-syscall path used by `kernel_mount.py`'s **planned future replacement** (per project memory's hybrid-removal note), not the current `mount(8)` shellout."
- OR clarify the perf justification: "A.2 unblocks `mount(8)` overlay up to 16 layers, sufficient for current workloads; direct `mount(2)` syscall path (199+ layer cap) is a future optimization gated by a separate experiment."

The plan must pick one. As written, Step 0 either runs the wrong test or makes a false claim about what `kernel_mount.py` does.

### #2 (NON-BLOCKING but flag) — ±25% perf delta gate is honest but architecturally awkward

The task prompt asks: is ±25% achievable AND meaningful given Daytona-remote-VM vs. Docker-local? **The plan's §5.3 step (1) acknowledges this caveat** ("Daytona's perf is partly host-independent because work runs in remote VMs; document this caveat in the test's baseline-data file") **but doesn't resolve it.** Comparing local-Docker p95 to remote-Daytona p95 measures `local_exec_cost vs (remote_exec_cost + network_RTT)`. On a fast network, Docker should be *faster*, not within ±25% — so ±25% is loose enough to almost always pass, making it weak. On slow networks, Daytona looks artificially slow.

**Verdict:** acceptable as v2 because (a) the alternative — platform-relative gating against `host_bare_metal` baseline — would require new perf infra outside the plan's scope, and (b) the gate exists to catch *gross* regressions (>25% slower than Daytona's already-network-tax'd baseline = something is broken), not to enforce parity. **Document this rationale in §5.3** so a future maintainer doesn't tighten it to ±10% without understanding why the loose number was deliberate.

### #3 (NON-BLOCKING) — Sentinel-gating + pytest fixture pattern

`_PROVIDER_BOOTSTRAPPED` is correct for production, but pytest fixtures that parametrize across providers (e.g., `test_provider_parity.py` in §5.2 — runs against both providers) must reset the sentinel between parametrizations. The plan's §5.1 test matrix covers "second call with same/different env" but doesn't say *how* `pytest` fixtures escape the sentinel for the parity test. Expected resolution: fixture-scoped `monkeypatch` that resets `_PROVIDER_BOOTSTRAPPED = False` and `_FIRST_PROVIDER = None` between cases. This is implementation-detail-level, not plan-level — but worth a one-line note in §6 Step 3 or §5.2: *"Tests that parametrize across providers must reset `_PROVIDER_BOOTSTRAPPED` between cases via a pytest fixture."* Without it, executor will either re-spawn pytest workers (slow) or modify the sentinel directly (which §9 doesn't ban for the new sentinel — only the existing `_BOOTSTRAPPED` one).

### #4 (NON-BLOCKING) — `daytona_baseline_p95.json` drift

Plan says re-baseline annually or on upgrade. **Annual is too lax** for a load-bearing acceptance gate. Recommendation: add an automated freshness check — `test_sweevo_docker_smoke.py` fails loud if `mtime` of the baseline file is >180 days old, with a clear message: `"baseline file is N days old; re-run §5.3 baseline procedure before flipping default"`. Two-line addition; not blocking but cheap insurance.

### #5 (NON-BLOCKING) — "Halt and re-trigger consensus loop" hand-wave

Step 0's decision rule says: failure → "halt plan and re-trigger consensus loop." In practice this means: executor stops; user manually invokes the next ralplan iteration with the failure documented. The plan does not need a paged-in process for this — the consensus loop *is* the process. Acceptable as v2 as long as the executor knows "halt" means "commit Step 0 artifacts + open an issue, do not start Step 1." Recommend one line in §6 Step 0 verify section: *"On halt, commit `preflight_docker_a2_caps.log` to the repo and surface to the consensus reviewer; do NOT proceed to Step 1."*

---

## Synthesis

**v3 must:**
1. Fix Step 0 item 3 to either probe the actual `mount(2)` direct-syscall path (with a small C helper or ctypes call) OR scope the experiment to `mount(8)` and remove the implied perf-justification for the 199-layer regime. **This is the only blocking change.**

**v3 should (cheap, optional):**
2. Document why ±25% is deliberately loose in §5.3.
3. Add fixture-reset note for pytest parametrization across providers in §6 Step 3.
4. Add 180-day mtime check on `daytona_baseline_p95.json`.
5. Add "commit artifact + don't proceed" note to Step 0's halt path.

**What v2 got right:** the structural revisions (1, 3–9) are all clean. The Critic's IF-2 and IF-3 in particular — sentinel correctness and workspace.py deferral — are resolved with file:line evidence, not hand-waves. The new dispatcher snippet (§6 Step 3) is implementation-ready. The macOS posture honors Principle 4 on every platform. The plan is one localized fix away from APPROVE.

---

## Consensus Addendum

- **Antithesis (steelman):** "Step 0 as written is good enough — the executor will read `kernel_mount.py` and figure out the intent; nitpicking `mount(2)` vs `mount(8)` is exactly the kind of pedantry deliberate mode is supposed to suppress." Rebuttal: in deliberate mode the gate spec IS the contract; if the executor reads `kernel_mount.py` and writes `subprocess.run(["mount", ...])` in the preflight, the gate passes at 16-layer ceiling but doesn't probe the 199-layer regime, and we ship A.2 without evidence for the load-bearing perf claim. The whole point of Architect ask #1 was to make the burden of proof empirical; an empirical test that measures the wrong thing is worse than a declarative claim because it produces false confidence.
- **Tradeoff tension:** Tightening Step 0 to direct `mount(2)` adds a small C helper or ctypes call to the preflight script — that's net-new code the team must maintain. The alternative (scope Step 0 to current `mount(8)` reality) is honest but admits A.2 is not yet proven for the 199-layer regime. Either is defensible; the plan must pick one and say so.
- **Synthesis:** Pick the second option for v3 — scope Step 0 to `mount(8)` (current code path) and explicitly defer the `mount(2)` 199-layer experiment to a follow-up. This keeps Step 0 cheap, ships A.2 as default for today's workloads (which are well under 16 layers per project memory), and tracks the residual risk honestly. If a future workload hits the 199-layer regime, that's when the second experiment runs.
- **Principle violations (deliberate mode):** Step 0 as written soft-violates the "deliberate mode prohibits punting structural decisions" principle (Critic IF-3 framing) because it asserts a test that doesn't match the code being tested. SEVERITY: MAJOR — load-bearing gate. Fix is local.

---

## References (verified during this review)

- `backend/src/sandbox/execution/overlay/kernel_mount.py:43-50` — uses `subprocess.run(["mount", ...])`, i.e., `mount(8)`, not `mount(2)`. **Contradicts §6 Step 0 item 3.**
- `backend/src/sandbox/execution/strategies/namespace.py:137-152` — `detect_private_mount_namespace()` matches plan citation.
- `backend/src/sandbox/provider/daytona/workspace.py:1-22, 32-51, 54-76` — Daytona SDK shape coupling confirmed; supports §6 Step 2 path (b).
- `backend/src/task_center_runner/core/bootstrap.py:16,41,68` — `_BOOTSTRAPPED` sentinel confirmed; supports §8 restart-required rollback claim.
- Project memory: "Overlay 16-layer cap is util-linux mount(8), not kernel — mount(2) syscall takes 199+ overlay layers" — directly contradicts the implied A.2-justification path in Step 0 item 3.
