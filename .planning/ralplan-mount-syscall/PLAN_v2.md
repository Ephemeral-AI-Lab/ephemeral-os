# PLAN_v2 — Migrate overlay `mount(8)` → `mount(2)` via ctypes

**Mode:** RALPLAN-DR deliberate.
**Scope:** unlock the 199+ overlay-layer regime before the Docker provider plan lands, so Docker provider PLAN_v3 §6 Step 0 preflight verifies the syscall path rather than util-linux's throwaway ceiling.
**Surface:** one file changed (`kernel_mount.py`), one block adjusted in a single caller (`namespace_child.py:83-92`), new unit tests, no new deps.

---

## 1. Principles

1. **Surgical, two-file change with one load-bearing invariant: the `mount_failed` / `recoverable=True` contract.** `validate_mount_inputs`, `MountInputs`, and the `/proc/self/fd/N` security mechanism stay byte-for-byte identical.
2. **Preserve the recoverable-error contract.** The caller at `namespace_child.py:83-88` classifies mount failures as `error_kind="mount_failed"` with `recoverable=True`, which triggers the COPY_BACKED fallback via `NAMESPACE_INFRA_EXIT_CODE` (`namespace.py:24`) and `NAMESPACE_FALLBACK_STRATEGY` (`namespace.py:26`). Any failure-path change must keep this trigger intact.
3. **No new system or Python dependencies.** ctypes is in stdlib; libc is on every Linux target. Bundling a helper binary or pulling `cffi`/`pylibmount` adds packaging surface for no benefit.
4. **Observable.** Log the loaded libc path on first mount; emit per-call mount/umount latency so perf parity with `subprocess.run` is provable.
5. **Reversible.** Single-commit revert restores `mount(8)` behavior; caller error path is backward-compatible.

## 2. Decision Drivers (top 3)

1. **Capability.** `mount(8)` caps overlay depth at ~10-16 layers; `mount(2)` succeeds at 199+ (project memory: `overlay_depth_cap_root_cause.md`). Without this, the depth-≤14 squash policy is the binding constraint, not the kernel.
2. **Cross-plan dependency.** Docker provider PLAN_v3 §6 Step 0 preflight needs the syscall path to probe true depth limits. Shipping this first unblocks honest preflight numbers.
3. **Risk surface.** Failure-path classification at `namespace_child.py:83-92` is load-bearing for COPY_BACKED fallback. Any errno or exception-type drift silently breaks recovery — the highest-priority regression to gate against.

## 3. Viable Options (how to call `mount(2)` from Python)

| Option | Pros | Cons |
|---|---|---|
| **A. ctypes against libc** (chosen) | Stdlib only; loads `libc.so.6` (glibc) or `libc.musl-*.so.1` (musl) via `CDLL(..., use_errno=True)`; ~30 lines; project memory explicitly says "single C extension or ctypes call is enough." | Must probe libc soname across glibc/musl; manual errno conversion. |
| **B. cffi C extension** | Slightly cleaner API. | New build-time dep; wheels per-arch; overkill for one syscall. |
| **C. Bundle static `eos-mount` helper, subprocess to it** | Same capability win; binary distributable. | New build pipeline; signed-binary policy concerns; still subprocess overhead per call; doesn't simplify error handling. |
| **D. `python-libmount` / `pylibmount`** | Existing bindings. | Wraps util-linux's `libmount`, which routes overlay through the SAME `fsopen()/fsconfig()/fsmount()` path that fails at depth >16. **Does not solve the problem.** Invalidated. (Per project memory `~/.claude/projects/-Users-yifanxu-machine-learning-LoVC-EphemeralOS/memory/overlay_depth_cap_root_cause.md`: util-linux 2.41 routes overlay mounts through `fsopen()`/`fsconfig()`/`fsmount()`; `python-libmount` is by definition Python bindings to that same `libmount.so`, so it inherits the failure.) |

**Decision: Option A (ctypes).** B is unjustified complexity; C duplicates B's downsides plus a binary supply chain; D doesn't fix the root cause.

