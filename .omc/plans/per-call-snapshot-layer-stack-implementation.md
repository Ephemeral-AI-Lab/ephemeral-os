# Per-Call Snapshot Layer Stack — Implementation Plan

## Status

- **Type:** replacement plan for `backend/src/sandbox/overlay/`.
- **Sources of truth:** `.omc/plans/per-call-snapshot-layer-stack.md` (design),
  `.omc/plans/per-call-snapshot-layer-stack-diagrams.md` (diagrams),
  `stack_overlay/` (validated prototype: layer manager, mount syscall, OCC,
  squash, lease budget).
- **Cutover model:** in-tree replacement of `backend/src/sandbox/overlay/`. No
  feature flag preserving the live-root bind path. Roll back via git, not via
  runtime toggle. (See ADR-3.)
- **Open ADRs in this plan:** placement of `LayerManager` (ADR-1), base-bytes
  read strategy (ADR-2), cutover strategy (ADR-3), in-namespace OCC apply
  (ADR-4).
- **Module purity:** `sandbox/overlay/*` and `stack_overlay/*` are
  **git-unaware**. They handle namespaces, mounts, layers, merged views,
  upperdir capture, lease/budget. **All gitignore evaluation and merge
  decisions live in `sandbox/occ/*`.** The runtime binary sequences both
  modules; the modules themselves do not import each other or `git`.
- **Mode policy: dropped (2026-05-04 revision).** The earlier four-mode design
  (`read_only` / `gated` / `strict_stale` / `exclusive`) is gone. Routing is
  automatic per the design doc §4d table: per-path classification by `(change
  type, gitignored?)`. The `read_only` perf case becomes an *empty-upperdir
  fast path* (no upperdir → no OCC round trip). The `exclusive` build case is
  covered by gitignored last-writer-wins (`dist/`, `build/`, `target/`,
  `.next/`, `node_modules/`, `**/*.cache/` etc.). Staleness is always emitted
  as informational telemetry; never a rejection signal. There is no caller-
  declared mode parameter on `OverlayClient.run` / `OverlayClient.shell`.

---

## Core Invariant — "Snapshot Identity"

> The manifest captured at lease acquire time is the *one* object used to
> (1) render the `lowerdir` argument passed to `mount(2)`,
> (2) resolve `base_bytes` during upperdir capture, and
> (3) compute `content_hash(base_text)` for `WriteChange.base_hash` /
> `DeleteChange.base_hash` in `overlay_changes_to_changeset`.
>
> `base_hash` is therefore the hash at **overlay checkout time** (immediately
> before the shell command starts). It is not the hash at capture-walk time, and
> it is not the hash of whatever the active manifest looks like at commit-decision
> time. The active-manifest read happens only inside the OCC gate, on the
> host-side merged view.

This is the user-named correction. It is the discriminator between
"correct" and "looks right but races on long shells." Every code change in
this plan must trace back to one of those three uses of the snapshot manifest.

Concretely, a long shell that started at manifest version `M` and ran while
unrelated commits advanced the active manifest to `M+k` will commit using
`base_hash` rooted at `M`. The OCC gate compares those `base_hash` values
against the *active* manifest and decides per-path accept/reject from there.
The shell's own `base_bytes` reads are kernel-pinned to `M` because the
overlayfs `lowerdir` was pinned at `mount(2)` time.

---

## Goals

1. Replace today's `bind live_root → lowerdir` runtime with an append-only
   stack of overlay directories whose `lowerdir` is the colon-joined manifest
   captured at mount time.
2. **Reuse `backend/src/sandbox/occ/` unchanged.** `ChangesetOrchestrator`
   (`occ/orchestrator.py`) already implements the §4d routing:
   `.git` writes silently dropped, `DirectChange` (Symlink/OpaqueDir/Binary)
   always direct-merged, gitignored `GatedChange` direct-merged, tracked
   `GatedChange` gated through OCC. We do not write a new classifier; we feed
   the existing orchestrator the right inputs. `overlay_changes_to_changeset`
   still produces `WriteChange` / `EditChange` / `DeleteChange` /
   `BinaryChange` / `SymlinkChange` / `OpaqueDirChange`. Only the read base
   and the write target move from `live_root` to the layer stack, and the
   gitignore decision is sourced from the call's snapshot view rather than
   the live workspace.
3. Honor the snapshot identity invariant end-to-end through the public
   `OverlayClient.run` / `OverlayClient.shell` API and through the in-process
   `shell_pipeline`.
4. Land squash/lease/budget machinery from `stack_overlay/` into production —
   `LayerManager`, manifest CAS publish, refcounted lease, depth-100 squash,
   per-path policy classification (design §4d), and lease-budget caps.

## Non-goals

- Replacing OCC gate semantics. `FileChangeApplier`, `OCCGatedCoordinator`,
  `DirectMergeCoordinator`, and `ChangesetOrchestrator` stay as-is; the only
  edit is rerouting their content reads/writes through layer storage instead
  of `live_root` (see Step 4).
- Cross-session durability of the layer stack. Session boundaries collapse
  the stack back to a single tree (deferred decision, see Open Questions).
- Distributed/multi-host consistency.
- Read-set tracking for derived-output staleness. The runtime cannot see
  hidden reads, so it does not pretend to gate them; staleness is recorded
  as informational warnings only and consumers handle hidden read deps
  app-side (re-verify / re-trigger).
- Caller-declared shell modes. There is no `mode` parameter; routing is
  automatic by `(change type, gitignored?)` per design §4d.
- Workspace-wide write mutex. The earlier `exclusive` mode is gone;
  build-output durability is handled by gitignored last-writer-wins.

---

## ADR-1 — Where `LayerManager` Lives

**Decision:** `LayerManager` runs **inside the sandbox container**, not on the
host.

**Drivers:**
- Layer dirs live on the sandbox filesystem (`/dev/shm/eos-layers/<sid>/L*`),
  the same filesystem visible to the `unshare -Urm` namespace where the overlay
  is mounted. A host-side manager would have to push every commit across the
  sandbox boundary and would also have to deal with `manifest.json` atomicity
  on a remote filesystem it cannot directly `os.replace` into.
- The capture runtime (`runtime/cli.py`) already runs inside the sandbox and
  needs to read base bytes from the snapshot manifest. Sandbox-side
  `LayerManager` lets the capture path read layers locally.
- The host already speaks to the sandbox through the runtime dispatch
  (`call_runtime_server`). Adding a sandbox-side `layer.*` op family is the
  same pattern as `overlay.run` / `shell` / `occ.apply_changeset`.

**Alternatives considered:**
- **Host-side LayerManager.** Rejected: requires bind-mounting `session_root`
  into the unshare namespace from the host (cross-process, Daytona doesn't
  expose host-side mount control), and forces every commit to round-trip
  the host process which adds latency and a serialization point.
- **Split (host-side manifest, sandbox-side layer dirs).** Rejected: two
  loci of truth for "current manifest version"; the manifest CAS publish
  invariant becomes a distributed problem.

**Consequences:**
- `LayerManager` is a sandbox-side singleton keyed by `(sandbox_id,
  workspace_root)`. The OverlayClient acquires/releases leases through new
  runtime ops: `layer.snapshot`, `layer.acquire`, `layer.release`,
  `layer.commit`. (See Step 6.)
