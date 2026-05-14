# Sandbox Folder Reframe — Consensus Plan (Ralplan, Deliberate Mode)

**Target:** `/Users/yifanxu/machine_learning/LoVC/EphemeralOS/backend/src/sandbox/` (160 .py files, ~17.5k LOC, 12 top-level subfolders/folders, 469 fs entries including 7 empty skeleton subdirs and ~10 `.DS_Store`).

**Mode:** RALPLAN-DR deliberate (auto-enabled: refactor crosses 17k LOC of host+daemon code, breaks ≥81 import paths consumed by 127 files, touches the daemon entrypoint and runtime-bundle tar produced by `host/runtime_bundle.py`).

---

## 0. Hard constraint

**No behavior change.** Every external call site must observe the same runtime semantics. Renames are allowed; signatures and import paths are rewritten in lock-step across the repo via codemod.

---

## 1. Principles (5)

1. **Public API is rename-frozen.** `sandbox.api` (18 prod refs, including `live_e2e/squad/runner.py`, `task_center/entry/sandbox_bridge.py`, engine/factory, benchmarks) keeps every currently-exported symbol path. No re-export drift.
2. **External import-path stability dominates top-level layout.** Deployment boundary (host vs daemon) is the strongest *physical* axis, but five top-level namespaces have consumer-cost weight: `sandbox.api` (18 **prod** refs — must be rename-frozen; see Principle 1) and `sandbox.layer_stack`, `sandbox.occ`, `sandbox.plugin`, `sandbox.provider` (high test-ref counts of 46+, 61, 10, 9 respectively — codemod-rewritable but expensive). The plan keeps all five at top level to bound the codemod surface. We apply the host/daemon axis **only via subfolder placement and renames**, not as a top-level `_host/` vs `_daemon/` split. Where the axis is hidden by the current name (`runtime/` actually means "daemon-side"), we rename to expose it.
3. **No single-file subdirs; no fragmentation.** Any subfolder containing exactly one non-`__init__` `.py` file is flattened into its parent (`folder/single.py` → parent file `folder_single.py` or just `folder/.py` merged up). Empty/skeleton dirs are deleted outright. Two-file subdirs whose `<sub>/<name>` import path is externally referenced (`layer_stack.workspace.base`, `layer_stack.manifest.store`, `occ.content.hashing`, `occ.changeset.types`, etc.) are kept — flattening them would break the rename-frozen rule.
4. **SRP at file granularity, but no oversplit.** Concretely: collapse `overlay/{factory.py (13 LOC), invoker.py (172), command.py (97)}` triple = 282 LOC, three stages of one runtime call, into one module; collapse `api/default.py` + `api/defaults.py`. Leave files >150 LOC alone unless they have a verified duplicate (`invoker.py` is the exception because it's part of a verified 3-way duplicate).
5. **Maximize structural deletion before refactor, not after.** Delete dead skeleton folders, junk files, stale pycaches, dead `__init__.py` re-exporters, and unreferenced modules *first*. Consolidate after.

## 2. Decision Drivers (top 3)

1. **External breakage surface.** 127 consumer files, 81 unique import paths. `sandbox.api` (18), `sandbox.occ.changeset.types` (19), `sandbox.layer_stack.*` (46+), `sandbox.runtime.daemon.*` (43) dominate. Any reframe must codemod these atomically and run the test suite green between batches.
2. **Daemon runtime bundle.** `host/runtime_bundle.py` packages `runtime/daemon/` into a tar uploaded to the sandbox VM; `runtime/scripts/launch_daemon.sh` extracts and execs it as `python -m sandbox.runtime.daemon`. Renaming `runtime/` → `daemon/` changes the `-m` path and the tar root. Both must move in the same commit.
3. **Aggressive vs preserve-test-imports tension.** The user said "aggressive". The dependency map says ≥61 test references import from `sandbox.occ` and ≥46 from `sandbox.layer_stack`. The user must accept test-import churn or accept a less aggressive reframe.

## 3. Viable Options (with bounded pros/cons)

### Option A — **Concept-first reframe (RECOMMENDED)**
10 top-level dirs → 7 top-level dirs by merging on responsibility axis. Public `sandbox.api` is preserved; `sandbox.layer_stack`, `sandbox.occ`, `sandbox.plugin`, `sandbox.provider` keep their top-level position (high external traffic). `sandbox.runtime` collapses to `sandbox.daemon`; `sandbox.command_exec` + `sandbox.overlay` merge into `sandbox.execution`; `sandbox.host` is unchanged (host-side bootstrap glue is its own concern).

```
sandbox/
├── __init__.py
├── models.py        (kept – 6 ext refs; re-exported by api/)
├── timing.py        (kept – 3 ext refs)
├── daemon_paths.py  (kept – shared host↔daemon path constants)
├── api/             (PRESERVED – public verbs, request/result types)
│   ├── __init__.py            (re-export, unchanged surface)
│   ├── default.py             (host transport facade)
│   ├── _impl/                 (lifecycle, edit, read, write, shell, raw_exec, audit, classifiers, payload, results)
│   ├── protocol.py, transport.py, lifecycle.py, timeouts.py, preview_urls.py, discovery.py
├── host/            (PRESERVED – host-side bootstrap & RPC client)
│   ├── bootstrap.py, daemon_client.py, context_preparer.py, runtime_bundle.py, lifecycle.py
├── provider/        (PRESERVED – ProviderAdapter registry + daytona impl)
├── audit/           (PRESERVED – cross-cutting event publication; 4 ext refs)
├── layer_stack/     (PRESERVED – append-only storage; 46+ ext refs)
│   ├── manager.py, transaction.py, errors.py, protocols.py, _paths.py, _storage_lock.py
│   ├── lease.py             (FLATTENED – was lease/registry.py, 1 file)
│   ├── view.py              (FLATTENED – was view/merged.py, 1 file)
│   ├── maintenance.py       (FLATTENED – was maintenance/squash.py, 1 file)
│   ├── commit.py            (FLATTENED – was commit/commit_staging_area.py, 1 file)
│   ├── layer/               (KEPT – 4 files: index, change, publisher, __init__)
│   ├── manifest/            (KEPT – 9 ext refs to manifest.store/_model)
│   └── workspace/           (KEPT – 10 ext refs to workspace.base, 5 to workspace.binding)
├── occ/             (PRESERVED – write-gate policy; 61 ext refs)
│   ├── service.py, client.py, router.py, ports.py, overlay.py, maintenance.py, commit_queue.py, timing_keys.py
│   ├── changeset/           (KEPT – 19 ext refs to changeset.types, 9 to changeset.prepared)
│   ├── content/             (KEPT – 9 ext refs to content.hashing)
│   └── stage/               (KEPT – 4 files: direct, gated, policy, transaction)
├── execution/       (NEW – merge of command_exec/ + overlay/, both host-side execution)
│   ├── orchestrator.py        (was command_exec/executor.py)
│   ├── policy.py              (was command_exec/policy.py)
│   ├── entrypoints.py         (FLATTENED – was command_exec/entrypoints/namespace_helper.py, 1 file; `-m sandbox.execution.entrypoints` is the new `-m` target)
│   ├── contract/              (KEPT – 4 files: request, result, ports, spec; 8 ext refs)
│   ├── strategies/            (KEPT – 4 files: base, copy_backed, private_namespace, registry)
│   ├── workspace/             (KEPT – 4 files: capture, environment, mount, path_rewrite)
│   └── overlay/               (was overlay/: runner, capture, change, request, result, mounts, worker, cli)
│       └── pipeline.py        (NEW – factory.py + invoker.py + command.py merged; renamed from `runtime.py` to avoid confusion with top-level `daemon/` previously named `runtime/`)
├── plugin/          (PRESERVED – plugin author surface; daemon-side runtime nested)
└── daemon/          (was runtime/ – collapse one level; daemon-side only)
    ├── __main__.py            (was runtime/daemon/__main__.py)
    ├── async_bridge.py        (was runtime/async_bridge.py)
    ├── handler/, rpc/, service/, scripts/   (1:1 from runtime/daemon/...)
```

**Pros:** matches host/daemon physical axis (renames the misnamed `runtime/`); kills 7 dead skeleton dirs; merges 4 verified-duplicate files; keeps the five highest-traffic external paths (`api`, `occ`, `layer_stack`, `plugin`, `provider`) stable; the new `execution/` cluster collocates the host-side command orchestrator with its overlay primitive — one fewer top-level dir, peer-vs-layered relationship made structural. (Note: lease still lives at `layer_stack/lease.py` and OCC at top-level `occ/`, so the full lease→mount→overlay→capture→occ pipeline still spans 3 top-level dirs — `execution/` clusters orchestration+primitive, not the full pipeline.)

**Cons:** breaks `sandbox.runtime.daemon.*` (43 test refs), `sandbox.command_exec.*` (17 refs, all test), `sandbox.overlay.*` (6 refs); requires codemod of test imports.

### Option B — **Deployment-axis top-level (more aggressive, more churn)**
Top-level becomes `api/`, `_host/`, `_daemon/`, `plugin/`, `_shared/`. Every storage/occ/execution module moves under `_host/`. **Rejected** because `sandbox.occ.changeset.types` (19 ext refs) and `sandbox.layer_stack.*` (46+ ext refs) become `sandbox._host.storage.*` paths — massive churn that the user's "no fragmentation" goal does not require, and the underscore-prefix convention for top-levels is non-idiomatic Python.

### Option C — **Surgical cleanup only (rejected for "aggressive" mandate)**
Delete dead skeleton dirs + junk files + verified file merges, no folder reorganization. ~50–80 LOC saved, zero import-path churn. **Rejected** because the user explicitly said "aggressive reframe", "most LOC deletion", "no fragmentation of files scattering around". Surgical cleanup leaves `runtime/daemon/` nested-twice and the `command_exec`↔`overlay` adjacency unsolved.

### Invalidation rationale for B and C
B costs more than it buys: the deployment axis is already legible from `host/` vs `runtime/`+`plugin/` (after the rename to `daemon/`). C fails the user's explicit "aggressive" framing.

---

## 4. Plan: Execution in Atomic Waves (each wave commit-safe)

### Wave 0 — junk and dead skeletons (zero-risk, fast LOC win)
- Delete 7 empty skeleton dirs: `overlay/runner/`, `overlay/namespace/`, `overlay/capture/`, `occ/routing/`, `occ/merge/`, `occ/capture/`, `api/tool/` (the leaf `*.py` files `overlay/capture.py` and `overlay/runner.py` are real and stay — only the empty *subdir* siblings die).
- Delete 10× `.DS_Store`.
- Delete `layer_stack/IMPLEMENTATION_REPORT.md` (status report, not source).
- Delete stale `__pycache__/*.pyc` for files that no longer exist (`occ/commit_transaction`, `occ/result_projection`, `host/setup`, `host/context`, `host/git`, `host/recovery`, `layer_stack/filesystem`, `layer_stack/timing`).
- Drop empty `__init__.py`: `runtime/__init__.py` (0 LOC), `command_exec/workspace/__init__.py` (0 LOC), keep as empty marker files where Python needs them; tighten `audit/__init__.py` (3 LOC) only if no re-export break.

**Verification:** `make test` green; `python -c "import sandbox.api"` succeeds.

### Wave 1 — verified file merge (api shim only)
1. `api/defaults.py` (15 LOC) → inline into `api/default.py`. Update any callers.

(The `overlay/{factory,invoker,command}` trio merge was originally Wave 1 step 2 but is **folded into Wave 2** per Critic defect #6 — merging and moving in the same commit avoids touching the same file twice.)

**Verification:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` green; `python -c "import sandbox.api.default"`.

### Wave 1.5 — flatten single-file subdirs (LOC + fragmentation kill)
The user said "no fragmentation". Each subdir below has exactly one non-`__init__` file. The `<sub>/<name>` import-path namespace has **5 verified test consumers** (Architect-corrected count, original draft was wrong):

- `layer_stack/commit/commit_staging_area.py` → `layer_stack/commit.py` (drops `commit/__init__.py` 7 LOC)
- `layer_stack/lease/registry.py` → `layer_stack/lease.py` (drops `lease/__init__.py` 7 LOC). **2 test refs to rewrite**: `backend/tests/unit_test/test_sandbox/test_layer_stack/test_lease_registry.py`, `test_lease_pinning.py`.
- `layer_stack/maintenance/squash.py` → `layer_stack/maintenance.py` (drops `maintenance/__init__.py` 15 LOC). **1 test ref to rewrite**: `backend/tests/unit_test/test_sandbox/test_layer_stack/test_squash_gc.py`.
- `layer_stack/view/merged.py` → `layer_stack/view.py` (drops `view/__init__.py` 7 LOC). **Test ref(s) to rewrite via `test_import_fence.py`**.
- `command_exec/entrypoints/namespace_helper.py` → kept *inside* the merged `execution/` (Wave 3) as `execution/entrypoints.py` (drops `entrypoints/__init__.py` 5 LOC) — flatten *piggybacks on Wave 3*; no separate step here.

Total: 4 subdirs → 4 files, 4 `__init__.py` removed = 36 LOC removed + 4 fewer folders. Internal-only `__init__` re-exports rewired. **The codemod must rewrite the 5 test imports in the same commit** — verified by:
```
grep -rE "sandbox\.layer_stack\.(commit|lease|maintenance|view)\." backend/src backend/tests --include='*.py' | grep -v /sandbox/
# returns 5 hits today; must return 0 after wave.
```

**Verification:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_layer_stack -q` green; ruff clean.

### Wave 2 — `command_exec/` + `overlay/` → `execution/` + overlay-trio merge (large churn, **moderate** risk)
**Reordered before runtime rename** (per Architect): this exercises the AST codemod on a higher-volume but lower-blast-radius change before Wave 3's daemon-bundle-critical rename. **Includes the overlay/{factory,invoker,command} merge** that was originally Wave 1 step 2 (per Critic defect #6).

- Create `execution/`.
- Move `command_exec/executor.py` → `execution/orchestrator.py` (rename; verb-noun consistency).
- Move `command_exec/policy.py` → `execution/policy.py`.
- **Flatten** `command_exec/entrypoints/namespace_helper.py` → `execution/entrypoints.py` (single file, no subdir). The `-m` target becomes `sandbox.execution.entrypoints` (drop `.namespace_helper` suffix). The `if __name__ == "__main__":` block in `entrypoints.py` provides the entrypoint. This resolves the §3-tree-vs-Wave-2 inconsistency from iteration 1 and honors Principle 3 (no single-file subdirs).
- Move `command_exec/{contract,strategies,workspace}/` → `execution/{contract,strategies,workspace}/`.
- **Merge `overlay/{factory.py (13), invoker.py (172), command.py (97)}` = 282 LOC** into single new file `execution/overlay/pipeline.py` (target ≤200 LOC after dedup). Move the rest of `overlay/*.py` → `execution/overlay/` 1:1.
- AST codemod (**libcst `ImportFrom`-node-only**, never identifier or string-literal sweep — see §8 codemod-spec section):
  - `sandbox.command_exec` → `sandbox.execution`
  - `sandbox.overlay` → `sandbox.execution.overlay`
  - applied across `backend/src`, `backend/tests`, `backend/benchmarks`.
  - **Total rewrite surface: 209 import sites** (107 internal + ~102 test, verified by `grep -rE "(from sandbox\.(overlay|command_exec)\b|import sandbox\.(overlay|command_exec)\b)" backend --include='*.py' | wc -l`).
- `command_exec/__init__.py` and `overlay/__init__.py` re-export shims removed (no external pinning).
- **Explicit string-literal sites (manual edits, verified by line number):**
  - `command_exec/strategies/private_namespace.py:93` — `"sandbox.command_exec.entrypoints.namespace_helper"` (passed as `python -m` argv) → `"sandbox.execution.entrypoints"` (drops `.namespace_helper` suffix because the subdir is flattened to a single file).
  - `command_exec/__init__.py:24-37` — `_LAZY_EXPORTS` dict contains 3 string-literal module paths: `"sandbox.command_exec.workspace.capture"`, `"sandbox.command_exec.executor"`, `"sandbox.command_exec.workspace.mount"`. Update each; this file itself moves to `execution/__init__.py` (or is deleted if Wave 2 also kills the lazy-export pattern).
  - Decision: **prefer deleting the lazy-export dict** since the new `execution/` package can use direct imports — fewer indirections, simpler.
- **`overlay/cli.py` shim** (`python -m sandbox.overlay.cli`): **delete entirely** in Wave 2. Verified: 0 external invokers of this path in `backend/` (Critic flagged but the only call site is `overlay/__init__.py` re-export, also dropping). If ops/CI scripts rely on it, that's caught by the post-Wave-2 e2e gate.
- **Audit event names are CONTRACTS, not module paths — DO NOT RENAME.** Verified `audit/events.py` defines `OVERLAY_EXECUTED = "sandbox.overlay.executed"` (line 14) and similar constants. These are downstream consumer identifiers (log parsers, dashboards). Keep the string literals exactly as-is even though the Python module path changes. Add post-Wave-2 gate: `grep -n 'sandbox.overlay.executed' backend/src/sandbox/audit/events.py` must still match.
- **Logger-name strings** (`logging.getLogger("sandbox.command_exec.…")` if any): inspect — if used for log-filtering config, keep the string literal. Verified for command_exec: no hardcoded logger names found; safe.
- **Pre-commit grep gates:**
  ```
  grep -rn "sandbox\.command_exec" backend/ && exit 1                          # all paths rewritten
  grep -rn "sandbox\.overlay\b" backend/ | grep -v "sandbox.execution.overlay" | grep -v "audit/events.py" && exit 1  # except the audit-event contract
  grep -rn "overlay\.factory\|overlay\.invoker\|overlay\.command\b" backend/ && exit 1
  grep -n '"sandbox.overlay.executed"' backend/src/sandbox/audit/events.py || exit 1  # contract preserved
  ```

**Verification:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` green; `.venv/bin/pytest backend/tests/live_e2e_test/sandbox -q -k "smoke or roundtrip"` green; live_e2e shell + edit_file roundtrip.

### Wave 3 — `runtime/` → `daemon/` rename (1-folder, daemon-bundle-critical, **highest** risk)
- Move `runtime/` → `daemon/` (top-level). Subdirs preserved verbatim: `daemon/{__main__.py, async_bridge.py, handler/, rpc/, service/, scripts/}`.
- AST-aware codemod (libcst `ImportFrom`-only; **never** touch identifiers like `runtime_bundle_bytes` / `ensure_runtime_uploaded`, **never** string-literal substring sweep on the bare word `"runtime"`):
  - `sandbox.runtime.daemon` → `sandbox.daemon`
  - `sandbox.runtime.scripts` → `sandbox.daemon.scripts`
  - `sandbox.runtime.async_bridge` → `sandbox.daemon.async_bridge`
  - applied across `backend/src`, `backend/tests`, `backend/benchmarks` (68 test refs verified).
- Update **explicit string-literal sites** (manual, surgical — each verified by line number; codemod must NOT touch these):

  **Behavior-critical (daemon will fail to boot if missed):**
  - `daemon_paths.py:17` — `RUNTIME_SCRIPT_DIR = f"{BUNDLE_REMOTE_DIR}/sandbox/runtime/scripts"` → `f"{BUNDLE_REMOTE_DIR}/sandbox/daemon/scripts"`. **This was missed in iteration 1 and is the highest-risk omission** — host extracts tar to `/tmp/eos-sandbox-runtime/sandbox/daemon/scripts/` (per `runtime_bundle.py:113,182` updates below) but reads `DAEMON_LAUNCH_SCRIPT_PATH` from the old path → file-not-found → 300s `provider.create()` hang.
  - `host/runtime_bundle.py:113` — `sandbox_dir / "runtime" / "scripts"` → `sandbox_dir / "daemon" / "scripts"` (tar source-path for scripts).
  - `host/runtime_bundle.py:182` — `sandbox_dir / "runtime"` → `sandbox_dir / "daemon"` (tar `arcname` root for daemon module).
  - `host/runtime_bundle.py` — bundle hash cache key (invalidates on first deploy; one-time re-upload per running sandbox; document in commit message).
  - `host/daemon_client.py:331` — hardcoded `"sandbox.runtime.daemon"` `-m` literal → `"sandbox.daemon"`.

  **Identifier `RUNTIME_SCRIPT_DIR` (decision):** Keep the constant name as-is to avoid breaking `from sandbox.daemon_paths import RUNTIME_SCRIPT_DIR` consumers (verified callers in `runtime_bundle.py`). The constant *value* changes but the name doesn't. If preferred for naming hygiene, rename `RUNTIME_SCRIPT_DIR` → `DAEMON_SCRIPT_DIR` in a separate Wave 4b commit with codemod for the 2-3 consumers; **out of scope for Wave 3**.

  **Other daemon_paths.py constants (`runtime.sock`, `runtime.pid`, `runtime.log`, `runtime.env`):** These are *filenames* in `/tmp/eos-sandbox-runtime/`, not module paths. **Keep as-is** — renaming would force another bundle-hash invalidation for cosmetic gain. Already-running sandboxes that have these files don't care about the host module path.

  **CLI / argparse / docstring sites (cosmetic but trip the grep gate):**
  - `runtime/daemon/__main__.py:1` (module docstring) — `"""Entrypoint for ``python -m sandbox.runtime.daemon`` ..."""` → `python -m sandbox.daemon`.
  - `runtime/daemon/__main__.py:18` — `argparse.ArgumentParser(prog="sandbox.runtime.daemon")` → `prog="sandbox.daemon"` (affects `--help` output only).
  - `runtime/daemon/rpc/dispatcher.py:21` — `logging.getLogger("sandbox.runtime.daemon.rpc.dispatcher")` → `"sandbox.daemon.rpc.dispatcher"`.
  - `runtime/daemon/rpc/server.py:3,14` (module docstring + comment) — prose mentions of `python -m sandbox.runtime.daemon.rpc.dispatcher` and `sandbox.runtime.daemon.rpc.dispatcher` → update to `sandbox.daemon`.
  - `runtime/daemon/rpc/server.py:46` — `logging.getLogger("sandbox.runtime.daemon.rpc.server")` → `"sandbox.daemon.rpc.server"`.
  - `runtime/daemon/service/occ_backend.py:9` (module docstring) — `:mod:\`sandbox.runtime.daemon.handler.request_context\`` → `sandbox.daemon.handler.request_context`. (Note: lines 23-25 are real imports caught by libcst codemod.)
  - `runtime/async_bridge.py:257` (docstring) — `:mod:\`sandbox.runtime.async_bridge\`` → `sandbox.daemon.async_bridge`.
  - `plugin/install.py:48` (comment) — `# sandbox.runtime.daemon imports the runtime bundle...` → `sandbox.daemon imports the runtime bundle...`. Conceptual: the comment refers to the *runtime bundle payload*, not the module path; preferable to reword: `# the in-sandbox daemon imports the runtime bundle).`.
  - `sandbox/__init__.py:5` (package docstring, lines 1-15) — `"- ``sandbox.runtime.daemon`` — in-sandbox dispatcher..."` → update to `sandbox.daemon`. Also update the docstring's mention of "host, runtime daemon, and provider" to "host, daemon, and provider".
  - `runtime/daemon/handler/__init__.py` and `runtime/daemon/handler/request_context.py` — any remaining prose mentions of "runtime daemon" in docstrings (cosmetic).
  - **Decision on the term "runtime bundle":** Keep this term (identifiers like `runtime_bundle_bytes`, `ensure_runtime_uploaded`, `RuntimeBundle` class) as-is. "Runtime bundle" refers to the *payload* shipped to the sandbox, not the module path. Renaming would be cosmetic churn with no value.

  **Logger-name renames — observability impact:** Logger names `sandbox.runtime.daemon.*` → `sandbox.daemon.*` is a behavior-visible change to log channel hierarchy. Any external log config that filters on `sandbox.runtime.daemon` must switch. **Document this explicitly in the Wave 3 commit message** so on-call/ops are alerted.

  **Shell script:** `daemon/scripts/launch_daemon.sh` — script is parameterized via env vars (`MODULE`, `SOCKET`, etc.); verified no hardcoded `runtime/` paths. No edit needed.
- **Path-shaped grep gates** (path-shaped only — do NOT scan for bare `"runtime"`):
  ```
  # all "sandbox.runtime" occurrences across imports, docstrings, comments, log names — must be 0 after manual edits
  grep -rn "sandbox\.runtime" backend/ && exit 1
  # quote-bounded path-shaped string literals
  grep -rn '"sandbox/runtime"\|"sandbox\.runtime"' backend/ && exit 1
  # unquoted /sandbox/runtime path fragments (catches daemon_paths.py:17-style f-string interpolation)
  grep -rn '/sandbox/runtime\b\|sandbox/runtime/' backend/ && exit 1
  # tar-arcname Path / operator form
  grep -rn 'sandbox_dir / "runtime"' backend/ && exit 1
  # -m invocation
  grep -rn "python -m sandbox.runtime" backend/ && exit 1
  ```
  *Identifier names `runtime_bundle_bytes`, `ensure_runtime_uploaded`, `RuntimeBundle`, and the `RUNTIME_SCRIPT_DIR` constant name are kept as-is — they refer to the runtime-bundle payload concept, not the module path. These won't match the gates above because the gates require `sandbox.runtime` / `sandbox/runtime` / `-m sandbox.runtime` shape, not bare `runtime`.*
- Stale pycache cleanup before commit.

**Note on observable behavior:** Bundle hash cache invalidation will cause every running sandbox to re-upload the daemon bundle on first contact post-deploy (one-time perf hit, briefly higher latency on first call). This is the closest the plan comes to a behavior change; document it in the commit message.

**Verification:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` green; `.venv/bin/pytest backend/tests/live_e2e_test/sandbox -q -k "smoke or roundtrip"` green; **manual live_e2e** — run `backend/src/live_e2e/squad/runner.py` with the default provider for one sandbox lifecycle to confirm daemon boots inside the provider and answers an RPC. **Highest-risk wave**; do not proceed without the e2e green.

### Wave 4a — vulture / dead-symbol audit (clean blame, single commit)
- Run `vulture backend/src/sandbox --min-confidence 80`; review report; delete confirmed-dead symbols/functions/imports in **one commit**.
- **Whitelist intentionally-kept-but-vulture-flagged constants** (avoid accidental deletion of `RUNTIME_SCRIPT_DIR` and similar): use a `vulture_whitelist.py` in `backend/scripts/` listing names; pass `--min-confidence 80 backend/src/sandbox backend/scripts/vulture_whitelist.py`.
- Expected yield: ~50–200 LOC depending on what vulture surfaces.

**Verification:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` green; `.venv/bin/ruff check backend/src/sandbox` clean.

### Wave 4b — narration-comment compression (clean blame, separate commit)
- `host/bootstrap.py`: compress ~17 narration doc lines (≥3-line module docstrings + comment blocks that re-narrate WHAT the code does).
- `provider/daytona/adapter.py`: compress ~15 inline narration comment lines.
- `host/daemon_client.py`: compress ~12 doc lines.
- Confirm no `__pycache__` in committed tree (add to `.gitignore` if absent).

**Verification:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` green; final LOC report (`find backend/src/sandbox -name '*.py' -not -path '*/__pycache__/*' | xargs wc -l | tail -1`).

---

## 5. Pre-mortem (4 high-risk scenarios)

### Scenario A: Daemon fails to launch in production sandbox after Wave 3 (runtime→daemon rename)
**Cause:** Rename misses one of: tar `arcname` root in `host/runtime_bundle.py:182`, `-m` invocation in `host/daemon_client.py:331`, shell var in `launch_daemon.sh`, or a hidden string reference (`"runtime.daemon"` in a config). Daemon boot 500s in the sandbox VM.

**Mitigation:** Before commit, run **path-shaped** greps (NOT bare-word `"runtime"`, which mangles identifiers like `runtime_bundle_bytes`): see Wave 3 grep gates. Run live_e2e against a fresh provider sandbox (not local-mock) before merging Wave 3. Per project memory `daytona_pending_build_root_cause.md`, daemon-boot failures show up as 300s `provider.create()` hangs — set explicit timeout in the smoke test.

### Scenario B: Test suite spuriously fails because codemod mis-rewrites a string literal or comment
**Cause:** A regex codemod like `s/sandbox\.command_exec/sandbox.execution/g` rewrites the substring inside a test fixture or audit-event constant string, changing observed behavior.

**Mitigation:** Use AST-aware codemod (libcst or `python -m lib2to3`-style with `import_path_only` filter). Forbid substring rewrites in string literals. Run `git diff --stat` per wave and manually spot-check at least 3 files per category. Per memory `feedback_use_venv_pytest.md`: use `.venv/bin/pytest`, never global pytest.

### Scenario C: Performance regression on hot path
**Cause:** New `__init__.py` indirection in `execution/` adds startup cost; or a circular-import workaround introduces a lazy-import-at-call cost that hits `_commit_changes` (~0.65s) or `overlay_run` (~0.43s) per project memory `codeact_overlay_cost_breakdown.md`.

**Mitigation:** Capture baseline timings before Wave 0 from `live_e2e/squad/runner.py` (svc.cmd p50/p95 for a 10-command run). Re-capture after each of Waves 2/3/4a/4b. Fail the wave if p50 regresses >5%. Add `import time` smoke check at module load.

### Scenario D: Internal codemod miss inside sandbox/ siblings
**Cause:** An import like `from sandbox.overlay.factory import build_invoker` inside another sandbox/ file (e.g., `command_exec/executor.py`, `runtime/daemon/handler/overlay.py`, `runtime/daemon/service/shell_runner.py`) is missed by the codemod because it uses a relative form (`from ..overlay.factory import ...`) or because the codemod's regex matched only the absolute form. The sandbox package imports cleanly at module load but fails when the specific call site runs — only caught by integration/e2e tests, not import-smoke.

**Mitigation:**
1. Run codemod against **both** absolute and relative forms (libcst's `ImportFrom` node walks both).
2. After Wave 1, 1.5, 3 commits, grep for **every** removed/renamed module name as a substring across the **whole repo**: e.g., after merging `overlay/{factory,invoker,command}` → `overlay/pipeline.py`, `grep -rn "overlay\.factory\|overlay\.invoker\|overlay\.command\b" backend/` must return 0 hits. Treat any hit as a Wave-blocker.
3. After each wave, run the **integration test slice** (`pytest backend/tests/unit_test/test_sandbox -q` + `backend/tests/live_e2e_test/sandbox -q -k "smoke or roundtrip"`), not just import smoke. Integration tests execute call sites the import-smoke skips.

### Scenario E: Wave 7a fake-transport tests silently switch to real transport
**Cause:** `api/_impl/{read,write,edit}.py` consolidation into `_run_verb.py` preserves the entry-function `transport: SandboxTransport | None = None` kwarg, BUT the internal call sites that consume `transport` (now inside `_run_verb`) drift — e.g., `_run_verb` defaults to `DaemonSandboxTransport()` even when the entry function received an explicit `None`, OR a test passes `transport=MockTransport()` but `_run_verb` ignores the kwarg because of a parameter-passing bug. Tests appear to pass because the real Daytona path also produces the expected mocked result, but downstream production behavior diverges.

**Mitigation:**
1. Wave 7a's `_VerbSpec` must thread the `transport` kwarg verbatim from the entry function to `_run_verb`'s body — no defaults set inside `_run_verb`.
2. Add a test (Wave 7a commit) that asserts `_run_verb(spec, transport=sentinel)` invokes `sentinel.call(...)` exactly once. Make sentinel raise on any other call. If the wave's diff doesn't include this test, the codemod was incomplete.
3. Grep gate post-Wave-7a: `grep -rn "transport=DaemonSandboxTransport" backend/src/sandbox/api/_impl/` must show 0 hits (the default is now set only in the entry functions, not in `_run_verb`).

### Scenario F: Wave 7c Daytona dedup breaks sync/async cache isolation
**Cause:** `_acquire_cached_client(factory_cls)` helper shared between `async_client.py` and `sync_client.py` accidentally shares cache state across sync/async paths because the cache key omits the factory class identity. A sync caller and an async caller compete for the same cached client; the async caller receives a `Daytona` (sync) instance and calls `await client.x()` on it, which produces an awaitable from a non-async method or fails opaquely. Per project memory `daytona_pending_build_root_cause.md`, the symptom is a 300s `provider.create()` hang while the SDK tries to coerce types.

**Mitigation:**
1. Wave 7c's `_acquire_cached_client` MUST key the cache on `(factory_cls, credential_hash, target)` — never omit `factory_cls`. Add an assertion: `assert factory_cls in (Daytona, AsyncDaytona)`.
2. Add a regression test (Wave 7c commit) that constructs both a sync and an async client back-to-back, asserts they are not the same object, and that each has the correct concrete type.
3. Wave 7c's manual live_e2e must run **both** sync and async Daytona paths in the same process to expose any cross-contamination.
4. Per project memory, set explicit timeout on `provider.create()` in the smoke test; a >60s call indicates the bug.

### Scenario G: Wave 5b `occ_backend.py` god-file overflow
**Cause:** §14 pre-flight unexpectedly classifies all 3 unverified files (`result_projection.py`, `shell_runner.py`, `workspace_server.py`) as THIN, and the executor inlines all 441 LOC into `occ_backend.py`. The file balloons from 115 LOC to ~680 LOC — violates AC #11's 600 LOC cap. Reviewer rejects; rework needed mid-wave.

**Mitigation:**
1. Wave 5b's spec (§12) already requires splitting `occ_backend.py` if the projection exceeds 500 LOC. Re-affirm at pre-flight time: if §14 verdict is "inline all 3", the wave plan must include a 2nd commit splitting `occ_backend.py` BEFORE the inline commit lands.
2. Pre-flight script auto-computes projection: `wc -l occ_backend.py + <inlined-files> > 500 && echo "SPLIT REQUIRED"`.
3. AC #11 grep gate runs after Wave 5b and rejects any file >600 LOC.

---

## 6. Expanded Test Plan (deliberate mode)

**Verified test layout** (re-checked, was wrong in draft):
- `backend/tests/unit_test/test_sandbox/` — primary unit tests
- `backend/tests/live_e2e_test/sandbox/` — sandbox e2e
- `backend/tests/conftest.py`, `backend/tests/occ_change_helpers.py`, `backend/tests/support/`
- `backend/src/live_e2e/squad/runner.py` — manual e2e runner (45× referenced per project memory)

| Layer | Test | Run when |
|---|---|---|
| **Unit** | `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` (105+ test files) | Every wave |
| **Unit (import smoke)** | `python -c "import sandbox; import sandbox.api; import sandbox.layer_stack; import sandbox.occ; import sandbox.plugin; import sandbox.provider; import sandbox.execution; import sandbox.daemon; import sandbox.host; import sandbox.audit"` | Every wave |
| **Integration** | `.venv/bin/pytest backend/tests/live_e2e_test/sandbox -q -k "smoke or roundtrip"` plus targeted slices: `-k test_layer_stack`, `-k test_occ`, `-k test_daemon`, `-k test_command_exec`, `-k test_overlay`, `-k test_plugin` | Waves 1.5–4b |
| **E2E** | `backend/src/live_e2e/squad/runner.py` against real provider (Daytona default) — single sandbox, shell + read + write + edit roundtrip | Waves 2, 3, 4a, 4b |
| **Observability** | (a) **Folder count**: 10 top-level `.py`-containing subdirs → 9 (kill `runtime/`, merge `command_exec/`+`overlay/`→`execution/`); +4 single-file flattens in `layer_stack/` + 1 in `command_exec/entrypoints/`. (b) **File count**: 160 → ≤152 (`defaults.py` merge −1, `overlay/{factory,invoker,command}` → 1 file = −2, 5 flattened `__init__.py` removals −5; Wave 4a may add more). (c) **LOC** floor **≥165** — see §7 AC #5 for the anchored arithmetic (single source of truth). Stretch ≥250 with Wave 4a vulture. (d) **Hot-path timing** via `bench_sandbox_e2e.py` (10-command run, default provider): `svc.cmd` p50 within 5% of baseline. | Before Wave 0 and after Wave 4b |
| **Ruff/lint** | `.venv/bin/ruff check backend/src/sandbox` (project memory: use venv ruff, not global) | Every wave |

**Round-2 test-plan addendum** (covers Waves 5a–9):

| Layer | Test | Run when |
|---|---|---|
| **5a sync-drop** | `.venv/bin/pytest backend/tests -q -k "sync"` — any test still importing the deleted sync verbs surfaces here; ensure deleted tests are removed cleanly. | Wave 5a |
| **5b daemon/service inline** | After §14 pre-flight commit: `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_daemon -q` + `wc -l backend/src/sandbox/runtime/daemon/service/occ_backend.py` ≤ 500 LOC. If §14 inlines all 3 unverified files, also: `wc -l ... ≤ 600` mandatory. | Wave 5b |
| **5c contract collapse** | Import smoke for `sandbox.execution.contract.*` (Wave-2 path) and `sandbox.occ.changeset.*`; verified by `python -c "from sandbox.execution.contract import CommandExecRequest, CommandExecResult, MountMode, OCCMutationClient, SnapshotManifest, ShellProcessResult"`. | Wave 5c |
| **6 Protocol thinning** | Static-type check: `.venv/bin/mypy backend/src/sandbox` (if mypy in project) or `pyright backend/src/sandbox`. Removing Protocol declarations is duck-typed-safe but type-checked-fragile. | Wave 6 |
| **7a mock-seam preservation** | Targeted regression test added in Wave 7a commit (per Scenario E mitigation): `_run_verb(spec, transport=sentinel)` invokes `sentinel.call(...)` exactly once. `grep -rn "transport=DaemonSandboxTransport" backend/src/sandbox/api/_impl/` must return 0 hits. | Wave 7a |
| **7c Daytona dedup** | Targeted regression test added in Wave 7c commit (per Scenario F mitigation): construct sync + async clients back-to-back, assert distinct objects and correct types. **Manual live_e2e on real Daytona** with explicit `provider.create()` timeout. | Wave 7c |
| **8a api/* inlines** | Pre-wave grep: `grep -rE "from sandbox\.api\.(lifecycle\|transport\|discovery\|preview_urls\|timeouts)" backend/src --include='*.py' \| grep -v /sandbox/` — if hits exist, codemod required in-wave. | Wave 8a |
| **9 occ/stage dedup** | `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ -q` exercises both direct and gated stagers; both must pass identically post-extraction. | Wave 9 |

---

## 7. Acceptance Criteria (testable, falsifiable)

1. `make test` green on `main`-rebased branch (uses `.venv/bin/pytest` per project memory `feedback_use_venv_pytest.md`).
2. `live_e2e/squad/runner.py` completes 1 sandbox lifecycle end-to-end against the default provider with no behavioral diff vs baseline (svc.cmd output match, p50 within 5%).
3. Top-level under `sandbox/` is exactly: `api/`, `audit/`, `daemon/`, `execution/`, `host/`, `layer_stack/`, `occ/`, `plugin/`, `provider/`, plus root files `__init__.py`, `models.py`, `timing.py`, `daemon_paths.py`. **No `runtime/`, no `command_exec/`, no `overlay/` at top level. No empty skeleton subdirs anywhere in the tree. No 1-file subdirs in `layer_stack/` or `execution/`.**
4. `sandbox.api` public symbols (verified by `dir(sandbox.api)`) is a **superset** of the pre-refactor symbol list. No external prod consumer file (non-test) under `backend/src/live_e2e/`, `backend/src/task_center/`, `backend/src/engine/`, `backend/src/tools/`, or `backend/benchmarks/` needs an import-path change. Verified pre-flight: `grep -rE "from sandbox\.(command_exec|overlay|runtime)" backend/src --include='*.py' | grep -v /sandbox/` returns 0 hits today; must remain 0 after refactor.
5. **LOC deletion ≥165 LOC** from the sandbox tree (anchored arithmetic, single source of truth):
   - 36 LOC: 4 flattened `__init__.py` files (`layer_stack/commit/`, `lease/`, `maintenance/`, `view/` = 7+7+15+7).
   -  5 LOC: `command_exec/entrypoints/__init__.py` (folded into Wave 2 flatten).
   - ~80 LOC: overlay-trio merge (282 → ≤200 in `execution/overlay/pipeline.py`).
   - ~44 LOC: narration-comment compression in Wave 4b (`host/bootstrap.py` ~17 + `provider/daytona/adapter.py` ~15 + `host/daemon_client.py` ~12).
   - Sum = **165 LOC** floor.
   **Stretch ≥250 LOC** if Wave 4a's `vulture` pass surfaces additional dead symbols.
6. **File count reduction ≥8** (160 → ≤152: 1 defaults.py, 2 overlay compression, 5 flattened init files).
7. **Folder count reduction**: top-level subdirs 10 → 9; `layer_stack/` flattens 4 of its 1-file subdirs; sandbox-wide empty/skeleton dirs (7) all deleted. Net ≥12 fewer directories.
8. `git log --oneline` shows one commit per wave (Waves 0, 1, 1.5, 2, 3, 4a, 4b — 7 commits); each commit individually passes `.venv/bin/pytest backend/tests/unit_test/test_sandbox`.

---

## 8. Concrete Verification Steps (for executor)

### 8.1 Codemod tool (prep — runs once before Wave 1)

`backend/src/live_e2e/squad/runner.py` is a Python module (`MockSquadRunner` class), **not a CLI** (verified: 0 `argparse` usage). Use the existing live_e2e_test pytest invocations instead of fabricated CLI flags. Treat the runner as a library callable from a small driver script for timing benchmarks.

Write a single codemod script and commit it separately *before* Wave 1:

```
backend/scripts/codemod_sandbox_imports.py    # libcst-based, ImportFrom-node-only
```

Specification:
- Use `libcst` (Python `pip install libcst` — already in `pyproject.toml` or add it).
- Walks every `*.py` under `backend/`, parses to a CST, visits only `cst.ImportFrom` and `cst.Import` nodes.
- Accepts a rewrite map: `{"sandbox.command_exec": "sandbox.execution", "sandbox.overlay": "sandbox.execution.overlay", ...}` (passed via JSON/argv).
- **Never** touches `cst.SimpleString`, `cst.Name`, `cst.Attribute` outside of an `ImportFrom`/`Import` context.
- Per-wave run: produce `git diff --stat` as a dry-run, require human review of the file list (executor commits the codemod script separately, then runs it per wave with `--commit` to apply).

### 8.2 Baseline (run once, before Wave 0)

```bash
find backend/src/sandbox -name '*.py' -not -path '*/__pycache__/*' | xargs wc -l | tail -1     # baseline LOC (expect ~17,492)
find backend/src/sandbox -name '*.py' -not -path '*/__pycache__/*' | wc -l                      # baseline files (expect 160)
find backend/src/sandbox -type d -not -path '*/__pycache__*' | wc -l                            # baseline dirs
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q                                        # baseline unit pass count
.venv/bin/pytest backend/tests/live_e2e_test/sandbox -q -k "smoke or roundtrip"                 # baseline integration pass
```

For e2e timing baseline, write a small driver (`backend/scripts/bench_sandbox_e2e.py`) that imports `MockSquadRunner` and runs N synthetic shell commands against the default provider, capturing svc.cmd p50/p95. Commit it alongside the codemod script. Re-run identically before/after the refactor; compare JSON.

### 8.3 Per-wave gate (run after each wave commit, before push)

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
.venv/bin/ruff check backend/src/sandbox
python -c "import sandbox; import sandbox.api; import sandbox.layer_stack; import sandbox.occ; import sandbox.plugin; import sandbox.provider; import sandbox.execution; import sandbox.daemon; import sandbox.host; import sandbox.audit"

# Wave-2-specific gates (command_exec/overlay merge — runs FIRST per Architect reorder)
grep -rn "sandbox\.command_exec" backend/ && echo "FAIL: residual command_exec refs" && exit 1
grep -rn "sandbox\.overlay\b" backend/ | grep -v "sandbox.execution.overlay" && echo "FAIL: residual sandbox.overlay refs" && exit 1
grep -rn "overlay\.factory\|overlay\.invoker\|overlay\.command\b" backend/ && echo "FAIL: residual overlay-trio refs" && exit 1
.venv/bin/pytest backend/tests/live_e2e_test/sandbox -q -k "smoke or roundtrip"

# Wave-3-specific gates (runtime → daemon rename — highest-risk, runs SECOND)
grep -rn "sandbox\.runtime" backend/ && echo "FAIL: residual sandbox.runtime refs" && exit 1
grep -rn '"sandbox/runtime"\|"sandbox\.runtime"' backend/ && echo "FAIL: residual string-literal runtime refs" && exit 1
grep -rn '/sandbox/runtime\b\|sandbox/runtime/' backend/ && echo "FAIL: residual /sandbox/runtime path fragments (daemon_paths.py?)" && exit 1
grep -rn 'sandbox_dir / "runtime"' backend/ && echo "FAIL: residual tar-arcname runtime refs" && exit 1
grep -rn "python -m sandbox.runtime" backend/ && echo "FAIL: stale -m invocation" && exit 1
.venv/bin/python backend/scripts/bench_sandbox_e2e.py --commands 10 --report=after-wave3.json   # e2e + timing

# Final report (after Wave 4b)
find backend/src/sandbox -name '*.py' -not -path '*/__pycache__/*' | xargs wc -l | tail -1
.venv/bin/python backend/scripts/bench_sandbox_e2e.py --commands 10 --report=after.json
.venv/bin/python -c "import json; b=json.load(open('baseline.json')); a=json.load(open('after.json')); assert abs(a['svc_cmd_p50'] - b['svc_cmd_p50']) / b['svc_cmd_p50'] < 0.05, 'p50 regressed >5%'"   # must be within 5%
```

### 8.3.5 Per-wave gate template (covers Waves 5a–9 uniformly)

Round-1's §8.3 has explicit gates for Waves 0/2/3. Round-2 waves use a uniform template applied after each commit, with wave-specific additions from §6 Round-2 test-plan addendum and §12 wave-level Verification lines:

```bash
# Uniform Round-2 per-wave gate (run after each of 5a, 5b, 5c, 6, 7a, 7b, 7c, 8a, 8b, 8c, 8d, 9)
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
.venv/bin/ruff check backend/src/sandbox
python -c "import sandbox; import sandbox.api; import sandbox.layer_stack; import sandbox.occ; import sandbox.plugin; import sandbox.provider; import sandbox.execution; import sandbox.daemon; import sandbox.host; import sandbox.audit"
find backend/src/sandbox -name '*.py' -not -path '*/__pycache__/*' -exec wc -l {} \; | awk '$1 > 600 { print; ec=1 } END { exit ec }'   # AC #11

# Wave-specific gates appended per §6 Round-2 addendum + §12 Verification lines:
# Wave 5b: also run `wc -l backend/src/sandbox/runtime/daemon/service/occ_backend.py` and verify ≤ 500 (or split required).
# Wave 7a: also run the transport-sentinel regression test added in this commit.
# Wave 7c: also run real-Daytona live_e2e with provider.create() timeout (60s max).
# Wave 9: also run `.venv/bin/pytest backend/tests/unit_test/test_sandbox/test_occ -q`.
```

### 8.4 Behavioral-diff definition (clarifies AC #2)

"No behavioral diff" means **all** of the following hold after Wave 4b:
1. Every pytest case in `backend/tests/unit_test/test_sandbox` and `backend/tests/live_e2e_test/sandbox` that passed pre-Wave-0 passes post-Wave-4b. No skipped, no xfailed.
2. The e2e svc.cmd p50 from `bench_sandbox_e2e.py` (10-command run, same provider, same EOS_TIER_RUN_ID convention per project memory `eos_tier_run_id_artifact_stability.md`) is within 5% of pre-Wave-0 baseline.
3. For each tool in `{shell, read_file, write_file, edit_file, raw_exec}`: run identical input pre and post, byte-compare the `ShellProcessResult` / `WriteFileResult` / etc. fields (excluding any timing or path fields that mention `/sandbox/runtime/`). Equality must hold.
4. Audit-event stream from `audit/events.py` constants is unchanged: `grep -nE '^[A-Z_]+ = "' backend/src/sandbox/audit/events.py` produces the same list of constant=value pairs pre and post.

Failure of any of (1)–(4) blocks merge.

---

## 9. ADR — Final (consensus-approved iteration 1)

- **Decision:** Option A (concept-first 10→7 reframe with host vs daemon axis applied where it removes fragmentation, public `sandbox.api` rename-frozen).
- **Drivers:** External breakage surface (81 import paths), daemon runtime-bundle dependency, "aggressive but no behavior change" mandate.
- **Alternatives considered:** B (deployment-axis top-level — too much churn, non-idiomatic), C (surgical-only — fails user's "aggressive" framing).
- **Why chosen:** A delivers user's three asks (aggressive, no behavior infringement, max LOC deletion) while minimizing breakage outside tests.
- **Consequences:** **209 import-path rewrites** (107 internal + ~102 test refs, verified by repo grep), mostly mechanical via AST codemod; one highest-risk wave (Wave 3, runtime→daemon rename, touches tar bundle); breaks `sandbox.runtime`, `sandbox.command_exec`, `sandbox.overlay` namespaces (all test-only externally — verified 0 prod consumers). One-time observable: bundle hash cache invalidation on rename, brief re-upload on first contact per sandbox.
- **Follow-ups:** Post-merge, consider extracting `provider/daytona/` to its own pip-installable adapter (out of scope here); revisit whether `audit/` should move into `host/` once cross-cutting consumers are mapped.

---

# Round 2 — Expanded Scope: Code Reduction (≥1,200 LOC floor)

## 10. Round-2 framing

User asked for a **20% code reduction (non-docstring)** beyond the structural reframe, plus a guarantee that no file exceeds 600 LOC, plus an investigation into "unnecessary functions (used but actually not useful)". Four parallel evidence-grounded audits were run.

### Empirical findings (all evidence-cited; see audits in conversation history)

| Audit | Yield | Note |
|---|---|---|
| Dead code (strict, vulture@80% + ruff F401/F811/F841) | **3 LOC** | Only `OverlaySnapshotRunner.supports_sync`. Codebase passes vulture/ruff/pyflakes clean. |
| Dead code (test-only-reachable) | ~80 LOC src + ~80 LOC test | Sync API variants and a few helpers reachable only from tests. |
| Over-abstraction (Protocols/registries/factories) | ~650 LOC | Most Protocols have exactly 1 implementation across 160 files. |
| Wrapper/indirection (thin pass-throughs) | ~310 LOC | `api/lifecycle`, `service/layer_stack_client`, `api/transport`, etc. |
| Duplication (cross-file logic) | ~120 LOC | `direct.py`↔`gated.py` shared helpers + 2 file inlines. |
| **Total after overlap dedup** | **~1,000–1,200 LOC** | ~6–8% of non-doc LOC. **Not 20%.** |

**Code-only baseline (measured):**
- Total LOC = 17,492
- Docstring LOC = 1,013
- **Non-doc LOC ≈ 16,479** (denominator for 20% target)
- **20% = 3,295 LOC of code deletion**
- **Empirical refactor ceiling = ~1,200 LOC = 7.3%**

### Gap acknowledgment and decision

The 20% target **cannot** be achieved through pure refactor of `sandbox/`. The codebase is mature: vulture/ruff clean, no `if False:` blocks, no stale TODOs, no detected speculative-flexibility comments. Closing the gap requires **feature surface removal**.

**User-authorized cuts (selected from menu):**
- ✓ Drop sync API variants (~80 LOC)
- ✓ Inline 5 `daemon/service/` wrappers (~140 LOC)
- ✓ Collapse contract/changeset multi-file packages (~90 LOC)
- ✗ DO NOT collapse `provider/` registry (kept for future provider extensibility)
- ✗ DO NOT delete `plugin/runtime/registry.py` (kept for plugin extensibility)
- ✗ DO NOT delete `layer_stack/maintenance/squash.py` (kept for layer compaction)
- ✗ DO NOT pre-commit the deeper `occ/stage/{direct,gated}.py` merge (needs targeted re-audit before authorization)

**Gap policy:** Accept the realistic ceiling. Set AC target to the empirical floor (≥1,200 LOC). Document the 20%-gap explicitly in ADR.

## 11. Round-2 LOC budget (anchored, single source of truth)

| Source | LOC saved | Wave | Risk |
|---|---|---|---|
| Round-1 structural floor (§7 AC #5) | 165 | 0–4b | low |
| Drop sync API variants | 80 | 5a | low (test rewrites only) |
| Inline 2 confirmed-thin daemon/service wrappers (`layer_stack_client.py` 85 + `workspace_binding.py` 38 = 123 LOC); 3 additional candidates (`result_projection.py` 87, `shell_runner.py` 181, `workspace_server.py` 173) **gated by §14 blocking pre-flight** — likely KEEP all 3 (they are real logic, not pass-throughs) | 123 (firm) + 0-127 (§14-contingent) | 5b | medium (tightens daemon coupling for 2 confirmed; gated for the other 3) |
| Collapse contract/changeset multi-file pkgs | 90 | 5c | low (module overhead only) |
| Protocol thinning: `occ/ports.py`, `layer_stack/protocols.py`, `occ/client.py` `OccMutationService` | 150 | 6 | low (internal Protocols, 1 impl each) |
| `api/_impl/{read,write,edit}.py` quintet consolidation | 120 | 7 | medium (test-mock seams) |
| `daemon/handler/tools/{read,write,edit}.py` trio extraction | 60 | 7 | medium |
| Daytona client dedup + `shutdown.py` trim | 70 | 7 | low |
| `api/{lifecycle,transport,protocol,discovery,preview_urls}.py` inlines | 155 | 8 | medium (changes `sandbox.api.*` internal layout but not symbols) |
| `command_exec/strategies/registry.py` inline + bootstrap simplification | 45 | 8 | low |
| `rpc/dispatcher.py` `register_op` indirection cleanup | 30 | 8 | low |
| `occ/maintenance.py` `NoopMaintenancePolicy` inline | 10 | 8 | low |
| `occ/stage/{direct,gated}.py` shared `_apply_edit_content` + `_with_timings` + dead-Optional | 80 | 9 | medium |
| `overlay/factory.py` + `command_exec/workspace/capture.py` inlines + `invoker.py` re-sanitization cleanup | 44 | 9 | low |
| **Round-2 firm floor (if §14 keeps all 3 non-thin)** | **~1,222 LOC** | total | mixed |
| **Round-2 ceiling (if §14 inlines all 3)** | **~1,311 LOC** | total | mixed |
| Wave 4a `vulture` stretch yield | +50 to +200 | 4a | review-gated |

**Hard floor: ≥1,222 LOC** (Round-1 165 + Round-2 firm 1,057 with the 3 non-thin daemon/service files KEPT — the empirically expected outcome). If §14 pre-flight surfaces unexpected pass-through structure in those 3 files, the floor rises to ≥1,311 LOC.

**Realistic target: ~1,222 LOC ≈ 7.4% non-doc reduction** (denominator 16,479).
**Stretch: ~1,400 LOC** if Wave 4a's vulture pass surfaces post-merge orphans.
**20% gap: ~2,100 LOC short.** Documented; not achievable without further feature removal (which user declined).

**Contingent-floor rule:** AC #9 (the "≥1,200 LOC" criterion) is restated as: "≥1,222 LOC OR documented §14-driven shortfall accepted as an ADR §15 amendment." The user has authorized this contingency by selecting "Accept realistic ceiling".

## 12. Round-2 waves (atomic, each commit-safe, all preserve behavior)

### Waves 5a / 5b / 5c — User-authorized feature cuts (~290 LOC, each commits separately)
Each sub-wave below is a distinct atomic commit with its own verification gate.

### Wave 5a — Drop sync API variants (~80 LOC, +~80 LOC test cleanup):
- Delete: `occ/service.py::apply_changeset_sync` (14 LOC), `overlay/runner.py::shell_sync` + `supports_sync` (23 LOC), `layer_stack/workspace/binding.py::layer_path_from_{relative,absolute}` (24 LOC), `plugin/session.py::reset_session_cache` (6 LOC), `occ/content/gitignore_oracle.py::PathspecGitignoreOracle.filter_ignored` line-197 variant (3 LOC), `layer_stack/manager.py::publish_changes` (10 LOC).
- Delete corresponding test files: `test_workspace_binding.py`'s sync-variant tests, `test_plugin_session.py::reset_session_cache` test, `live_e2e_test/sandbox/overlay/native/*` (3 files, ~80 LOC test code), `test_layer_stack/test_publish_changes.py` parts that don't apply to the transaction wrapper.

### Wave 5b — Inline confirmed-thin `runtime/daemon/service/*.py` wrappers (123 LOC firm; up to +127 §14-gated)
**Blocking pre-flight (§14): produce `.planning/wave-5b-preflight.md`** classifying each of the 3 unverified files. Wave 5b cannot commit until that report exists. Commit the pre-flight script + report as a prep step before Wave 5b's primary commit.

**Confirmed-thin (commit unconditionally):**
- `service/layer_stack_client.py` (85 LOC) → fold `RuntimeLayerStackClient` into `service/occ_backend.py::build_occ_backend()` (which is the only constructor); the 8 forwarding methods become direct `LayerStackManager.X` calls.
- `service/workspace_binding.py` (38 LOC) → fold `RuntimeWorkspaceBindingReader.require_workspace_binding` into `service/occ_backend.py` (its only caller).

**§14-gated (commit only if pre-flight classifies as THIN):**
- `service/result_projection.py` (87 LOC) → likely **KEEP** (Architect-verified: real logic, not pass-through to `occ.client::project_changeset`). Inline only if §14 report says THIN.
- `service/shell_runner.py` (181 LOC) → likely **KEEP** (Architect-verified: 181 LOC of arg validation + command_exec dispatch glue). Inline only if §14 report says THIN.
- `service/workspace_server.py` (173 LOC) → likely **KEEP** (Architect-verified: 173 LOC, owns `get_layer_stack_manager()` singleton + setup). Inline only if §14 report says THIN.

**Post-Wave-5b file-size check:** `occ_backend.py` (current 115 LOC) + 85 + 38 = projected ~238 LOC; under 600. If §14 inlines all 3 additional files (worst case, +441 LOC), `occ_backend.py` would balloon to ~680 LOC — **over the 600 cap**. In that case the wave **must split occ_backend.py into 2 modules** before commit, or the inlines redirect to handlers instead of `occ_backend.py`.

### Wave 5c — Collapse contract/changeset multi-file packages (~90 LOC):
- `command_exec/contract/{request,result,ports,spec}.py` (4 files, ~274 LOC total) → 1 file `execution/contract.py` (after Wave 2 move), ~220 LOC after dedup. Saves the 4 `__init__.py` re-export shims + module-header boilerplate. **NOTE: 19 ext refs to `sandbox.occ.changeset.types` and 9 to `sandbox.occ.changeset.prepared` exist — the test-side rewrites are included in the Wave 2 codemod's 209-import budget.** Wait — verified: Wave 2 codemod handles `sandbox.command_exec.contract.*`. After collapse, the new path is `sandbox.execution.contract`. Test imports rewrite to the new path.
- `occ/changeset/{builders,prepared,types}.py` (3 files, ~439 LOC total) → 2 files `occ/changeset/types.py` (model + builders) + `occ/changeset/prepared.py` (kept as-is since 9 ext refs). Saves ~40 LOC of module overhead.

**Verification:** `.venv/bin/pytest backend/tests/unit_test/test_sandbox -q` green; integration tests green; `.venv/bin/python backend/scripts/bench_sandbox_e2e.py --commands 10 --report=after-w5.json` shows p50 within 5% of baseline.

### Wave 6 — Internal Protocol thinning (~150 LOC, pure refactor, low risk)
Protocols where exactly 1 concrete implementation exists internally. Replace `Protocol` declarations with `TYPE_CHECKING` imports of the concrete classes. **No external behavior change; no `sandbox.api` impact.**

- `occ/ports.py` (~95 LOC): 6 narrow Protocols (`SnapshotReader`, `CommitStagingStore`, `CommitTransactionPort`, `CommitPublisher`, `OccLayerStackPort`, `WorkspaceBindingReader`). All back to `LayerStackManager` or `RuntimeWorkspaceBindingReader`. Keep `WorkspaceBindingSnapshot` dataclass. **Save ~65 LOC.**
- `layer_stack/protocols.py` (~77 LOC): 5 internal-collaborator Protocols (`ManifestStore`, `SnapshotMaterializer`, `ChangePublisher`, `LeaseStore`, `CommitStagingStore`). Single impl each. **Save ~60 LOC.**
- `occ/client.py::OccMutationService` Protocol (~25 LOC): single backend `OccService`. Inline. **Save ~25 LOC.**

**Verification:** unit tests + ruff clean.

### Waves 7a / 7b / 7c — Structural wrapper consolidation (~250 LOC, each commits separately)
Test-mock-seam risk (7a) and network-adjacent risk (7c) require independent commits.

### Wave 7a — `api/_impl/{read,write,edit}.py` consolidation (~120 LOC, medium risk: test-mock seams)
- 3 modules (68 + 46 + 57 = 171 LOC) collapse into one `api/_impl/_run_verb.py` of ~50 LOC. Each verb registers via a dataclass: `_VerbSpec(op_name, timeout, payload_builder, result_projector, optional_conflict_classifier)`.
- **Preserve test-mock seam:** `read.py`, `write.py`, `edit.py` shrink to ~10 LOC each but **keep their public entry-function signature with `transport: SandboxTransport | None = None` kwarg** so tests injecting fake transports continue to work. The entry function just builds the spec and calls `_run_verb(spec, transport=transport, ...)`.
- **Keep `shell.py` (102 LOC) and `raw_exec.py` (39 LOC) separate** — `shell.py` does stdin pre-check + timing; `raw_exec.py` bypasses transport. Not parameterizable.

**Verification:** unit tests + targeted run of tests that inject fake transports: `grep -rn "transport=.*Mock\|transport=.*Fake" backend/tests --include='*.py' | head` — every hit must still pass.

### Wave 7b — `daemon/handler/tools/{read,write,edit}.py` trio extraction (~60 LOC, low risk)
- 3 files share `_with_snapshot_lease()` async context manager pattern (used 3×) and `_classify_and_dispatch()` skeleton.
- Extract both helpers to `daemon/handler/tools/_common.py` (new file, ~30 LOC). Each tool handler shrinks by ~20 LOC.

**Verification:** unit tests + integration.

### Wave 7c — Daytona client dedup + shutdown trim (~70 LOC, medium risk: network-adjacent)
- Extract `_acquire_cached_client(factory_cls)` helper shared between `async_client.py` and `sync_client.py` (~60 LOC of duplicated credential load + cache key + stale shutdown logic).
- Compress `shutdown.py` (91 LOC) → ~35 LOC by unifying the 3 close paths.

**Verification:** unit tests + integration; **manual live_e2e against real Daytona** (highest-risk in Round 2 for provider behavior) — verify sandbox create/destroy/shutdown lifecycle survives the dedup. Per project memory `daytona_pending_build_root_cause.md`, Daytona connection bugs surface as 300s hangs; set explicit timeout in the smoke test.

### Waves 8a / 8b / 8c / 8d — Indirection inlines (~240 LOC, each commits separately)
Per AC #13 styling consistency with Waves 5/7.

### Wave 8a — `api/{lifecycle,transport,protocol,discovery,preview_urls,timeouts}.py` inlines (~155 LOC, medium risk)
- `api/lifecycle.py` (61 LOC): 6 of 7 functions are bare delegations to `host/lifecycle.py`. Inline at the `api/default.py` call site. **Save ~45 LOC.**
- `api/transport.py` (54 LOC) + `api/protocol.py::SandboxTransport` Protocol (~22 LOC) → drop both; merge `versioned_payload()` into `host/daemon_client.py`. Replace Protocol with `Callable[..., Awaitable[dict]]` type alias. **Save ~60 LOC.**
- `api/discovery.py` (39 LOC) + `api/preview_urls.py` (21 LOC) → consolidate with `api/lifecycle.py` remainder into `api/_control.py` (~50 LOC total). **Save ~25 LOC.**
- `host/lifecycle.py::bootstrap_in_sandbox_runtime` (20 LOC pure log wrapper) → inline at 2 callers. `ensure_sandbox_running` alias → drop. **Save ~25 LOC.**

**Verification:** unit tests + import-smoke (`sandbox.api.lifecycle` is consumed externally — `grep -rn "from sandbox.api.lifecycle" backend --include='*.py' | grep -v /sandbox/`; if hits exist, add a Wave 2-style codemod step). **Pre-Wave-8a hard check:** if any prod (non-test) external consumer imports `sandbox.api.lifecycle` symbols directly, Wave 8a must include a Wave 2-equivalent codemod for those callers, or the wave is split to preserve the public surface.

### Wave 8b — `command_exec/strategies/registry.py` inline (~45 LOC, low risk)
- 2-strategy `StrategyRegistry.bootstrap()` → inline as a 4-line tuple in `workspace/mount.py`. The `is_available(mode)` helper is used in 1 non-test site; inline.

**Verification:** unit tests; ruff clean.

### Wave 8c — `rpc/dispatcher.py` `register_op` cleanup (~30 LOC, low risk)
- Inline the 20-op `OP_TABLE` dict directly; keep `register_op` only for the plugin pipeline (per user: plugin extensibility is kept).

**Verification:** unit tests; integration test `test_daemon` slice.

### Wave 8d — `occ/maintenance.py::NoopMaintenancePolicy` inline (~10 LOC, low risk)
- Replace `self._maintenance = maintenance or NoopMaintenancePolicy()` with `if self._maintenance is None: return {}` guard.

**Verification:** unit tests; `test_occ` slice.

### Wave 9 — occ/stage shared logic + small inlines (~125 LOC, low risk)

**9a. Shared logic between `occ/stage/direct.py` and `gated.py` (~80 LOC):**
- Extract `_apply_edit_content` (~30 LOC duplicated) to `occ/stage/_edit.py`. Both stagers call it.
- Move `_with_timings` (~6 LOC duplicated) to `occ/stage/policy.py`.
- Collapse `stage_write_from_path: StageWriteFromPath | None = None` dead-Optional and the conditional branches in `direct._stage_group` + `gated._delta_for_final_state` (~30 LOC).

**9b. Small file inlines (~44 LOC):**
- `overlay/factory.py` (13 LOC) inline into `overlay/runner.py` (its only caller).
- `command_exec/workspace/capture.py` (34 LOC) inline into `command_exec/executor.py` (its only caller).
- `overlay/invoker.py:97-105` re-sanitization block + speculative comment removal (~6 LOC).

**Note:** The deeper `occ/stage/{direct,gated}.py` merge (potential 200–300 LOC, advisor-flagged) requires a separate targeted audit. NOT included in this wave; user did not authorize. Listed as a follow-up.

**Verification:** unit tests; `pytest backend/tests/unit_test/test_sandbox/test_occ -q` exercises the staging dedup.

## 13. Updated Acceptance Criteria (additions to §7)

Append the following criteria; original §7 #1–#8 still apply:

9. **LOC deletion ≥1,222 LOC** from the sandbox tree (Round-1 floor 165 + Round-2 firm 1,057, assuming §14 pre-flight keeps the 3 non-thin daemon/service files). Single source of truth: §11 LOC budget table. **Contingent floor: if §14 keeps all 3 non-thin files (the empirically expected outcome — `result_projection.py` 87 LOC, `shell_runner.py` 181, `workspace_server.py` 173 are not thin per Architect verification), AC #9 is met at 1,222 LOC.** If §14 inlines any of them, floor rises proportionally.
10. **Realistic target: ≥1,222 LOC ≈ 7.4% of non-doc LOC** (denominator 16,479; matches §11 single source of truth). The 20% target is **explicitly waived** by user gap-policy decision; documented in ADR §15.
11. **No file in `backend/src/sandbox/` exceeds 600 LOC** after all waves. Verify: `find backend/src/sandbox -name '*.py' -not -path '*/__pycache__/*' -exec wc -l {} \; | awk '$1 > 600 { print; ec=1 } END { exit ec }'` must succeed (exit 0). **Current largest file is `layer_stack/manager.py` at 328 LOC.** Other notable: `audit/translation.py` 234, `daemon/handler/tools/edit.py` 236. **Highest projected post-merge risk: `runtime/daemon/service/occ_backend.py`** — Wave 5b's 2 confirmed inlines bring it from 115 LOC to ~238 LOC. If §14 inlines all 3 unverified files, it would balloon to ~680 LOC (over cap) — Wave 5b spec requires splitting `occ_backend.py` in that case.
12. **No file >500 LOC may be produced by any wave inline without justification.** If a wave inline produces a file >500, the wave must be split or the file restructured in a follow-up commit before merge.
13. **Wave count: 16 named waves** (0, 1, 1.5, 2, 3, 4a, 4b, 5a, 5b, 5c, 6, 7a, 7b, 7c, 8, 9). Wave 8 commits as 4 atomic sub-commits (8a `api/lifecycle/transport/protocol/discovery/preview` inlines; 8b `command_exec/strategies/registry.py` inline; 8c `rpc/dispatcher.py register_op` cleanup; 8d `occ/maintenance.py NoopMaintenancePolicy` inline). Each named wave (and each Wave 8 sub-commit) passes its verification gate before push.
14. **Authorized feature cuts only.** Verify no unauthorized feature removed by scoping greps to **definition sites in prod source** (excludes comments/docstrings/tests):
   - `grep -rE '^class ProviderAdapter\b|^class DaytonaProviderAdapter\b' backend/src/sandbox --include='*.py'` must return ≥2 hits (Protocol + impl preserved).
   - `grep -rE '^def register_op\b' backend/src/sandbox/runtime --include='*.py' || grep -rE '^def register_op\b' backend/src/sandbox/daemon --include='*.py'` (post-Wave-3 path) must return ≥1 hit.
   - `grep -rE '^def.*squash|^class.*Squash' backend/src/sandbox/layer_stack --include='*.py'` must return ≥1 hit.

## 14. Wave 5b pre-flight investigation — **BLOCKING DELIVERABLE**

Three of the 5 daemon/service inlines (`result_projection.py` 87 LOC, `shell_runner.py` 181 LOC, `workspace_server.py` 173 LOC) were named by the wrapper audit but **Round-2 Architect verified they are NOT thin** — they contain real validation, transformation, and state-management logic. Defaulting them to KEEP unless pre-flight proves otherwise.

**Before Wave 5b commit, the executor MUST commit a separate prep commit containing `.planning/wave-5b-preflight.md`** — a 1-page report classifying each of the 3 files as either THIN (suitable to inline) or REAL-LOGIC (KEEP). The report's format:

```markdown
# Wave 5b Pre-Flight Report
## result_projection.py (87 LOC) — verdict: [THIN / REAL-LOGIC]
- One-paragraph summary of what the file owns.
- Functions: list each with LOC + 1-line description.
- Verdict rationale: [is every function a 1-line delegation OR does any contain branching/validation/state?]
- If THIN: inline target (which handler/service file absorbs it).
- If REAL-LOGIC: keep at current location; LOC budget unchanged.
## shell_runner.py (181 LOC) — verdict: [THIN / REAL-LOGIC]
... (same template)
## workspace_server.py (173 LOC) — verdict: [THIN / REAL-LOGIC]
... (same template)

## LOC budget impact
| File | Pre-flight verdict | LOC delta |
| ... |
**Wave 5b firm LOC: 123 (confirmed) + N (gated) = X.**
```

**Wave 5b's primary commit is BLOCKED until `wave-5b-preflight.md` is committed.** AC #9 floor of 1,222 LOC assumes the empirically-expected outcome (all 3 KEPT). The pre-flight either confirms the floor or triggers an ADR amendment if it surfaces unexpected pass-through structure.

**Enforcement mechanism (git-checkable, not advisory):**
1. **Pre-flight commit must touch `.planning/wave-5b-preflight.md`** and have commit message starting with `wave-5b-preflight: ...`.
2. **Wave 5b primary commit must reference the pre-flight commit** in its message body: `Refs: <pre-flight-sha>`.
3. **Pre-commit hook** at `backend/scripts/hooks/check_wave5b_preflight.sh` (committed alongside the codemod script in §8.1) verifies: if branch is `codex/sandbox-reframe` and HEAD-1 message doesn't contain `wave-5b-preflight:` and HEAD touches `runtime/daemon/service/`, reject with exit 1. If the project doesn't use pre-commit hooks, run as a manual CI step before merging.
4. **CI grep gate** (added to `.github/workflows/` if present): `git log --oneline | grep -q "wave-5b-preflight:" || (echo "FAIL: Wave 5b primary commit landed without pre-flight" && exit 1)`.

If the project has neither pre-commit hooks nor CI, the executor commits **a single squash branch** where pre-flight and Wave 5b's primary commit appear in correct order; the reviewer's manual gate is reading `wave-5b-preflight.md` content before approving the PR.

This investigation is part of the executor's **mandatory prep**. The Round-2 Architect approved this gating; the enforcement chain above makes it git-checkable rather than self-attested.

## 15. ADR — Round 2 supplement

- **Decision:** Extend the round-1 reframe with 5 additional waves (5–9) targeting ~1,057 LOC of refactor-driven reduction, on top of the 165-LOC round-1 floor.
- **Drivers:** User asked for 20% non-doc code reduction. Empirical audits (4 parallel) show ceiling is ~1,200 LOC = 7.3% under pure refactor. User declined to authorize the high-impact feature cuts (plugin registry, squash policy, deeper occ/stage merge) needed to bridge to 20%.
- **Alternatives considered:**
  - Push harder to 20% (squeeze every option, brittle plan) — **rejected** by user.
  - Expand denominator beyond `sandbox/` — **rejected** by user.
  - Accept realistic ceiling and document gap — **chosen**.
- **Authorized cuts:** sync API variants, 5 daemon/service inlines, contract/changeset 4→1 + 3→2 collapse.
- **Explicitly rejected cuts (kept for future capability):** provider registry, plugin runtime registry, layer_stack squash policy, deeper `occ/stage` merge.
- **Consequences:**
  - **Final achievable target ≈ 1,222 LOC ≈ 7.4% non-doc reduction** (firm floor, single source of truth = §11). Stretch ceiling with vulture pass: ~1,400 LOC ≈ 8.5%. Not 20%.
  - 12-wave execution sequence; each commit-atomic.
  - Round-2 waves don't move folders; they delete/inline within the round-1 target tree, so all round-1 codemod budget (209 imports) is unchanged.
  - Test-mock seams in `api/_impl/*` may need rework if tests inject fake transports (Wave 7a).
  - Sync API removal forces affected tests to be rewritten async or deleted.
- **Follow-ups:**
  - **Deeper `occ/stage/{direct,gated}.py` merge** — flagged by advisor as potentially 200–300 LOC win (vs. current Wave 9's ~80 LOC). Requires targeted re-audit + user re-authorization for the policy-merging risk. Recommend as a separate PR after the 12-wave sequence lands.
  - If 7.4% (or stretch ~8.5% with vulture) proves insufficient for the user's downstream goal, the conversation should restart with feature-removal trade-offs (e.g., remove plugin extensibility = ~226 LOC, or remove `provider/` registry = ~150–200 LOC).