## 4. Pre-mortem (3 failure scenarios)

1. **musl vs glibc libc loading.** `ctypes.CDLL("libc.so.6")` succeeds on glibc, fails on Alpine/musl which uses `libc.musl-x86_64.so.1`. *Mitigation:* probe order `["libc.so.6", "libc.musl-x86_64.so.1", ctypes.util.find_library("c")]`; raise a clear `RuntimeError` on first call if none load. Log the resolved path once per process. Test on a musl image in CI if available; otherwise gate on glibc only and document.
2. **Errno → exception classification drift.** Replacing `subprocess.CalledProcessError` with `OSError` will cause the current `except subprocess.CalledProcessError` block at `namespace_child.py:83-88` to miss, falling through to `except OSError as exc:` at line 91 → `error_kind="setup_failed"` (NOT recoverable). This **silently disables COPY_BACKED fallback**. *Mitigation:* define `MountError(OSError)` in `kernel_mount.py`; update `namespace_child.py:83-92` to catch `MountError` first with `recoverable=True`; keep generic `OSError` handler unchanged. Regression-test the failure path explicitly.
3. **High-layer-count secondary limits.** At 199 layers the `lowerdir=...` option string may exceed PAGE_SIZE (4096 bytes) — overlayfs's historical option-buffer cap. *Mitigation:* unit test at depths 1/10/16/50/100/199 measures option-string length; if a kernel-side limit appears before 199, surface it via a distinct errno (E2BIG/EINVAL) and document. Out-of-scope: changing the squash policy in `layer_stack/` to compress lowerdirs — that's a future plan. If the kernel returns E2BIG/EINVAL on an over-long option string, `MountError` propagates with errno preserved; the existing COPY_BACKED fallback at `namespace.py:113-134` already handles it as a recoverable mount failure. No new code path required; document as a known capability ceiling that surfaces cleanly via the existing recoverability contract.

## 5. Expanded Test Plan

**Unit tests (new file `backend/tests/unit_test/test_sandbox/test_overlay/test_kernel_mount.py`):**
- `test_mount_round_trip_depth_{1,10,16,50,100,199}` — build N tmpdirs as lowerdirs, mount+umount inside an `unshare -Urm` subprocess wrapper; skip if `detect_private_mount_namespace()` returns False (so dev macOS/locked-down hosts skip cleanly).
- `test_mount_error_raises_MountError_with_errno` — point `lowerdir` at a non-existent path; assert raises `MountError`, `.errno` is set, message includes `strerror`.
- `test_namespace_child_classifies_MountError_as_mount_failed_recoverable` — invoke `namespace_child.execute` with a payload designed to fail at mount; assert exit code == `NAMESPACE_INFRA_EXIT_CODE` (125) and control_ref contains `error_kind="mount_failed"`, `fallback=COPY_BACKED`.
- `test_libc_probe_logs_path_once` — assert libc soname is logged at INFO on first call, not on subsequent calls.

**Integration:**
- Existing `tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_*.py` (20 tests, listed) must pass unchanged. These exercise the real overlay path end-to-end.

**Observability:**
- `command_exec.mount_workspace_s` timing already captured at `namespace_child.py:82`; verify p50 ≤ subprocess baseline at depth 10 (ctypes eliminates fork/exec; a regression here indicates libc resolution not cached). Add a parallel `command_exec.umount_workspace_s` measured around the `umount` call in the `finally` block at `namespace_child.py:129` (one-line addition is in-scope; this is the same execution context the mount timing lives in).
- Log libc soname once per process at INFO (e.g., `"overlay mount: libc resolved to /lib/x86_64-linux-gnu/libc.so.6"`).

## 6. File-by-file Work Breakdown

