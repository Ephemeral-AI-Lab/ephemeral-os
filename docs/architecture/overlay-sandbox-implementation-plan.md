# Implementation Plan: Replace Git-Workspace CodeAct Sandbox with Overlayfs + Bind-Mount Lower

## 1. Restated Requirements

- The CodeAct execution path inside Daytona currently runs each command inside a per-sandbox **Git workspace slot** (`git clone --shared`, replay live state via `git diff` + untracked copy, snapshot the slot, run the command, walk slot vs snapshot, hand a `WorkspaceDiff` to OCC via `GitDiffCommitter`). This works but is **gitignore-blind** — `.venv/`, `node_modules/`, `__pycache__/` are not staged into the slot, so `pytest`/`pip install` workloads run against an empty Python env.
- Goal: replace the slot with an **overlayfs** whose `lowerdir` is a **bind-mount of the live workspace** (full visibility, including .git and gitignored trees), `upperdir` is **tmpfs** (captures only what the command writes), and `merged` is what the command sees at `$repo_root`. Use the `upperdir` tree as the precise, surgical delta source.
- Classification of the upperdir delta:
  - `.git/**` writes → **REJECT** (corruption vector).
  - Tracked-file writes/deletes/whiteouts → route through OCC (existing `GitDiffCommitter` / `WriteCoordinator` path).
  - Gitignored-path writes (e.g., `.venv/site-packages/...`, `node_modules/...`) → **direct-merge** to live workspace (no OCC, no semantic merge).
  - Whiteouts (deletions) → mirror to live (with rules, see risks).
- Preserve the existing `FileChangeResult` shape returned by `submit_codeact_cmd` (downstream tools depend on `changed_paths` / `ambient_changed_paths` / `git_commit_status`).
- Replace the slot pool, not the OCC engine. `WriteCoordinator.commit_operation_against_base` and `commit_many_operations_against_base` remain authoritative for tracked files.
- The four sharp edges (copy-up amplification, gitignored concurrency, whiteout-then-recreate destroying lower cache, unconfirmed Daytona privilege) are real and must each have a concrete decision in the plan.

---

## 2. In Scope vs Out of Scope

**Recommendation**: build the overlay path **side-by-side** behind a per-sandbox env flag (`EOS_CODEACT_SANDBOX_BACKEND={git_workspace,overlay}`, default `git_workspace`). Soak overlay on internal eval workloads for ≥1 week, then flip default and remove git-workspace in a final phase.

- **In scope**: capability probing; new `OverlayWorkspacePool`/`OverlayWorkspaceAuditor`; classifier; OCC handoff for tracked deltas; direct-merge writer for gitignored deltas; feature flag wiring in `command_executor.py`; observability; eventual git-workspace removal.
- **Out of scope (this plan)**: replacing OCC itself; changing `WriteCoordinator` semantics; LSP/symbol-index refresh path (overlay reuses existing hooks); changing the Daytona base image; multi-sandbox shared lower (each sandbox owns its own bind).

**Tradeoff**: side-by-side doubles the code path during soak (~2 weeks), but git-workspace currently meets perf at 100-load (20.9s) and the only motivation is capability — there is no urgency that justifies a one-shot cutover. Safe rollback wins.

---

## 3. Probe-Outcome Branch Table (gate for everything else)

The probe scripts (`backend/scripts/probe_overlay_capability.py`, `probe_overlay_followup.py`) must be re-run first, and the plan branches on which result wins:

| Probe outcome (highest precedence first) | Design |
|---|---|
| `PRIV_OVERLAY_CROSSFS=YES` (or `SUDO_OVERLAY_CROSSFS_LIVELOW=YES` from followup) | **Design A**: at sandbox bootstrap, `mount --bind <live> <slot>/lower; mount -t tmpfs … <slot>/upper; mount -t overlay …` once per slot. Cheapest exec model (no per-command wrapping). |
| Only `USERNS_CROSSFS_BIND_OVERLAY=YES` (rootless via `unshare -Urm + userxattr`) | **Design B**: every command runs inside `unshare -Urm bash -c '<setup-mounts> && <user-cmd>'`. Mounts die with the namespace, cleanup is automatic. **But** changes signal/uid semantics — must validate `pytest`, `npm`, interactive-ish tools still behave. |
| Neither | **Abort**. Escalate: either request a privileged Daytona base image, or stay on git-workspace and close the gitignored-deps gap a different way (e.g., explicit allowlist copy of `.venv`/`node_modules` into the slot during baseline). The rest of this plan does not run. |

Ranked preference: A > B > abort. Phase 1 produces a written probe report and a one-line decision before phase 2 starts.

---

## 4. Phased Plan

### Phase 1 — Capability Gate (probe + decision) — **Low / 0.5 day**

