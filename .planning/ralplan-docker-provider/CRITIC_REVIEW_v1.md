# CRITIC REVIEW v1 — Docker as Default Sandbox Provider

**Reviewing:** `PLAN_v1.md` + `ARCHITECT_REVIEW_v1.md`
**Reviewer role:** Critic (deliberate-mode quality gate)

---

## Verdict: **ITERATE**

## Summary

Plan is structurally sound — seam discipline, dispatcher, snapshot-branch isolation are all defensible against the codebase. But four of the Architect's five revision asks are valid AND blocking, and two independent issues (env-var precedence, `set_default_provider` race, deferred workspace.py decision) further block APPROVE. None are fundamental — all are addressable in one revision pass.

---

## Architect concerns — adjudication table

| # | Architect ask | Valid? | Blocking? | Minimum revision required |
|---|---|---|---|---|
| 1 | §3 Axis A: reframe capability decision as empirical — A.1 default with A.2 opt-in until A.2 proven sufficient | **Partially valid** — the burden-of-proof framing is correct, but Architect's *recommended* inversion (A.1 default) contradicts Plan Principle 4 just as hard as A.2 contradicts it on macOS. The real fix is empirical evidence, not flipping the default. | **Blocking** | Plan must add a Step 0 / pre-flight: run layer-stack hottest paths in CI under A.2; if any kernel-touching path silently degrades, demote A.2 or expand cap set. Do not flip default to `--privileged` without evidence; do not ship A.2 without evidence. Update §3 Axis A's "Cons" column to acknowledge the unverified-sufficiency risk explicitly. |
| 2 | §5.3/§7.3: strengthen perf acceptance criterion to mount-mode coverage ratio + p95 latency delta vs Daytona | **Valid** | **Blocking** | Replace §7.3's "at least one exec returns PRIVATE_NAMESPACE" with: (a) ≥95% of `attempt`-strategy execs in the smoke run report PRIVATE_NAMESPACE on Linux CI host, (b) p95 exec latency within ±25% of Daytona baseline on the same SWE-EVO instance. Without this gate, the plan's Driver #2 (perf preservation) is unfalsifiable. |
| 3 | §8: fix rollback claim — `_BOOTSTRAPPED` sentinel at `task_center_runner/core/bootstrap.py:16,41` makes env flip require process restart | **Valid** — verified at `task_center_runner/core/bootstrap.py:16,41,68`. Sentinel is exactly as Architect describes. | **Blocking** | §8 must say "restart-required env flip" explicitly, not just "single env-var flip." Also amend §1 Principle 2 from `runtime env-var flip` → `process-startup env-var flip`. §9 already bans modifying `_BOOTSTRAPPED`, so option (a) from Architect (honest scoping) is the cleaner path. |
| 4 | §6/§9: make macOS posture explicit — darwin default should be Daytona to honor Principle 4 | **Valid** — directly verified that §6 step 6 ships COPY_BACKED-as-normal for macOS, contradicting §1 Principle 4's "not a co-equal mode." The user is on darwin (env confirms `Platform: darwin`), so this is operationally consequential. | **Blocking** | One-line fix in §3 Axis B and §6 step 6: dispatcher should consult `sys.platform == "darwin"` when `EOS_SANDBOX_PROVIDER` is unset, defaulting to `daytona` on darwin and `docker` on linux. Acceptable alternative: explicitly mark macOS-Docker as "unsupported configuration; use Daytona on darwin," documented in `provider/docker/__init__.py`. |
| 5 | §5.1: broaden HTTP-tolerance audit — `sandbox/api/_sandbox_control.py:84-89` re-exports unwrapped; downstream JSON consumers untested | **Partially valid** — verified at `_sandbox_control.py:84-89` (unwrapped re-export) and `sandbox/api/__init__.py:42,45,86,89`. However, my grep across the backend found **only one** downstream consumer outside the api/provider boundary: `benchmarks/sweevo/sandbox.py:310`, which IS try/except wrapped. No FastAPI route or frontend client in the searched paths consumes these unwrapped — so the actual blast radius is smaller than Architect implied. | **Non-blocking** but plan should still add the unit test asserting Docker's `get_signed_preview_url` returns `{"url": None, "reason": ...}` shape (not raises) so downstream tolerance is locked in. Downgrade from "audit all callers" to "add unit test asserting shape." |

---

## Independent findings (not raised by Architect)

### IF-1 (MAJOR) — Env-var precedence vs `DAYTONA_API_KEY` is unspecified