1. **Modify `backend/src/sandbox/execution/overlay/kernel_mount.py`** (only file with logic changes):
   - Add module-level `_libc` resolution with a private `_load_libc()` helper that probes `["libc.so.6", "libc.musl-x86_64.so.1", ctypes.util.find_library("c")]` and caches via `functools.lru_cache`. The `logger.info('libc loaded: %s', path)` call lives INSIDE the `_load_libc` function body, which is `@functools.lru_cache`-decorated; the function body executes exactly once on first invocation, so log-once semantics are enforced by the cache.
   - Add `class MountError(OSError): ...` — subclass of OSError so existing generic `OSError` handlers still work as a safety net; subclass marker lets `namespace_child.py` catch it specifically.
   - Rewrite `mount_overlay` (`kernel_mount.py:35-50`): drop `subprocess.run`; call `_libc.mount(b"overlay", target.encode(), b"overlay", 0, options.encode())`; on `rc != 0`, read `ctypes.get_errno()`, build `MountError(errno, os.strerror(errno), str(workspace_root))` and raise.
   - Rewrite `umount` (`kernel_mount.py:53-59`): call `_libc.umount2(target.encode(), 0)`; on `rc != 0`, log at DEBUG (keeps `check=False` semantics — non-fatal) and return.
   - **Remove `pass_fds` parameter** from `mount_overlay`'s signature. `MountInputs.fds` are still opened by `validate_mount_inputs` and closed in the `finally` block — the `/proc/self/fd/N` security mechanism is unchanged; the fds just no longer need to be inherited by a subprocess.
   - Keep `__all__` intact; add `"MountError"`.

2. **Modify `backend/src/sandbox/execution/strategies/namespace_child.py:83-92`** (single error-handling block):
   - Insert `except MountError as exc:` (importing `MountError` from `kernel_mount`) BEFORE the existing `except OSError` block. Body: `return _fail(request, timings, "mount_failed", f"{exc}; errno={exc.errno}", recoverable=True)`.
   - Remove `except subprocess.CalledProcessError` block (no longer reachable from `mount_overlay`).
   - Leave `except ValueError`, `except OSError`, `except Exception` untouched.
   - Remove now-unused `import subprocess` if no other use remains in the file (it does — `run_command_to_refs` may import indirectly; verify; if file no longer uses `subprocess` directly, drop the import).
   - **Drop the `pass_fds=mount_inputs.fds` keyword from the single call site at `namespace_child.py:75-81`.** `MountInputs.fds` are still opened by `validate_mount_inputs` and closed in the `finally` block — the `/proc/self/fd/N` security mechanism is unchanged; the fds just no longer need to be inherited by a subprocess. `MountError` is a class object imported by `namespace_child.py` from `kernel_mount.py`, so `isinstance(exc, MountError)` is reliably True across the module boundary.

3. **New file `backend/tests/unit_test/test_sandbox/test_overlay/test_kernel_mount.py`** — tests listed in §5. Add `__init__.py` under `test_overlay/` if directory doesn't exist.

4. **No changes to:** `validate_mount_inputs`, `MountInputs`, `_open_dir_no_follow`, `namespace.py`, `layer_stack/`, `daemon/`, `host/`, env_policy.

## 7. Acceptance Criteria

- [ ] All pre-existing tests pass unchanged (`uv run pytest backend/tests/unit_test/test_sandbox/`).
- [ ] New unit tests pass at depths 1, 10, 16, 50, 100, 199 on Linux CI; skip cleanly on macOS / dev hosts without rootless-unshare.
- [ ] `mount_overlay` raises `MountError(OSError)` on failure; `namespace_child.execute` classifies it as `mount_failed` with `recoverable=True` and writes `control_ref` containing `fallback=COPY_BACKED` (verified by new unit test).
- [ ] `command_exec.mount_workspace_s` p50 ≤ subprocess baseline at depth 10 (ctypes eliminates fork/exec; a regression here indicates libc resolution not cached).
- [ ] Existing `tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_*.py` suite passes unchanged.
- [ ] **Cross-plan:** enables (does not obligate) PLAN_v3's ADR §Follow-ups item (line 404) to land. PLAN_v3 §6 Step 0 is explicitly mount(8)-scoped today; a separate plan can rewrite it once this work ships.
- [ ] No new entries in `backend/pyproject.toml` dependencies.
- [ ] `_fail(..., recoverable=True)` path at `namespace_child.py:211-236` is already wired correctly for the new exception type; no `_fail` signature change required (verified during Critic review).