- **What**: re-run both probe scripts against the current Daytona target; capture raw output; pick design A/B/abort per the table above.
- **Files touched**: none in `backend/src/`. New: `docs/architecture/overlay-sandbox-capability-report.md` (probe transcripts + decision).
- **Dependencies**: none.
- **Exit criteria**: design choice committed in writing. If "abort," stop here and re-plan.

### Phase 2 — Mount Lifecycle (`OverlayWorkspacePool`) — **High / 2–3 days**

- **What**: new module mirroring `GitWorkspacePool`'s public surface (`lease`, `release`, `prepare_baseline`, `pool_root`).
  - Slot layout per sandbox: `/tmp/eos-codeact-overlay/<sandbox_id>/<workspace_hash>/slots/slot-NNN/{lower,upper,work,merged}`.
  - `lower` = bind-mount of `workspace_root` (live).
  - `upper`/`work` = tmpfs with bounded size (`EOS_OVERLAY_UPPER_SIZE_MB`, default 512MB).
  - `merged` = overlay mount; commands `cd` into `merged` instead of into `workspace_root`.
  - `prepare_baseline` becomes a near-no-op: just record live `git rev-parse HEAD` and a stat of `merged` for telemetry — the lower **is** the baseline.
- **Mount lifecycle pin**: **one overlay per `lease`**; pool reuses the slot directory but **remounts upper/work tmpfs fresh on each release** (cheap; tmpfs unmount drops everything). Bind-mount of lower is permanent for the slot's lifetime; only torn down on sandbox dispose.
- **Sandbox-reuse handling**: pool keeps a stale-mount detector — on `lease()`, verify `findmnt merged` shows a live overlay; if the prior tear-down crashed and left a half-mount, force-unmount before remounting. Add a startup sweep that unmounts any orphaned `slot-*/merged` from a previous process.
- **Files added**: `backend/src/code_intelligence/routing/overlay_workspace_pool.py`, `backend/src/code_intelligence/routing/overlay_workspace_types.py`.
- **Risk**: High. Mount leakage and stale-mount corruption are the dominant failure modes for sandbox reuse.

### Phase 3 — Upperdir Walker + Classifier (`OverlayDeltaCollector`) — **Medium / 2 days**

- **What**: replace the `_COLLECT_DIFF_SCRIPT` walker. New script walks **`upperdir`** (much smaller than slot vs snapshot), emits per-entry `(rel_path, change_kind)` where `change_kind ∈ {add, modify, whiteout, opaque_dir_marker}`.
  - Detect overlayfs whiteouts: char-device 0/0 → deletion of the lower path.
  - Detect opaque-dir markers: `trusted.overlay.opaque="y"` xattr on a dir → dir replaced wholesale.
  - Classify each path against:
    1. starts with `.git/` → **REJECT**.
    2. matches `.gitignore` (run `git check-ignore -z --stdin` once for the whole batch in the sandbox) → **gitignored-direct-merge**.
    3. otherwise → **tracked-OCC**.
- **`.git/` REJECT UX**: whole-run abort; surface as `git_conflict_reason="overlay_rejected_dotgit_writes: <paths>"` in the auditor result, exit_code stays the command's actual code, but `success=False` so the agent sees the failure.
- **Files added**: `backend/src/code_intelligence/routing/overlay_delta_collector.py`.
- **Output shape**: produce a `WorkspaceDiff`-compatible payload for tracked files (so `GitDiffCommitter` reuses unchanged) **plus** a new `gitignored_changes: list[GitignoredChange]` field for direct-merge.
- **Risk**: Medium. Whiteout / opaque-marker semantics are subtle; need unit tests against synthetic upperdir trees.

### Phase 4 — Merge-Back: Gitignored Direct-Writer + OCC Handoff — **Medium / 2 days**

- **What**: split the post-command merge into two paths.
  - **Tracked path**: hand `WorkspaceDiff` to the existing `GitDiffCommitter.commit(...)` → `WriteCoordinator.commit_operation_against_base`. Zero changes to OCC engine.
  - **Gitignored path**: new `GitignoredDirectWriter` runs in-sandbox (single python script): for each `(rel, kind)`:
    - `add`/`modify` → atomic copy `upper/<rel>` → live `<rel>` via tempfile + rename; mkdir parents as needed.
    - `whiteout` → `os.unlink` / `shutil.rmtree` on live (subject to whiteout policy below).
    - `opaque_dir_marker` → wholesale replace dir contents on live (rare; flagged in telemetry).
- **Whiteout-then-recreate policy**: for any `whiteout` whose path matches a known **shared cache root** (configurable allowlist, default: `.venv/`, `node_modules/`, `__pycache__/`), **refuse the deletion and warn**. Agents that genuinely want to rebuild can use `daytona_delete_file` (the OCC-aware path).
- **Files added**: `backend/src/code_intelligence/routing/overlay_direct_writer.py`.
- **Files touched**: `backend/src/code_intelligence/routing/git_diff_committer.py` — extend the result shape with a `gitignored_writes` summary; `WriteCoordinator` itself unchanged.
- **Risk**: Medium. The atomicity story for direct-merge is weaker than OCC's — document it.

