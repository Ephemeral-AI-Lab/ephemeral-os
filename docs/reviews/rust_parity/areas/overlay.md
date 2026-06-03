# Rust Parity Audit — Overlay (full-FS overlay, only target workspace mounted over layer stacks)

Domain: sandbox. Ground truth = Python under `/tmp/oldpy/backend/src/sandbox/overlay/`.
Rust under audit = `sandbox/crates/eos-overlay/src/`.

## Ground truth

The overlay turns a versioned layer log into a temporary working tree for one
operation/session. Key anchors:

- **Mount mechanics** — `kernel_mount.py:49-75 mount_overlay`: raw new-mount API
  `fsopen("overlay")` → one `fsconfig_string("lowerdir+", layer)` per layer in
  newest-first order → `fsconfig_string("upperdir"/"workdir")` →
  `fsconfig_create` → `fsmount` → `move_mount(mfd, workspace_root)`. The mount
  target is `workspace_root`, never `/` (docs `overlay.html` §3.2 "Does overlay
  replace the whole filesystem? No.").
- **Input validation** — `kernel_mount.py:137-198 validate_mount_inputs`: every
  path goes through `policy.validate_overlay_path_text` (line 156); workspace
  root must not be a symlink and must be a dir (158-161); each lowerdir likewise
  (164-168); upper/work must not be symlinks, must be dirs, are `mkdir(parents,
  exist_ok)` (171-176). Lowerdirs are pinned and passed as `/proc/self/fd/N`
  paths (180); upper/work and the mount target stay real paths.
- **Layer ordering** — `kernel_mount.py:6-8,58` + docs: `lowerdir+` is added in
  manifest natural order, newest-first = highest priority. `OVL_MAX_STACK = 500`
  is the only depth ceiling (`mount_syscalls.py:61`); runtime keeps no separate
  cap.
- **Upper/work allocation** — `writable_dirs.py:13 OVERLAY_WRITABLE_ROOT =
  Path("/eos/mount")`; `:29-43 overlay_writable_root` (mkdir only if parent is a
  dir, then require is_dir, NO fallback); `:46-52 allocate_overlay_writable_dirs`
  creates `run_dir/upper` and `run_dir/work`.
- **Upperdir capture** — `capture.py:19-89 walk_upperdir`: `os.walk(topdown=True,
  followlinks=False)` with per-level `dirnames.sort()`/`filenames.sort()`.
  Per node: files first (opaque marker `.wh..wh..opq` → opaque_dir;
  `.wh.<name>` → delete; char-dev/xattr whiteout → delete; symlink → symlink;
  regular file → write), then opaque-xattr dirs at this level, then descend.
  "opaque_dir before children" relies on topdown. Whiteout convention:
  `S_ISCHR && st_rdev == 0`, OR zero-size file with `user.overlay.whiteout`
  xattr (`:105-118`). Opaque xattr = `trusted.overlay.opaque == b"y"` or
  `user.overlay.opaque == b"y"` (`:121-129`).
- **Path-change model** — `path_change.py:15-44 OverlayPathChange` + `content_hash`
  (sha256 of bytes, or of the readlink target for symlinks). `__post_init__`
  normalizes via `normalize_layer_path(allow_root = kind=="opaque_dir")` and
  enforces per-kind field presence.
- **Overlay→OCC conversion** — `occ/overlay_change_conversion.py:19-72`: write
  threads `content_path`+`precomputed_hash` into `build_overlay_write_change`
  *without reading bytes* ("the OCC stager copies the file in-kernel and reuses
  the precomputed hash"); symlink reads `os.readlink(content_path)`.
- **Lifecycle (relocated in Rust)** — `lifecycle.py:27-103 acquire` (lease +
  upper/work + error cleanup), `:106-107 capture_changes`, `:110-137
  release_overlay` (idempotent handle release + FileNotFoundError-tolerant
  rmtree + `overlay_workspace.cleaned`/`cleanup_failed` audit emit + scratch
  accounting). `handle.py OverlayHandle` (lock-guarded idempotent release).
- **Teardown** — `kernel_mount.py:78-121 umount`: loop up to 64×, peel **every**
  stacked mount at `workspace_root`, fall back to `umount -l` only on non-zero
  return when `lazy=True`, optional `raise_on_failure`. Docstring: a single
  umount only peels the top mount; persistent daemon overlays may be remounted
  across runtime-bundle upgrades.

## Rust mapping

`eos-overlay` is deliberately a **pure leaf** (no `eos-occ`/`eos-layerstack`
dep): mount + capture + writable-dir allocation only.

- `kernel_mount.rs:106-137 mount_overlay` — raw API via `rustix::mount`.
- `kernel_mount.rs:69-87 Drop for OverlayMount` + `:149-157 unmount_overlay` —
  teardown.
- `kernel_mount.rs:191-247 ValidatedMountInputs::open` — input validation +
  `/proc/self/fd/N` lowerdir pinning.
- `kernel_mount.rs:287-297 reject_forbidden_chars` — path char policy.
- `writable_dirs.rs:15,41-74` — `OVERLAY_WRITABLE_ROOT`, `overlay_writable_root`,
  `allocate_overlay_writable_dirs`.
- `path_change.rs:155-209 capture_upperdir`/`walk_upperdir` — upperdir walk.
- `path_change.rs:62-141 OverlayPathChange::new`/`into_layer_change` — model +
  OCC conversion.
- The **lifecycle / lease / audit** layer moved OUT of the crate into
  `eos-daemon/src/command.rs` (`:856-917` allocate/build workspace, `:1079-1081`
  cleanup+release, `:1306` capture) and `eos-isolated/src/session.rs` (enter =
  acquire+scratch+mount; exit = unmount+release+discard upperdir). The namespace
  passthrough (mount only `workspace_root`, rest of FS inherited) is realized in
  `eosd ns-runner` / `eos-runner` (PORT of `namespace_entrypoint.py` +
  `setns_exec.py`), NOT in `eos-overlay`.

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | Full FS presented; only `workspace_root` subtree overlay-mounted, rest passthrough | partial | medium | `kernel_mount.py:70` `move_mount(...workspace_root)`; docs `overlay.html` §3.2 | `kernel_mount.rs:126-133` move_mount targets `workspace_root`; passthrough in `eosd/src/main.rs:125-166` ns-runner (out of crate) | `eos-overlay` only proves the move_mount target; the "rest is passthrough" namespace property lives in ns-runner — verified-out-of-crate, see Open Questions |
| 2 | Overlay mounts over ordered layer 0..n as lowerdirs + an upperdir | match | none | `kernel_mount.py:64-67` lowerdir+ loop then upperdir/workdir | `kernel_mount.rs:111-118` same order | Newest-first iteration preserved; `OverlayHandle.layer_paths` is the leased stack |
| 3 | Writes land in upperdir; stacked layers are read-only lowerdirs | match | none | `kernel_mount.py:65-67`; docs §3.1 | `kernel_mount.rs:112-118`; `writable_dirs.rs:63-74` | upper/work allocated writable; lowerdirs fd-pinned read-only inputs |
| 4 | Free/teardown releases the head layer (lease) it mounted | partial | medium | `lifecycle.py:110-137` release_overlay + lease release; `kernel_mount.py:97-121` umount peel-loop | lease release `command.rs:1080-1081`, `session.rs:391`; unmount `kernel_mount.rs:69-87,149-157` | Lease release present (relocated). BUT unmount diverges: Rust never combines peel-loop + lazy; remount path uses single lazy umount (D1) |
| 5 | Path-change capture (added/modified/deleted/whiteout) matches Python semantics | divergent | medium | `capture.py:49-89`; `overlay_change_conversion.py:32-46` | `path_change.rs:167-209` walk; `:114-140` into_layer_change | Walk order + whiteout/opaque/marker detection MATCH. Two divergences: write conversion re-reads bytes + drops precomputed hash (D2); symlink-to-existing-dir classified differently (D3) |
| 6 | Writable-dir / passthrough policy preserved | partial | low | `writable_dirs.py:13,29-52`; `kernel_mount.py:156` validate policy | `writable_dirs.rs:15,41-74`; `kernel_mount.rs:287-297` | Writable-root + upper/work logic match. Path-char policy is a hardcoded list vs Python's `validate_overlay_path_text` (D4, unverifiable — module not materialized) |

## Disparities

### D1 — Remount/teardown uses a single lazy umount, no peel-loop (invariant 4) — medium

- Python `umount()` (`kernel_mount.py:97-121`) loops up to **64×** calling plain
  `umount` to peel *every* stacked mount at `workspace_root`, only falling back
  to `umount -l` when a normal umount returns non-zero AND `lazy=True`. The
  docstring (`:86-96`) is explicit: "A single `umount` only peels the top mount;
  loop until the path is no longer a mountpoint ... Persistent daemon overlays
  may be remounted across runtime-bundle upgrades or interrupted tests."
- Rust splits this into two non-equivalent paths:
  - `Drop for OverlayMount` (`kernel_mount.rs:69-87`) loops 64× but uses only
    `UnmountFlags::empty()` (no lazy fallback at all), and returns early on the
    first error.
  - `unmount_overlay(workspace_root, lazy)` (`kernel_mount.rs:149-157`) is a
    **single** umount (`MNT_DETACH` when lazy) with **no peel-loop**.
- The remount caller `eosd/src/main.rs:504` calls
  `unmount_overlay(workspace_root, true)` then immediately re-mounts
  (`:506`). If `workspace_root` carried a stack of >1 overlay mount (the exact
  "remounted across upgrades / interrupted tests" case the Python docstring
  names), Rust detaches only the top mount and then mounts over a still-stacked,
  lazily-detached point. Neither Rust path replicates Python's loop-until-clean +
  conditional-lazy contract.
- Why it matters: stale lower mounts can remain visible / leak under the new
  overlay after a runtime-bundle upgrade, diverging from the documented
  "backing checkout is visible to raw provider setup commands again" guarantee.
- Suggested fix: give `unmount_overlay` a peel-loop (mirror Python: loop up to 64,
  plain umount, fall back to `MNT_DETACH` on failure when lazy, stop when no
  longer a mountpoint via the existing `is_mountpoint`). Have the remount caller
  use that loop.

### D2 — Write conversion re-reads the whole file and discards the precomputed hash (invariant 5) — medium

- Python `overlay_change_conversion.py:32-46`: a `write` change threads
  `content_path` + `final_hash` (both computed at capture) into
  `build_overlay_write_change(... precomputed_hash=...)` and does **not** read
  bytes — "the OCC stager copies the file in-kernel and reuses the precomputed
  hash."
- Rust `path_change.rs:118-124 into_layer_change`: `OverlayPathChangeKind::Write
  => std::fs::read(content_path)` into `LayerChange::Write { content: Vec<u8> }`.
  And `content_change` (`:271-282`) already computed `final_hash` at capture via
  `content_hash` (a full read), so the file is read **twice** and the precomputed
  hash is thrown away.
- Why it matters: per-file O(filesize) buffering in process memory plus a double
  read, against a Python path explicitly engineered to avoid both (the whole
  "changed-data cost, not repo size" / O(1) memory invariant in `space-model.html`
  §9 leans on not buffering content here). This is a silently dropped
  implementation detail with a real perf/memory dynamic.
- Caveat: this depends on the Rust `LayerChange::Write` ABI carrying inline
  `content: Vec<u8>` (cas.rs:226). If OCC staging genuinely needs inline bytes in
  the Rust port, the divergence is in the protocol shape, not just this function —
  flag to OCC area. Either way the discarded precomputed hash + double read is
  wasteful and should be fixed.
- Suggested fix: carry `content_path` + `final_hash` through to the OCC stager
  (in-kernel copy / reflink) instead of `fs::read` here; at minimum reuse the
  already-computed hash rather than re-reading.

### D3 — Symlink-to-existing-directory classified differently (invariant 5) — low

- Python `_walk_upperdir` splits entries via `os.walk`, whose `dirnames` is
  populated by `entry.is_dir()` which **follows symlinks**. A symlink whose
  target resolves to an existing directory lands in `dirnames`, is never emitted
  (the dir loop emits only on opaque xattr, `capture.py:80-88`), and is not
  recursed (`followlinks=False`). Python therefore **silently drops** such a
  symlink from the write set.
- Rust `walk_upperdir` (`path_change.rs:181-188`) splits via
  `entry.file_type()` (from `read_dir`), which does **not** follow symlinks, so a
  symlink-to-dir lands in `files` and is emitted as `Symlink`
  (`:255-260`).
- Symlink-to-file and broken symlinks match on both sides (both land in the file
  branch). Only symlink-to-existing-dir diverges.
- Why it matters: Rust captures a symlink that Python drops. This is arguably a
  Python latent bug that Rust "fixed," but per the audit mandate a behavior
  change is itself a finding. Low severity (rare upperdir shape).
- Suggested fix: decide intended semantics. If parity is required, branch on
  `metadata` (following symlinks) before the file/dir split to match Python's
  drop; if the Rust behavior is the desired correction, document it as an
  intentional divergence.

### D4 — Path-char validation is a hardcoded list, not the Python policy (invariant 6) — low/unverifiable

- Python validates every mount path through
  `policy.validate_overlay_path_text` (`kernel_mount.py:156`,
  `DEFAULT_COMMAND_EXEC_POLICY`). Rust uses a hardcoded reject list
  `[",", ":", "\\", "\n", "\r", "\t", "\0"]` (`kernel_mount.rs:287-297`).
- The `_shared/command_exec_policy.py` module was **not materialized** in
  `/tmp/oldpy` (only the overlay package is present), so the real policy's rule
  set cannot be compared. The Rust list is reasonable for overlayfs option syntax
  (`,` and `:` are overlayfs option separators) but may be narrower or broader
  than the Python policy.
- Suggested fix: confirm `validate_overlay_path_text`'s rules against the live
  Python and reconcile (e.g. absolute-path / length / control-char rules) if they
  differ.

## Extra findings

- **EF1 — run_dir cleanup lost audit + error semantics (lost observability).**
  Python `release_overlay` (`lifecycle.py:110-137`) does a
  `FileNotFoundError`-tolerant `shutil.rmtree(onerror=...)`, tracks
  `scratch_removed`, and emits `overlay_workspace.cleaned` /
  `overlay_workspace.cleanup_failed` audit events with `cleanup_ms` /
  `cleanup_failure_kind`. The relocated Rust cleanup is a bare
  `let _ = std::fs::remove_dir_all(&self.workspace.run_dir)` (`command.rs:1079`)
  — no scratch accounting, no cleanup-failed event. Leaked/undeleted scratch is
  now silent. Confirm whether this audit signal was intentionally dropped or
  belongs elsewhere.

- **EF2 — `acquire` error-boundary cleanup relocated.** Python `acquire`
  releases the lease AND `rmtree(run_dir)` on any post-`acquire_snapshot`
  exception (`lifecycle.py:100-103`). Rust spreads this across `command.rs:786`
  (`release_lease` on `prepare_command_session` failure) and
  `session.rs:391` (rollback releases lease). The run-dir rmtree on the error
  path is less obviously covered than in Python's single try/except — worth a
  targeted check that a failed allocate/mount does not leak `run_dir`.

- **EF3 — empty `layer_paths` pre-rejected (benign).** Rust
  `ValidatedMountInputs::open` returns `InvalidMountInput` when `layer_paths`
  is empty (`kernel_mount.rs:194-198`); Python would proceed and fail at the
  kernel. Behavior-equivalent (both fail), earlier/clearer in Rust. Not a bug.

- **EF4 — `overlay_writable_root` create idiom (benign).** Python uses
  `root.mkdir()` guarded by `not root.exists() and root.parent.is_dir()`
  (`writable_dirs.py:37-38`); Rust uses `create_dir_all` guarded by
  `root.parent().is_some_and(is_dir)` (`writable_dirs.rs:44-46`). Functionally
  equivalent (single-level create under an existing parent); the is_dir gate +
  no-fallback contract is preserved.

- **EF5 — `OVL_MAX_STACK = 500` / squash threshold absent in `eos-overlay`.**
  The Python depth ceiling and the "auto-squash before mounting deep manifests"
  logic (docs `space-model.html` §9.3, `pipeline.py:221-230`) live outside
  `eos-overlay` on both sides; not a crate-local concern but flagged so the
  manifest-depth guard is checked in the LayerStack/pipeline area.

## Open questions

1. Where exactly does the Rust port realize "rest of FS is passthrough; mount
   only `workspace_root`" — confirm `eos-runner`/`eosd ns-runner` mounts overlay
   onto `workspace_root` inside a fresh user+mount namespace and inherits the rest
   (PORT of `namespace_entrypoint.py`). Out of scope for `eos-overlay` but gates
   invariant 1.
2. Does the Rust OCC stager require inline `content: Vec<u8>` on
   `LayerChange::Write`, or can it consume a `content_path` + precomputed hash
   like Python? This decides whether D2 is a local fix or a protocol-shape
   divergence to escalate to the OCC area.
3. What are the actual rules of `validate_overlay_path_text`? Needed to resolve
   D4 (module not in the materialized tree).
4. Was the `overlay_workspace.cleaned` / `cleanup_failed` audit emission
   (EF1) intentionally dropped in the Rust relocation, or relocated to an
   audit sink not yet wired in `command.rs`?