## 8. Rollback

- Single-commit revert restores `subprocess.run(["mount", ...])` semantics.
- `namespace_child.py` caller change is backward-compatible: `MountError` is an `OSError` subclass, so even if `kernel_mount.py` is reverted and `mount_overlay` reverts to raising `CalledProcessError`, the old `except subprocess.CalledProcessError` block would also need to be restored — bundle both reverts in the same commit so revert is atomic.

## 9. Out of Scope (explicit non-goals)

- Do **NOT** change `MountInputs`, `validate_mount_inputs`, or the `/proc/self/fd/N` path mechanism.
- Do **NOT** change `namespace.py`, or any block in `namespace_child.py` outside lines 83-92 (plus removing an unused `import subprocess` if applicable).
- Do **NOT** touch `layer_stack/`, `daemon/`, `host/`, or any provider/* directory.
- Do **NOT** modify Docker provider PLAN_v3 or any provider code — that's PLAN_v3's job; this plan only unblocks its preflight.
- Do **NOT** add a static helper binary, `eos-mount` package, or any new Python/system dep.
- Do **NOT** change the squash policy in `layer_stack/`. Raising the actual layer cap above 14 is a future plan with its own perf characterization (lowerdir option-string length, mount latency at depth, OCC fan-in cost).

---

## 10. ADR

- **Decision:** Replace `subprocess.run(["mount", "-t", "overlay", ...])` in `backend/src/sandbox/execution/overlay/kernel_mount.py:44-50` with a direct `mount(2)` syscall via Python ctypes against libc. Same treatment for `umount` at `kernel_mount.py:53-59` using `umount2(2)`.
- **Drivers:**
  1. Capability — unlock the 199+ overlay-layer regime (primary-source: `overlay_depth_cap_root_cause.md`).
  2. Unblock Docker provider PLAN_v3 §6 Step 0 honest preflight.
  3. Preserve the COPY_BACKED fallback recoverability contract at `namespace_child.py:83-92` / `namespace.py:24-26,121-133`.
- **Alternatives considered:** cffi extension (B, rejected: unjustified build complexity); bundled `eos-mount` static helper (C, rejected: same downsides as B plus a binary supply chain); `pylibmount` Python bindings (D, **invalidated**: wraps the same util-linux `libmount` codepath that fails — does not fix root cause).
- **Why chosen:** ctypes is stdlib, ≤30 lines of new code, matches project memory's explicit recommendation, preserves all security mechanisms (`/proc/self/fd/N` paths, `MountInputs`, `validate_mount_inputs`), and the failure-path error contract can be preserved by introducing a single `MountError(OSError)` subclass.
- **Consequences:**
  - Removes util-linux 2.41 `mount(8)` from the overlay hot path; depth-cap moves from ~16 to whatever the kernel actually permits (project memory: tested 199+).
  - `pass_fds` parameter on `mount_overlay` is removed; the single caller at `namespace_child.py:75-81` drops the `pass_fds=mount_inputs.fds` keyword. The `/proc/self/fd/N` security mechanism is unchanged.
  - Slightly more involved test-skip logic on non-Linux dev machines, but `detect_private_mount_namespace()` already exists at `namespace.py:137-152` and gates this.
  - Process now loads libc at first overlay mount — one-time cost, ~µs; logged once.
- **Follow-ups:**
  - (Separate plan) Raise the squash-policy depth cap in `layer_stack/` from 14 to a higher number with per-depth perf characterization.
  - (Separate plan) Add a musl-libc CI lane if/when Alpine becomes a target.
  - (Cross-plan) Update Docker provider PLAN_v3 §6 Step 0 preflight to probe up to 199 layers once this plan lands.