**Evidence:** `provider/daytona/client.py:70-72` reads `DAYTONA_API_KEY` and `DAYTONA_API_URL` from env / dotenv. The Plan §3 Axis B introduces `EOS_SANDBOX_PROVIDER=docker` as the new default. **What happens when a developer has both `DAYTONA_API_KEY` set (from `.env`) and `EOS_SANDBOX_PROVIDER=docker` set (from shell)?** Plan is silent. The verified registry behavior at `provider/registry.py:25-29` is "last set wins, no warning" — `_DEFAULT` is silently overwritten — so whichever bootstrap call lands last decides. This is a footgun for the most common dev workflow (`.env` with Daytona creds + new docker default).

**Fix:** §3 Axis B must specify: "`EOS_SANDBOX_PROVIDER` is authoritative; `DAYTONA_API_KEY` presence does NOT auto-select Daytona. If `EOS_SANDBOX_PROVIDER=docker` but Daytona creds are present in env, log INFO once: `Daytona credentials detected but provider=docker; ignoring DAYTONA_*`." This also closes the question the task prompt specifically asked about.

### IF-2 (MAJOR) — `bootstrap_sandbox_provider()` idempotency claim is false in spirit

**Evidence:** Plan §6 Step 3 line 154 claims `set_default_provider` is "idempotent." Verified at `registry.py:25-29`: it overwrites `_DEFAULT` under a lock. That's **not idempotent** — that's "last-writer-wins." Plan §5.1 calls for a "table-driven test of `bootstrap_sandbox_provider()` against env-var matrix" but never tests "what happens when called twice with different env values mid-process." Plan's §3 Axis B claim that `set_default_provider is already process-local` is true; the idempotency claim isn't.

**Fix:** Either (a) make `bootstrap_sandbox_provider()` itself sentinel-gated (call it a no-op on second invocation, log a warning if env changed since first call), OR (b) drop the "idempotent" framing and document "re-calls overwrite — caller is responsible for not flipping mid-run." The current §6 Step 3 language is misleading. Pair with §5.1 test for "second call with different env value" behavior.

### IF-3 (MAJOR) — "Decision deferred to implementation" for `workspace.py` violates deliberate-mode contract

**Evidence:** Plan §6 Step 2 line 134: `"investigate whether the daytona one is reusable. If reusable, lift to sandbox/host/workspace.py. Decision deferred to implementation."` This is a deliberate-mode plan; the whole point of the consensus loop is to **not** punt structural decisions to executor improvisation. The §9 scope guard says `host/*` is off-limits — but if Step 2 decides to lift workspace.py to `sandbox/host/workspace.py`, that **explicitly violates §9**. The plan creates a contradiction whose resolution it then defers.

**Fix:** Decide now. Read `provider/daytona/workspace.py` and `prepare_sandbox_runtime_context` and commit to one of: (1) provider-neutral helper exists → still lift to `sandbox/host/` and revise §9 scope guard accordingly; (2) helper is Daytona-specific → Docker writes its own; lift is a follow-up. Document the answer in §6 Step 2. Either path is fine; "decide later" is not.

### IF-4 (MINOR, defensible but worth flagging) — §6 Step 5 provider-name branch is a leaky abstraction, but acceptably so

**Evidence:** `benchmarks/sweevo/sandbox.py:498-540` and the plan's branching there. Plan's rationale (snapshot creation is "benchmark setup concern, not runtime container primitive") is sound — Daytona's `subprocess.run(["daytona", "snapshot", ...])` is genuinely benchmark-tooling, not Protocol surface. **However**, the future maintainer hint is real: when a third provider arrives, this branch must be updated in lockstep with the Protocol. The plan's ADR §Consequences acknowledges this. **Acceptable separation, NOT a leaky abstraction in the architectural sense**, but the plan should add a defensive `else: raise NotImplementedError(f"register_sweevo_snapshot does not support provider={provider_name}")` so silent skips don't happen.

**Fix:** Two-line addition to §6 Step 5: branch must have an explicit `else: raise NotImplementedError(...)` so unknown providers fail loud, not silent. Also add to ADR §Consequences: "linter rule or test enforces the branch covers every registered provider name."

---

## Required revisions (numbered, tied to plan §)

