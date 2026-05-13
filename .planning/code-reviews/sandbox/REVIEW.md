---
status: issues_found
depth: standard
scope: backend/src/sandbox (whole subtree)
files_reviewed: 134
findings:
  blocker: 21
  warning: 64
  info: 23
  total: 108
generated: 2026-05-13
fanout: 9 subsystem reviewers in parallel
---

# Sandbox Subtree — Consolidated Code Review

Whole-subtree adversarial code review of `backend/src/sandbox/` (134 Python files, ~13.5K LOC) at standard depth. Nine subsystem reviewers ran in parallel; per-subsystem reports are in this directory.

## Per-subsystem report counts

| Subsystem | BLOCKER | WARNING | INFO | Total | Report |
|---|---:|---:|---:|---:|---|
| `api` | 0 | 6 | 5 | 11 | [api-REVIEW.md](api-REVIEW.md) |
| `command_exec` | 1 | 7 | 2 | 10 | [command_exec-REVIEW.md](command_exec-REVIEW.md) |
| `host` | 0 | 7 | 3 | 10 | [host-REVIEW.md](host-REVIEW.md) |
| `layer_stack` | 7 | 8 | 0 | 15 | [layer_stack-REVIEW.md](layer_stack-REVIEW.md) |
| `occ` | 2 | 6 | 3 | 11 | [occ-REVIEW.md](occ-REVIEW.md) |
| `overlay` | 2 | 8 | 4 | 14 | [overlay-REVIEW.md](overlay-REVIEW.md) |
| `plugin` | 1 | 8 | 4 | 13 | [plugin-REVIEW.md](plugin-REVIEW.md) |
| `provider` | 3 | 6 | 2 | 11 | [provider-REVIEW.md](provider-REVIEW.md) |
| `runtime` | 5 | 8 | 0 | 13 | [runtime-REVIEW.md](runtime-REVIEW.md) |
| **Total** | **21** | **64** | **23** | **108** | |

## Top BLOCKER issues by theme

### Theme 1 — Crash-safety / durability (layer_stack, occ)
The layer-stack persistence layer has no `fsync` discipline. Combined with unvalidated layer paths, a torn manifest with adversarial `LayerRef.path` values can drive `rmtree` against arbitrary filesystem locations on the next GC.

- `layer_stack` BL-01 `manifest/store.py:31-39` — `write_manifest_atomic` no fsync on tmp or parent
- `layer_stack` BL-02 `layer/publisher.py:95-112` — Publish skips fsync on staging contents/dir/parent
- `layer_stack` BL-03 `manifest/model.py:20-25` + `filesystem.py:22-26` — `LayerRef.path` accepts `..` / absolute; `_remove_unreferenced_layers` can `rmtree` arbitrary FS locations on a torn manifest
- `layer_stack` BL-06 `workspace/binding.py:95-107` — Same fsync-absent defect in workspace binding
- `layer_stack` BL-07 `workspace/base.py:296-331` — `_write_base_layer` writes no fsync and no digest; a torn base persists and `_reject_existing_base_state` refuses recovery
- `occ` BL-02 `commit_transaction.py:321-337` — `_LayerChangeStager.write_from_path` swallows `OSError`, can stage empty bytes past the consistency guard, then publish the caller's original `precomputed_hash` → hash/content mismatch

### Theme 2 — Overlay / view correctness (layer_stack, occ)
Overlay semantics edges that silently mis-apply.

- `layer_stack` BL-04 `view/merged.py:266-269` — `_clear_directory` calls `mkdir(exist_ok=True)` without removing a pre-existing file/symlink → crashes on legitimate file→opaque-dir transitions
- `layer_stack` BL-05 `layer/publisher.py:97-189` — Publisher does not deduplicate via `aggregate_layer_changes`; duplicate paths from OCC flattening crash `_write_symlink` and corrupt rolling sha256 idempotency
- `occ` BL-01 `merge/direct.py:113-127` — `DirectMerge.EditChange` silently drops anchor-not-found, count-mismatch, non-utf-8, and prior-delete cases (vs. `GatedMerge` which rejects loud) → public OCC API contract violated for gitignored paths

