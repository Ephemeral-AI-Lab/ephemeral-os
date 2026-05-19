# ARCHITECT_REVIEW_v1 — PLAN_v1 (mount(8) → mount(2) via ctypes)

## Verdict: **APPROVE with revisions**

Single-file logic change, clean blast radius, recoverability contract correctly identified as the load-bearing invariant. Revisions below are tightening, not blocking — the structural direction is right.

---

## Antithesis (steelman)

The plan's §1 framing as "surgical, single-file change" is *almost* true but understates the blast radius. The failure-classification contract spans **two** files (`kernel_mount.py` raises, `namespace_child.py:83-92` translates → exit code 125 → COPY_BACKED fallback at `namespace.py:113-134`). §8 implicitly admits this by bundling both reverts atomically. The principal risk is not the ctypes call itself — it is errno-to-exception drift breaking the `mount_failed` / `recoverable=True` path, which silently disables the COPY_BACKED safety net for the entire overlay execution strategy. The plan recognizes this (Pre-mortem #2) and addresses it with `MountError(OSError)` plus a catch-order edit; the regression test in §5 closes the gap. So: structurally sound, but §1 should say "two-file change with one load-bearing invariant" rather than "single-file change."

---

## Tradeoff tensions

1. **Capability win vs. fallback continuity.** Unlocking the 199+ depth regime requires moving off the well-trodden subprocess error path onto a new exception hierarchy. The only graceful-degradation mechanism (COPY_BACKED via exit-code 125) is exactly what the new exception path must continue to trigger. The plan correctly identifies this as the single load-bearing thread; **the regression test for it is non-optional**.

2. **Surgical minimalism vs. orphan cleanup.** §6 item 1 keeps `pass_fds` as a "deprecated/ignored" parameter to avoid editing `namespace_child.py:80`. But the project's CLAUDE.md §3 says: "Remove imports/variables/functions that YOUR changes made unused." Carrying a dead parameter to avoid a one-line caller edit inverts the principle.

---

## Principle check (deliberate mode)

- **CLAUDE.md §3 "Surgical Changes" / orphan cleanup** — *LOW severity violation*. `pass_fds` becomes dead by this plan's own changes (the syscall has no inherited-fd contract). Rationale "avoid expanding caller-change surface" is backwards: a one-line keyword removal at `namespace_child.py:80` is smaller surface than a vestigial parameter living indefinitely with a docstring note. **Revise.**
- **CLAUDE.md §2 "Simplicity First"** — borderline. The deprecated-parameter carve-out adds code (docstring, rationale comment) to preserve an interface no one calls except the one caller you're also editing. Same fix as above resolves it.
- **Recoverable-error contract** (plan's own §1.2) — preserved correctly. `MountError(OSError)` + ordered catch is mechanically sound (verified: Python catches subclass before parent).

---

## Revision asks (numbered)

1. **Remove `pass_fds` outright** from `mount_overlay` signature. Update the single caller (`namespace_child.py:75-81`) by dropping the `pass_fds=mount_inputs.fds` line. `MountInputs.fds` are still opened by `validate_mount_inputs` and closed by `MountInputs.close()` in the `finally` block — the `/proc/self/fd/N` security mechanism is unchanged. Two-line net diff vs. the plan's docstring carve-out; aligns with project CLAUDE.md §3.

2. **Reframe §1 principle 1** from "surgical, single-file change" to "two-file change with one load-bearing invariant (the `mount_failed` / `recoverable=True` contract)." Honest framing, no scope expansion — the surface is still the same.

3. **Pre-mortem #3 (option-string >PAGE_SIZE)**: state the fallback explicitly. Project memory shows opts.len = 725 bytes at depth 199 with relative paths, well under 4096 — but absolute paths could be larger. Add to the mitigation: "If kernel returns `E2BIG`/`EINVAL`, `MountError` propagates with errno preserved; the existing COPY_BACKED fallback at `namespace.py:113-134` handles it as a recoverable mount failure. No new code path needed; document as a known capability ceiling." No hard cap or fallback strategy required beyond what the recoverability contract already provides.

4. **§5/§7 performance gate**: change the acceptance from "p50 within ±10% of subprocess baseline" to **"p50 ≤ subprocess baseline at depth 10"**. ctypes eliminates fork/exec/path-resolution overhead; a regression on this path would indicate something pathological (e.g., libc resolution on the hot path instead of cached, or `lru_cache` misconfigured). Asymmetric one-sided gate, no test-infrastructure change.

5. **§7 cross-plan acceptance bullet**: soften wording from "unblocks Docker provider PLAN_v3 §6 Step 0 preflight" to **"enables the PLAN_v3 ADR §Follow-ups item (line 404) to land — preflight rewrite is optional, not obligatory."** Verified: PLAN_v3 §6 Step 0 (line 135-163) explicitly scopes preflight to the current `mount(8)` path, with the syscall migration tracked as a follow-up. This plan enables the follow-up; it does not obligate a PLAN_v3 edit.

6. **§3 Option D dismissal — clarify rationale**: the plan calls D "invalidated" by asserting it wraps the same util-linux libmount path. This is logically tight (project memory shows util-linux 2.41 routes overlay through `fsopen()/fsconfig()/fsmount()`; `python-libmount` is by definition Python bindings to `libmount.so`), but the plan should cite the memory note (`overlay_depth_cap_root_cause.md`) as the primary source rather than presenting the claim bare. One sentence addition; closes a "how do you know?" gap for the next reviewer.

---

## Synthesis

The plan's core architectural direction is correct and the change is appropriately scoped. Five of six revisions are wording/principle tightening (asks #2, #3, #5, #6 cite-add) with zero scope impact; ask #1 (`pass_fds` removal) is a 2-line diff that *reduces* the change surface relative to the plan as written; ask #4 is a one-sided acceptance gate that strengthens regression detection without adding test infrastructure. Implement the six revisions, re-emit as PLAN_v2, ship. The COPY_BACKED safety net combined with the regression test in §5 makes this a low-blast-radius capability unlock — exactly the kind of change that should ship in one commit.

## References

- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/sandbox/execution/overlay/kernel_mount.py:35-59` — current `mount_overlay`/`umount` implementations the plan replaces.
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/sandbox/execution/strategies/namespace_child.py:75-94` — single call site of `mount_overlay` and the exception-classification block the plan modifies.
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/sandbox/execution/strategies/namespace.py:113-134` — `should_fall_back` consumer of the `mount_failed` / `recoverable=True` contract; the load-bearing downstream.
- `/Users/yifanxu/.claude/projects/-Users-yifanxu-machine-learning-LoVC-EphemeralOS/memory/overlay_depth_cap_root_cause.md` — primary source for the util-linux 2.41 `mount(8)` ceiling and syscall-path 199-layer success.
- `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/.planning/ralplan-docker-provider/PLAN_v3.md:135-163, 404` — confirms Step 0 preflight is `mount(8)`-scoped and the syscall migration is tracked as a separate follow-up.