### Phase 5 — `OverlayCommandAuditor` + `AuditedCommandExecutor` Wiring — **High / 2 days**

- **What**: build `OverlayCommandAuditor` mirroring `GitWorkspaceAuditor.execute(...)`. It composes pool → run command in `merged` → collector → tracked OCC commit → gitignored direct-merge → assemble `SimpleNamespace` with `result/exit_code/changed_paths/ambient_changed_paths/git_commit_status/git_conflict_*`.
- **Multi-shell-per-codeact decision**: one Python CodeAct call's wrapper invokes `shell()` N times. **Decision: one overlay per `svc.cmd` call, N shells share it.** The auditor sees the cumulative upperdir delta after the wrapper exits and classifies/commits once.
- **Wire-in**: `AuditedCommandExecutor._ensure_git_workspace_auditor` becomes `_ensure_command_auditor` and selects backend by env flag:
  - `EOS_CODEACT_SANDBOX_BACKEND=git_workspace` (default) → existing `GitWorkspacePool`/`GitWorkspaceAuditor`.
  - `EOS_CODEACT_SANDBOX_BACKEND=overlay` → new `OverlayWorkspacePool`/`OverlayCommandAuditor`.
- **Command-mapping change**: `_map_command_to_slot` currently rewrites `workspace_root` → `slot_path` in the command string. For overlay, the command runs in `merged` which is **mounted at the same path the command expects** — no string rewriting needed.
- **Files touched**:
  - `backend/src/code_intelligence/routing/command_executor.py` — backend selection, type the auditor as a protocol.
  - `backend/src/code_intelligence/routing/service.py` — `_git_workspace_pool` property becomes `_command_pool`.
  - `backend/src/tools/daytona_toolkit/codeact_tool.py` — error strings become backend-agnostic.
- **Files added**: `backend/src/code_intelligence/routing/overlay_command_auditor.py`.
- **Risk**: High. This is where backend mis-selection or shape drift breaks every CodeAct call.

### Phase 6 — Concurrent-Write Policy for Gitignored Paths — **Medium / 1 day**

- **What**: two parallel `pip install`s racing on `site-packages/INSTALLER` etc.
- **Decision**: **per-prefix serialization + last-writer-wins within prefix**, implemented as a sandbox-local async lock keyed by the top-level gitignored dir (`.venv/`, `node_modules/`, etc.). Locks held only during the direct-merge step (post-command), not during command execution.
- **Files touched**: `backend/src/code_intelligence/routing/overlay_direct_writer.py` (lock manager), one config knob in new `overlay_workspace_config.py`.
- **Risk**: Medium. Lock granularity choice can deadlock if a single command writes to multiple roots; document and test.

### Phase 7 — Observability — **Low / 0.5 day**

- **What**: per-`svc.cmd` metrics: `overlay.upper_bytes`, `overlay.upper_files`, `overlay.tracked_changes`, `overlay.gitignored_changes`, `overlay.whiteouts`, `overlay.dotgit_rejects`, `overlay.copy_up_bytes`, `overlay.mount_setup_ms`, `overlay.merge_back_ms`. Threshold alarms on `upper_bytes` approaching size cap.
- **Files touched**: `backend/src/code_intelligence/routing/telemetry.py`.
- **Risk**: Low.

### Phase 8 — Rollout & Soak — **Low / ~1 week wall-clock, ~0.5 day work**

- Default flag `git_workspace`. Flip to `overlay` for one team's sandboxes via per-sandbox env override. Watch metrics + failure rates.
- **Exit criteria**: ≥1 week with overlay default-on for soak cohort, no regressions in CodeAct success rate.

### Phase 9 — Remove Git-Workspace Path — **Low / 1 day**

- Delete `git_workspace_pool.py`, `git_workspace_auditor.py`, `git_workspace_types.py`, `git_workspace_config.py`, `git_diff_committer.py` glue (keep the `WriteCoordinator` consumer).
- Simplify `command_executor.py` (no backend selection).
- Update tests under `backend/tests/code_intelligence/routing/`.
- **Risk**: Low (mechanical), but **only after** soak passes.

---

## 5. Phase Dependencies

```
Phase 1 (probe)
  └─ Phase 2 (pool)
       ├─ Phase 3 (collector)
       │    └─ Phase 4 (merge-back) ─┐
       └────────────────────────────┴─ Phase 5 (auditor + wiring)
                                          ├─ Phase 6 (concurrent policy)
                                          └─ Phase 7 (observability)
                                               └─ Phase 8 (soak)
                                                    └─ Phase 9 (remove git-workspace)
```