- `OverlayLease` carries the manifest layer list and version across the
  wire so the gate can decide stale/conflict on the host.
- A host-side merged read view (for `Read` API and OCC CAS reads) is also
  sandbox-side: the OCC `ContentManager` already runs in-sandbox, and its
  read base just gets repointed at a sandbox-mounted merged view.

---

## ADR-2 — Base-Bytes Read Strategy

**Decision:** Walk the snapshot manifest in Python (whiteout-aware), as
`stack_overlay/layer_manager.py:LayerManager.read_text` already does.

**Drivers:**
- `_read_base_bytes` is called once per upper change, after the command has
  finished. The cost dimension is per-change, not per-byte of workspace; for
  realistic workloads (E10/E14 prototype: 1–5k changes), Python-level
  layer-walk is well below a millisecond per change.
- Avoids a second overlay mount per call. A read-only overlay-of-the-snapshot
  works but doubles `mount(2)` syscalls, doubles cleanup paths, and complicates
  cleanup-on-error.
- Symmetric with the squash worker and the prototype, so the same merged-view
  walker is reused for capture, OCC CAS reads, and squash.
- Whiteout/opaque-dir handling is already implemented in capture.py; we
  preserve those semantics by walking layer dirs in newest→oldest order and
  stopping at the first hit (or whiteout).

**Alternative:** mount a read-only "base-only" overlay at `/tmp/eos-shell-ns/base`
with `lowerdir=<manifest>` and no upperdir, and read base bytes from there.
Faster amortized read for huge bases but slower setup, harder to clean up on
error, and the syscall mount path can hit the same util-linux issue if anyone
goes through `mount(8)`. Keep as a fallback only if E3 cold-read measurements
show layer-walk as the bottleneck.

**Consequences:**
- A small `merged_view.py` module exposes `read_bytes(rel: str, manifest:
  Manifest, session_root: Path) -> tuple[bytes | None, bool]` returning
  `(content_or_None, exists)` where `exists=False` after a whiteout.
- This module is the *single* implementation of "resolve a path through the
  manifest." Capture, OCC reads, and squash all call it. No second walker.

---

## ADR-3 — Cutover Strategy

**Decision:** In-tree replacement of `backend/src/sandbox/overlay/`. No
runtime feature flag. Roll back via `git revert` if needed.

**Drivers:**
- The design doc phrases dual-path as a recommendation, not a requirement.
- Dual-path code adds carrying cost: every consumer of `OverlayLease`,
  `OverlayRunOutcome`, and `setup_mounts` would need to handle both shapes.
- The capture path is small and the prototype has already validated the
  semantics; we are not introducing risk we cannot test up-front.

**Consequences:**
- One PR, one cutover. Shell pipeline + write API + read API all flip together.
- `runtime/mounts.py` rewrite is destructive: the old `_NS_LOWER` bind step
  is removed in the same change as the new `lowerdir=manifest` mount.
- Rollback strategy is explicit: `git revert` the merge commit. Document
  this in the PR description.

**Out of scope:** parallel-running both designs, runtime mode toggle, telemetry
that compares old vs new at runtime.

---

## ADR-4 — OCC Apply Runs Inside the Unshare Namespace

**Decision:** the runtime binary that runs inside `unshare -Urm` performs
**both** overlay capture **and** OCC apply, in sequence, before exiting. The
host-side `shell_pipeline` reads the verdict envelope; it does not call
`gate.apply` itself.

**Drivers:**
- **Overlay/stack_overlay must be git-unaware** (this plan's module-purity
  contract). The only place where the snapshot view is alive — and where
  `git check-ignore` can run against the snapshot's `.gitignore` files — is
  inside the unshare child while the overlay is still mounted. Once that
  process exits, the merged view is gone.
- Existing OCC code (`occ/orchestrator.py`, `occ/content/gitignore_oracle.py`,
  `occ/gated/*`, `occ/direct/*`) already runs sandbox-side and shells out to
  `git check-ignore -C <workspace_root>`. With the runtime binary owning the
  apply, `workspace_root` is the call's bound merged view, which IS the
  snapshot. No oracle changes needed.
- Removes wire-payload bloat. With OCC apply inside the namespace, the
  result envelope is just `(exit_code, ChangesetResult-projection,
  new_manifest_version, warnings)`. The full `upper_changes` payload doesn't
  cross the boundary back to the host.
- The layer is already published before the runtime exits. Fewer steps in
  the failure-recovery path: if the runtime crashes mid-OCC, the staging
  dir (or no-op manifest) is the *only* state to clean up.

**Alternatives considered:**
- **Stamp gitignore in-namespace, apply OCC host-side.** Considered (rev 3).
  Rejected because the runtime CLI then has to either import OCC code or
  shell to git, which leaks gitignore knowledge into the overlay-side CLI.
  Also doubles the wire payload (upper_changes shipped back, then result
  shipped in).
- **Re-implement gitignore host-side using `pathspec`.** Subtle differences
  from `git check-ignore` (negation across nested files, precedence rules)
  risk classification drift from `OCCClient.apply_changeset`'s existing
  behavior. Avoid until forced.
- **Reread `.gitignore` content from snapshot manifest via
  `merged_view.read_bytes` host-side and hand bytes to `git check-ignore` via
  `--no-index`.** Possible but complex; staying with the existing in-place
  oracle in-namespace is simpler.

**Consequences:**
- The runtime binary lives at `sandbox/runtime/inproc_cli.py` (relocated
  from `sandbox/overlay/runtime/cli.py`). It imports overlay capture and
  OCC apply; neither imports the other.
- A new module `sandbox/occ/runtime/apply.py` exposes
  `apply_inproc(upper_changes, merged_view_path, layers) ->
  ChangesetResult` — constructs the existing orchestrator with
  layer-backed `ContentManager` and the existing live-rooted
  `GitignoreOracle(merged_view_path)`.
- `OverlayCaptureEngine` returns a `RuntimeResultEnvelope` containing the
  changeset projection. `shell_pipeline` becomes a thin projector.
- `OverlayClient.shell` retains its surface; it just reads the result of
  the in-namespace apply rather than triggering a host-side apply.

---

## Components Summary

This plan ships seven net-new modules across `backend/src/sandbox/`,
plus rewrites of existing files. The OCC tree (`backend/src/sandbox/occ/`)
is untouched **except** for (a) the `ContentManager` content base
(layer-backed via Step 4), and (b) one new thin shim
`sandbox/occ/runtime/apply.py` that wires the existing orchestrator with the
right dependencies inside the unshare namespace (Step 7a, ADR-4).
`ChangesetOrchestrator`, `OCCGatedCoordinator`, `DirectMergeCoordinator`,
`FileChangeApplier`, `GitignoreOracle`, and the changeset types are reused
verbatim.

### Net-new modules

