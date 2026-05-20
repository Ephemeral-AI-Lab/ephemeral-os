# RALPLAN-DR — O(1) per-lease workspace via direct overlay new-mount API

**Iteration:** 2 (revised after Architect REVISE + Critic ITERATE on iter1).

**Mode:** DELIBERATE (high-risk: kernel-boundary mechanics, EXDEV-driven 2 GB tmpfs wall, command-exec hot path on every shell, replaces a widely-touched return type).

**Author:** Planner (consensus workflow). Downstream: Architect → Critic.

**Sources of truth this plan cites (read first):**
- `backend/src/sandbox/layer_stack/stack.py:126-179` — `prepare_workspace_snapshot`
- `backend/src/sandbox/layer_stack/view.py:195-269` — `MergedView.materialize` (the EXDEV-copy victim)
- `backend/src/sandbox/layer_stack/view.py:214` — `for layer in reversed(manifest.layers)` (oldest-first apply ⇒ `manifest.layers` is ordered top-of-stack first / newest-first; written-on-stack-top wins)
- `backend/src/sandbox/layer_stack/lease.py:40-66` — `LeaseRegistry.acquire`, `release`, `pinned_layers` (pin path that `materialize=False` leases reuse unchanged)
- `backend/src/sandbox/execution/strategies/namespace.py:46-111` — payload builder, mounts via subprocess.run
- `backend/src/sandbox/execution/strategies/namespace_child.py:50-130` — calls `mount_overlay`
- `backend/src/sandbox/execution/overlay/kernel_mount.py:35-50` — current `subprocess.run(["mount", ...])`
- `backend/src/sandbox/execution/service.py:69-95` — `execute_command`: builds `OverlayLayout`, hands `lease.lowerdir` to `base_repo`
- `backend/src/sandbox/execution/contract.py:210-226` — `WorkspaceSnapshotLease`, `WorkspaceLeaseClient` protocols
- `backend/src/sandbox/execution/overlay/layout.py:9-49` — `OverlayLayout` validation invariants
- `backend/src/sandbox/provider/docker/client.py:32-34` — `/eos-mount-scratch` tmpfs (2 GB, the wall)
- `backend/src/sandbox/occ/service.py:23` — `AUTO_SQUASH_MAX_DEPTH = 100`
- `backend/src/task_center_runner/tests/_live_config.py:8-13` — `heavy_enabled` and database gates
- `backend/src/task_center_runner/core/stores.py:148-180` — sqlite bundle creation via `Base.metadata.create_all` (this IS the schema bootstrap; no separate migration CLI exists)

Primary external reference (cited inline at Step 2 / §10):
- Linux kernel: `Documentation/filesystems/overlayfs.rst` (overlayfs ordering rules, `lowerdir`, `lowerdir+`); `Documentation/filesystems/mount_api.rst` (`fsopen`/`fsconfig`/`fsmount`/`move_mount`); `arch/x86/entry/syscalls/syscall_64.tbl` + `arch/arm64/include/asm/unistd.h` (syscall numbers 428–442 stable on both arches since v5.2).

---

## Table of contents