Phase 1 is a hard gate — every later phase is moot without it.

---

## 6. Risks (with severity)

1. **[HIGH] Daytona privilege probe fails** — kills the entire effort. Mitigate by running Phase 1 in isolation before committing engineering time.
2. **[HIGH] Copy-up amplification** — a single `sed -i` on a 2GB log file copies the whole file to upper. Mitigate: enforce `upperdir` size cap via tmpfs `size=` mount option (fail-fast with ENOSPC); the auditor surfaces "upper_full" as a distinct conflict reason.
3. **[HIGH] Mount leakage on crash** — partial unmounts wedge sandbox reuse. Mitigate: startup sweep + per-`lease()` stale-mount check (Phase 2). Treat any non-clean unmount as `discard=True` and rebuild the slot.
4. **[HIGH] Backend-selection drift** — feature flag mis-read silently switches users. Mitigate: explicit enum parse + log the chosen backend on every `svc.cmd` first invocation per sandbox.
5. **[MED] Concurrent-write policy** — covered by Phase 6; risk is that lock-prefix list is incomplete and a new ecosystem (e.g., Cargo `target/`) creates an unprotected race. Mitigate: configurable, default-allowlist surfaced in telemetry as `unlocked_gitignored_writes`.
6. **[MED] Whiteout-then-recreate destroys lower cache** — covered by Phase 4 refusal policy; risk is agents legitimately wanting to rebuild deps. Mitigate: clear error message pointing to `daytona_delete_file` or a future explicit "rebuild env" tool.
7. **[MED] Multi-shell-in-one-CodeAct semantics shift** — currently each `shell()` inside a Python CodeAct call passes through one `_exec_shell_command` → `submit_codeact_cmd` → `svc.cmd` round-trip. **Re-verify**: read `_WRAPPER_TEMPLATE` in `codeact_tool.py` — `shell()` calls happen *inside* the sandbox python wrapper, not via `svc.cmd`. So all N shells share one overlay (one `svc.cmd` invocation wraps the whole wrapper).
8. **[MED] `git check-ignore` cost on large deltas** — for a `pip install` writing 10k files, batch into one `git check-ignore -z --stdin` call. Risk: Daytona stdin size limits. Mitigate: chunk at 1MB stdin if needed.
9. **[LOW] Overlay-aware tools confused** — tools that walk `/proc/mounts` may see overlay and behave oddly. None known in the agent's typical workflow; flag if it surfaces.
10. **[LOW] tmpfs OOM on the sandbox host** — many parallel sandboxes × 512MB cap. Mitigate: default cap is per-slot, total bounded by `pool_size * cap`. Document the math.

---

## 7. Complexity Summary

| Phase | Complexity | Estimate |
|---|---|---|
| 1 Probe | Low | 0.5 day |
| 2 Pool / mount lifecycle | High | 2–3 days |
| 3 Walker / classifier | Medium | 2 days |
| 4 Merge-back (direct + OCC) | Medium | 2 days |
| 5 Auditor + wiring | High | 2 days |
| 6 Concurrent-write policy | Medium | 1 day |
| 7 Observability | Low | 0.5 day |
| 8 Soak | Low | ~1 week wall, 0.5 day work |
| 9 Remove git-workspace | Low | 1 day |
| **Total engineering** | — | **~11–12 dev-days + 1 week soak** |

---

## 8. Open Questions for User Before Implementation

1. Confirm `.git/` policy: **REJECT whole run** (proposed) vs strip+warn vs allow.
2. Confirm whiteout policy: **refuse on shared-cache prefixes** (proposed) vs always mirror vs always refuse all whiteouts of gitignored content.
3. Confirm one-overlay-per-`svc.cmd` (proposed) vs per-shell.
4. Confirm side-by-side soak (proposed) vs one-shot cutover.
5. Confirm we should **rerun** both probe scripts as Phase 1 — they exist but haven't been re-run since the git-workspace switch landed.

---

## 9. Files Referenced

- `backend/scripts/probe_overlay_capability.py`
- `backend/scripts/probe_overlay_followup.py`
- `backend/src/code_intelligence/routing/service.py`
- `backend/src/code_intelligence/routing/command_executor.py`
- `backend/src/code_intelligence/routing/git_workspace_pool.py`
- `backend/src/code_intelligence/routing/git_workspace_auditor.py`
- `backend/src/code_intelligence/routing/git_diff_committer.py`
- `backend/src/code_intelligence/routing/git_workspace_types.py`
- `backend/src/code_intelligence/routing/git_workspace_config.py`
- `backend/src/code_intelligence/editing/write_coordinator.py`
- `backend/src/tools/daytona_toolkit/codeact_tool.py`
- `backend/src/tools/daytona_toolkit/_commit.py`