| Module | Purpose | Source basis |
|---|---|---|
| `sandbox/overlay/layer_manager.py` | manifest, leases, commit, squash, GC | `stack_overlay/layer_manager.py` |
| `sandbox/overlay/merged_view.py` | whiteout-aware layer-walk read | `stack_overlay/layer_manager.py:read_text` (extracted) |
| `sandbox/overlay/policies.py` | staleness telemetry helper, lease-budget evaluator | `stack_overlay/policies.py` (narrow subset; no classifier, no modes) |
| `sandbox/overlay/manifest.py` | `Manifest`, `Lease`, `LayerChange`, on-disk serialization, fsck | `stack_overlay/models.py` (subset) |
| `sandbox/overlay/handlers/layer.py` | sandbox-side runtime ops `layer.snapshot` / `acquire` / `release` / `commit` / `materialize_session` | new |
| `sandbox/occ/runtime/apply.py` | in-namespace entry point: builds the existing `ChangesetOrchestrator` with `GitignoreOracle(merged_view_path)` and layer-backed `ContentManager`; returns `ChangesetResult` | new (thin shim over existing OCC) |
| `sandbox/runtime/inproc_cli.py` | runtime binary that runs inside `unshare -Urm`: sequences overlay-mount → user-cmd → overlay-capture → `occ.runtime.apply.apply_inproc` → write result envelope | relocated from `sandbox/overlay/runtime/cli.py` |

### Rewrites

| Module | What changes | What stays |
|---|---|---|
| `sandbox/overlay/runtime/mounts.py` | `setup_mounts` signature replaced; `bind live_root → _NS_LOWER` removed; mount via direct `mount(2)` syscall (`stack_overlay/mounts.py:mount_overlay_syscall`); **no git** | tmpfs upper/work setup; namespace dirs |
| `sandbox/overlay/runtime/capture.py` | `_read_base_bytes` resolves through `merged_view.read_bytes` against the snapshot manifest, *not* a single bind mount; **no git, no OCC** — pure capture only | `walk_upperdir`, whiteout/opaque/symlink classification, xattr reads |
| `sandbox/overlay/types.py` | `OverlayLease` carries `Manifest` (layer tuple + version); `OverlayRunOutcome` carries `snapshot_version`, `active_manifest_version_at_capture`, and the embedded `ChangesetResult` projection from in-namespace OCC apply | `UpperChange`, `OverlayCapture`, `OverlayError` |
| `sandbox/overlay/wire.py` | encodes/decodes `OverlayLease` manifest fields, the `ChangesetResult` projection, and the staleness telemetry fields | other field mappings |
| `sandbox/overlay/engine/capture_engine.py` | `_new_lease` calls `layer.snapshot` + `layer.acquire`; `_execute_with_lease` releases on cleanup; `_run_overlay_direct_runtime` and `_run_overlay` pass the snapshot manifest path/handle into runtime args; **empty-upperdir fast path** is detected by the runtime CLI and reported as "no layer published"; engine returns the runtime's `ChangesetResult` envelope | semaphore, timing, error envelopes; lowerdir guard removed (Step 11) |
| `sandbox/overlay/engine/runtime_execution.py` | `_runtime_args` adds the manifest reference | upload/exec wrappers |
| `sandbox/overlay/handlers/run.py` / `handlers/shell.py` | unchanged in surface; pick up new `OverlayCaptureEngine` constructor that wires `LayerManager` | dispatch |
| `sandbox/overlay/client.py` | unchanged surface — no new parameters; `OverlayClient.run` / `shell` keep their existing signature | error mapping |
| `sandbox/runtime/pipelines.py` | `shell_pipeline` becomes a **thin projector**: read the runtime's `ChangesetResult` envelope and project to `ShellResult`. **Does not call `gate.apply`** — OCC ran in-namespace. Appends informational staleness warnings | `_shell_result_from_changeset` projection |
| `sandbox/occ/content/manager.py` | reads/writes go through `LayerManager` (read = manifest walk, write = layer dir) instead of touching `live_root` directly. Constructed inside the runtime binary with the call's `LayerManager` instance | hashing API, `read_text`/`write_text` surface |

### Untouched

`occ/changeset/builders.py`, `occ/orchestrator.py`, `occ/gated/*`,
`occ/direct/*`, `occ/changeset/types.py`, `occ/handlers/*`, `occ/wire.py` —
these consume and produce the same types regardless of where bytes live.
The whole point of routing `ContentManager` through `LayerManager` is to keep
the gate untouched.

---

## Implementation Steps

Each step has a verify line. The plan executes in order; do not start
the next step until the previous step's verify passes.

### Step 0 — Promote prototype invariants into a target module layout

Create empty files with the final import paths so subsequent steps land in
their final home:

- `backend/src/sandbox/overlay/manifest.py`
- `backend/src/sandbox/overlay/layer_manager.py`
- `backend/src/sandbox/overlay/merged_view.py`
- `backend/src/sandbox/overlay/policies.py`
- `backend/src/sandbox/overlay/handlers/layer.py`

Verify: `make build` is green; `ruff check` is green; module imports resolve
even though bodies are stubs.

### Step 1 — Land `manifest.py` and `layer_manager.py` from the prototype

Port `stack_overlay/models.py` → `sandbox/overlay/manifest.py` (rename
`Manifest`, `Lease`, `LayerChange`; keep `WriteChange`/`DeleteChange` *only*
inside `stack_overlay/` since production OCC types live in
`sandbox/occ/changeset/types.py` and we will not duplicate them).

Port `stack_overlay/layer_manager.py` → `sandbox/overlay/layer_manager.py` with
two production-grade changes:

1. **Async-safe locking.** Replace `threading.RLock` with `asyncio.Lock` for
   mutating ops and `threading.RLock` for read-only snapshot if any sync
   call site remains. Sandbox runtime is single-threaded asyncio; the lock
   guards against interleaved `commit`/`squash`/`gc` coroutines.
2. **Background squash.** The prototype squashes synchronously inside
   `commit()`. Production version creates an `asyncio.Task` for squash and
   uses `EMERGENCY_DEPTH=95` foreground squash + `BACKPRESSURE` queue exactly
   as design §6 specifies. Squash plan/build/publish phases stay in the
   prototype's order.

Verify: copy `stack_overlay/tests/test_layer_manager.py` into
`backend/tests/sandbox/overlay/test_layer_manager.py`, adjusted for the new
module path. `uv run --project backend python -m pytest backend/tests/sandbox/overlay/test_layer_manager.py -q`
passes 100%.

### Step 2 — Land `merged_view.py`

Extract `_apply_layer_to_tree` and `read_text` from
`stack_overlay/layer_manager.py` into a side-effect-free module that takes a
`session_root: Path` and a `Manifest` and exposes:

```python
def read_bytes(rel: str, manifest: Manifest, session_root: Path) -> tuple[bytes | None, bool]: ...
def list_dir(rel: str, manifest: Manifest, session_root: Path) -> list[DirEntry]: ...
def materialize(dest: Path, manifest: Manifest, session_root: Path) -> None: ...
```

`read_bytes` returns `(None, False)` for whiteouts and `(None, False)` for
absent paths; otherwise returns `(content_bytes, True)`. The walker is
newest→oldest with first-hit semantics.

`materialize` is reused by squash and by session-end "collapse to single tree"
cleanup.

Verify: unit tests for whiteout, symlink, opaque-dir, and shadowed-file cases
in `backend/tests/sandbox/overlay/test_merged_view.py`. Acceptance: 100% of
the prototype's `test_layer_manager.py::test_read_*` cases reproduce
identical results.

### Step 3 — Land `policies.py` (no classifier, no modes)

The §4d routing is **already implemented** by
`backend/src/sandbox/occ/orchestrator.py:ChangesetOrchestrator.apply`. We do
not duplicate it. `policies.py` ships only what OCC does *not* already cover.

