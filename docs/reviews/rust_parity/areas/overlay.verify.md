# Independent Verification — Overlay (full-FS overlay; only target workspace mounted over layer stacks)

Area: sandbox / `eos-overlay`. Verifier opened every cited file; nothing trusted from the investigator.

Ground truth Python: `/tmp/oldpy/backend/src/sandbox/overlay/*` + `/tmp/oldpy/backend/src/sandbox/occ/overlay_change_conversion.py` + `/tmp/oldpy/backend/src/sandbox/layer_stack/changes.py` + `layer_index.py`.
Rust: `sandbox/crates/eos-overlay/src/*`, with cross-crate anchors in `eos-protocol/src/cas.rs`, `eosd/src/main.rs`, `eos-daemon/src/command.rs`, `eos-isolated/src/session.rs`.

## Invariant verdict table (independent evidence)

| # | Invariant | Status | Python anchor | Rust anchor | Notes |
|---|-----------|--------|---------------|-------------|-------|
| 1 | Full FS presented; only `workspace_root` subtree overlay-mounted, rest passthrough | confirmed_match | `kernel_mount.py:70` `move_mount(mfd, fsencode(workspace_root))`; `namespace_runner.py:237-242` `unshare -Urm` (no `/` remount) | `kernel_mount.rs:126-133` `move_mount(.. &inputs.workspace_root, MOVE_MOUNT_F_EMPTY_PATH)`; passthrough scaffolding out-of-crate (`eosd/main.rs:125-168`, `eos-runner`) | Overlay overmounts ONLY the workspace root on both sides; nothing remounts `/`. Passthrough is correctly outside the `eos-overlay` crate. |
| 2 | Mounts over ordered layer 0..n as lowerdirs (newest-first) + upperdir | confirmed_match | `kernel_mount.py:64-67` loop `fsconfig_string(fsfd,"lowerdir+",layer)` then `upperdir`/`workdir`; module docstring "first lowerdir+ = top priority, newest-first" | `kernel_mount.rs:111-118` `for layer in &inputs.layer_paths { fsconfig_set_string("lowerdir+", layer) }` then `upperdir`/`workdir`; doc same newest-first contract | Exact op order + `"lowerdir+"`/`"upperdir"`/`"workdir"` strings match. |
| 3 | Writes land in upperdir; stacked layers are RO lowerdirs | confirmed_match | `kernel_mount.py:66-67` upperdir/workdir writable; `:164-180` lowerdirs pinned via `/proc/self/fd` RO dir fds | `kernel_mount.rs:115-118,211-238` upperdir/workdir set; lowerdirs passed as fd-paths; `writable_dirs.rs:63-74` allocates `upper`/`work` | Upper/work are real writable paths; lowerdirs are read-only fd-pinned inputs. Match. |
| 4 | Free/teardown releases the head layer (lease) it mounted | confirmed_match | `lifecycle.py:110-137` `release_overlay`→`handle.release()`→`release_lease(lease_id)` + `rmtree(run_dir)` | command session: `command.rs:1079-1081` `remove_dir_all(run_dir)` + `release_lease(workspace.lease_id)`; isolated: `session.rs:941` `release_lease(handle.lease_id)` (phase `release_snapshot`) | Lease release relocated OUT of `eos-overlay` (crate has no layer-stack dep) but DOES fire on teardown in both pipelines. Independently confirmed by opening both sites — not inherited from investigator. |
| 5 | Path-change capture (added/modified/deleted/whiteout) matches Python semantics | confirmed_disparity (narrow) | `capture.py:49-89` os.walk topdown, per-level sort, opaque-before-children, whiteout/symlink/write; `path_change.py:22-44` normalize+hash | `path_change.rs:167-209` read_dir + sort_by file_name, files-then-opaque-then-recurse; markers/whiteout/symlink/write match | Constants `.wh.`/`.wh..wh..opq` match (`layer_index.py:28-29` vs `path_change.rs:18-19`). One real divergence: symlink-to-DIRECTORY (D3) — see below. |
| 6 | Writable-dir / passthrough policy preserved | confirmed_match (char policy unproven) | `writable_dirs.py:13` `/eos/mount`, `:29-52` no fallback; `changes.py:28-41` normalize_layer_path | `writable_dirs.rs:15,41-74` `"/eos/mount"`, mkdir-if-parent, no fallback; `cas.rs:53-78` `LayerPath::parse` (rejects abs/`..`/NUL/empty, `\`→`/`) | Root constant, no-fallback gate, and path normalization match exactly. The mount-path char reject list (D4) cannot be byte-compared — see below. |

## Constant / operator extraction (both sides)

- `OVERLAY_WRITABLE_ROOT`: Python `Path("/eos/mount")` (`writable_dirs.py:13`) == Rust `"/eos/mount"` (`writable_dirs.rs:15`). MATCH.
- `WHITEOUT_PREFIX` = `.wh.`; `OPAQUE_MARKER` = `.wh..wh..opq` (Python `layer_index.py:28-29`; Rust `path_change.rs:18-19`). MATCH.
- Whiteout detection: `S_ISCHR(mode) && rdev==0`, OR `is_file && size==0 && getxattr("user.overlay.whiteout")` (Python `capture.py:105-118`; Rust `path_change.rs:329-339`). MATCH.
- Opaque xattr: `trusted.overlay.opaque == b"y"` OR `user.overlay.opaque == b"y"` (Python `capture.py:121-129`; Rust `path_change.rs:341-344`). MATCH.
- Whiteout-marker name gate: `startswith(".wh.") && name != OPAQUE_MARKER && len > len(".wh.")` (Python `capture.py:91-98`; Rust `path_change.rs:311-313`). MATCH.
- Mount-path reject chars (Rust only): `[",", ":", "\\", "\n", "\r", "\t", "\0"]` (`kernel_mount.rs:289`). Python counterpart `policy.validate_overlay_path_text` (`kernel_mount.py:156`) lives in `command_exec_policy.py`, NOT materialized in `/tmp/oldpy` nor present in the live tree — cannot byte-compare.

## Disparity adjudication

### D1 — Remount path uses single lazy umount, no peel-loop. VERDICT: confirmed (medium)
- Rust remount: `eosd/main.rs:504` `unmount_overlay(workspace_root, true)` → `kernel_mount.rs:149-157` single `unmount(.., MNT_DETACH)`, no loop. Then `mount_overlay` + `std::mem::forget(mount)` (`main.rs:508`) so Drop never peels.
- Python remount-equivalent (`namespace_entrypoint.py:134`) calls `umount(workspace_root)` with default `lazy=False` → `kernel_mount.py:97-119` **64x peel-loop**, plain umount per iteration.
- Decisive: even Python's `lazy=True` branch is STILL the 64x loop with per-iteration lazy fallback (`kernel_mount.py:108-116`), NOT a single detach. So Rust's single `MNT_DETACH` diverges regardless of which Python caller it maps to. A single `MNT_DETACH` peels only the TOP mount — the exact reason Python loops — so repeated remounts across runtime-bundle upgrades can accumulate stacked mounts underneath the workspace root.
- NOTE the Rust `Drop` path (`kernel_mount.rs:69-87`) DOES have the 64x peel-loop (no lazy), matching Python's default `umount()` used by teardown callers (`pipeline.py:302/363`, `overlay_child.py:74`). The divergence is isolated to the remount caller, which uses `unmount_overlay(.., true)` instead of the loop.

### D2 — Write conversion re-reads whole file into memory and discards precomputed hash. VERDICT: adjusted (medium → low)
- Rust `into_layer_change` (`path_change.rs:118-124`) for `Write`: `std::fs::read(content_path)` → `LayerChange::Write { path, content }`; `final_hash` is dropped.
- Python `overlay_path_changes_to_occ_changes` (`overlay_change_conversion.py:38-45`): threads `content_path` + `precomputed_hash` into `build_overlay_write_change` WITHOUT reading bytes here ("OCC stager copies the file in-kernel and reuses the precomputed hash").
- Correctness: equivalent. Rust `eos_protocol::LayerChange::Write` is byte-carrying by design (`cas.rs:226`), and `update_digest` hashes `content` raw (`cas.rs:284`); Python defers the read to `prepare_layer_change` (`changes.py:117`) and sha256's the same bytes. Same input → same digest. So this is forced by the protocol shape, not a capture-semantics bug. Severity dropped to low.
- BUT two real costs to record (not hidden behind "equivalent"): (1) Rust reads each written file TWICE — once in `content_change`→`content_hash` (`path_change.rs:280,346-355`) and again in `into_layer_change` (`path_change.rs:122`); the computed `final_hash` exists only to pass its own non-empty validation gate, then is discarded. (2) Rust holds full file content in a `Vec<u8>` in memory where Python holds only a path. Real perf/memory regression on large/many writes.
- Out-of-scope note: Python's `prepare_layer_change` re-reads and RAISES on `content hash mismatch` (`changes.py:119-120`); Rust has no equivalent TOCTOU guard, but that guard lives in layer_stack/occ — outside this area — so not failed here.

### D3 — Symlink-to-existing-directory classified differently. VERDICT: confirmed (low)
- Verified empirically: Python `os.walk(topdown=True, followlinks=False)` places a symlink-to-DIR in `dirnames` (run: `dirnames=['linkdir','realdir'] filenames=['file.txt']`). In `capture.py:80-88` the dirnames loop only checks `_has_overlay_opaque_xattr` and otherwise SKIPS — so a symlink-to-dir is NOT emitted.
- Rust `walk_upperdir` (`path_change.rs:181-188`) splits via `entry.file_type()` which does NOT follow symlinks → symlink-to-dir `is_dir()==false` → lands in `files` → `capture_file_entry` `symlink_metadata().is_symlink()` true → emitted as `Symlink` (`path_change.rs:255-260`).
- Net: symlink-to-DIR → Python drops silently, Rust emits a `Symlink` change. Symlink-to-FILE matches on both (file loop / files bucket → Symlink). Narrow edge case; Rust arguably more correct but it is a parity divergence. Low.

### D4 — Path-char validation hardcoded list vs Python policy. VERDICT: confirmed but unprovable (low)
- Rust `reject_forbidden_chars` (`kernel_mount.rs:287-297`) hardcodes `[",", ":", "\\", "\n", "\r", "\t", "\0"]`. `,` and `:` are the overlayfs option/lowerdir separators, so rejecting them is semantically sound for mount-path safety.
- Python `validate_overlay_path_text` (`command_exec_policy.py`, called at `kernel_mount.py:156`) is NOT in `/tmp/oldpy` (only `overlay/` was materialized) and NOT in the live tree. Cannot byte-compare the exact char set. Unproven (investigator already flagged it unverifiable).

## New findings

1. Rust `Drop` (`kernel_mount.rs:69-87`) correctly mirrors Python's DEFAULT teardown `umount()` 64x peel-loop (plain umount, no lazy) used by `pipeline.py`/`overlay_child.py`/`namespace_entrypoint.py`. The D1 divergence is confined to the explicit remount caller; the steady-state teardown is faithful.
2. Rust adds an empty-`layer_paths` rejection (`kernel_mount.rs:194-198`) that Python's `validate_mount_inputs` lacks. Defensive improvement, not a regression; one line.
3. D2 double-read: `content_change` already pays a full `std::fs::read` to compute the (later-discarded) hash, so the `into_layer_change` read is the second pass over the same bytes — worth a single-read refactor that threads the already-read content.

## Overall verdict

High fidelity. Invariants 1, 2, 3, 4 and the constant/operator surface (lowerdir+/upperdir/workdir ordering, `/eos/mount`, `.wh.`/`.wh..wh..opq`, whiteout/opaque detection, `LayerPath` normalization) are faithful and independently confirmed — including the lease-release relocation (invariant 4), which I opened myself rather than trusting the investigator. The only genuine behavioral divergences are: D1 (remount uses single `MNT_DETACH` vs Python's 64x peel-loop — medium, can leak stacked mounts on repeated upgrades), D3 (symlink-to-dir emitted vs dropped — low edge case), and D2 (write conversion reads bytes twice and holds them in memory vs Python's deferred path-threaded read — correctness-equivalent, low, perf cost). D4 path-char policy is unprovable from available sources. No FALSE MATCH detected: every "match" the investigator claimed holds under independent inspection, and invariant 4's relocated lease release is real.
