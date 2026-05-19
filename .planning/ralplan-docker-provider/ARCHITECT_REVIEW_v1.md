# ARCHITECT REVIEW v1 — Docker as Default Sandbox Provider

**Reviewing:** `PLAN_v1.md` (Deliberate consensus, RALPLAN-DR)
**Reviewer role:** Architect (steelman antithesis + tradeoff tension + principle check)

---

## Verdict: **REVISE**

The plan's scope discipline is real and most claims check out under inspection. But Option A.2 (the load-bearing recommendation) rests on an unverified capability-equivalence assumption, the rollback story has a latent process-cache bug, and one HTTP-boundary regression risk is downplayed. None of these kill the design; all three need explicit treatment before commit.

---

## Antithesis (strongest objection)

**The plan does not prove that `--cap-add=SYS_ADMIN --security-opt seccomp=unconfined --security-opt apparmor=unconfined` is sufficient for *every* kernel-touching path the layer stack exercises — only that `unshare -Urm` returns 0.**

This is structural, not nitpick: Option A.2 is the **only** recommendation that achieves the perf driver (§2 Driver #2). If A.2's cap set is insufficient for some overlay/mount/devtmpfs interaction the runtime triggers later (e.g., `mount(2)` with specific flag combinations, `pivot_root`, devmount during workspace bootstrap, or fanotify/inotify under hardened seccomp), the system silently degrades to COPY_BACKED — which the plan itself names "unacceptable as default-performance mode" (§2). The acceptance criterion in §7.3 ("at least one strategy-`attempt` exec returns `PRIVATE_NAMESPACE`") is too weak to detect partial coverage: one passing namespace exec proves the *trivial* mount path works, not that the **layer-stack overlay+OCC code path** does — which per project memory is the entire perf justification (199+ overlay layers via `mount(2)` syscall).

The plan also implicitly equates Option A.1 (`--privileged`) and Option A.2 for the kernel surface this code needs ("smallest cap surface that unblocks `unshare -Urm` and `mount(2)`," §3 Axis A). `--privileged` additionally grants `CAP_SYS_PTRACE`, `CAP_NET_ADMIN`, all `--device` access, and disables cgroup namespace remapping. Whether any of those matter is **answerable empirically** but the plan never asks. If they do, the recommendation collapses into "Option A.2 plus whatever Docker storage-driver workarounds the host needs."

**Why this forces a different design (not just a different test):** the right answer might be to ship **Option A.1 (`--privileged`) as the default** with Option A.2 as an `EOS_DOCKER_MIN_CAPS=1` opt-in for security-sensitive deployments — not the other way around. The plan inverts the burden of proof: it asks A.2 to demonstrate it's sufficient (with COPY_BACKED as silent backstop), when it should ask A.1 to demonstrate it's *necessary* relaxation.

## Tradeoff Tensions

### 1. Overlay-on-overlay storage-driver penalty (entirely unmentioned)

Docker's default storage driver is `overlay2`. Mounting your own kernel-overlay via `mount(2)` from **inside** an overlay2-backed container stacks overlays. Project memory ("Overlay 16-layer cap is util-linux mount(8), not kernel — mount(2) syscall takes 199+ overlay layers") refers to overlay-on-ext4 on the *host*. Overlay-on-overlay2 has documented performance regressions (CoW amplification, syscall overhead) and a known kernel restriction set distinct from native overlay. The plan claims overlay perf is preserved "if `unshare -Urm` works" (§2 Driver #1) — that's the wrong condition. The right condition is "if `unshare -Urm` works AND inner-overlay-on-outer-overlay2 doesn't multiply per-syscall cost." Neither §5 nor §7 measures this. **The plan needs a perf-comparison acceptance criterion: Docker vs. Daytona on a representative SWE-EVO instance, p50/p95 exec latency budget, before flipping the default.**

### 2. Caller-contract regression at the HTTP boundary

The plan focuses on `benchmarks/sweevo/sandbox.py:310`, which wraps `get_build_logs_url()` in try/except. But the surface is wider: `sandbox/api/_sandbox_control.py:84-89` re-exports `get_signed_preview_url` and `get_build_logs_url` unwrapped, and `sandbox/api/__init__.py:42,45,86,89` re-exports those for HTTP/CLI consumers. Docker's `get_signed_preview_url()` returning `{"url": None, "reason": ...}` conforms to the Protocol signature (`dict[str, Any]`) — so this is **not** a Protocol violation as I initially suspected. The real risk is downstream tolerance: HTTP clients (frontend, integration test fixtures, anything that hits these endpoints) will receive a dict with `url=None` instead of a URL. None of these consumers are audited in §5.

## Principle-Violation Check

**§1 Principle 4** ("Capability-minimum container ... the existing COPY_BACKED fallback ... is the safety net, **not a co-equal mode**") is in **soft tension with §4 Scenario 1 mitigation** and **§6 step 6 docs** (`"On macOS Docker Desktop, expect mount_mode=COPY_BACKED for some execs ..."`). The plan tells macOS users to live with the fallback as their normal mode, while declaring the fallback non-co-equal. The synthesis is: macOS-on-Docker is **not a supported default configuration**, and the plan should say so explicitly (the user *is* on darwin per the environment, so this is operationally consequential). Either macOS users get Daytona by default on darwin, or this principle is honored only on Linux.

**§1 Principle 2** ("Both providers coexist ... runtime env-var flip with no DB migration") is contradicted by `task_center_runner/core/bootstrap.py:16,41` — the `_BOOTSTRAPPED = True` sentinel makes `bootstrap_real_agent_runtime()` a no-op on second invocation. **The single-env-var rollback in §8 only works on process restart.** In a long-lived runner daemon, test harness with module-cached state, or notebook session, flipping `EOS_SANDBOX_PROVIDER=daytona` is inert until process exit. The plan needs to acknowledge this or expose `reset_sandbox_provider()` that bypasses the sentinel. (The plan explicitly bans modifying `_BOOTSTRAPPED` in §9 — so the rollback story must be: "restart required.")

## Specific Revision Asks

1. **§3 Axis A — reframe the capability decision as empirical, not declarative.** Before approving A.2 as default, add a verification gate: run the layer-stack's hottest mount/overlay code path under A.2 in a Linux CI container; if it ever falls through to COPY_BACKED in that run, demote A.2 to "minimum-caps opt-in" and elevate A.1 (`--privileged`) to default. Treat the burden of proof as "necessary relaxation," not "sufficient cap surface."

2. **§5.3 / §7.3 — strengthen the perf acceptance criterion.** Replace "at least one `attempt` exec returns `PRIVATE_NAMESPACE`" with a **mount-mode coverage ratio** (e.g., "≥95% of `attempt`-strategy execs in the SWE-EVO smoke run report `PRIVATE_NAMESPACE`, with a p95 exec-latency delta vs. Daytona within ±20%"). One passing namespace exec is necessary, not sufficient.

3. **§8 — fix the rollback claim or scope it honestly.** Either (a) state "Rollback requires process restart due to the `_BOOTSTRAPPED` sentinel at `task_center_runner/core/bootstrap.py:16,41`," or (b) carve out `reset_sandbox_provider()` to invalidate the sentinel — but this contradicts §9's no-change-to-`_BOOTSTRAPPED` rule, so option (a) is cleaner.

4. **§6 step 6 / §9 — make the macOS posture explicit.** Add: "On `darwin` host, `bootstrap_sandbox_provider()` defaults to `daytona` unless `EOS_SANDBOX_PROVIDER=docker` is set explicitly." This preserves Principle 4 (COPY_BACKED isn't the default mode anywhere) and protects the local-dev experience for the user-on-darwin case the environment surfaces.

5. **§5.1 — broaden the HTTP-tolerance audit.** Add a unit test that calls `sandbox.api.get_signed_preview_url(sandbox_id, port)` and `sandbox.api.get_build_logs_url(sandbox_id)` against the Docker provider, asserts no exception, AND asserts the response shape is unchanged for downstream JSON serializers. If any FastAPI route or frontend client unwraps `result["url"]` without a None-check, that's the breakage to surface.

## Synthesis (what the plan should become)

Keep the seam discipline, the dispatcher design, the snapshot-creation branch isolation — those are sound. Change four things:

- **Invert the capability default**: ship Docker with `--privileged` (Option A.1) as the *Linux default* and `EOS_DOCKER_MIN_CAPS=1` opt-in for Option A.2. Justify with a CI experiment that proves A.2 is sufficient for the full layer-stack code path, then flip the default back once proven. (This is the path that *honors* Principle 4 — minimum-caps becomes a documented hardening track, not an unverified default.)
- **Per-platform default**: on `darwin`, the dispatcher defaults to `daytona` to avoid shipping COPY_BACKED-mode as the macOS default.
- **Honest rollback**: §8 says "restart-required env flip"; no in-process flip claim.
- **Stronger acceptance gates**: mount-mode coverage ratio + perf delta vs. Daytona before promoting Docker to "default" in `main`.

If the team disagrees with the Linux-default A.1 inversion: ship A.2 as default but make the perf/coverage acceptance criterion blocking. Either way, the current §7 cannot detect the failure mode that matters.

---

**References (verified during this review):**
- `backend/src/sandbox/provider/protocol.py:21-64` — Protocol surface (single seam claim holds).
- `backend/src/sandbox/provider/registry.py:25-29` — `set_default_provider` silently overwrites.
- `backend/src/task_center_runner/core/bootstrap.py:16,41,68` — `_BOOTSTRAPPED` sentinel blocks re-init.
- `backend/src/sandbox/api/_sandbox_control.py:84-89` — unwrapped HTTP-boundary callers.
- `backend/src/sandbox/execution/strategies/namespace.py:113-134` — COPY_BACKED fallback wiring verified.
- `backend/src/sandbox/provider/daytona/exec_context.py:91-95,97-100` — `prepare_sandbox_runtime_context` and adapter-registration shape (mirror target for Docker).
- `backend/src/benchmarks/sweevo/sandbox.py:310` — single try/except site (plan's tolerance evidence — but only one site, not "callers" plural).