1. [RALPLAN-DR summary (deliberate mode)](#1-ralplan-dr-summary-deliberate-mode)
2. [Scope, in/out](#2-scope-inout)
3. [Implementation plan (file-anchored)](#3-implementation-plan-file-anchored)
4. [Validation runbook — heavy_enabled live_e2e on sqlite](#4-validation-runbook--heavy_enabled-live_e2e-on-sqlite)
5. [Audit harness — Bounds A, B, C](#5-audit-harness--bounds-a-b-c)
6. [ADR](#6-adr)
7. [Pre-mortem and mitigations (four independent failure families)](#7-pre-mortem-and-mitigations-four-independent-failure-families)
8. [Expanded test plan](#8-expanded-test-plan)
9. [Observability](#9-observability)
10. [Open questions (resolved + remaining)](#10-open-questions-resolved--remaining)

---

## 1. RALPLAN-DR summary (deliberate mode)

### 1.1 Principles (4, reframed for honesty per Architect A3)

1. **No per-lease workspace materialization on the command-exec hot path WHEN the new mount API is available.** Reads against an unchanged manifest must not copy bytes. Hosts without the new mount API fall through to the existing materialize path; this dual-path is explicit and time-bounded (see Follow-up #5 in §6: sunset target = Linux ≥ 5.11 floor by 2027-Q1, when Debian bookworm-LTS minimum is reached for EphemeralOS supported targets).
2. **Kernel-enforced bounds over runtime checks.** The overlay layer count is bounded by `OVL_MAX_STACK = 500` (empirically bisected this session); we let the kernel reject N > 500 explicitly, and pre-empt it via a tight `OVL_MAX_STACK_GUARD = 110` — i.e., +10% headroom above `AUTO_SQUASH_MAX_DEPTH = 100` (per Architect redline: 450 would catch nothing in practice; 110 catches squash-pressure drift early).
3. **Fail fast over silent truncation.** Classic `mount(2)` data-string truncates at PAGE_SIZE (4 KiB) silently — proven this session at manifest depth ≈ 128. The new mount API per-call `lowerdir+=` API has explicit EINVAL semantics at the boundary and is the only correct path.
4. **Surgical scope: command-exec namespace strategy only.** Plugin/LSP (`WorkspaceProjection`), copy-backed fallback, and squash-checkpoint construction stay on `MergedView.materialize`. They are explicitly out of scope; rationale in §2.

### 1.2 Decision drivers (top 3)

| # | Driver | What it pins |
|---|---|---|
| D1 | The current `MergedView.materialize` invocation at `stack.py:153` hits EXDEV between `/tmp/eos-sandbox-runtime/layer-stack` (overlay2-backed) and `/eos-mount-scratch` (tmpfs 2 GB) on every lease, falling through to `shutil.copy2` per file (`view.py:266-269`). The 2 GB tmpfs is a hard concurrency ceiling — N concurrent leases × workspace_bytes → eviction. | We must eliminate the intermediate lowerdir tree; not "make it faster". |
| D2 | Empirical `OVL_MAX_STACK = 500` (bisected this session); util-linux `mount(8)` argv-truncates around 800-byte option strings (`overlay_depth_cap_root_cause.md` — wrong cause attribution there: 16 is not a kernel limit, it's util-linux). `AUTO_SQUASH_MAX_DEPTH = 100` keeps us 5× under the kernel ceiling, and the new `OVL_MAX_STACK_GUARD = 110` keeps us at a tight pre-empt threshold. | We use the new mount API (`fsopen`/`fsconfig`/`fsmount`/`move_mount`), not the classic `mount(2)` data-string. |
| D3 | Hot-path budget: command-exec is invoked on every agent shell. Current per-call cost includes `layer_stack.materialize_s` plus `command_exec.mount_workspace_s`; the prepare path's `materialize_s` is reported by `_metrics.py:148-163` and visible in the perf payload. | Whatever replaces materialize must remove the materialize cost AND not regress the mount cost AND not introduce per-read CPU regression as M grows (Bound C in §5). The cost target is `materialize_s → 0`, `mount_workspace_s` unchanged or lower, and per-read CPU growth linear in M with a tight slope budget. |

### 1.3 Viable options (steelmanned per Architect A1)

Three options enumerated. Option A chosen; B, C, D invalidated with quantitative argument anchored on the EphemeralOS workload.

**Option A — Direct new mount API with `lowerdir+=` chained writes (CHOSEN).**

Replace `MergedView.materialize` invocation in `prepare_workspace_snapshot` with returning a `tuple[str, ...]` of newest-first layer paths. In `namespace_child.py`, replace `mount_overlay` subprocess with a ctypes wrapper that calls `fsopen("overlay")`, iterates `fsconfig(SET_STRING, "lowerdir+", <path>)` for each layer, sets `upperdir`/`workdir`, then `fsconfig(CMD_CREATE)` + `fsmount` + `move_mount`.

| Pros | Cons |
|---|---|
| Eliminates per-lease lowerdir disk cost — true O(1) (per Bound A in §5). | Requires Linux ≥ 5.2 for `fsopen` family + `lowerdir+=` (5.2 added the new API, `lowerdir+=` is overlayfs-only post 5.11). Fallback path needed for older kernels. |
| Removes the EXDEV pathological case entirely — kernel just opens dir-fds in storage, no cross-FS staging. | Bound by OVL_MAX_STACK = 500. We're 5× under via `AUTO_SQUASH_MAX_DEPTH = 100` but adds a hard pre-mount guard at 110. |
| Surfaces depth violations as explicit EINVAL on `fsconfig`, not silent truncation. | ctypes wrappers add a maintenance surface (libc symbol lookups, syscall numbers per-arch). Risk: aarch64 vs x86_64 syscall numbers. Mitigation: use `libc.syscall(SYS_xxx, ...)` resolved via `os.uname().machine`; assert syscall-number stability via unit test. |
| Empirically validated this session in CAP_SYS_ADMIN container at N=4..500. | Layer-eviction race: kernel holds dirs open via mount; squash `rmtree` deletes dir entries. Need to verify lease-pin invariant survives. (Pre-mortem #3.) |
| Per-read CPU cost grows linearly in M (overlayfs walks the layer chain on each negative-dentry lookup), but the kernel walk is microseconds-per-layer in cache. Quantified in Bound C (§5.4) with a hard slope budget. | Per-read CPU regression at very high M (>100). Mitigated by `OVL_MAX_STACK_GUARD = 110` keeping M small; verified by Bound C harness. |

**Option B — Refcount-cached materialization keyed by manifest version (INVALIDATED).**

Cache the materialized lowerdir keyed by `manifest.version` + `manifest_root_hash`; refcount leases; only materialize when missing. Drop on manifest version change.

| Pros | Cons |
|---|---|
| Pure-userspace; no syscall surface. | Doesn't solve EXDEV — first materialize still pays full copy onto tmpfs ≥ workspace_bytes. The 2 GB ceiling stays. |
| Compatible with old kernels. | Cache invalidation is complex across squash (active manifest changes layer set without changing user-visible content). Two leases sharing a cache while a squash runs is a correctness landmine. |
| | Bound A still violates: N concurrent prepare calls before any cache hit each pays full cost (cold-start herd). |
| | Doesn't unlock Bound B (manifest-depth scaling) — materialize cost grows linearly with M. |

**Invalidation rationale:** B keeps the EXDEV failure (D1) and the silent-truncation failure (D2, indirectly: materialize-back-to-old-mount-path). It is incompatible with Principle 1 and Principle 2.

**Option C — Userspace-merged FUSE overlay (INVALIDATED).**

Run a FUSE process per-lease that presents a merged view.

| Pros | Cons |
|---|---|
| Bypasses overlayfs depth limit (no kernel-side stack). | Adds a persistent process per lease — explicit O(N) memory cost. Defeats Bound A. |
| | FUSE I/O performance penalty on the shell hot path. |
| | New trust boundary (FUSE daemon) — out of scope for "replace one mount step". |

**Invalidation rationale:** C trades disk-O(N) for memory-O(N) plus a new component; not a win.

**Option D — Cached materialize keyed by `(manifest.version, root_hash)` + bind-mount-per-lease (INVALIDATED on the EphemeralOS workload — quantitative argument below).**

Materialize each unique `(manifest.version, root_hash)` exactly once into a stable cache directory; bind-mount the cached directory as the per-lease lowerdir; refcount cache entries against active leases; evict only when refcount = 0 AND a higher-version supersedes. Per-lease cost is the bind-mount syscall + workdir/upperdir creation (cheap).

This is the Architect's synthesis — a serious alternative to A that does not require the new mount API.

| Pros | Cons |
|---|---|
| Per-lease O(1) IF cache hits. | Per-lease O(workspace_bytes) on every cache miss. Misses are workload-driven; see hit-rate analysis below. |
| No new mount-API surface; ships on Linux 4.18+. | First miss still pays EXDEV-style cross-FS copy (overlay2-backed storage → tmpfs scratch), same as today. The 2 GiB scratch ceiling does not move — it now bounds cache size instead of bounding concurrent lease count, which is strictly worse: cache eviction under N concurrent misses is a thundering herd. |
| No OVL_MAX_STACK ceiling (mount is just a single bind, single overlay layer presented to the kernel). | No depth bound = no fail-fast on pathological depth growth (D2). A bug in `AUTO_SQUASH_MAX_DEPTH` enforcement now bloats the cache silently. |
| No per-read CPU regression as M grows (Bound C trivially passes). | Cache key complexity: `manifest.version` flips on every write (see EphemeralOS layer-stack design — every commit produces a new layer + new manifest version, per `project_ephemeralos_layerstack_occ_design.md`). Within one agent session, near-zero intra-session hit rate. |

**Workload-specific quantitative invalidation (the core reason D is rejected for THIS codebase):**

EphemeralOS treats each tool-write as a new layer (see `lease.py:53` — `manifest.layers` is the refcount key; squash compacts them). The reference scenario `complex_project_build_full` writes `AUTO_SQUASH_MAX_DEPTH + 4 = 104` times per session before squash kicks in (`task_center_runner/agent/mock/runner.py:863`, `complex_project_build_probe.py:795`). Each write advances `manifest.version`. The intra-session manifest-version count for one agent is therefore in the order of ~100 versions.

A concurrent agent on a divergent fork sees a different `manifest.version` at the same wall-clock — the cache key does not match across agents. Cross-agent sharing is statistically rare in any session with > 1 write per agent.

So on the reference workload:
- **Intra-session hit rate** ≤ `(shells_per_version) / 104` ≈ a few %, since each new write invalidates the cache key.
- **Cross-session hit rate** ≈ near zero (different agents diverge after their first write).
- **Cache misses per session** ≈ ~100 full materialize events. Each is workspace_bytes onto tmpfs.

D therefore pays approximately the same number of full materialize events as today, while adding cache-keying complexity, cache-eviction logic, and a thundering-herd risk on miss bursts. It also still hits D1 (the EXDEV wall) on every miss. **D is strictly worse than A for this workload** — same materialize cost, more code surface, no new mount-API capability gained.

Cross-check on per-read latency: a steady-state kernel layer walk over M=100 lowers takes ~100µs in cache (overlayfs caches the first layer's positive lookup; subsequent identical lookups skip the walk via dentry cache). On cold-cache reads, the walk is ~10s of µs per layer (kernel does `vfs_lookup` per layer until hit). 1-layer overlay (D) saves ≤ ~1ms per cold read at M=100. The materialize cost saved by A is `workspace_bytes / bandwidth` ≈ seconds at workspace_bytes = 100 MB. A wins by 3 orders of magnitude.

On Docker Desktop's default storage driver: Docker Desktop runs Linux in a VM (LinuxKit ≥ 5.15 today) and supports the new mount API. The EXDEV-on-Docker-Desktop failure mode that motivates D's "old kernel" promise applies only to literal pre-5.2 hosts (RHEL 7, CentOS 7, Ubuntu 18.04 LTS without HWE kernel). These are explicitly out of EphemeralOS supported targets — see `pyproject.toml`'s `requires-python` and Docker's minimum kernel claim. So D's "compatible with old kernels" pro does not buy us a real target.

**Migration risk comparison:**
- A: ctypes module, per-arch syscall numbers, daemon-startup probe (Step 10), feature flag for safety. ~200 LoC new code.
- D: cache directory layout, cache key/eviction (LRU, refcount, watch-for-supersede), per-lease bind-mount syscall wrapper, recovery from cache-corruption, GC on daemon restart. ~500-800 LoC new code, much higher invariant surface.

**Conclusion:** D adopted = same materialize cost as today + new cache subsystem to maintain + still hits the 2 GiB tmpfs ceiling. A adopted = removes materialize entirely on the hot path, surfaces depth violations explicitly, kernel-API surface bounded to one ctypes module. Stick with A.

### 1.4 Mode: DELIBERATE

Required artifacts present in this plan:
- Pre-mortem with **four independent scenarios** (depth/eviction split out; kernel availability; harness measurement failure) — §7
- Expanded test plan (unit / integration / e2e / observability) — §8, §9
- ADR with consequences and follow-ups — §6

---

## 2. Scope, in/out

### In scope (command-exec namespace strategy only)

| Code | Change |
|---|---|
| `sandbox/layer_stack/stack.py:67-83`, `126-179` | Add `layer_paths: tuple[str, ...]` field to `PrepareWorkspaceSnapshotResult`; new code path skips `MergedView.materialize` when caller opts in via flag. Existing `lowerdir` field kept for plugin/copy-backed callers (additive change, no wire break). |
| `sandbox/execution/service.py:69-95` | Pass new `materialize=False` flag (or call a new method); read `lease.layer_paths`; build `LayerPathsLayout` (separate dataclass — see Step 4). |
| `sandbox/execution/contract.py:210-226` | Extend `WorkspaceSnapshotLease` protocol with `layer_paths: Optional[tuple[str, ...]] = None` (Architect A4 redline: `Optional[str] = None`, not empty-string sentinel; same principle applied to tuple form). |
| `sandbox/execution/overlay/layout.py:9-49` | Split into two dataclasses (Architect A4 Step 4 redline — no `__post_init__` branching on a flag): `MaterializeLayout` (existing semantics, requires `base_repo`) and `LayerPathsLayout` (new, requires `layer_paths` + `layer_storage_root`). Both implement a common `OverlayLayoutProtocol`. Validation is per-dataclass; no shared branching. |
| `sandbox/execution/strategies/namespace.py:62-83` | Payload-builder dispatches on layout type; writes `layer_paths` when `LayerPathsLayout`, `lowerdir` when `MaterializeLayout`. |
| `sandbox/execution/strategies/namespace_child.py:50-97`, `133-163` | Read `layer_paths` from payload; pass through to mount helper. |
| `sandbox/execution/overlay/kernel_mount.py` (REWRITE) | Replace subprocess `mount` call with ctypes `fsopen/fsconfig/fsmount/move_mount` driver. Keep `mount_overlay()` signature; accept `layer_paths: tuple[Path, ...]` instead of single `lowerdir`. |
| `sandbox/daemon/handler/workspace.py:68-85`, `sandbox/daemon/workspace_server.py:131-150`, `sandbox/daemon/service/layer_stack_client.py:72-81` | Plumb the new flag/method through the daemon RPC. (Wire format additive; `to_dict()` adds new keys.) Daemon startup probe (Step 10) refuses to advertise namespace mode if `probe_supported()=False`. |

### Out of scope, with rationale (Architect/Critic: do NOT request these)

| Caller | Why it stays on materialize |
|---|---|
| `LayerStack.materialize` (public, `stack.py:244-245`) | Callers explicitly want a real on-disk tree (snapshot dump, debugging). Out of scope. |
| `SquashService.build_checkpoint` (`squash.py:115`) | Builds a NEW on-disk layer to publish. The output IS the materialized tree. Out of scope. |
| `WorkspaceProjection.acquire` (`projection.py:93-104`) → `pyright_session.py:46-94` | Pyright opens `file://<lowerdir>/<repo_path>` URIs; needs a real filesystem path with stable inode lifetime across the LSP session. Direct overlay mount per-LSP-session is a separate optimization with different invariants (mount tied to plugin session, not to one command). **Acknowledged as a follow-up in the ADR.** |
| `CopyBackedStrategy` (`copy_backed.py:60-67`) | The whole point of copy-backed is "I cannot mount overlay, do a real `shutil.copytree`". It is the fallback for hosts where Option A fails. Keeps materialize. |

This scope is non-negotiable for this plan. Expanding it triples the surface area and breaks the surgical-change principle.

---

## 3. Implementation plan (file-anchored)

Each step lists files+line ranges, what changes, and the verification check. Steps are ordered so that any subset that lands compiles and tests pass.

**Critical sequencing note (Critic C1):** Step 2 contains a blocking pre-implementation experiment that resolves overlay `lowerdir+=` priority ordering. **No subsequent step is implementable until that experiment returns a concrete result and the result is recorded inline in this plan at §10 Q1.**

### Step 1 — Add empirical-bound constants and a feature probe

**Files:**
- New: `backend/src/sandbox/execution/overlay/new_mount_api.py`
- `backend/src/sandbox/execution/overlay/__init__.py` (re-export)

**Change:** ctypes-level wrappers for `fsopen` (SYS=430), `fsconfig` (SYS=431), `fsmount` (SYS=432), `move_mount` (SYS=429). Per-arch syscall number table (x86_64, aarch64). Per Architect A4 Step 1 redline: cite `arch/x86/entry/syscalls/syscall_64.tbl` and `arch/arm64/include/uapi/asm/unistd.h` (or equivalently `man 2 syscalls` for stability claim) in the module docstring; assert numerical equality (`SYS_FSOPEN_X86_64 == SYS_FSOPEN_AARCH64 == 430` etc.) via unit test, so a future arch addition (riscv64) that diverges fails the test instead of silently regressing. Architectures not in the table raise `OSError(ENOTSUP)` at module import.

Per Critic minor-finding §1.1: until we run a live aarch64 mount the aarch64 path is asserted-equal-to-x86_64 by unit test only. Add ADR Consequence (§6 Negative): "aarch64 syscall path is empirically unvalidated; first production aarch64 host will be canary."

Constants: `FSCONFIG_SET_STRING=1`, `FSCONFIG_CMD_CREATE=6`, `MOVE_MOUNT_F_EMPTY_PATH=0x00000004`, `OVL_MAX_STACK=500`, `OVL_MAX_STACK_GUARD=110` (per Architect A4 Step 9 redline: `OVL_MAX_STACK_GUARD = AUTO_SQUASH_MAX_DEPTH + 10 = 110`, 10% over the squash target, catches squash-pressure drift; rationale recorded inline).

A module-level `def probe_supported() -> bool` that does an empty `fsopen("overlay")` close-on-success. Probe handles three negative cases distinctly (Critic minor C7): `ENOSYS` (kernel too old), `EPERM` (seccomp/cgroup denial), `EBADF` (caller-context misconfig). All three → `False` plus a structured log line naming the errno.

**Verification:**
- `pytest backend/tests/unit_test/test_sandbox/test_execution/test_new_mount_api.py::test_probe_supported_smokes`
- `test_probe_supported_returns_false_on_enosys` (mocked libc returns ENOSYS)
- `test_probe_supported_returns_false_on_eperm` (mocked libc returns EPERM) — Critic C7
- `test_probe_supported_returns_false_on_ebadf` (mocked libc returns EBADF) — Critic C7
- `test_syscall_numbers_stable_across_x86_64_and_aarch64` (asserts equality; FAILS if a contributor adds a new arch with diverging numbers without updating the table)
- Manual: run probe inside the Docker provider container; expect True.

**Acceptance:** `probe_supported()` returns True on Linux ≥ 5.2 with CAP_SYS_ADMIN; False on macOS/Windows host; False on missing libc symbol; False on each of ENOSYS/EPERM/EBADF with errno logged.

### Step 2 — Blocking pre-implementation experiment: resolve `lowerdir+=` priority + implement `mount_overlay(layer_paths)`

**Critical (Critic C1):** This step is split into 2a (experiment) and 2b (implementation). 2b cannot land until 2a resolves the ordering question with a concrete primary-source citation OR a passing A/B/C marker test recorded inline.

#### Step 2a — Resolve `lowerdir+=` priority ordering (BLOCKING)

**Two acceptable resolution paths; pick one before 2b begins.**

**Path 1 — Primary-source citation.** Quote the Linux kernel `Documentation/filesystems/overlayfs.rst` (in the running kernel's source tree on the validation host) for the precedence rule of `lowerdir+`. The relevant kernel doc text we expect to find: each `lowerdir+` append adds a layer **below** the previously added lowers (so the first `lowerdir+` call sets the topmost — highest priority — lower layer; subsequent calls add layers underneath). If this is unambiguous on the validation kernel (Linux 6.x), record the exact quote and the kernel version in §10 Q1 and proceed to 2b with newest-first wiring.

**Path 2 — Empirical A/B/C marker test (this is the safer default; do this regardless even after Path 1 finds a citation).** Implement a self-contained pytest in `backend/tests/live_e2e_test/sandbox/overlay/syscall/test_lowerdir_priority_ordering.py`:

```python
def test_lowerdir_plus_priority_ordering():
    # Three real overlay layers, each containing the same file ./marker.txt
    # with distinct content "A", "B", "C".
    layer_a, layer_b, layer_c = make_three_layers_with_marker({"A", "B", "C"})

    # Mount with lowerdir+= layer_a, then lowerdir+= layer_b, then lowerdir+= layer_c.
    workspace = mount_via_new_api(
        lowers_in_order=[layer_a, layer_b, layer_c],
        upper=fresh_upper, work=fresh_work,
    )
    content = (workspace / "marker.txt").read_text()

    # Whichever marker wins identifies which append-order is top-priority.
    # Document the result inline in §10 Q1 and choose newest-first or oldest-first
    # in Step 3 accordingly.
    assert content in {"A", "B", "C"}  # weak; the REAL assertion comes after first run

    # Once the first run is observed, freeze the expected value (e.g., "A" if
    # first-append wins) and fail any future run that observes a different one.
    assert content == EXPECTED_PRIORITY_WINNER  # filled in after first run
```

**Acceptance for 2a:** The chosen ordering convention is recorded inline in §10 Q1 with a citation (kernel doc quote + kernel version) OR a passing marker test (with the test's discovered winner pinned by name in the test source). Step 3 then uses the matching `manifest.layers` order. **Default working hypothesis (until 2a resolves): first-append wins = top priority** — this matches the overlayfs documentation pattern where comma-separated `lowerdir=a:b:c` has `a` as topmost (highest priority), and `lowerdir+` appends to the end of the list but each append is treated as a new lower-priority addition. The hypothesis is testable; do not commit to it until 2a is run.

#### Step 2b — Implement `mount_overlay(layer_paths=...)`

**File:** `backend/src/sandbox/execution/overlay/kernel_mount.py:35-50` (current `mount_overlay`).

**Change:** Change signature from
```python
def mount_overlay(*, workspace_root: Path, lowerdir: Path, upperdir: Path, workdir: Path, pass_fds: tuple[int, ...]) -> None
```
to
```python
def mount_overlay(*, workspace_root: Path, layer_paths: tuple[Path, ...], upperdir: Path, workdir: Path, pass_fds: tuple[int, ...]) -> None
```
Implementation uses `new_mount_api`:
1. `fd = fsopen(b"overlay", 0)`
2. For each `layer` in `layer_paths` **in the order resolved by 2a** (current working hypothesis: first-append = top priority, so iterate top-priority-first): `fsconfig(fd, FSCONFIG_SET_STRING, b"lowerdir+", layer)`
3. `fsconfig(fd, FSCONFIG_SET_STRING, b"upperdir", upperdir)`
4. `fsconfig(fd, FSCONFIG_SET_STRING, b"workdir", workdir)`
5. `fsconfig(fd, FSCONFIG_CMD_CREATE, NULL)`
6. `mfd = fsmount(fd, 0, 0)`
7. `move_mount(mfd, b"", AT_FDCWD, workspace_root, MOVE_MOUNT_F_EMPTY_PATH)`

Errors surface as `OSError` with errno; pre-mount guard raises `WorkspaceLayerDepthExceeded` if `len(layer_paths) > OVL_MAX_STACK_GUARD = 110`. `validate_mount_inputs` at line 62 changes accordingly: open one fd per layer in `layer_paths`; per-layer `O_DIRECTORY | O_NOFOLLOW`. The `MountInputs.fds` tuple grows.

**Verification:**
- Unit: `test_kernel_mount.py::test_mount_overlay_raises_on_depth_over_guard` (uses a fake new_mount_api; asserts at 111).
- Live (in-sandbox via existing `_harness/native_cases.py`): a new test that does N=1, 10, 50, 100, 110 layer mounts and reads back a marker from each layer.
- Live: the 2a marker test (canonical content-correctness gate).

**Acceptance:** N=110 succeeds; N=111 raises `WorkspaceLayerDepthExceeded(...)`; 2a marker test asserts ordering-correctness.

### Step 3 — `PrepareWorkspaceSnapshotResult` gains `layer_paths`; new prepare flag

**File:** `backend/src/sandbox/layer_stack/stack.py:67-83, 126-179`.

**Change:**
1. Add field `layer_paths: Optional[tuple[str, ...]] = None` to `PrepareWorkspaceSnapshotResult` (per Architect A4 Step 3 redline: `Optional[...] = None`, **not** empty tuple/string sentinel — this is the wire-protocol contract). Default `None` keeps backward compat for plugin callers. `to_dict()` emits the key only when not `None`.
2. Add kwarg to `prepare_workspace_snapshot`: `materialize: bool = True`.
3. New code path when `materialize=False`:
```python
manifest = self._manifest_store.read()
lease = self._leases.acquire(manifest, owner_request_id)   # SAME pin path as materialize=True
# Ordering: per Step 2a resolution, iterate manifest.layers in the priority order
# the kernel uses for lowerdir+=. Working hypothesis: first-append = top-priority,
# so iterate manifest.layers as-is (newest-first per view.py:178/214).
# If 2a resolves the opposite, iterate reversed(manifest.layers) instead.
layer_paths = tuple(self._layer_path(layer).as_posix() for layer in manifest.layers)
if len(layer_paths) > OVL_MAX_STACK_GUARD:
    raise WorkspaceLayerDepthExceeded(
        f"manifest depth {len(layer_paths)} exceeds OVL_MAX_STACK_GUARD={OVL_MAX_STACK_GUARD}"
        f"; squash target is AUTO_SQUASH_MAX_DEPTH={AUTO_SQUASH_MAX_DEPTH}"
    )
return PrepareWorkspaceSnapshotResult(
    lease_id=lease.lease_id,
    manifest_version=manifest.version,
    root_hash=manifest_root_hash(manifest),
    manifest=manifest,
    lowerdir=None,            # explicit None (Architect A4 redline)
    layer_paths=layer_paths,
    timings={"layer_stack.prepare_workspace_snapshot.total_s": monotonic_now() - total_start},
)
```

**Lifetime ownership invariant (Architect A3 + Critic M3) — recorded inline:**

> `LeaseRegistry.acquire(manifest, owner_request_id)` (`lease.py:40-54`) remains the **sole** pinning entry for layer dirs against squash GC. `materialize=False` does **not** bypass this registration: `manifest.layers` flows through `self._refcounts.update(manifest.layers)` (`lease.py:53`) identically in both modes. `pinned_layers()` (`lease.py:64-66`) returns refs sourced from `_refcounts`, which is keyed off `manifest.layers` not off `lowerdir`. Therefore the existing `_unreferenced_layers` filter at `stack.py:344-351` correctly excludes pinned layers for `materialize=False` leases without code change. This invariant is tested by `test_squash_respects_layer_paths_lease.py` (§8.2).

**Verification:**
- Unit: `test_snapshot_lease.py::test_prepare_with_materialize_false_returns_layer_paths`.
- Unit: `test_snapshot_lease.py::test_prepare_with_materialize_false_skips_view_materialize` — assert `MergedView.materialize` is not invoked.
- Unit: `test_snapshot_lease.py::test_prepare_materialize_false_registers_pin_via_lease_registry` — Critic M3: holds the lease, calls `LeaseRegistry.pinned_layers()`, asserts the manifest's layers are returned.
- Unit: `test_snapshot_lease.py::test_prepare_raises_when_depth_exceeds_guard` — depth 111 raises; covers the guard.

**Acceptance:** `result.layer_paths == tuple(layer_path for layer in manifest.layers)` in 2a-resolved order; no transient lowerdir tree is created; `LeaseRegistry.pinned_layers()` returns the expected refs.

### Step 4 — Two dataclasses: `MaterializeLayout` and `LayerPathsLayout`

**File:** `backend/src/sandbox/execution/overlay/layout.py:9-49` (significant restructure per Architect A4 Step 4 redline — **no `__post_init__` branching on a flag**).

**Change:**
```python
from typing import Protocol

class OverlayLayoutProtocol(Protocol):
    workspace_root: str
    writes: str
    kernel_scratch: str
    scratch_root: str

@dataclass(frozen=True, slots=True)
class MaterializeLayout:
    """Layout used by materialize=True callers (plugin, copy-backed, debug)."""
    workspace_root: str
    base_repo: str               # required, must be under scratch_root
    writes: str
    kernel_scratch: str
    scratch_root: str

    def __post_init__(self) -> None:
        # Existing invariants: base_repo non-empty, strictly under scratch_root.
        if not self.base_repo:
            raise ValueError("base_repo must not be empty")
        if not self.base_repo.startswith(self.scratch_root.rstrip("/") + "/"):
            raise ValueError("base_repo must be under scratch_root")
        # ... existing writes/kernel_scratch checks ...

@dataclass(frozen=True, slots=True)
class LayerPathsLayout:
    """Layout used by materialize=False callers (namespace strategy)."""
    workspace_root: str
    layer_paths: tuple[str, ...]   # required, each under layer_storage_root
    layer_storage_root: str
    writes: str
    kernel_scratch: str
    scratch_root: str

    def __post_init__(self) -> None:
        # Disjoint invariant set: no base_repo at all (callers pick one dataclass).
        if not self.layer_paths:
            raise ValueError("layer_paths must not be empty")
        if not self.layer_storage_root:
            raise ValueError("layer_storage_root must not be empty")
        for path in self.layer_paths:
            if not path.startswith(self.layer_storage_root.rstrip("/") + "/"):
                raise ValueError(
                    f"layer path {path!r} must be under layer_storage_root "
                    f"{self.layer_storage_root!r}"
                )
        if len(self.layer_paths) > OVL_MAX_STACK_GUARD:
            raise WorkspaceLayerDepthExceeded(...)
        # ... existing writes/kernel_scratch checks ...

OverlayLayout = MaterializeLayout | LayerPathsLayout   # type alias for ergonomics
```

Callers downstream branch via `isinstance` (a single dispatch point in `namespace.py` and `service.py`); validation is **per-dataclass** with no shared `__post_init__` flag branching.

**Companion change for `PrepareWorkspaceSnapshotResult.to_dict()`:** when `materialize=False` is used, `lowerdir` serializes as JSON `null` (not empty string). Document at `stack.py:75-83` that consumers in `materialize=False` mode MUST read `layer_paths` and MUST NOT depend on `lowerdir`. The daemon JSON-RPC wire format adds the new key; clients running an old `materialize=True` codepath continue to receive `lowerdir` as a real path. The protocol in `contract.py:210-226` updates to `layer_paths: Optional[tuple[str, ...]] = None`.

**Verification:**
- Unit `test_overlay_layout.py::test_layer_paths_layout_rejects_empty_layer_paths`
- Unit `test_overlay_layout.py::test_layer_paths_layout_rejects_path_outside_layer_storage_root`
- Unit `test_overlay_layout.py::test_materialize_layout_unchanged_behavior` (regression — invariants still trip on the same inputs as before).

**Acceptance:** Constructing `LayerPathsLayout(workspace_root="/w", layer_paths=("/storage/layers/L1", "/storage/layers/L2"), layer_storage_root="/storage", writes=..., kernel_scratch=..., scratch_root="/scratch")` succeeds; passing `/etc/passwd` as a layer path raises; constructing `MaterializeLayout` with the same invariants as before still works.

### Step 5 — `execute_command` opts in via flag; builds `LayerPathsLayout` for namespace, `MaterializeLayout` for copy-backed

**File:** `backend/src/sandbox/execution/service.py:69-95`.

**Change:**
```python
use_namespace = (mount_mode == MountMode.NAMESPACE) and new_mount_api_supported()

lease = layer_stack.prepare_workspace_snapshot(
    request_id=request.request_id,
    materialize=not use_namespace,   # False only for namespace path on a supported kernel
)

if use_namespace:
    spec = LayerPathsLayout(
        workspace_root=request.workspace_root,
        layer_paths=lease.layer_paths,        # tuple[str, ...] from Step 3
        layer_storage_root=str(storage_root),
        writes=str(run_dir / "upper"),
        kernel_scratch=str(run_dir / "work"),
        scratch_root=str(scratch_root),
    )
else:
    spec = MaterializeLayout(
        workspace_root=request.workspace_root,
        base_repo=lease.lowerdir,
        writes=str(run_dir / "upper"),
        kernel_scratch=str(run_dir / "work"),
        scratch_root=str(scratch_root),
    )
```

The fallback path (when `mount_mode=COPY_BACKED`, e.g. from `daemon/handler/overlay.py:53-60`, **or** when `new_mount_api_supported()` returns False) flows through `MaterializeLayout` and the existing materialize+`mount(8)` path. Branch is on a single boolean `use_namespace`.

**Verification:**
- Unit: `test_execution_service.py::test_execute_command_uses_layer_paths_layout_for_namespace_mode_on_supported_kernel`.
- Unit: `test_execution_service.py::test_execute_command_falls_back_to_materialize_layout_when_probe_negative` — Critic minor / §10 Q5: also tests `EOS_OVERLAY_FORCE_MATERIALIZE=1` mid-flight flip (env-var read on every call, kill switch verified to take effect on the next lease without daemon restart).
- Unit: `test_execution_service.py::test_execute_command_uses_materialize_layout_for_copy_backed_mode`.

**Acceptance:** namespace path on supported kernel produces no transient lowerdir; namespace path on unsupported kernel falls back; copy-backed path still produces a transient lowerdir; mid-flight `EOS_OVERLAY_FORCE_MATERIALIZE=1` forces the fallback on the next call.

### Step 6 — `namespace.py` payload + `namespace_child.py` parse `layer_paths`

**Files:** `backend/src/sandbox/execution/strategies/namespace.py:62-83`, `backend/src/sandbox/execution/strategies/namespace_child.py:147-163`.

**Change:** Payload schema (when `LayerPathsLayout` is the spec):
```json
{
  "workspace_root": "...",
  "layer_paths": ["...", "..."],   // ordered per Step 2a resolution; replaces "lowerdir"
  "upperdir": "...",
  "workdir": "...",
  ...
}
```
`_NamespaceRequest` dataclass: change `lowerdir: Path` → `layer_paths: tuple[Path, ...]`. `validate_mount_inputs` signature changes accordingly.

Payload builder dispatches on `isinstance(spec, LayerPathsLayout)`. For `MaterializeLayout`, emits the existing `lowerdir` key unchanged.

**Verification:** Unit `test_namespace_child_payload.py::test_payload_round_trip_layer_paths`.

**Acceptance:** child reads `layer_paths`, builds fds, calls `mount_overlay`.

### Step 7 — `_drop_transient_lowerdir` no-op when layer_paths is used

**File:** `backend/src/sandbox/execution/service.py:271-307`.

**Change:** Guard with `if lease.layer_paths is not None: return  # nothing to drop`. The existing path-validation guard (line 290-298) stays for the materialize path.

**Verification:** Unit `test_execution_service.py::test_drop_transient_lowerdir_skipped_for_layer_paths_lease`.

**Acceptance:** No `rmtree` call when `layer_paths` is non-None.

### Step 8 — Daemon RPC wire-format additive change

**Files:**
- `backend/src/sandbox/daemon/workspace_server.py:131-150` — accept `materialize=False`.
- `backend/src/sandbox/daemon/service/layer_stack_client.py:72-81` — pass through.
- `backend/src/sandbox/daemon/handler/workspace.py:68-85` — wire format `to_dict()` already serializes `layer_paths` from step 3.

**Verification:** Integration `test_daemon_workspace_rpc.py::test_prepare_workspace_snapshot_returns_layer_paths_when_materialize_false`.

**Acceptance:** RPC client receives `layer_paths` in response when `materialize=False` requested; receives `lowerdir` (existing) when `materialize=True` (default for plugin path).

### Step 9 — Pre-mount guard `OVL_MAX_STACK_GUARD = 110`

**Rationale (Architect A4 Step 9 redline):** Old value 450 catches nothing — `AUTO_SQUASH_MAX_DEPTH = 100` is the squash target, so a properly-functioning system runs at depth ~100. A guard at 110 = 10% headroom is tight enough to detect squash-pressure drift (e.g., a partially-failed squash leaving depth at 130) while permitting the normal `AUTO_SQUASH_MAX_DEPTH + 4 = 104` peak observed in `task_center_runner/agent/mock/runner.py:863`. The kernel hard limit `OVL_MAX_STACK = 500` is a backstop, not a target.

**File:** `backend/src/sandbox/layer_stack/stack.py:126-179` (already added in Step 3 inline) and `backend/src/sandbox/execution/overlay/layout.py` (LayerPathsLayout `__post_init__`, Step 4).

**Verification:**
- Unit: `test_snapshot_lease.py::test_prepare_raises_when_depth_exceeds_guard` (depth 111 raises).
- Unit: `test_overlay_layout.py::test_layer_paths_layout_rejects_depth_over_guard` (depth 111 raises before mount).
- Metric `layer_stack.depth_guard_violations_total` increments on each rejection.

**Acceptance:** Manifest of depth 111 raises before any fd is opened; `AUTO_SQUASH_MAX_DEPTH + 4 = 104` (the canonical peak from sweevo scenarios) passes; depth 500 also raises (well above guard).

### Step 10 — Daemon-startup probe + capability advertising

**Per Architect A4 Step 10 redline (and §10 Q2 resolution):** Move the probe from per-call lazy to **daemon startup**. If `probe_supported()` is False, the daemon refuses to advertise namespace mode entirely — `execute_command` on namespace mode then forces `MaterializeLayout` (Step 5 already branches on this).

**Files:**
- `backend/src/sandbox/execution/service.py:69-95`
- New: `backend/src/sandbox/execution/overlay/capability.py` (module-level singleton `new_mount_api_supported()` populated once at daemon boot from `new_mount_api.probe_supported()`).
- `backend/src/sandbox/daemon/server.py` (or equivalent daemon-startup entry) — call `capability.initialize()` at boot.

**Additional checks at daemon startup (Critic minor C7 + §10 Q3 resolution):**
- Inspect `resource.getrlimit(resource.RLIMIT_NOFILE)`. If soft limit < 8192, attempt `setrlimit(RLIMIT_NOFILE, (8192, hard))`; if hard < 8192, log a daemon-startup warning that high-concurrency lease workloads may hit EMFILE.
  - 8192 budget justification: At `OVL_MAX_STACK_GUARD = 110` layers × 32 concurrent leases peak observed in `complex_project_build_full` runs × overhead headroom (per-lease 3 non-layer fds + room for unrelated daemon sockets) → ~4-5k working set; 8192 is 1.6-2× headroom.

**Change:** On daemon boot:
1. Call `new_mount_api.probe_supported()`.
2. If False: emit `overlay.new_mount_api.unavailable=1 errno=<ENOSYS|EPERM|EBADF|...> fallback=materialize`; set the capability singleton to False; daemon serves namespace mode via the materialize fallback path forever in this process.
3. Check `RLIMIT_NOFILE`; bump or warn.

**Verification:**
- Unit: `test_capability.py::test_capability_initialize_sets_singleton_true_on_probe_pass`.
- Unit: `test_capability.py::test_capability_initialize_sets_singleton_false_on_probe_fail` (parametrized over ENOSYS/EPERM/EBADF).
- Unit: `test_capability.py::test_daemon_startup_bumps_rlimit_nofile_to_8192`.
- Integration: `test_daemon_startup_capability.py::test_unsupported_kernel_daemon_serves_namespace_via_materialize_fallback`.

**Acceptance:** On Linux ≥ 5.2 with CAP_SYS_ADMIN, daemon boot sets capability=True; on macOS/older kernel host, capability=False and namespace mode silently falls back to materialize+mount(8). `ulimit -n` ≥ 8192 after daemon boot (or warned).

### Step 11 — Cleanup: keep `MergedView.materialize` for other callers

**File:** `backend/src/sandbox/layer_stack/stack.py:153`.

**Change:** When `materialize=True` is requested (plugin, copy-backed, OR namespace-with-unsupported-kernel-fallback), the existing code path is **UNCHANGED**. The `share_inodes=True` invocation stays — squash/checkpoint and plugin paths still rely on it. No deletions in `view.py`.

**Verification:** All existing tests for plugin / squash / copy-backed must pass unmodified.

**Acceptance:** Test diff is purely additive.

---

## 4. Validation runbook — heavy_enabled live_e2e on sqlite

### 4.1 Pre-flight

Confirm in this order — STOP if any fails:

1. Linux host (or Docker provider with `CAP_SYS_ADMIN`):
   ```bash
   uname -r  # expect ≥ 5.2
   ```
2. New mount API available:
   ```bash
   python3 -c "from sandbox.execution.overlay.new_mount_api import probe_supported; print(probe_supported())"
   # expect: True
   ```
3. heavy_enabled is set (already true in `ephemeralos.yaml:47`):
   ```bash
   grep -A1 "live_e2e:" /Users/yifanxu/machine_learning/LoVC/EphemeralOS/ephemeralos.yaml
   # expect: heavy_enabled: true
   ```
4. SQLite database URL exported (the test gate is `bool(database.url)`, dialect-agnostic; the skip message in `test_complex_project_build_full.py:28` saying "PostgreSQL" is stale — `core/stores.py:148` proves sqlite path works):
   ```bash
   export EPHEMERALOS_DATABASE_URL="sqlite:////tmp/eos-validation.db"
   ```
5. **Initialize the sqlite schema (per Critic M4 — without this, the first `task_center_runner` write fails with "no such table").** EphemeralOS does NOT use a separate migration CLI; the schema is created inline via `Base.metadata.create_all(engine)` (`backend/src/task_center_runner/core/stores.py:173`). Run the one-shot bootstrap:
   ```bash
   cd backend && .venv/bin/python -c "
   from sqlalchemy import create_engine
   from task_center_runner.core.models import Base
   engine = create_engine('sqlite:////tmp/eos-validation.db')
   Base.metadata.create_all(engine)
   tables = [t.name for t in Base.metadata.sorted_tables]
   print(f'created {len(tables)} tables:', sorted(tables)[:5], '...')
   "
   # Expected stdout (sample):
   # created 17 tables: ['agent_attempts', 'agent_runs', 'goal_iterations', 'lease_events', 'task_attempts'] ...
   ```
   The exact table count and first-5 sorted names may shift with `task_center_runner.core.models` updates — the assertion in the runbook is **`len(tables) > 0`** and **command exits 0**, not a specific table list.

### 4.2 Heavy scenarios that run when `heavy_enabled=true`

All three are in `backend/src/task_center_runner/tests/sweevo/`:
- `test_complex_project_build_full.py` (registry: `sandbox.complex_project_build`)
- `test_complex_project_build_grep_glob_full.py` (registry: `sandbox.complex_project_build_grep_glob`)
- `test_complex_project_build_shell_edit_lsp_full.py` (registry: `sandbox.complex_project_build_shell_edit_lsp`)

The LSP scenario is the canary for the plugin out-of-scope decision (§2): it MUST still pass, proving `WorkspaceProjection` was not regressed.

### 4.3 Pre-change baseline (capture for delta comparison) — **5-run baseline per Critic M5**

```bash
cd backend
git tag baseline-pre-fsopen $(git rev-parse HEAD)

# Run each heavy scenario FIVE times (Critic M5 — single-sample baseline is undisciplined).
for run in 1 2 3 4 5; do
  .venv/bin/pytest -xvs \
    src/task_center_runner/tests/sweevo/test_complex_project_build_full.py \
    src/task_center_runner/tests/sweevo/test_complex_project_build_grep_glob_full.py \
    src/task_center_runner/tests/sweevo/test_complex_project_build_shell_edit_lsp_full.py \
    2>&1 | tee /tmp/eos-baseline-run-${run}.log
  cp -r .sweevo_runs /tmp/eos-baseline-run-${run}-sweevo_runs
done

# Aggregate the 5 runs: compute median, p95, σ per metric.
.venv/bin/python scripts/analyze_complex_build_perf.py \
  --baseline-aggregate \
  /tmp/eos-baseline-run-*/sweevo_runs/*/perf.json \
  > /tmp/eos-baseline-stats.txt
```

`analyze_complex_build_perf.py --baseline-aggregate` is a small extension to the existing analyzer (also part of this plan; ~30 LoC). It emits per-metric `median, p95, σ, p95_over_median_pct, three_sigma_upper`.

Expected baseline (D1 territory):
- `layer_stack.materialize_p95_s` > 0 (per `_metrics.py:163`)
- `layer_stack.materialize_count` ≈ shell_count per session (`_metrics.py:161`)
- Per-lease tmpfs delta > 0 (the EXDEV copy)
- σ recorded so the regression threshold is data-driven not arbitrary.

### 4.4 Post-change run

After all Step-1..11 changes are merged:

```bash
.venv/bin/pytest -xvs \
  src/task_center_runner/tests/sweevo/test_complex_project_build_full.py \
  src/task_center_runner/tests/sweevo/test_complex_project_build_grep_glob_full.py \
  src/task_center_runner/tests/sweevo/test_complex_project_build_shell_edit_lsp_full.py \
  2>&1 | tee /tmp/eos-post.log

.venv/bin/python scripts/analyze_complex_build_perf.py \
  .sweevo_runs/*/perf.json \
  > /tmp/eos-post-perf.txt

# Compare against the 5-run baseline median+3σ threshold.
.venv/bin/python scripts/analyze_complex_build_perf.py \
  --compare-to /tmp/eos-baseline-stats.txt \
  .sweevo_runs/*/perf.json \
  > /tmp/eos-post-vs-baseline.txt
```

### 4.5 Pass/fail criteria — data-driven thresholds (Critic M5)

For every numeric metric: pass condition is `post_value ≤ max(baseline_median × 1.20, baseline_median + 3σ)`. The +3σ floor handles low-noise metrics where 20% is below the variance band.

| Metric | Baseline | Post | Pass condition |
|---|---|---|---|
| `layer_stack.materialize_p95_s` | > 0 (5-run median) | 0 (or absent) | < baseline_median × 0.05 (improvement gate, not regression gate) |
| `layer_stack.materialize_count` | ≈ shell_count | 0 | == 0 for namespace mode |
| `command_exec.mount_workspace_s` p95 | 5-run median+σ recorded | post p95 | ≤ max(median × 1.20, median + 3σ) |
| `complex_project_build` scenario exit code | 0 | 0 | unchanged |
| `complex_project_build_shell_edit_lsp` exit code | 0 | 0 | unchanged (proves plugin path not regressed) |
| `complex_project_build_grep_glob` exit code | 0 | 0 | unchanged |
| Per-lease upperdir+workdir bytes (from §5 audit harness, **per-lease attribution not whole-tmpfs `df`**) | > 0 (current materialize path) | ≤ 64 KiB | hard threshold per lease (workdir scratch only) |

### 4.6 Spotting a regression

If the post run violates the data-driven threshold for `command_exec.mount_workspace_s`:
1. Check `overlay.new_mount_api.unavailable_total` — if > 0, the feature probe is failing and we fell back to materialize+mount(8). Investigate `probe_supported` failure mode.
2. Check `layer_stack.depth_guard_violations_total` — if > 0, the manifest is exceeding the depth guard; suggests squash isn't running.
3. Compare measured `command_exec.layer_count` distribution against baseline — Bound C regression would show as growing mount cost at the same depth.

If `complex_project_build_shell_edit_lsp` fails: the plugin path is regressed. Verify Step 11 (no changes to materialize=True branch).

---

## 5. Audit harness — Bounds A, B, C

This is the falsifiable proof. **Per Critic M1, all per-lease measurements use per-lease attribution (`du -sb <run_dir>/upper` + `du -sb <run_dir>/work` measured inside each lease's own teardown), NOT whole-tmpfs `df`.** `df` is retained as an aggregate sanity reading at the outer harness level (N=1 sanity) but is never a Bound A acceptance input.

### 5.1 What we measure (per lease, snapshot before and after — per-lease scope only)

| Metric | Source | Unit |
|---|---|---|
| `lower_bytes_delta` | `du -sb <storage_root>/runtime/transient-lowerdirs/<this_lease_id>` (post − pre, **scoped to this lease's transient lowerdir if any**) | bytes |
| `upperdir_bytes` | `du -sb <run_dir>/upper` (this lease's own run_dir) | bytes |
| `workdir_bytes` | `du -sb <run_dir>/work` | bytes |
| `lease_inode_delta` | `find <run_dir> \| wc -l` (post − pre, **scoped to this lease**) | inodes |
| `rss_delta` | `cat /proc/self/status \| grep VmRSS` (post − pre) | bytes |
| `mount_layer_count` | reported by mount helper | int |
| `mount_workspace_s` | existing timing | seconds |
| `materialize_s` | existing timing (`stack.py:162`) — expected 0 in new mode | seconds |
| `negative_lookup_cpu_ms` | NEW: time `find <workspace_root> -name __NONEXISTENT__` inside the mounted namespace, per Bound C | ms |
| `tmpfs_used_aggregate` | `df -B1 /eos-mount-scratch \| awk 'NR==2{print $3}'` — recorded but NOT used as Bound A pass/fail input | bytes |

### 5.2 Acceptance — Bound A (concurrent leases, same manifest)

**Procedure:** Hold one fixed manifest (depth M=10). Run N ∈ {1, 10, 50, 100, 200} prepare→mount→trivial-command→release cycles concurrently. Per-lease metrics are captured per lease (no averaging across leases for acceptance — Critic M2).

**Pass condition (falsifiable, per Critic M2 — max not avg):**
```
For each N:
    max(lower_bytes_delta over all leases) ≤ 4 KiB
    max(upperdir_bytes + workdir_bytes) ≤ 64 KiB
    max(materialize_s)  ≤ 0.005s          (effectively zero)
    sum(upperdir_bytes + workdir_bytes over all N leases) ≤ N × 64 KiB
                                          (sanity sum-check)
```
**On failure**, the harness emits p50/p95/p99 of `lower_bytes_delta` and the lease IDs of the top 3 outliers — so a single regressing lease cannot be hidden by averaging (this directly addresses M2: 800 KiB single regression at N=200 averages to 4 KiB exactly at threshold; `max()` catches it).

**Aggregate sanity (informational only, NOT acceptance):**
- `tmpfs_used_aggregate` delta ≤ `N × 64 KiB + 256 KiB` (256 KiB outer overhead allowance).

**Negative control (still required):** Force `materialize=True` and re-run. Expect `max(lower_bytes_delta) ≥ workspace_bytes`. This proves the harness detects regressions.

**Adversarial harness self-test (new per Critic C6 / Pre-mortem #4):** A test that intentionally regresses exactly ONE lease (out of N=50) by patching that lease's `materialize` flag to True. The harness MUST flag this lease's `lower_bytes_delta` outlier in p99 AND fail the `max(lower_bytes_delta) ≤ 4 KiB` assertion AND name that specific lease ID in the failure output. If the harness false-passes this test, it's not measuring what it claims.

### 5.3 Acceptance — Bound B (manifest depth)

**Procedure:** Fix N=10 concurrent leases. Vary manifest depth M ∈ {1, 10, 50, 100, 110}. (Stop at 110 — `OVL_MAX_STACK_GUARD`; depth 111+ should hit the guard, separately tested in unit.)

**Pass condition (falsifiable):**
```
For each M:
    max(lower_bytes_delta) ≤ 4 KiB                ← independent of M (disk)
    median(mount_workspace_s_at_M) ≤
        median(mount_workspace_s_at_M=1) × (1 + 0.005 × M)
                                          ← linear in M with small slope (mount time)
    mount_layer_count == M                          ← invariant
```

The mount-time linear growth in M is expected (kernel walks the chain per lookup). The slope `0.005` corresponds to ~5ms per added layer — generous; we expect ~1ms based on prior empirics.

### 5.4 Acceptance — Bound C (per-read CPU as M grows) — NEW per Architect A2

**Why this exists:** Overlayfs walks the full layer stack on every negative-dentry lookup. Bound B tests mount time, not steady-state read cost. A workload that does many negative lookups (e.g., `find . -name <not-present>`, ESM resolver probing) pays per-layer CPU on every miss. Without this bound, M=100 might silently regress steady-state shells.

**Procedure:** For each M ∈ {1, 10, 50, 100, 110}: mount a workspace with M layers. Inside the namespace, run a fixed-cost negative-lookup benchmark:

```bash
# Inside the namespace, with M overlay layers underneath:
time find . -name __NONEXISTENT_FILE__ > /dev/null
# Repeat 3 times; take median wall-clock and CPU time.
```

Record `negative_lookup_cpu_ms_at_M` per workspace.

**Pass condition (falsifiable, slope budget):**
```
For each pair (M_lo, M_hi) with M_lo < M_hi:
    let slope_us_per_layer =
        (cpu_ms_at_M_hi - cpu_ms_at_M_lo) * 1000 / (M_hi - M_lo)
    assert slope_us_per_layer ≤ 50   # µs per layer per workspace traversal
```

A 50µs/layer budget at M=110 = 5.5ms additional negative-lookup CPU per `find` — well below human-perceptible — but the slope guarantee is what's load-bearing: if a kernel regression or unfortunate filesystem layout pushes the slope to 500µs/layer, this bound catches it before users see it as 55ms per `find` at M=110.

**Workload reference:** measure on a representative workspace (50k files across `node_modules/` + `src/` + `.git/` — sourced from `complex_project_build` fixture).

**Negative control:** Same procedure with M=1 — recorded as floor; the slope assertion only triggers across pairs.

### 5.5 Where the harness lives

New test files (under existing `backend/tests/live_e2e_test/sandbox/overlay/syscall/`):
- `test_o1_lease_count_bound.py` — the Bound A sweep, drives via `OverlayRuntimeInvoker` (the existing native invoker pattern used by `test_runtime_invoker.py`).
- `test_o1_manifest_depth_bound.py` — the Bound B sweep.
- `test_o1_per_read_cpu_bound.py` — NEW: the Bound C sweep.
- `test_o1_adversarial_harness_self_test.py` — NEW per Critic C6: the adversarial self-test that intentionally regresses one lease and asserts the harness catches it.

These tests gate themselves on `live_e2e_heavy_enabled()` (existing gate from `_live_config.py:12`). Same skip mechanism as the sweevo heavies.

Harness module: `backend/tests/live_e2e_test/sandbox/_harness/lease_resource_probe.py`. Functions:
```python
def snapshot_resources(run_dir: Path) -> ResourceSnapshot: ...        # per-lease scope
def diff(pre: ResourceSnapshot, post: ResourceSnapshot) -> ResourceDelta: ...
def assert_bound_a(deltas_by_lease: dict[str, ResourceDelta]) -> None: ...   # per-lease
def assert_bound_b(deltas_by_depth: dict[int, list[ResourceDelta]]) -> None: ...
def assert_bound_c(cpu_by_depth: dict[int, float]) -> None: ...
```

**Artifact location (§10 Q4 resolved):** Harness writes `.sweevo_runs/<run_id>/o1_audit.json` — one JSON per scenario, schema:
```json
{
  "scenario": "complex_project_build_full",
  "ulimit_nofile": 8192,
  "kernel_version": "6.5.0-...",
  "bound_a": {"N": 200, "max_lower_bytes_delta": 0, "p99_lower_bytes_delta": 0, ...},
  "bound_b": {"M": 110, ...},
  "bound_c": {"slope_us_per_layer": 12.4, ...}
}
```

### 5.6 Wiring into heavy_enabled

The harness tests are gated on the same flag as the sweevo heavies. The `pytest -xvs <sweevo>` command in §4.4 picks them up automatically when run with `-k overlay/syscall` or as part of the full live_e2e suite invocation in CI.

### 5.7 Fail-on-regression

The harness has a hard threshold (`max(lower_bytes_delta) ≤ 4 KiB`). When the assertion fires, the test logs:
```
[O(1) BOUND A VIOLATION] N=100
    max(lower_bytes_delta) = 43.2 MiB   (lease 7e3a91, p99=43.2 MiB, p95=8 KiB)
    materialize_count outliers: lease 7e3a91 -> 1 (expected 0)
    ⇒ regression: materialization path re-engaged for one or more leases
[O(1) BOUND C VIOLATION] slope=420µs/layer (budget 50µs/layer)
    ⇒ regression: per-read CPU growing faster than budget — overlay layer walk regressed
```
This makes the failure mode explicit — Critic will care about this. Per-lease attribution + outlier reporting means a single regressing lease cannot be averaged away (M2).

---

## 6. ADR

**Title:** Replace per-lease workspace materialization with direct overlay new-mount API on command-exec path.

**Status:** Proposed (this RALPLAN-DR, iteration 2).

**Context:**
Command-exec leases require a workspace-root replacement mount. Today, `LayerStack.prepare_workspace_snapshot` calls `MergedView.materialize` to build a transient lowerdir tree by walking every layer and hardlinking files into a scratch tmpfs (`stack.py:148-153`). Because the storage filesystem (overlay2-backed `/tmp`) differs from the scratch tmpfs (`/eos-mount-scratch`, 2 GiB), `os.link` returns EXDEV and falls through to `shutil.copy2` (`view.py:320-325`) — a full byte copy per file per lease. Per-lease disk = workspace_bytes; concurrent leases saturate the 2 GiB tmpfs.

**Decision:**
Add a `materialize=False` mode to `prepare_workspace_snapshot` returning a tuple of layer storage paths (`layer_paths`) ordered per Step 2a resolution. The namespace command-exec strategy passes `layer_paths` directly to a kernel-boundary mount helper that uses the new mount API (`fsopen("overlay")`, repeated `fsconfig(SET_STRING, "lowerdir+", ...)`, `fsmount`, `move_mount`). No transient lowerdir tree is built. Plugin and copy-backed callers keep the existing materialize path unchanged. A daemon-startup probe ensures namespace mode is only advertised on kernels supporting the new mount API; older kernels fall back to materialize transparently.

**Drivers:**
- D1: EXDEV copy across tmpfs is a hard 2 GiB concurrency wall.
- D2: `OVL_MAX_STACK = 500` empirically validated (this session, N-bisected); util-linux `mount(8)` truncates ≥ ~128 silently; new mount API surfaces EINVAL at boundary; `OVL_MAX_STACK_GUARD = 110` catches squash-pressure drift early.
- D3: Command-exec is invoked per agent shell; `materialize_s` is on the hot path and visible in `_metrics.py:148-163`. Bound C (per-read CPU as M grows) ensures the layer-walk cost doesn't silently regress.

**Alternatives considered:**
- B: Refcount-cached materialization keyed by `manifest_version+root_hash`. Invalidated — still pays full EXDEV copy on cold start; cache-vs-squash invalidation is a correctness landmine; doesn't address depth scaling (Bound B).
- C: Userspace FUSE merged view. Invalidated — trades disk-O(N) for memory-O(N) plus a new long-lived process and a FUSE I/O penalty.
- D: Cached-materialize + bind-mount-per-lease. Invalidated on the EphemeralOS workload specifically — every tool-write produces a new `manifest.version`, so intra-session cache hit rate is ~1/`AUTO_SQUASH_MAX_DEPTH` ≈ 1%; cross-session hit rate ≈ 0%. Pays the same full materialize cost ~100 times per session AND adds a cache subsystem. Per-read CPU advantage (1-layer mount) is ~1ms per cold read; materialize cost saved by A is ~seconds. A wins by 3 orders of magnitude. See §1.3 for the full quantitative comparison.
- Plugin-path inclusion. Invalidated for this plan — LSP needs a real on-disk tree for `file://` URIs; mounting overlay per-LSP-session has different lifetime invariants. Follow-up.

**Why chosen:**
Option A is the only path that achieves Bound A (per-lease lowerdir disk cost = O(1) in N concurrent leases), Bound B (cost = O(1) in M manifest depth, up to OVL_MAX_STACK_GUARD = 110), AND keeps Bound C steady-state read cost within budget. It is empirically validated this session for N ∈ {4..500}. The fallback (Step 10) preserves correctness on hosts where the new mount API is unavailable, with a stated sunset target.

**Consequences:**

| Positive | Negative (accepted) |
|---|---|
| Per-lease disk cost = O(1) (just upperdir + small workdir). | New kernel-API surface (ctypes, per-arch syscall numbers). |
| The 2 GiB `/eos-mount-scratch` tmpfs ceiling no longer scales with concurrent lease count. | Adds feature-probe + dual-path complexity (Step 10). **Dual-path debt is explicit and time-bounded — sunset criterion in Follow-up #5.** Mitigation: probe at daemon boot, log once. |
| Removes EXDEV cross-FS pathology entirely. | Layer dirs are held open by kernel mounts; squash eviction must respect lease pinning. **Pinning invariant is recorded inline in Step 3 — `LeaseRegistry.acquire(manifest, ...)` remains sole pinning entry, `manifest.layers` flows unchanged through `_refcounts`** — Pre-mortem #3 covers integration test. |
| OVL_MAX_STACK violations now fail explicitly (`WorkspaceLayerDepthExceeded` at the guard, EINVAL at fsconfig boundary if guard somehow bypassed) rather than silently truncating (mount(8)). | Old `overlay_depth_cap_root_cause.md` memory note is superseded; "16-layer cap" claim was a util-linux argv-size symptom, not a kernel limit. Memory note update tracked in Follow-up #3. |
| Steady-state per-read CPU growth bounded at ≤ 50µs/layer (Bound C). | Per-read CPU does grow linearly in M (overlay layer walk); kept negligible by `OVL_MAX_STACK_GUARD = 110`. |
| Critic minor §1.1: aarch64 path is unit-tested via syscall-number equality but not yet live-validated. | First production aarch64 host is the canary; if syscall numbers diverge we get an explicit unit-test failure on landing, not a silent prod break. |

**Follow-ups (out of scope, tracked):**
1. **Plugin/LSP path on direct overlay mount** — `WorkspaceProjection.acquire` to mount overlay at a stable path per Pyright session lifetime. Different invariants: mount lives across many shell commands; eviction must be plugin-session-scoped. Tracked in `.planning/follow-ups.md`.
2. **Copy-backed fallback retirement** — once new mount API is the dominant path, copy-backed is only for hosts without CAP_SYS_ADMIN; consider removing it from the public contract.
3. **Update `overlay_depth_cap_root_cause.md` memory note** — record that the 16-cap was util-linux argv truncation; the real ceiling is OVL_MAX_STACK = 500; the new guard is 110.
4. **Squash target depth tuning** — `AUTO_SQUASH_MAX_DEPTH = 100` is conservative; the new ceiling allows up to ~400. Consider raising to reduce squash frequency. **Coordination required with the new `OVL_MAX_STACK_GUARD = 110`** — raising squash target above ~110 requires raising the guard.
5. **Sunset criterion for the materialize fallback (Architect A3 — dual-path debt):** Remove the `materialize=True` branch from `execute_command` (Step 5) when the minimum supported kernel of all EphemeralOS deployment targets reaches Linux ≥ 5.11 (the `lowerdir+` floor). Target date is TBD pending a deployment-target survey of EphemeralOS hosts (the survey is a prerequisite to scheduling the cleanup phase). Provisional planning hook: revisit at the next milestone after `overlay.new_mount_api.unavailable_total` stays at 0 across all production hosts for 90 consecutive days. Until then, the dual path is intentional and accepted. Operator runbook stays "if probe is False, materialize is used; metric `overlay.new_mount_api.unavailable_total > 0` is an info signal, not an alert."
6. **aarch64 live validation** — first production aarch64 host runs the full Step 8 integration suite as a canary; if it fails, gate aarch64 to materialize-only via `os.uname().machine` until verified.

---

## 7. Pre-mortem and mitigations (four independent failure families)

### 7.1 Manifest depth exceeds `OVL_MAX_STACK_GUARD = 110`

**Failure mode:** A workload that produces more layers than `AUTO_SQUASH_MAX_DEPTH` can keep up with (e.g., squash temporarily paused, or many small writes during a long-running agent session) reaches depth > 110. The pre-mount guard at Step 3 raises `WorkspaceLayerDepthExceeded` BEFORE any fd is opened. Daemon catches and surfaces a clear error code. Agent sees explicit "depth guard exceeded" message, not opaque mount failure.

**Leading indicator:** `layer_stack.prepare_workspace_snapshot.depth_p99 > 100` for > 5 minutes; `layer_stack.depth_guard_violations_total > 0`.

**Mitigation:** Pre-mount guard at `OVL_MAX_STACK_GUARD = 110` (Step 9) raises a typed exception (`WorkspaceLayerDepthExceeded`) BEFORE any fd is opened. Daemon catches and surfaces a clear error code. Operator dashboard panel for `depth_p99`. Squash plan target depth pinned ≤ 100 (verified at `occ/service.py:23`). Note: this is the **only** failure family in this list rooted in `OVL_MAX_STACK`; eviction (§7.3) is a separate failure family.

**Detection in tests:** Unit `test_overlay_layout.py::test_layer_paths_layout_rejects_depth_over_guard` (depth 111 raises) + live e2e check that `complex_project_build` at peak depth 104 passes (since 104 < 110).

### 7.2 `fsopen` / `fsconfig` ENOSYS on host kernel or seccomp denial

**Failure mode:** A production host running Linux 5.0 or with a restrictive seccomp profile blocks the new mount syscalls. Daemon boot probe returns False; namespace mode automatically falls back to materialize+mount(8) via Step 5's `use_namespace` branch. Performance regresses to today's baseline, correctness preserved.

**Leading indicator:** `overlay.new_mount_api.unavailable_total > 0` at daemon boot; one-time log line `overlay.new_mount_api.unavailable=1 errno=<...> fallback=materialize`.

**Mitigation:** Step 10's feature probe at daemon startup; on False, command-exec sets `materialize=True` and uses the existing `mount(8)` path. Metric drives an operator info signal (NOT alert — this is by design for older-kernel hosts).

**Detection in tests:** Unit test with mocked `probe_supported() → False` (parametrized over ENOSYS / EPERM / EBADF — Critic C7) confirms fallback path. Integration test `test_unsupported_kernel_daemon_serves_namespace_via_materialize_fallback`.

### 7.3 Layer eviction race during active overlay mount

**Failure mode:** Lease A holds a mount referencing layers L1, L2, L3. Squash decides L2 + L3 can be coalesced into a checkpoint and calls `_remove_layers([L2, L3])` (`stack.py:353-360`). The kernel mount stays alive (anonymous reference) — but new lookups via the merged mount might see ESTALE if the kernel resolves through dentries.

**Leading indicator:** Command-exec failures with stderr containing "Stale file handle" or `ESTALE` errno during squash windows.

**Mitigation (per Step 3 lifetime invariant — recorded inline):** The pinning chain is `LeaseRegistry.acquire(manifest, ...)` → `_refcounts.update(manifest.layers)` → `pinned_layers()` returns refs → `_unreferenced_layers` filter at `stack.py:344-351` excludes them from `_remove_layers`. This path is **identical** for `materialize=False` leases (Step 3 explicitly preserves the `_leases.acquire(manifest, ...)` call). Verified by:
- (1) Integration test `test_squash_respects_layer_paths_lease.py`: hold a `materialize=False` lease, trigger squash, confirm pinned layer dirs are not in the removal set; lease's mount still reads after squash completes.
- (2) Unit test `test_prepare_materialize_false_registers_pin_via_lease_registry`: holds the lease, calls `LeaseRegistry.pinned_layers()`, asserts the manifest's layers are returned.

**Detection in tests:** Integration test runs as a live_e2e (squash + mount + namespace are all kernel-touching).

### 7.4 Audit harness false-passes under concurrency (NEW per Critic C6)

**Failure mode:** The audit harness reports "Bound A pass" while production tmpfs fills — because the harness averages or aggregates measurements, hiding per-lease regressions, or because per-lease attribution is broken (a lease's `du -sb` accidentally measures another lease's data). One regressing lease in N=200 stays under the threshold via averaging; ops sees the green check; tmpfs fills in prod; we get paged. The harness is the false-pass surface.

**Leading indicator:** Harness reports pass on Bound A in CI; production `tmpfs_used` climbs over time at the rate of one workspace_bytes per lease, not the expected ~64 KiB.

**Mitigation:**
- Per-lease attribution (already addressed in §5.1 — `du -sb <run_dir>/upper`, NOT `df -B1 /eos-mount-scratch`).
- `max()` not `avg()` in acceptance assertions (§5.2 — Critic M2).
- p99 + top-3-outlier reporting on failure (§5.7) makes a single regressing lease impossible to hide.
- **Adversarial harness self-test `test_o1_adversarial_harness_self_test.py` (§5.5)** — intentionally regress one lease in N=50 by forcing its `materialize=True`; harness MUST fail and name that lease ID. If this self-test ever false-passes, the harness is structurally broken and CI fails.

**Detection in tests:** The adversarial self-test runs alongside the regular Bound A sweep on every CI invocation of `heavy_enabled` live_e2e.

These four scenarios are **independent failure families**: depth-guard (§7.1, design-time bound enforcement), kernel availability (§7.2, runtime capability), eviction race (§7.3, runtime lifetime), and measurement validity (§7.4, observability). Pre-mortem completeness gate (deliberate mode) is met.

---

## 8. Expanded test plan

### 8.1 Unit (in `backend/tests/unit_test/`)

| Test file | What it tests | Acceptance |
|---|---|---|
| `test_sandbox/test_execution/test_new_mount_api.py` | `probe_supported()` returns True/False correctly; syscall number table covers x86_64 + aarch64. | All cases pass; ENOSYS → False; EPERM → False; EBADF → False (Critic C7). |
| `test_sandbox/test_execution/test_kernel_mount.py` | `mount_overlay(layer_paths=...)` raises on depth > 110 guard; honors `MOVE_MOUNT_F_EMPTY_PATH`. | Depth 111 raises `WorkspaceLayerDepthExceeded`; mock-libc trace records correct syscall sequence. |
| `test_sandbox/test_execution/test_overlay_layout.py` | `LayerPathsLayout` accepts paths under `layer_storage_root`; rejects empty `layer_paths`; rejects paths outside the root; rejects depth > 110. `MaterializeLayout` invariants unchanged. | All branches covered; **no `__post_init__` flag branching** — per-dataclass validation only. |
| `test_sandbox/test_layer_stack/test_snapshot_lease.py` (extend existing) | `prepare_workspace_snapshot(materialize=False)` returns `layer_paths` in 2a-resolved order; doesn't invoke `MergedView.materialize`; raises on depth > 110 guard; **registers pin via `LeaseRegistry.pinned_layers()` (Critic M3)**. | All four assertions pass. |
| `test_sandbox/test_execution/test_execution_service.py` (extend) | `execute_command` picks `LayerPathsLayout` for namespace mode on supported kernel; falls back to `MaterializeLayout` when probe negative; `_drop_transient_lowerdir` is skipped for layer_paths leases; **`EOS_OVERLAY_FORCE_MATERIALIZE=1` mid-flight flip takes effect on next call (Critic minor / §10 Q5)**. | Four parametrized cases. |
| `test_sandbox/test_execution/test_namespace_child_payload.py` (new) | Payload schema round-trips `layer_paths`; backwards compat: payloads with only `lowerdir` still parse (until copy-backed retirement). | Round-trip equality. |
| `test_sandbox/test_execution/test_capability.py` (new) | Capability probe gates the materialize-vs-not decision at **daemon startup** (Architect A4 Step 10). Daemon startup bumps `RLIMIT_NOFILE` to ≥ 8192 (Critic C7 / §10 Q3). | Both branches; rlimit check verified. |

### 8.2 Integration (in `backend/tests/integration_test/`)

| Test file | What it tests | Acceptance |
|---|---|---|
| `test_layer_stack_command_exec_seam.py` (new) | End-to-end inside-namespace: prepare → mount → echo > file → release; verify upperdir has the file, no transient lowerdir on disk. | Functional + filesystem assertion. |
| `test_squash_respects_layer_paths_lease.py` (new — Critic M3) | Hold a layer_paths lease; concurrent squash; verify pinned layer dirs survive; lease's mount still reads. **Also asserts `LeaseRegistry.pinned_layers()` returns the expected refs before squash runs.** | No ESTALE; layer dirs still exist post-squash; `pinned_layers()` returns expected. |
| `test_daemon_workspace_rpc.py` (extend) | RPC `prepare_workspace_snapshot(materialize=False)` returns `layer_paths` in JSON; `materialize=True` (default) returns `lowerdir`. | Wire-format compat. |
| `test_plugin_projection_unchanged.py` (new) | `WorkspaceProjection.acquire` still materializes a real lowerdir; Pyright session paths still resolve. | Plugin path untouched. |
| `test_unsupported_kernel_daemon_serves_namespace_via_materialize_fallback.py` (new) | Daemon with `probe_supported() = False` (via env-var injection) accepts namespace mode and serves via materialize fallback. | Functional success; metric `overlay.new_mount_api.unavailable_total > 0`. |

### 8.3 E2E (live_e2e_test, gated on `heavy_enabled`)

| Test file | What it tests | Acceptance |
|---|---|---|
| `live_e2e_test/sandbox/overlay/syscall/test_lowerdir_priority_ordering.py` (NEW — Step 2a, Critic C1 blocker) | A/B/C marker test for `lowerdir+=` priority. | Concrete priority winner asserted; pinned in test source. |
| `live_e2e_test/sandbox/overlay/native/test_namespace_command.py` (extend) | Single-lease namespace command with `materialize=False` mode; verify exit code 0 and upperdir capture. | Existing test passes after migration. |
| `live_e2e_test/sandbox/overlay/syscall/test_o1_lease_count_bound.py` (new) | Bound A sweep — see §5.2. **Per-lease `max()` threshold (Critic M2).** | Hard threshold `max(lower_bytes_delta) ≤ 4 KiB`. |
| `live_e2e_test/sandbox/overlay/syscall/test_o1_manifest_depth_bound.py` (new) | Bound B sweep — see §5.3. | Disk delta flat over M; mount time linear in M with slope ≤ 5ms/layer. |
| `live_e2e_test/sandbox/overlay/syscall/test_o1_per_read_cpu_bound.py` (new — Architect A2 / Bound C) | Bound C sweep — see §5.4. | Slope ≤ 50µs/layer. |
| `live_e2e_test/sandbox/overlay/syscall/test_o1_adversarial_harness_self_test.py` (new — Critic C6 / Pre-mortem #4) | Intentionally regress one lease in N=50; harness MUST flag it. | Harness fails with named lease ID. |
| `live_e2e_test/sandbox/overlay/syscall/test_o1_negative_control.py` (new) | Run with `materialize=True` forced; harness MUST flag a violation. | Confirms harness is not vacuous. |
| Three heavy sweevo scenarios (existing) | Functional regression. | All three exit 0; LSP scenario proves plugin path unchanged. |

### 8.4 Observability tests

| What | Assertion |
|---|---|
| `layer_stack.materialize_count` reported by `_metrics.py:161` | After change, ≈ 0 for namespace mode (any non-zero means we re-engaged materialize). |
| `layer_stack.materialize_s_total` | After change, < 0.5s aggregate per scenario (was multi-seconds). |
| `overlay.new_mount_api.unavailable_total` | Reported at daemon boot. = 0 on supported hosts. |
| `layer_stack.depth_guard_violations_total` | Reported. = 0 in healthy runs; > 0 fires the depth-guard alert. |
| `command_exec.mount_workspace_s` p95 | Stays within `max(baseline_median × 1.20, baseline_median + 3σ)` (data-driven, Critic M5). |
| Daemon-startup `ulimit -n` log line | ≥ 8192 after boot. |

---

## 9. Observability

Metrics added/preserved:

| Metric | Type | Where emitted |
|---|---|---|
| `layer_stack.materialize_s` | gauge (seconds) | Already at `stack.py:162`; expect 0 in new mode. |
| `layer_stack.materialize_count` | counter | Already aggregated at `_metrics.py:161`; expect 0 in new mode. |
| `layer_stack.depth_p99` | gauge | NEW — emit in `prepare_workspace_snapshot` (depth = `len(manifest.layers)`). |
| `layer_stack.depth_guard_violations_total` | counter | NEW — increment when guard raises. |
| `overlay.new_mount_api.unavailable_total` | counter | NEW — set at boot if probe returns False; carries errno label. |
| `command_exec.mount_workspace_s` | gauge | Already at `namespace_child.py:82`. |
| `command_exec.layer_count` | gauge | NEW — `len(layer_paths)` per lease, for correlation with `mount_workspace_s` and Bound C. |
| `command_exec.negative_lookup_cpu_ms` | gauge | NEW (Bound C) — recorded by audit harness; aggregated by `_metrics.py`. |
| `daemon.startup.rlimit_nofile` | gauge | NEW — `RLIMIT_NOFILE` soft limit at boot. |

Dashboards/alerts (operator):
- Panel: `layer_stack.depth_p99` over time. Alert ≥ 100 (since guard fires at 110).
- Panel: `overlay.new_mount_api.unavailable_total` rate. Info signal > 0 at boot (NOT alert — older kernel hosts are a supported configuration until 2027-Q1).
- Panel: `layer_stack.materialize_count` / shell_count ratio. Alert ≥ 0.05 (post-change should be ~0).
- Panel: `command_exec.negative_lookup_cpu_ms` p95 by `command_exec.layer_count` bucket. Alert if slope > 50µs/layer.
- Daemon startup log line includes `rlimit_nofile` for forensics.

---

## 10. Open questions (resolved + remaining)

These are questions where reasonable people could disagree; iter-2 resolves the ones that were structural unknowns and downgrades the rest to engineering notes.

### Resolved in iter 2

**Q1 (was: Manifest order vs `lowerdir+=` priority — RESOLVED-AS-BLOCKING).** Promoted from open question to **Step 2a blocking pre-implementation experiment** (Critic C1). The plan no longer commits to a specific iteration order in Step 3 until 2a returns a concrete answer (primary-source citation OR passing A/B/C marker test). Working hypothesis (testable, not committed): **first-append wins = top priority** — matching the overlayfs comma-separated `lowerdir=a:b:c` pattern where `a` is topmost. Step 3 wires `tuple(manifest.layers)` directly (since `manifest.layers` is already newest-first per `view.py:178/214`); if 2a flips the convention, Step 3 wires `tuple(reversed(manifest.layers))` instead. **Either way, the marker test (`test_lowerdir_priority_ordering.py`) is a permanent regression gate** — a future kernel change to `lowerdir+=` semantics fails CI before reaching prod.

**Q2 (was: Capability probe placement — RESOLVED-AT-STARTUP).** Per Architect A4 Step 10: daemon boot, not per-call. Closed.

**Q3 (was: `MountInputs.fds` count growth — RESOLVED-WITH-RLIMIT-CHECK).** Per Critic C7: daemon startup inspects `RLIMIT_NOFILE`, bumps to ≥ 8192 if soft limit is below, logs a warning otherwise. The 8192 budget covers 110 layers × 32 concurrent leases × overhead headroom. Closed.

**Q4 (was: Test artifact location — RESOLVED).** `.sweevo_runs/<run_id>/o1_audit.json` (schema in §5.5). Closed.

**Q5 (was: Rollback signal — RESOLVED-AS-KILL-SWITCH-WITH-TEST).** `EOS_OVERLAY_FORCE_MATERIALIZE=1` env-var kill switch; mid-flight flip takes effect on the next lease without daemon restart (read per call). Tested by `test_execute_command_falls_back_to_materialize_layout_when_probe_negative` parametrization (Critic C7). Closed.

### Remaining (engineering notes, non-blocking)

- **Squash target depth retuning under new guard.** Follow-up #4 in §6 — if we eventually raise `AUTO_SQUASH_MAX_DEPTH` to reduce squash frequency, we must coordinate with `OVL_MAX_STACK_GUARD = 110`. Tracked.
- **aarch64 production canary.** Follow-up #6 in §6 — first prod aarch64 host validates the syscall-number assertion; until then, the syscall-equality unit test is the only check. Tracked.

---

**Plan length self-check:** RALPLAN-DR + 11-step implementation (with Step 2 split into 2a-blocking-experiment / 2b-implementation) + runbook (with sqlite bootstrap + 5-run baseline) + audit harness (Bounds A, B, C + adversarial self-test) + ADR (with quantitative D-invalidation and dual-path sunset criterion) + pre-mortem (four independent failure families) + test plan + observability + open questions (5 resolved, 2 remaining). Critic-iter1 gates closed: C1 promoted to Step 2a; M1 per-lease attribution adopted; M2 `max()` not `avg()`; M3 pinning invariant recorded inline + integration test; M4 sqlite `create_all` bootstrap added; M5 5-run median+3σ thresholds. Architect-iter1 gates closed: A1 Option D quantitatively invalidated against EphemeralOS workload; A2 Bound C added; A3 dual-path sunset criterion stated; A4 Steps 1/3/4/9/10 redlined; A5 `max()` adopted.
</content>
</invoke>