Drop from the prototype:
- `ShellMode` enum, `ShellCommitGate`, `classify_shell_mode` (mode policy gone)
- `StalenessPolicy.strict_stale` rejection branch (type stays as a thresholds
  carrier for warnings)
- Any `PathPolicy.classify_change` work — duplicates the orchestrator

Keep / port from the prototype:
1. `staleness_warnings(manifest_lag, shell_age_s, thresholds) ->
   tuple[str, ...]` — pure function, returns informational tags only. Never
   used as a rejection signal.

2. `LeaseBudget.evaluate` — keeps the four caps from design §9
   (`MAX_LEASE_AGE`, `MAX_PINNED_LAYER_BYTES_PER_SESSION`,
   `MAX_PINNED_OLD_MANIFESTS`, `MAX_TOTAL_PINNED_BYTES_GLOBAL`). Returns
   decisions only; enforcement is owned by `LayerManager` (Step 9). The
   "new shell call would publish a layer" predicate is computed by the
   pipeline post-capture (empty upperdir → no layer pressure).

No `SnapshotGitignoreOracle` is needed. ADR-4 has OCC apply running inside
the namespace, so the existing `GitignoreOracle(merged_view_path)` works
unchanged — it shells out to `git check-ignore -C <merged_view>` against the
snapshot view (kernel-pinned `lowerdir`). Overlay/stack_overlay code never
imports git or OCC.

Verify: threshold-boundary tests for `staleness_warnings`;
`LeaseBudget.evaluate` matrix test against the four pathological cases in
design §E12.

### Step 4 — Reroute `ContentManager` through `LayerManager`

This is the highest-risk change. Approach:

1. Add a thin façade `LayerBackedFs` to `sandbox/occ/content/` that exposes
   the subset `ContentManager` actually uses (`read_text(path)`,
   `write_text(path, content)`, `delete(path)`, `exists(path)`,
   `read_bytes(path)`, `write_bytes(path, content)`).
2. `LayerBackedFs.read_*` calls `merged_view.read_bytes(path, layers.snapshot(),
   session_root)`. **The active manifest is read fresh on every call** —
   this is the OCC gate's "current view," and it is the *active* side of the
   snapshot-vs-active comparison the gate performs.
3. **Layer-creation rate: one layer per accepted changeset, not per write.**
   `LayerBackedFs.write_*` / `delete` do not commit on each call; instead they
   buffer accepted bytes into a per-changeset staging dict held on the
   `ChangesetOrchestrator` request. After OCC finishes evaluating *all*
   changes for the request, the orchestrator calls
   `LayerManager.commit(staged_changes)` exactly once. Result: one shell call
   ≈ one layer; one API write/edit call ≈ one layer. This keeps within the
   `SQUASH_TRIGGER=80` budget for typical agent traffic.

   Interaction with `FileChangeApplier`'s per-path lock: `FileChangeApplier`
   continues to serialize *per path within a request* (anchor evaluation, hash
   CAS). The per-changeset layer commit happens *after* the orchestrator
   collects every applier's verdict, so the per-path lock and the per-commit
   `LayerManager._lock` never contend.

   Optional 50ms cross-request coalescing (design §7) is a follow-up; not in
   this plan. Do not add it before E5's append-rate measurement on the new
   pipeline.

4. `ContentManager.__init__(workspace_root)` becomes
   `ContentManager(layers: LayerManager)`. Existing call sites that pass
   `workspace_root` get a small constructor wrapper
   `ContentManager.for_workspace(workspace_root)` that resolves the
   `LayerManager` singleton and builds the layer-backed fs. Audited
   instantiation sites (2026-05-04 grep): `runtime/pipelines.py:77` and
   `occ/handlers/apply_changeset.py:19` — both are sandbox-side, confirming
   ADR-1's foundation.
5. Apply `policies.DirectMergePolicy` inside `DirectMergeCoordinator` to
   restrict `BinaryChange` / `SymlinkChange` / `OpaqueDirChange` writes to the
   allowed prefixes (`.cache/`, `node_modules/.cache/`, `tmp/`, `build/`,
   `dist/`). Out-of-prefix direct changes return `FileStatus.ABORTED_OVERLAP`
   with reason `direct_merge_requires_allowed_prefix`. This is the design
   doc's §4d explicit-scoping requirement.

Verify: existing OCC tests (`backend/tests/sandbox/occ/**`) must pass after
the constructor wiring. Failures here indicate a contract drift to
investigate, not to paper over — the changeset/builders/orchestrator surface
is intentionally untouched, so any test break maps to either the
`LayerBackedFs` façade contract or the `DirectMergePolicy` prefix rule.

### Step 5 — Rewrite `runtime/mounts.py` to syscall-mount the manifest

Replace `setup_mounts(live_root, upper_size_mb)` with:

```python
def setup_mounts(
    *, manifest: Manifest, session_root: str, run_dir: str, upper_size_mb: int
) -> MountedView: ...
```

Implementation:
1. `unshare -Urm` is already done by the parent invocation (cli wrapper).
2. `mount tmpfs` for upper/work in `run_dir/{u,w}` (was `_NS_TMP`).
3. Build `OverlayMountSpec` from the manifest using
   `stack_overlay/mounts.py:build_mount_spec` (relative lowerdir, `userxattr`).
4. `mount("overlay", merged, "overlay", 0, opts)` via direct `mount(2)`
   syscall (Python `ctypes`). **Never use `subprocess.run(["mount", ...])`**;
   util-linux 2.41 fails at depth ≥ 10 (E1 result).
5. `mount --bind merged → live_root` so callers see the merged view at
   the same path as before.

Returns a `MountedView(merged: str, upper: str, work: str)` so the CLI
can pass `upper` to `walk_upperdir`.

