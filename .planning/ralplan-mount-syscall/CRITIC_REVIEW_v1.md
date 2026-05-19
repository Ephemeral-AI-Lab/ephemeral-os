# CRITIC_REVIEW_v1 — PLAN_v1 (mount(8) → mount(2) via ctypes)

## Verdict: **APPROVE**

The plan is structurally sound, single-commit-revertable, and correctly identifies the recoverability contract as the load-bearing invariant. The architect's six revisions are all tightening — five wording/cite-add and one (#1) is a net code reduction that aligns with project CLAUDE.md §3. No independent blocking issue surfaces on verification.

---

## Architect revision adjudication

| # | Ask | Blocking? | Notes |
|---|---|---|---|
| 1 | Remove `pass_fds` outright | **No, but should be adopted** | Project CLAUDE.md §3: "Remove imports/variables/functions that YOUR changes made unused." `pass_fds` becomes dead by this plan's own changes — the syscall has no inherited-fd contract. Keeping it adds docstring + rationale comment to preserve an interface no one else calls. A one-line removal at `namespace_child.py:80` (verified) is *smaller* surface than vestigial parameter + docstring. Not a hard blocker because the plan's design is correct either way; the principle violation is LOW severity. Plan v2 should adopt the removal. |
| 2 | Reframe §1 principle 1 as "two-file change" | No, stylistic | Wording fix. Honest framing, no scope impact. |
| 3 | Pre-mortem #3 explicit E2BIG/EINVAL fallback | No, stylistic clarification | The existing COPY_BACKED path already handles this. `MountError(OSError)` with errno preserved propagates through `except MountError as exc` → `_fail(..., recoverable=True)` → exit 125 → `should_fall_back` at `namespace.py:113-134` returns True for any `error_kind="mount_failed"` regardless of errno. No new code path needed — just one sentence in the pre-mortem stating the existing fallback covers it. |
| 4 | One-sided perf gate (`p50 ≤ subprocess baseline`) | No, but justified | ctypes eliminates fork/exec/PATH-resolution overhead; a regression on this hot path would indicate a configuration error (libc resolution not cached, lru_cache misapplied). Asymmetric gate strengthens regression detection without new infra. Adopt. |
| 5 | Soften cross-plan obligation wording | No, stylistic | Wording fix. PLAN_v3 §6 Step 0 is mount(8)-scoped with syscall migration as follow-up; this plan *enables* not *obligates* a PLAN_v3 edit. |
| 6 | Cite memory source for Option D dismissal | No, stylistic | One-sentence cite of `overlay_depth_cap_root_cause.md`. Closes a "how do you know?" gap for the next reviewer. |

---

## Independent findings (verified, none blocking)

1. **Test discovery on rootless-unshare-disabled hosts.** Verified `detect_private_mount_namespace()` at `namespace.py:137-152` correctly returns False on macOS (non-Linux) and on locked-down Linux hosts where `unshare -Urm true` fails. CI Linux runners with rootless-unshare enabled will exercise the tests; developer macOS hosts skip cleanly. Acceptable — no dockerized fixture required for this plan's scope. A future plan can add a musl/Alpine CI lane (already listed in §10 Follow-ups). **Not blocking.**

2. **MountError exception identity across module boundaries.** `MountError(OSError)` defined in `kernel_mount.py`, raised there, caught in `namespace_child.py` after import. Python's exception identity is by class object, not name string — since both files share the same module import (`from sandbox.execution.overlay.kernel_mount import ... MountError`), `isinstance(exc, MountError)` is reliably True. **Confirmed sound.** Worth one sentence in the plan for clarity but not blocking.

3. **`_load_libc` log-once intent.** Plan §6.1 specifies `functools.lru_cache` on `_load_libc()`; plan §5 says "log libc soname once per process." With `lru_cache`, the function body executes exactly once on first invocation — so a log statement *inside* the function body fires exactly once, matching intent. If a future maintainer moved the log to the call site, it would fire every call. Worth a one-line clarification in §6.1 ("log statement lives inside `_load_libc()` so `lru_cache` semantics enforce log-once"). **Not blocking** — implementation pattern is correct as specified.

4. **`namespace.py:24-26` constants and `_fail(..., recoverable=True)` wiring.** Verified: `NAMESPACE_INFRA_EXIT_CODE = 125` (line 24), `NAMESPACE_FALLBACK_STRATEGY = MountMode.COPY_BACKED.value` (line 26). The `_fail` function at `namespace_child.py:211-236` already wires recoverable=True → writes control_ref containing `{"error_kind": ..., "fallback": NAMESPACE_FALLBACK_STRATEGY}` → returns 125. Consumer at `namespace.py:113-134` (`should_fall_back`) reads exit_code==125 AND `error_kind=="mount_failed"` AND `fallback==COPY_BACKED` — the new `except MountError` block reuses this exact contract by passing `error_kind="mount_failed"` and `recoverable=True`. **The path is already wired correctly for the new exception type; no `_fail` signature change needed.**

---

## Final decision rationale

The plan correctly identifies the single load-bearing invariant (recoverable-error contract → COPY_BACKED), proposes a mechanically sound preservation strategy (`MountError(OSError)` + ordered catch), and gates the regression with an explicit unit test. Scope is correct: two-file change, no new deps, single-commit revertable.

All six architect revisions are tightening — none blocks execution. The most substantive (#1, `pass_fds` removal) is a net code reduction and CLAUDE.md alignment, but the plan as written still works correctly with the deprecated parameter; the violation is LOW severity. Recommend adopting #1 in PLAN_v2 for cleanliness, but do not gate shipping on it.

The four independent checks all came back green: test skip semantics correct, exception identity sound, log-once pattern correctly relies on `lru_cache` body semantics, recoverable-path wiring already in place. No independent blocker exists.

Realist check: worst-case failure is `MountError` slips through to generic `OSError` handler at `namespace_child.py:91`, classified as `setup_failed` (not recoverable), silently disabling COPY_BACKED for overlay failures. Mitigated by the explicit regression test in §5 (`test_namespace_child_classifies_MountError_as_mount_failed_recoverable`). Detection time: immediate at CI. Fix: trivial catch-order adjustment. Severity correctly contained.

**Ship PLAN_v2 once architect's six revisions are folded in.** No further consensus iteration required.

---

## Ralplan summary

- Principle/Option Consistency: **Pass** — principles 1-5 cleanly map to Option A choice and §6 work breakdown.
- Alternatives Depth: **Pass** — Option D explicitly invalidated with codepath argument; B/C dismissed with cost/benefit.
- Risk/Verification Rigor: **Pass** — pre-mortem identifies the load-bearing recoverability invariant; §5 includes a regression test for exactly that path.
- Deliberate Additions: **Pass** — pre-mortem (3 scenarios) and expanded test plan (unit/integration/observability) both present and substantive.
