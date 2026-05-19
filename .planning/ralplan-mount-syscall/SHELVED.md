# SHELVED — 2026-05-20

This ralplan is **shelved**. Original framing assumed `kernel_mount.py` issues multi-lowerdir overlay mounts at depths up to 32. **It doesn't.**

## Why shelved

Tracing the actual call chain:

1. `LayerStack.prepare_workspace_snapshot` calls `view.py:195-219` `materialize()`, which **hardlinks** all 32 manifest layers into a **single merged directory**.
2. `kernel_mount.mount_overlay(lowerdir: Path, ...)` takes that single Path as the lowerdir.
3. The actual kernel call is `mount -t overlay overlay -o "lowerdir=<one_dir>,upperdir=<one_dir>,workdir=<one_dir>" <target>` — a **depth-1 overlay** from the kernel's perspective.

So `AUTO_SQUASH_MAX_DEPTH = 32` (`occ/service.py:23`) is a **manifest** depth, not an overlay-mount depth. The kernel sees 1 lowerdir, always. `mount(8)`'s util-linux 2.41 multi-lowerdir cliff (project memory `overlay_depth_cap_root_cause.md`) is irrelevant to this codebase.

## What the original driver was, and what survives

- Original driver #1: "unlock 199+ overlay-layer regime via mount(2)". **Invalidated** — runtime never constructs >1 lowerdir per mount.
- Original driver #2: "unblock Docker provider Step 0 preflight". **Moot** — Docker provider preflight no longer needs depth ladder; depth-1 mount is the only regime to verify.
- Surviving residual value: replacing `subprocess.run(["mount", ...])` with `mount(2)` ctypes saves one fork+exec per command exec. Sub-millisecond perf nit, not architectural. Not worth a deliberate-mode plan today.

## Future revival conditions

Revive this plan ONLY if one of:
- Layer-stack design changes to construct multi-lowerdir overlays (skip the `materialize()` hardlink farm) for some workload.
- Per-exec fork overhead becomes a measured bottleneck.
- A third reason emerges.

Until then, the artifacts here (`PLAN_v1.md`, `PLAN_v2.md`, `ARCHITECT_REVIEW_v1.md`, `CRITIC_REVIEW_v1.md`) remain as **archived consensus output** in case the design direction changes.