1. **§3 Axis A** — add explicit pre-flight CI experiment to prove A.2 is sufficient for the layer-stack hottest paths before approving A.2 as default. Update Cons column for A.2 to call out the unverified-sufficiency risk. (Addresses Architect ask #1.)

2. **§7.3 + §5.3** — replace "at least one exec returns PRIVATE_NAMESPACE" with mount-mode coverage ratio (≥95%) + p95 exec-latency delta vs Daytona within ±25%. Move the Daytona perf baseline measurement into §5.3 setup. (Addresses Architect ask #2.)

3. **§1 Principle 2 + §8** — change "runtime env-var flip" to "process-startup env-var flip"; §8 rollback must say "restart required" explicitly, citing `task_center_runner/core/bootstrap.py:16,41`. (Addresses Architect ask #3.)

4. **§3 Axis B + §6 Step 6** — dispatcher defaults: `darwin → daytona`, `linux → docker`, when `EOS_SANDBOX_PROVIDER` unset. Document in `provider/docker/__init__.py`. (Addresses Architect ask #4.)

5. **§3 Axis B** — specify env-var precedence: `EOS_SANDBOX_PROVIDER` is authoritative; `DAYTONA_API_KEY` does not auto-select Daytona. Log mismatch on startup. (Addresses IF-1.)

6. **§6 Step 3** — drop the misleading "idempotent" framing OR add sentinel-gating to `bootstrap_sandbox_provider()` itself. Add §5.1 test case for "second call with different env value." (Addresses IF-2.)

7. **§6 Step 2** — decide `workspace.py` reuse vs duplication now. Document the answer; revise §9 scope guard if lift to `sandbox/host/` is chosen. (Addresses IF-3.)

8. **§6 Step 5** — add `else: raise NotImplementedError(...)` to the snapshot-registration branch. (Addresses IF-4.)

9. **§5.1** — add unit test asserting Docker's `get_signed_preview_url(sandbox_id, port)` returns `{"url": None, "reason": str}` shape and does not raise. (Addresses Architect ask #5, downgraded.)

---

## What's already good (preserve through revisions)

1. **Scope guard discipline** — §5 line 5 + §9 list. `host/`, `daemon/`, `execution/`, `layer_stack/` are correctly fenced off. Verified that the call sites genuinely already use `adapter.exec(...)` / `call_daemon_api(...)` — no daytona_sdk imports leaked into host/. This is the strongest part of the plan.

2. **Pre-mortem Scenario 2** (`mount(2)` blocked by seccomp despite caps) is sharp, detectable (`exit_code == 125` + `mount_mode == PRIVATE_NAMESPACE`), and has actionable mitigation (codify seccomp=unconfined in default flags). Keep this exactly as written.

3. **Step-by-step commit/revert plan in §8** is genuine atomic-revert-by-step, not theater. Step 1 (Protocol method add) really is independently shippable since `_sandbox_control.py:92-99` already uses `getattr(adapter, "context_preparer", None)`. Verified.

4. **Snapshot-branch isolation in §6 Step 5** — keeping `register_sweevo_snapshot` off the Protocol is the right call. Snapshot creation from a Dockerfile is genuinely benchmark-setup concern, not runtime container primitive. Defended in the ADR.

5. **Out-of-scope list §9** is unusually concrete and disciplined for a plan of this size — 8 explicit non-goals with rationale. Future executors will know exactly what NOT to touch.

---

## Verdict justification

ITERATE, not REJECT — design is fundamentally sound. ITERATE, not APPROVE — five blocking concerns (four from Architect + one independent: env-var precedence + sentinel/idempotency + workspace decision = three independent MAJORs). Plan needs one revision pass addressing the 9 required revisions; do not need another full architect round-trip if revisions land cleanly.

Review escalated to ADVERSARIAL mode at IF-1 discovery (third MAJOR independent of Architect's list). Realist check applied: IF-2 (idempotency) survives at MAJOR because misleading language in a deliberate-mode plan is exactly what review must catch; IF-3 (workspace deferral) survives at MAJOR because deliberate-mode explicitly prohibits punting structural decisions to executor; IF-4 downgraded to MINOR because Plan's rationale for the branch is defensible — only the missing else-raise is the actionable gap.

Architect ask #5 downgraded from blocking to non-blocking after independent codebase grep confirmed only one downstream consumer outside the audited path, and it's already try/except wrapped.

---

## Ralplan summary row

- **Principle/Option Consistency:** FAIL — Principle 2 contradicted by `_BOOTSTRAPPED` sentinel; Principle 4 contradicted by macOS COPY_BACKED-as-normal mitigation.
- **Alternatives Depth:** PASS-WITH-CAVEAT — Axis A/B options are real, not strawmen, BUT Option A.2's sufficiency is asserted, not demonstrated. Architect's burden-of-proof inversion is correct.
- **Risk/Verification Rigor:** FAIL — §7.3 acceptance criterion ("at least one PRIVATE_NAMESPACE exec") cannot detect partial coverage; this is the load-bearing gate and it doesn't bear the load.
- **Deliberate Additions (pre-mortem + expanded test plan):** PASS — three pre-mortem scenarios are detectable and actionable; §5 covers unit / integration / e2e / observability with real test names. Quality is good; what's missing is coverage ratio and perf delta in acceptance criteria, not the test scaffolding itself.