### Theme 3 — In-sandbox daemon RPC hardening (runtime)
The in-sandbox daemon's RPC layer has no defense-in-depth against buggy callers.

- `runtime` BL-01 `rpc/server.py:57` — Unbounded request payload silently drops the connection (`LimitOverrunError` swallowed; no error envelope written). Trivially reachable via `api.write_file` with a >64 KiB body
- `runtime` BL-02 `rpc/server.py:57` — `await reader.readline()` has no read timeout; slow-loris drops the daemon scheduler
- `runtime` BL-03 `rpc/server.py:128-132` — TOCTOU on socket permissions: socket is world-accessible between `bind()` and `chmod 0o600`; chmod failure silently suppressed
- `runtime` BL-04 `rpc/dispatcher.py:73-78` — Full Python traceback leaks in every error envelope (file paths, line numbers, repr'd args)
- `runtime` BL-05 `handler/tools/edit.py:41` — `int(edit.get("expected_occurrences") or 1)` silently rewrites `0 → 1`; breaks the anchor-count contract for "must hit zero occurrences" callers

### Theme 4 — Sandbox-escape / workspace-boundary failures (command_exec, overlay)
Workspace replacement and sandboxed execution can be redirected outside the leased root.

- `command_exec` BL-01 `environment.py:28-34` — `resolve_workspace_cwd` validates only absolute paths against `declared_workspace_root`; relative `cwd="../../../etc"` is concatenated unchecked, `mkdir(parents=True)` runs, and `subprocess.run(cwd=...)` then executes outside the leased workspace. Verified by hand
- `overlay` CR-01 `namespace/command.py:39` — `run_user_command` does `env={**os.environ, **env, ...}`, leaking every host secret (`AWS_*`, `OPENAI_API_KEY`, etc.) into the arbitrary user command. Defeats sandboxing on the in-process path
- `overlay` CR-02 `RuntimeInvoker` / `snapshot_overlay_runner` — `run_dir` (lower/upper/work/merged + stdout/stderr/result.json) never cleaned up; unbounded growth of `storage_root/runtime/overlay_shell/`

### Theme 5 — Provider egress / scheduler hangs (provider)
Matches the user memory ("Daytona pending_build hang root cause"). The blockers concentrate at the egress boundary.

- `provider` CR-01 `daytona/bash.py:52-59` — `extract_exit_code` returns `(sanitized, 0)` on non-numeric SDK `exit_code` + missing `__CODEX_EXIT_CODE__=` marker; failed remote commands silently classified as success
- `provider` CR-02 `sync_client.py:84-90`, `async_client.py:96-102`, `adapter.py:126` — No timeout on `client.get` / `client.list(limit=1)`. Scheduler-degraded 300s hang is the exact failure mode flagged in memory; health-probe endpoint itself has no upper bound
- `provider` CR-03 `sync_client.py:63-81` — Sync client overwrites `_cached_client` on credential rotation without `.close()`; the async path tracks `stale_clients` correctly — sync was never updated. SDK clients leak pooled HTTP connections across rotation

### Theme 6 — Plugin lifecycle wedge (plugin)
- `plugin` BL-01 `handler.py:96-116` — `plugin_ensure` writes `_LOADED`/`_LOADED_DIGEST` *before* awaiting `_warm_plugin_runtime`. A failed warm leaves the registry permanently half-initialized; every subsequent call takes the "already loaded" branch and re-fails forever until host restart. Directly relevant to the LSP plugin provisioning stabilization in recent commits — this exact failure mode survived that work

## Notable WARNING themes (selected)

- **Symlink/path-traversal TOCTOU at tool-handler boundary** — `runtime` WR-02 (classify-then-write race), `overlay` WR-07 (absolute / `..`-escaping symlink targets preserved into upperdir), `command_exec` WR-02 (rmtree of `upperdir`/`workdir` with only an emptiness check)
- **Env-var leakage / injection** — `runtime` WR-04 (NUL bytes and `=` in env keys not rejected), `command_exec` WR-01 (caller-supplied `LD_PRELOAD`/`PATH`/`PYTHONPATH`/`BASH_ENV` override host env), `overlay` CR-01 (host env leak)
- **Unbounded reads / response size** — `runtime` WR-01 (out-of-workspace `read_text` unbounded → OOM), `plugin` WR-02 (sandbox-controlled JSON payloads unbounded), `provider` WR-06 (`_paginate_all` no `total_pages` cap)
- **Concurrent-host / multi-worker races** — `host` WR-03 (concurrent runtime-bundle uploads corrupt tarball), `runtime` WR-05 (`fence_stale_staging` rmtrees concurrent daemon's staging on restart race), `plugin` WR-01 (no async lock on `plugin_ensure`)
- **Non-idempotent retry** — `host` WR-01 (daemon-client retry can double-execute `api.shell`/`api.write_file`/`api.edit_file` on substring-classifier false positive)
- **Idempotency / dead writes** — `overlay` WR-04 (subprocess timeout not caught), `host` WR-02 (dead writes in `recovery.ensure_running`), `runtime` WR-07 (dead `error_holder` branch in `async_bridge`)

## Cross-cutting observations

1. **Crash-safety is uniformly absent in the persistence layer.** `layer_stack` and `occ` between them write manifests, layer blobs, workspace bindings, and base layers — none with fsync. This is the highest-priority systemic gap.

2. **The "trusted host" model is over-relied on at the runtime daemon.** Every BLOCKER in `runtime` is "buggy host crashes daemon" rather than "malicious peer escapes". Adding size caps, read timeouts, and structured error envelopes hardens against operational reality (oversized writes from agent loops), not just adversaries.

3. **Path/argv validation is inconsistent.** `host` uses `shlex.quote` rigorously and `provider` separates trusted/untrusted args by contract, but `command_exec` (BL-01), `runtime` (WR-04 env, WR-08 argv length), and `plugin` (WR-09 unvalidated `plugin_name` flowing into `importlib.import_module`) each have a different gap.

4. **The dot-path normalization branch name was not a red herring** — `occ/layer/change.py:18-31` is correct (verified by the occ reviewer), but the surrounding hash/staging logic (occ BL-02) is where the latent defect lives.

5. **`overlay` subsystem scope mismatch noted by reviewer:** Code on disk is a `shutil.copytree`-backed merged view; the real OverlayFS mount / unshare path described in the brief does not exist yet. The reviewer named the divergence rather than fabricate findings; future kernel-mount work needs its own review pass.

## Recommended remediation order

1. Land `fsync` discipline in `layer_stack` (BL-01, BL-02, BL-06, BL-07) plus `LayerRef.path` validation (BL-03). These are mutually amplifying: a torn manifest with adversarial paths is exactly the scenario where missing validation becomes exploitable
2. Fix `command_exec` BL-01 (workspace boundary) and `overlay` CR-01 (host env leak) — both are sandbox-escape category
3. Harden `runtime` RPC (BL-01..BL-05) — all are quick to fix, all matter under operational load
4. Fix `occ` BL-01 (DirectMerge silent acceptance) and BL-02 (hash/content mismatch) — these break the public OCC contract callers rely on
5. Fix `provider` CR-01..CR-03 — these are the concrete shape of the documented Daytona scheduler-hang failure mode
6. Fix `plugin` BL-01 — small change, high operational value (no more host-restart-to-recover loop)
7. Address WARNING themes in order: symlink TOCTOU → env injection → unbounded reads → multi-worker races

## Notes

- Reviewers were instructed to score adversarially and not soften BLOCKER → WARNING; treat that bias when prioritizing
- Two reviewers (host, runtime) hit a hook that blocked Write inside their sandbox and returned findings inline; their reports were persisted by the orchestrator with their text verbatim
- This review's `files_reviewed` count includes the 2 sandbox-root files (`__init__.py`, `models.py`) folded into the api group, totaling 134; subsystem reports sum to 132 because they don't redundantly count root files