Verify: a unit test that builds a 1-, 5-, 50-, 100-layer manifest and asserts
the syscall mount succeeds inside `unshare -Urm` (skips on environments where
namespaces aren't available; CI runs on Linux). Re-runs `stack_overlay`
mount probe `python -m stack_overlay.experiments mount-probe --depth 100
--iterations 1000` with 0 failures, p99 < 5ms.

### Step 6 — Rewrite `runtime/capture.py` to honor snapshot identity

Replace `_read_base_bytes(rel)` with
`_read_base_bytes(rel, manifest, session_root)` that calls
`merged_view.read_bytes`. Remove the `_NS_LOWER` constant and the
`_safe_lower_path` helper that depended on it.

`build_upper_change(entry, *, manifest, session_root)` is the one place
where `base_bytes` is read. By construction, the `manifest` argument is the
snapshot manifest. The kernel-pinned overlay also guarantees `_NS_LOWER`
contents are stable for the call's lifetime, but we no longer rely on
`_NS_LOWER` — we rely on the *manifest itself*, which is the design's
single source of truth.

`runtime/capture.py` is git-free. It walks the upperdir, reads `base_bytes`
through `merged_view`, and returns `tuple[UpperChange, ...]`. Nothing more.
The runtime binary (Step 7a) hands the tuple to `occ.runtime.apply.apply_inproc`
which does the gitignore evaluation and the orchestrator dispatch.

Verify: two complementary invariant tests. Both must pass; they catch
different regression shapes.

**(a) `test_base_hash_resolves_through_snapshot_manifest`** — positional
resolution. Capture upperdir from a 50-layer manifest where each layer
rewrote `foo.txt`. Assert `change.base_bytes` equals
`merged_view.read_bytes('foo.txt', snapshot_manifest, session_root)` (newest
layer's content for that file), and `content_hash(change.base_bytes)`
matches what the host computes from the same manifest. Catches "capture
reads the wrong layer."

**(b) `test_base_hash_is_pinned_to_snapshot_not_active_manifest`** —
time-drift resolution, the user's correction directly. Acquire a lease on
manifest `M` containing `foo.txt` with content X. Start a no-op shell command
(`sleep 0.5`). While it runs, an **unrelated** OCC commit (different path,
e.g. `bar.txt`) advances the active manifest to `M+1`. Shell finishes;
capture writes a new `foo.txt` with content Y. Assert
`WriteChange.base_hash == content_hash(X)` (snapshot view at `M`), **not**
`content_hash(read_bytes('foo.txt', M+1, session_root))`. With the snapshot
identity invariant, both happen to equal `content_hash(X)` because `foo.txt`
is unchanged between `M` and `M+1`; to make the test sharp, also include a
variant where the unrelated commit *does* touch `foo.txt` — assert
`base_hash` still resolves to the snapshot's content X, not the active
manifest's newer content. Catches "capture reads the active manifest at
capture-walk time instead of the snapshot manifest" — the exact failure
mode the user's correction names.

Together (a)+(b) form the invariant test suite for ADR-1+ADR-2+the user's
correction. Failing either is a hard release blocker.

### Step 7 — Wire `LayerManager` lifecycle into `OverlayCaptureEngine`

Add a sandbox-side runtime ops module `handlers/layer.py` exposing:
- `layer.snapshot(workspace_root) -> Manifest`
- `layer.acquire(workspace_root, manifest) -> Lease`
- `layer.release(workspace_root, lease_id) -> None`
- `layer.commit(workspace_root, layer_changes) -> Manifest` (used by
  `ContentManager` writes; routed via `OCCClient`-equivalent local handle)
- `layer.materialize_session(workspace_root) -> None` (session end)

These bind a process-wide singleton `_LAYER_MANAGERS: dict[(sandbox_id,
workspace_root), LayerManager]`.

`OverlayCaptureEngine.__init__` takes an optional `layer_client` (host-side
shim that calls into the dispatch). If absent, the engine uses the
`direct_runtime=True` path and instantiates the in-process `LayerManager`
directly — same dispatch surface, just shorter path.

`_new_lease`:
1. `manifest = await layer_client.snapshot(...)`
2. `lease = await layer_client.acquire(manifest)` — bumps refcounts.
3. Returns `OverlayLease(run_dir=..., manifest=manifest, lease_id=lease.lease_id)`.

`_execute_with_lease.finally`:
1. Always `await layer_client.release(lease.lease_id)` (even on exception).
2. Optional GC sweep is async fire-and-forget.

`_runtime_args`:
1. Serialize `manifest` into `run_dir/manifest.json` *before* `unshare -Urm`
   so it's visible inside the namespace.
2. Pass `--manifest-path` and `--session-root` to the runtime CLI.

Verify: integration test where 8 concurrent shells each snapshot, acquire,
hold for 100ms, release. Expected: refcounts drop to 0, no orphaned layer
dirs. Aligns with prototype's E4 stress shape.

### Step 7a — In-namespace OCC apply (`occ/runtime/apply.py` + relocate runtime CLI)

Per ADR-4, OCC apply runs inside the unshare namespace.

**`sandbox/occ/runtime/apply.py`** (new file, ~30 LOC):
```python
async def apply_inproc(
    *,
    upper_changes: tuple[UpperChange, ...],
    merged_view_path: str,    # the call's bound merged view
    layers: LayerManager,     # for layer-backed ContentManager
) -> ChangesetResult:
    typed = overlay_changes_to_changeset(upper_changes)
    if not typed:
        return ChangesetResult(files=())  # empty-upperdir fast path
    content = ContentManager(layers=layers)
    return await ChangesetOrchestrator(
        gitignore=GitignoreOracle(merged_view_path),
        direct=DirectMergeCoordinator(content),
        gated=OCCGatedCoordinator(content),
    ).apply(typed)
```

This is the **only new OCC code in the plan**. It's a thin shim — every
class it imports already exists in `backend/src/sandbox/occ/`. The
`GitignoreOracle` shells out to `git check-ignore -C <merged_view_path>`
which is the snapshot view by construction (lowerdir is kernel-pinned).

**Relocate runtime CLI:** `sandbox/overlay/runtime/cli.py` →
`sandbox/runtime/inproc_cli.py`. Update `bootstrap.py`, `runtime_execution.py`,
and the `capture_runtime_bundle` packaging to point at the new path. The new
CLI flow:

```python
# inside unshare -Urm
manifest, session_root = decode(args)
mounted = overlay.mounts.setup_mounts(...)         # overlay code
exit_code = command.run_user_command(...)          # bash subprocess
upper_changes = overlay.capture.walk_upperdir(...) # overlay code (git-free)
result = await occ.runtime.apply.apply_inproc(     # OCC code (git-aware)
    upper_changes=upper_changes,
    merged_view_path=mounted.merged,
    layers=LayerManager(session_root),
)
write_result_envelope(run_dir, exit_code, result, upper_changes)
exit
```

The CLI imports `sandbox.overlay` and `sandbox.occ.runtime`; neither imports
the other. Module purity is preserved.

Verify:
- `test_inproc_apply_runs_orchestrator` — give `apply_inproc` a fixture
  with mixed tracked + gitignored upper changes; assert the existing
  `ChangesetOrchestrator` is invoked exactly once and the result matches
  per-row §4d expectations.
- `test_runtime_cli_no_git_imports_in_overlay` (grep-style) —
  `grep -rn "subprocess.*git\|import git\|check-ignore" backend/src/sandbox/overlay backend/src/stack_overlay` returns zero matches.
- Integration: end-to-end shell call inside a real sandbox; assert the
  layer is published before the runtime exits, and `ShellResult.changed_paths`
  reflects the verdict.

There is no `mode` parameter, no new classifier, and **no host-side OCC
apply** — the runtime already published the layer (Step 7a). The pipeline
just reads the runtime's verdict envelope and projects to `ShellResult`.

`shell_pipeline`:
1. `outcome = await OverlayCaptureEngine.execute(...)`. The outcome already
   carries the in-namespace `ChangesetResult` projection.
2. Compute informational staleness:
   `manifest_lag = active.version - outcome.snapshot_version`,
   `shell_age_s = now - shell_start_ts`. Append
   `policies.staleness_warnings(...)` tags to `ShellResult.warnings`. Never
   reject on staleness.
3. Project the `ChangesetResult` to `ShellResult` via the existing
   `_shell_result_from_changeset` (unchanged).

That's the entire pipeline. No `gate.apply` call. No oracle construction.
`ContentManager` is built inside the runtime (Step 7a), not in the pipeline.

Verify:
- `test_pipeline_is_thin_projector` — patch
  `OverlayCaptureEngine.execute` to return a stub envelope; assert the
  pipeline does *not* call `ChangesetOrchestrator.apply` host-side, and the
  projection produces the expected `ShellResult` shape.
- `test_empty_upperdir_fast_path` — runs `true` end-to-end; assert no layer
  published, `ShellResult.changed_paths == ()`. (The fast path is realized
  inside Step 7a's `apply_inproc`: `typed == [] → ChangesetResult(files=())`,
  no `LayerManager.commit` call.)
- `test_gitignored_build_artifact_lwins` — concurrent writes to `dist/foo.js`
  (gitignored); assert the second write commits via the orchestrator's
  direct path with last-writer-wins.
- `test_tracked_codegen_race_rejects` — concurrent writes to the same
  tracked generated file; assert the second commit rejects with
  `ABORTED_VERSION` through `OCCGatedCoordinator` unchanged.
- `test_gitignore_seen_against_snapshot` — start a shell at manifest `M`
  with `dist/` *not* gitignored. Mid-call, an unrelated commit adds `dist/`
  to `.gitignore` (advancing active to `M+1`). Shell finishes, writes
  `dist/foo.js`. Assert the write goes through the OCC gate (tracked at
  snapshot `M`), not the direct path. The existing
  `GitignoreOracle(merged_view_path)` resolves this naturally because the
  merged view is the snapshot view.
- `test_staleness_warnings_are_informational` — long shell, advancing
  manifest, OCC-clean writes; assert warnings are present in
  `ShellResult.warnings` AND the writes still committed.

### Step 9 — Lease-budget enforcement

A background asyncio task in `LayerManager` runs every
`LEASE_BUDGET_TICK_SECONDS` (default 5s):
1. Build `LeaseSnapshot` list from active leases.
2. Call `LeaseBudget.evaluate(leases, ...)`.
3. For `kill` decisions, deliver `SIGTERM` to the lease's owning process via
   a callback registered on lease acquire (the engine registers
   `os.kill(pid, signal.SIGTERM)`); `SIGKILL` after grace.
4. For `backpressure` decisions, set a `commit_blocked: bool` flag that
   `LayerManager.commit` checks on entry and waits on a condition variable
   with timeout.
5. For `evict_session`, hard-release all leases owned by the session.

Verify: design §E12 four-scenario test in
`backend/tests/sandbox/overlay/test_lease_budget.py`. Each scenario asserts
the deterministic enforcement action.

### Step 10 — `bootstrap.py` and dispatch wiring

Register the new `layer.*` ops in `sandbox/overlay/bootstrap.py` /
`sandbox/runtime/_server_dispatch.py`. The pattern is the same as `overlay.run`
and `shell` already use.

Verify: `make build && make test` is green from `backend/`. Integration:
`uv run --project backend python -m pytest backend/tests/sandbox -q` is green.

### Step 11 — Delete dead code from old design

Once Steps 5/6/7 land, these constants and helpers are dead:
- `runtime/mounts.py`: `_NS_LOWER` (and any imports of it)
- `runtime/capture.py`: `_safe_lower_path` (replaced by `merged_view`'s
  internal normalization)
- `engine/run_artifacts.py`: `_workspace_root_fingerprint` and the
  lowerdir-guard pair (`_begin_lowerdir_guard`/`_end_lowerdir_guard`) — the
  guard's purpose was to detect "workspace changed outside the overlay OCC
  path"; in the new design, the workspace IS the layer stack, and OCC commits
  go through `LayerManager.commit`, so external mutation is impossible by
  construction.

Verify:
- `ruff check` reports no unused imports.
- `make test` is green.
- `grep -rn "_NS_LOWER\|_safe_lower_path\|_workspace_root_fingerprint\|_begin_lowerdir_guard\|_end_lowerdir_guard" backend/src` returns zero hits.
- **Live-root writer audit**: `grep -rn "live_root\|workspace_root" backend/src --include="*.py" | grep -v "ContentManager\|tests/"` shows that all `workspace_root` references are either path-string parameters threaded through dispatch (acceptable) or absolute-path projection helpers (`_absolutize`, `_ci_workspace_root`). No code path *writes* to `workspace_root` directly outside the `LayerManager`/`ContentManager` route. If a writer is found, it is a regression: the lowerdir guard removed in this step exists precisely to detect external writers, so the audit must replace it.

### Step 12 — Doc update

Update:
- `docs/architecture/overlay-sandbox-plan.md` (currently points at the bind
  model) to reference this implementation plan and the layer-stack docs.
- `backend/src/sandbox/overlay/__init__.py` — single-line module docstring
  pointing at the layer-stack design.
- A row in `CHANGELOG.md` if the project keeps one.

Verify: `make docs` if the project has a docs target, otherwise visual
review.

---

## Acceptance Criteria

1. The snapshot identity invariant holds: both
   `test_base_hash_resolves_through_snapshot_manifest` and
   `test_base_hash_is_pinned_to_snapshot_not_active_manifest` from Step 6
   pass. These are the gating tests for the user's correction.
2. `make build && make test` is green from `backend/`. Existing OCC, shell
   pipeline, write API, and edit API tests pass without modifying their
   source beyond `ContentManager` constructor wiring.
3. The existing `ChangesetOrchestrator` (from
   `backend/src/sandbox/occ/orchestrator.py`) handles the §4d routing
   unchanged. `apply_inproc` (Step 7a) constructs it with
   `GitignoreOracle(merged_view_path)` and a `LayerManager`-backed
   `ContentManager`, both running inside the unshare namespace where the
   merged view IS the snapshot view. No new classifier exists; the only
   OCC source files added or modified are `sandbox/occ/runtime/apply.py`
   (new thin shim) and `sandbox/occ/content/manager.py` (`ContentManager`
   content base swap). Overlay/stack_overlay never import git. The
   empty-upperdir fast path is realized inside `apply_inproc` (no
   `LayerManager.commit` call when `typed == []`). Staleness warnings are
   informational only and never cause a rejection.
4. Live-image probe `python -m stack_overlay.experiments mount-probe --depth
   100 --iterations 1000` passes 0/1000 failures, p99 < 5ms when run inside a
   Daytona-class sandbox.
5. End-to-end perf bar (design §E8): new design ≤ 1.2× old design's median
   shell wall time on the 100-load workload, ≤ 1.5× p99. Drift incidents
   reduce to zero.
6. Lease-budget enforcement: design §E12's four pathological scenarios
   produce deterministic kill/backpressure/eviction outcomes.
7. Layer GC: design §E6's "long shell holds layers across squash" produces
   zero ENOENT/stale-handle errors over 100 runs.

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Snapshot manifest leaks past lease release | Low–Med | `acquire`/`release` always bracketed in `try/finally`; integration test holds a lease past explicit release and verifies refcount drops to 0. |
| Capture `_read_base_bytes` accidentally reads from `_NS_LOWER` (i.e., live root) | Med (regression risk) | Delete `_NS_LOWER` constant entirely in Step 11. The compiler error is the safety net. |
| `mount(8)` re-introduced through a refactor | Low | Layer-walk syscall mount is a single function call with an explicit comment. CI grep for `subprocess.*\["mount"` in `backend/src/sandbox/overlay`. |
| Squash worker contends with capture's per-call mounts | Med | Squash never modifies retired layer dirs in place; manifest CAS publish is atomic; lease refcount keeps retired layers readable until release. Validated by E6 in prototype. |
| `ContentManager` rewrite breaks API write/edit paths | High if buggy | Thin façade `LayerBackedFs` with the exact existing surface; existing OCC tests catch contract drift. |
| Build outputs lost to OCC false-reject | Low | Gitignored paths bypass OCC entirely (last-writer-wins per §4d); typical build outputs (`dist/`, `build/`, `target/`, `.next/`) qualify automatically. No caller mode needed. |
| Tracked codegen race produces stale committed output | Med | Per-path CAS rejects concurrent updates to the same generated file; agent retries; for hidden-input dependencies the consumer re-triggers codegen. Design doc §Risks. |
| Gitignore evaluated against active workspace instead of snapshot | Med | OCC apply runs inside the unshare namespace (ADR-4); `GitignoreOracle(merged_view_path)` queries the snapshot view by construction (kernel-pinned `lowerdir`). Test `test_gitignore_seen_against_snapshot` asserts mid-call `.gitignore` mutations do not re-classify the in-flight call. |
| Git imported into overlay/stack_overlay | Med (regression risk) | Module purity: only `sandbox/occ/` invokes git. Grep test `test_runtime_cli_no_git_imports_in_overlay` (Step 7a) fails CI if anyone reintroduces git in `sandbox/overlay/` or `stack_overlay/`. |
| Runtime crashes mid-OCC, leaving partial layer | Low | `LayerManager.commit` writes accepted bytes into `L(N+1).staging/`, atomic-renames to `L(N+1)/`, then CAS-swaps the manifest. Crash before the rename → orphan staging dir, never referenced by manifest, fsck removes on restart. Crash between rename and CAS-swap → orphan layer dir, also unreferenced, fsck removes. No additional global lock needed. |
| Sandbox-side `LayerManager` singleton leaks across sessions | Med | Keyed by `(sandbox_id, workspace_root)`; explicit `layer.materialize_session` op clears it on session end. |
| Manifest serialization across the unshare boundary | Low | Serialize to `run_dir/manifest.json` before `unshare -Urm`; `run_dir` is a tmpfs path the namespace inherits. Validated by Step 5/6 integration test. |
| Staleness telemetry resurrected as a rejection signal | Med (regression risk) | The `staleness_warnings` helper returns `tuple[str, ...]` — there is no rejection branch. Test `test_staleness_warnings_are_informational` (Step 8) asserts OCC-clean writes commit even when warnings are emitted. |

---

## Verification & Test Matrix

| Test ID | Lives in | Asserts |
|---|---|---|
| `test_base_hash_resolves_through_snapshot_manifest` | `backend/tests/sandbox/overlay/test_capture_invariant.py` | User's correction; ADR-1; ADR-2 (positional resolution) |
| `test_base_hash_is_pinned_to_snapshot_not_active_manifest` | `backend/tests/sandbox/overlay/test_capture_invariant.py` | User's correction (time-drift) |
| `test_layer_manager_*` | `backend/tests/sandbox/overlay/test_layer_manager.py` | Step 1 — manifest, lease, commit, squash, GC |
| `test_merged_view_*` | `backend/tests/sandbox/overlay/test_merged_view.py` | Step 2 — whiteout/symlink/opaque/shadow |
| `test_staleness_warnings_thresholds` | `backend/tests/sandbox/overlay/test_policies.py` | Step 3 — informational only |
| `test_lease_budget_evaluate` | `backend/tests/sandbox/overlay/test_policies.py` | Step 3 — §E12 cases |
| `test_content_manager_layer_backed` | `backend/tests/sandbox/occ/test_content_manager.py` | Step 4 — façade contract; existing OCC orchestrator reused |
| `test_mount_syscall_depth_100` | `backend/tests/sandbox/overlay/test_mounts.py` (Linux-only) | Step 5 — depth-100 mount via `mount(2)` |
| `test_capture_against_manifest` | `backend/tests/sandbox/overlay/test_capture.py` | Step 6 — capture reads through `merged_view`, git-free |
| `test_engine_lease_lifecycle` | `backend/tests/sandbox/overlay/engine/test_capture_engine.py` | Step 7 — snapshot/acquire/release |
| `test_inproc_apply_runs_orchestrator` | `backend/tests/sandbox/occ/runtime/test_apply_inproc.py` | Step 7a — `apply_inproc` invokes existing `ChangesetOrchestrator` |
| `test_runtime_cli_no_git_imports_in_overlay` (grep-style) | `backend/tests/sandbox/overlay/test_module_purity.py` | Step 7a — overlay/stack_overlay never import git |
| `test_pipeline_is_thin_projector` | `backend/tests/sandbox/runtime/test_pipelines.py` | Step 8 — no host-side `gate.apply` call |
| `test_empty_upperdir_fast_path` | `backend/tests/sandbox/runtime/test_pipelines.py` | Step 8 — `apply_inproc` short-circuits, no layer published |
| `test_gitignored_build_artifact_lwins` | `backend/tests/sandbox/runtime/test_pipelines.py` | Step 8 — orchestrator routes gitignored writes to direct merger |
| `test_tracked_codegen_race_rejects` | `backend/tests/sandbox/runtime/test_pipelines.py` | Step 8 — per-path CAS via existing `OCCGatedCoordinator` |
| `test_gitignore_seen_against_snapshot` | `backend/tests/sandbox/runtime/test_pipelines.py` | Step 8 — `GitignoreOracle(merged_view_path)` resolves snapshot view by kernel pinning |
| `test_staleness_warnings_are_informational` | `backend/tests/sandbox/runtime/test_pipelines.py` | Step 8 — staleness never rejects |
| `test_lease_budget_scenarios` | `backend/tests/sandbox/overlay/test_lease_budget.py` | Step 9 — design §E12 |
| `test_dispatch_layer_ops` | `backend/tests/sandbox/runtime/test_dispatch.py` | Step 10 — wire surface |
| `test_no_dead_lower_dir_constant` (grep-style) | `backend/tests/sandbox/overlay/test_dead_code.py` | Step 11 — `_NS_LOWER` is gone |

Live experiments (rerun after merge, not blocking PR):
- E1, E2, E3, E5, E6, E10, E11, E12, E13, E14 from
  `.omc/plans/per-call-snapshot-layer-stack.md` against the production image.
- E8 cross-design benchmark.

---

## Open Questions (deferred)

1. **Session boundaries.** What does session-end cleanup do — squash to a
   single tree and write back to `live_root`, or preserve the stack across
   sessions? Defer until session lifecycle is closed by the broader
   sandbox-lifecycle owner; for now `layer.materialize_session` collapses the
   stack and removes the layer dirs.
2. **Tmpfs vs scratch dir.** `/dev/shm/eos-layers/` default; fall back to a
   scratch dir if tmpfs is full. Implement the fallback after E7's real-trace
   measurement informs sizing.
3. **API read view caching.** Currently each OCC CAS read walks the manifest.
   If E3 cold-read hot path measurement shows this is the bottleneck, add an
   in-process LRU keyed by `(manifest.version, rel)`.
4. **Cross-request commit coalescing (50ms window).** Design §7 specifies
   a coalescing window; this plan ships per-changeset commit (Step 4) only.
   Add the 50ms window if E5 shows append-rate exceeding `SQUASH_TRIGGER`
   on the new pipeline.
5. **Tracked binary CAS strength.** Design §4d picks "best-effort existence
   + size CAS" for tracked binaries with last-writer-wins fallback on a
   path-scoped allowlist. The exact allowlist needs telemetry from real
   workloads.

---

## Effort Estimate

~750–950 LOC (production), ~550–750 LOC (tests). Smaller than the prior
mode-policy estimate because `ShellMode`/`ShellCommitGate`/
`classify_shell_mode`/exclusive-lock plumbing are gone. 3 sprints (~1.25
weeks) of focused work:

- Sprint A: Steps 0–4 (manifest, layer manager, merged view, policies,
  content manager rewrite). Highest design-risk; most prototype reuse.
- Sprint B: Steps 5–7 (runtime mount/capture/engine rewrite). Highest
  integration risk.
- Sprint C: Steps 7a–12 (in-namespace OCC apply + runtime CLI relocation,
  thin host pipeline, lease-budget enforcement, dispatch wiring, dead-code
  removal, doc update). Lowest risk; verifies whole stack end-to-end.

---

## ADR Summary (for retrospective record)

**ADR-1 — `LayerManager` placement: sandbox-side.** Layer dirs and the
unshare-mount target live in the sandbox; running `LayerManager` host-side
forces every commit to round-trip the host process and complicates atomic
manifest publish.

**ADR-2 — Base-bytes read strategy: walk manifest in Python.** Per-change
walker is sub-millisecond at realistic change counts; avoids a second overlay
mount per call; lets capture, OCC reads, and squash share one merged-view
walker.

**ADR-3 — Cutover strategy: in-tree replacement, no feature flag.** Dual-path
adds carrying cost; rollback via `git revert` is the simpler and safer
fallback.

**ADR-4 — OCC apply runs inside the unshare namespace.** The runtime binary
sequences capture + OCC apply in the same `unshare -Urm` child. Keeps
overlay/stack_overlay git-unaware (module purity contract). `GitignoreOracle`
operates against the still-mounted snapshot view, satisfying §4c by
construction. Layer is published before the runtime exits; host pipeline
becomes a thin projector.

---

## Appendix — Mapping to design diagrams

- Diagram 1 (Stacked Overlay Concept vs Traditional COW): Steps 5, 7. The
  "L0 baseline immutable" is created by `LayerManager.create` from
  `live_root`; "L(K+1) fresh immutable layer" is `LayerManager.commit`.
- Diagram 2 (Lowerdir + Upperdir Command Lifecycle): Step 7. The
  `snapshot/acquire` calls live in `_new_lease`; the manifest CAS-publish on
  the OCC accept side lives in `ContentManager` write paths (Step 4).
- Diagram 3 (Conditions to Create a New Overlay Layer): Steps 7a + 8.
  With the mode policy dropped and OCC apply moved in-namespace, the diagram
  simplifies to: capture (git-free) → `apply_inproc` (existing
  `ChangesetOrchestrator.apply` does the §4d routing) → one
  `LayerManager.commit(staged_changes)` publishes the layer **inside the
  namespace** → host pipeline projects the result envelope.
  Cross-request 50ms coalescing is deferred (Open Question 4).
- Diagram 4 (Squash Algorithm): Step 1. The plan/build/publish/retire/GC
  phases are exactly the prototype's `_squash_to_target_locked`.
- Diagram 5 (Long Polling Requests, Leases, Squash): Step 9. The "skip
  retired layers pinned by request lease" property is enforced by
  `LayerManager.collect_garbage` checking `_lease_layers[layer] > 0` before
  deleting.

---

## Changelog

- 2026-05-04: Initial draft. Incorporates user correction
  ("hash of changed files refers to the one at overlay checkout time")
  as the named "Snapshot Identity" invariant. ADR-1, ADR-2, ADR-3 chosen
  after orientation through `backend/src/sandbox/overlay/`,
  `backend/src/sandbox/occ/`, and `stack_overlay/` prototype.
- 2026-05-04 (rev 2): Mode policy dropped to track design doc revision.
  Removed `ShellMode` enum, `ShellCommitGate`, `classify_shell_mode`,
  `strict_stale` rejection branch, and `exclusive` workspace-write lock.
  Replaced with the design §4d per-path classification table (automatic
  routing by `(change type, gitignored?)`), the empty-upperdir fast path
  (replaces `read_only`), gitignored last-writer-wins (replaces
  `exclusive` for build artifacts), and informational-only staleness
  telemetry. Step 3 (`policies.py`) and Step 8 (pipeline routing)
  rewritten; risks/tests/open-questions/effort updated accordingly.
  `OverlayClient.run`/`shell` keeps its existing signature — no `mode`
  parameter.
- 2026-05-04 (rev 3): Reuse `backend/src/sandbox/occ/` unchanged. Removed
  the proposed `policies.PathPolicy.classify_change` function — the §4d
  routing it duplicated is already implemented by
  `ChangesetOrchestrator.apply`. The pipeline now constructs the existing
  orchestrator with two injected dependencies: `ContentManager.for_workspace`
  (layer-backed, Step 4) and a new `SnapshotGitignoreOracle` (set-based,
  Step 3). Gitignore decisions are stamped at capture time inside the
  runtime while the merged view is still kernel-pinned (Step 6
  enhancement). `OverlayRunOutcome` carries `gitignored_paths`. No source
  file under `backend/src/sandbox/occ/` is modified except `ContentManager`.
  Tests adjusted: dropped `test_classify_change_table`; added
  `test_runtime_stamps_gitignored_paths`,
  `test_pipeline_uses_existing_orchestrator`,
  `test_gitignore_stamped_against_snapshot_not_active`,
  `test_snapshot_gitignore_oracle_membership`. Tracked binary CAS upgrade
  per §4d row 7 deferred to a follow-up plan (out of scope here; reusing
  OCC means `BinaryChange` keeps its current direct-merge semantics).
- 2026-05-04 (rev 4): Module-purity contract — overlay and stack_overlay
  are git-unaware. Added ADR-4: OCC apply runs inside the unshare namespace.
  The runtime binary (relocated from `sandbox/overlay/runtime/cli.py` to
  `sandbox/runtime/inproc_cli.py`) sequences overlay capture + OCC apply
  in the same child process. Removed the rev 3 gitignore stamping path:
  no `git check-ignore` invocation in `runtime/capture.py`, no
  `gitignored_paths` field on `OverlayRunOutcome`, no `SnapshotGitignoreOracle`.
  Replaced with a thin OCC entry point `sandbox/occ/runtime/apply.py:apply_inproc`
  that invokes the existing `ChangesetOrchestrator` with
  `GitignoreOracle(merged_view_path)` (the merged view IS the snapshot view
  by kernel pinning). The host-side `shell_pipeline` becomes a thin projector
  with no `gate.apply` call. Tests adjusted accordingly: dropped the rev 3
  stamping/snapshot-oracle tests; added `test_inproc_apply_runs_orchestrator`,
  `test_runtime_cli_no_git_imports_in_overlay`,
  `test_pipeline_is_thin_projector`, `test_gitignore_seen_against_snapshot`.
  Also recorded design answer to "should OCC apply be globally serialized?":
  no — `LayerManager.commit`'s staging+rename+manifest-CAS-swap is already
  layer-atomic; per-path OCC semantics are an intentional design choice;
  global serialization defeats the layer-stack's concurrency goal.
