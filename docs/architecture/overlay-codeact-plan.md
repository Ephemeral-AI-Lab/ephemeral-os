# Overlay CodeAct Auditor — Design Plan

Status: proposed
Replaces: `docs/architecture/git-worktree-codeact-migration.md`
Companion to: `docs/architecture/git-workspace-codeact.md`

## Why

Git-worktree auditor works for source edits but breaks on real CodeAct workloads:

- `pip install`, `npm install`, build artifacts → gitignored → invisible in worktree slot
- Slot rebuilt without gitignored files each call → installed deps don't persist
- Installs that do get captured → huge OCC batches (10k+ files), binary-content failures

Overlay + bind-mount lowerdir + sandbox-side diff classifier solves all three.

---

## 1. High-level architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Daytona Sandbox                               │
│                                                                      │
│   Live workspace: /home/daytona/workspace                            │
│     ├── src/*.py           (tracked source)                          │
│     ├── .venv/             (gitignored — installed deps)             │
│     ├── node_modules/      (gitignored)                              │
│     ├── __pycache__/       (gitignored — caches)                     │
│     ├── .git/                                                        │
│     └── .gitignore                                                   │
│                                                                      │
│   Per CodeAct op:                                                    │
│   ┌────────────────────────────────────────────────────────────────┐ │
│   │  unshare -Urm (user + mount namespace)                         │ │
│   │                                                                │ │
│   │   tmpfs ┌─────────────── upperdir  (writes land here)          │ │
│   │        │                                                       │ │
│   │   bind ┌─────────────── lowerdir   (live workspace)            │ │
│   │        │                                                       │ │
│   │   overlay mount (userxattr)                                    │ │
│   │        │                                                       │ │
│   │        ▼                                                       │ │
│   │   merged view  ◀── user command reads/writes here              │ │
│   │        ▲                                                       │ │
│   │        │ bind-mount --bind merged $repo_root                   │ │
│   │        │                                                       │ │
│   │   $repo_root (user command sees this as cwd)                   │ │
│   │                                                                │ │
│   └────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│   After command exits (inside ns):                                   │
│     [diff script walks upperdir, classifies each entry]              │
│     [emits NDJSON to container-fs run_dir]                           │
│                                                                      │
│   [namespace exits → tmpfs gone → upperdir destroyed]                │
└──────────────────────────────────────────────────────────────────────┘
             │                                      │
             │ NDJSON (OCC payload only)            │
             ▼                                      │
┌──────────────────────────────────────────────────────────────────────┐
│                        Orchestrator                                  │
│                                                                      │
│   Parse NDJSON → OperationChange[] (strict_base=True)                │
│         │                                                            │
│         ▼                                                            │
│   WriteCoordinator.commit_operation_against_base(...)                │
│         │                                                            │
│         ▼                                                            │
│   [OCC re-reads live, compares hash to base_hash]                    │
│   match → commit to live workspace                                   │
│   mismatch → abort, no partial state                                 │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Per-op lifecycle

```
 Orchestrator                 Sandbox                         Sandbox (inside ns)
      │                          │                                   │
      │── acquire semaphore ─────│                                   │
      │                          │                                   │
      │── SNAP = git commit-tree ─►                                  │
      │    (dangling commit,     │                                   │
      │     no ref moved)        │                                   │
      │◄─ SNAP SHA ──────────────│                                   │
      │                          │                                   │
      │── exec overlay bash ─────►                                   │
      │                          │── unshare -Urm ──────────────────►│
      │                          │                                   │── mount tmpfs /ns
      │                          │                                   │── mount --bind $workspace /ns/lower
      │                          │                                   │── mount -t overlay ... userxattr /ns/merged
      │                          │                                   │── mount --bind /ns/merged $repo_root
      │                          │                                   │── cd $repo_root
      │                          │                                   │── bash -c "$user_command"
      │                          │                                   │     │ reads: pass-through to lower
      │                          │                                   │     │ writes: copy-up to upper
      │                          │                                   │     ▼
      │                          │                                   │── python diff_script.py $ns/upper $SNAP
      │                          │                                   │     │ walks upperdir
      │                          │                                   │     │ classifies each entry (git check-ignore)
      │                          │                                   │     │ direct-merges ignored → live workspace
      │                          │                                   │     │ emits NDJSON for tracked → $run_dir/diff.ndjson
      │                          │                                   │── [ns exits, tmpfs dies]
      │                          │◄──────────────────────────────────│
      │◄─ stdout + exit_code ────│                                   │
      │                          │                                   │
      │── read diff.ndjson ──────►                                   │
      │◄─ JSON payload ──────────│                                   │
      │                          │                                   │
      │── parse → OperationChange[] ─┐                               │
      │                               │                              │
      │── OCC commit_operation_against_base(...) ────────────────────►
      │     strict_base=True checks live hash vs SNAP-derived hash   │
      │◄─ committed or aborted_version ─────────────────────────────│
      │                          │                                   │
      │── cleanup $run_dir ──────►                                   │
      │── release semaphore                                          │
      │                          │                                   │
```

---

## 3. Filesystem layer composition

```
  Time t=0 (mount complete, command starts)
  ════════════════════════════════════════════

  lowerdir (bind-mount of live workspace):     upperdir (fresh tmpfs):
  ┌──────────────────────────────┐             ┌──────────────────────┐
  │ src/main.py          [text]  │             │    (empty)           │
  │ src/utils.py         [text]  │             │                      │
  │ .venv/...            [deps]  │             │                      │
  │ node_modules/...     [deps]  │             │                      │
  │ __pycache__/...      [cache] │             │                      │
  │ .git/                        │             │                      │
  │ .gitignore                   │             │                      │
  └──────────────────────────────┘             └──────────────────────┘

  merged (what command sees)  =  upper OVER lower  =  identical to lower


  Time t=N (command ran `pip install requests` + edited src/main.py)
  ══════════════════════════════════════════════════════════════════

  lowerdir (unchanged):                         upperdir (captured writes):
  ┌──────────────────────────────┐             ┌──────────────────────────┐
  │ src/main.py          [text]  │             │ src/main.py  [modified]  │
  │ src/utils.py         [text]  │             │ .venv/.../requests/*     │
  │ .venv/numpy/                 │             │ .venv/.../req-X.dist-info│
  │ node_modules/...             │             │                          │
  │ __pycache__/...              │             │                          │
  │ .git/                        │             │                          │
  │ .gitignore                   │             │                          │
  └──────────────────────────────┘             └──────────────────────────┘
         ▲                                              ▲
         │                                              │
         └──── reads for files not in upper             └── writes go here
              (numpy, node_modules, .gitignore, etc)

  merged (what command now sees):
  ┌─────────────────────────────────────────────────────────────────────┐
  │ src/main.py      [command's modified version — from upper]          │
  │ src/utils.py     [original — pass-through from lower]               │
  │ .venv/numpy/     [pre-existing — pass-through from lower]           │
  │ .venv/requests/  [just installed — from upper]                      │
  │ node_modules/    [from lower]                                       │
  │ .gitignore       [from lower]                                       │
  └─────────────────────────────────────────────────────────────────────┘
```

**Key property:** reads are free (pass-through), only writes materialize. A `pip install` of already-installed packages writes nothing. Upperdir stays empty for that path.

---

## 4. Diff classifier (the core of the auditor)

```
          Walk upperdir recursively
                    │
                    ▼
          ┌───────────────────┐
          │ For each entry:   │
          │   rel = path      │
          │   kind = ?        │
          └─────────┬─────────┘
                    │
          Classify kind first (overlay semantics):
                    │
          ┌─────────┴──────────────────────────────────┐
          │                                            │
      whiteout?                                  regular file?
  (char dev, rdev=0)                                   │
          │                                            │
       DELETE                                       MODIFY/CREATE
          │                                            │
          └────────────┬───────────────────────────────┘
                       │
                       ▼
           ┌──────────────────────┐
           │ Classify by path:    │
           │ git check-ignore rel │
           └──────┬───────────────┘
                  │
       ┌──────────┴──────────┐
       │                     │
    ignored?              not ignored?
  (.venv, node_modules,  (src/, tests/, tracked + new-source)
   __pycache__, *.pyc)
       │                     │
       ▼                     ▼
  ╔════════════════╗    ╔═════════════════════════════════╗
  ║ DIRECT MERGE   ║    ║ EMIT FOR OCC                    ║
  ║                ║    ║                                 ║
  ║ whiteout:      ║    ║ base = git show $SNAP:rel       ║
  ║   rm -rf live  ║    ║         (or "" if not in SNAP)  ║
  ║ create/modify: ║    ║ final = read upperdir/rel       ║
  ║   cp → .tmp    ║    ║         (None for whiteout)     ║
  ║   rename(.tmp) ║    ║                                 ║
  ║                ║    ║ emit NDJSON line:               ║
  ║ (atomic per    ║    ║   {path, base_content,          ║
  ║  file, no      ║    ║    final_content,               ║
  ║  OCC payload)  ║    ║    strict_base: true,           ║
  ║                ║    ║    base_existed}                ║
  ╚════════════════╝    ╚═════════════════════════════════╝

             Result at end of diff pass:
             ──────────────────────────
             - Ignored writes already applied to live (direct)
             - NDJSON contains only source-code OCC payload
             - Orchestrator parses NDJSON → OperationChange[]
             - Submits to OCC as one atomic batch
```

### Classifier reference table

| Path pattern | `git check-ignore` | Kind | Route |
|---|---|---|---|
| `src/foo.py` (tracked) | no | MODIFY | OCC |
| `src/bar.py` (new) | no | CREATE | OCC |
| `tests/test_x.py` (tracked, dirty) | no | MODIFY | OCC |
| `tests/test_y.py` whiteout (deleted) | no | DELETE | OCC |
| `.venv/lib/.../requests/__init__.py` | **yes** | CREATE | **Direct merge** |
| `node_modules/foo/index.js` | **yes** | CREATE | **Direct merge** |
| `__pycache__/main.cpython-312.pyc` | **yes** | MODIFY | **Direct merge** |
| `.pytest_cache/v/cache/nodeids` | **yes** | MODIFY | **Direct merge** |
| `dist/app.bundle.js` | **yes** | CREATE | **Direct merge** |
| `.gitignore` (tracked) | no | MODIFY | OCC |
| `.git/*` (any) | always skip | — | **Ignore entirely** |

---

## 5. OCC correctness under concurrency

```
  Time ─────────────────────────────────────────────────────────────►

  Op1 (CodeAct):
    t0   SNAP1 = commit-tree        (captures live state @ t0)
    t1   mount overlay
    t2   command writes src/foo.py upper-version A'
    t3   diff script: base_A = git show SNAP1:src/foo.py    = A
                      final  = read upper/src/foo.py         = A'
         emit OperationChange(path=foo, base=A, final=A', strict_base)
    t4   ns exits
    t5   OCC commit:
           - re-reads live src/foo.py → hash(A)
           - compares to base_hash(A) → MATCH
           - writes A' to live
           - commits

  Op2 (peer edit, concurrent):
    t1.5 OCC write: live src/foo.py = A → B
           (goes through OCC's own path, not overlay)

  Op3 (CodeAct, started after Op2's peer edit):
    t6   SNAP3 = commit-tree        (captures live state @ t6 = includes B)
    t7   mount overlay
    t8   command writes src/foo.py upper-version B''
    t9   diff script: base_B = git show SNAP3:src/foo.py    = B
                      final  = read upper/src/foo.py         = B''
         emit OperationChange(path=foo, base=B, final=B'', strict_base)
    t10  ns exits
    t11  OCC commit:
           - re-reads live src/foo.py → hash(B) (unchanged since Op3 started)
           - compares to base_hash(B) → MATCH
           - writes B'' to live

  Race scenario (Op1 + another peer during Op1's run):
    t0   Op1: SNAP1, base=A
    t2   Op1 writes A'
    t3.5 Peer: live src/foo.py A → C
    t5   Op1 OCC commit:
           - re-reads live → hash(C)
           - compares to base_hash(A) → MISMATCH
           - aborts (status=aborted_version)
           - live still = C (unchanged)
           - Op1's writes lost (tmpfs already dead)
           - safe
```

**Invariant:** OCC `strict_base=True` compares live-at-commit-time hash against the base we recorded from `git show $SNAP:path` (frozen in git's object store). Peer writes between SNAP capture and OCC commit are always caught.

**Direct-merge paths** (gitignored) skip OCC. They accept last-writer-wins for concurrent writes to the same path. This matches the behavior of running `pip install` twice concurrently on any machine.

---

## 6. Storage / compute cost model

```
  Per-op cost breakdown
  ════════════════════════════════════════════════════════════════

  Mount phase (once per op):
    SNAP = git commit-tree        O(index) + O(dirty bytes)   ~10-30ms
    unshare -Urm                                               ~1-2ms
    mount -t tmpfs                                             ~1-2ms
    mount --bind $workspace                                    ~1-2ms
    mount -t overlay              ~1-3ms
    mount --bind merged $repo     ~1-2ms
                                  ────────────────
                                  ~15-40ms total

  Command phase:
    [user command runs]           ← dominated by user workload
    reads from lower              ← free (no copy)
    writes to upper (tmpfs)       ← O(write bytes) in RAM

  Diff phase:
    walk upperdir                 O(changed files)
    git check-ignore (batch)      ~10ms
    direct-merge ignored paths    O(ignored write bytes)
    read base from git SNAP       O(tracked change bytes)
    emit NDJSON                   ~KB typical
                                  ────────────────
                                  ~20-100ms for typical CodeAct

  Cleanup (automatic):
    [ns exits] tmpfs destroyed    ~0ms (kernel releases)
    rm -rf $run_dir               ~1ms


  Comparison: per-op storage at 100-load
  ══════════════════════════════════════

  Component          Old overlay       Git-workspace      New overlay (this plan)
  ─────────────────  ────────────────  ─────────────────  ───────────────────────
  Lowerdir           cp -a workspace   N/A                bind-mount (0 bytes)
                     (2× workspace     
                      in RAM per op)   
  Upperdir           tmpfs (RAM,       N/A                tmpfs (RAM, only
                     caps 2GB)                            command's writes)
  Slot working tree  N/A               full checkout      N/A
                                       per slot (disk)
  SNAP object        N/A               blob+tree+commit   blob+tree+commit
                                       (content-dedup)    (content-dedup, shared
                                                          with git-workspace)

  At 100 ops × typical:
    Old overlay:    ~50GB RAM (100 × 500MB cp-a)       ← previous pain
    Git-workspace:  ~50GB disk (100 × 500MB checkouts) ← current pain
    New overlay:    ~100MB RAM (100 × few MB upper)    ← proposed
```

---

## 7. Concurrency model

```
  Concurrent CodeAct ops against one sandbox
  ═════════════════════════════════════════════════════

    asyncio.Semaphore(N=10)      ← concurrency cap
          │
          ├── Op A: lease permit, mount, run, diff, OCC, unmount, release
          ├── Op B: lease permit, mount, run, diff, OCC, unmount, release
          └── Op C: [waits for permit]

  Mount namespaces:
    Each op gets its own unshare-Urm ns. Fully isolated.
    No cross-op upperdir visibility.

  Lowerdir (bind mount):
    All ops see the SAME live workspace via their own bind mount.
    Peer commits (via OCC) become visible through lowerdir for any
    op still running — acceptable because OCC strict_base catches
    any resulting base_hash drift at commit time.

  OCC coordinator:
    Single global arbiter per workspace. Sorted-path locking across
    the op's OperationChange batch. Concurrent ops with disjoint
    paths proceed in parallel. Overlapping paths serialize at file
    lock acquisition.

  Direct-merge (ignored paths):
    No lock. Last-writer-wins for concurrent writes to same ignored
    path. Acceptable semantics for dep installs (equivalent to
    running two pip-install commands concurrently on any host).
```

---

## 8. File layout (what changes)

```
backend/src/code_intelligence/routing/
  ├── overlay_probe.py                 [restored from 3d0084f8^, unchanged]
  ├── overlay_exec.py                  [restored, ONE-LINE CHANGE:
  │                                     cp -a $lower → mount --bind $lower]
  ├── overlay_sandbox_diff.py          [NEW: sandbox-side walker +
  │                                     classifier + direct-merge
  │                                     logic, + NDJSON emitter]
  ├── overlay_auditor.py               [restored, MODIFIED:
  │                                     - remove lowerdir_provider/cache
  │                                     - remove tar download + walker
  │                                     - call overlay_sandbox_diff]
  ├── overlay_config.py                [NEW: concurrency cap config]
  │
  ├── git_workspace_auditor.py         [unchanged initially —
  │   git_workspace_pool.py             keep both paths behind a flag
  │   git_workspace_config.py           for a migration window]
  │   git_workspace_types.py
  │   git_diff_committer.py
  │
  └── command_executor.py              [MODIFIED: flag selects overlay
                                        vs git-workspace auditor]

backend/scripts/
  ├── probe_overlay_capability.py      [done — verifies sandbox supports path]
  └── probe_overlay_followup.py        [done — verifies bind-mount lowerdir
                                        works under unshare -Urm]

backend/tests/test_e2e/
  └── test_live_overlay_sandbox_diff_load.py   [NEW: bench harness, runs
                                                the new auditor at
                                                10/30/50/100 ops]
```

---

## 9. Acceptance criteria

1. **Capability probes pass** on real Daytona sandbox (already done).
2. **Unit tests** for sandbox-side diff classifier — tracked/ignored/new/delete/whiteout cases.
3. **OCC correctness** — concurrent peer edit during overlay run aborts with `aborted_version`; live workspace unchanged. Existing `test_live_daytona_occ_load.py` patterns apply.
4. **Capability test** — `pip install requests && python -c "import requests"` across two consecutive CodeAct ops produces successful import on the second op.
5. **Bench** — wall time, p95, throughput at 10/30/50/100 ops. Reported in same format as the git-workspace table:
   ```
   warmup: Xs
   10 ops:  ok 10/10, wall Xs, throughput X ops/s, p95 Xs
   30 ops:  ...
   50 ops:  ...
   100 ops: ...
   ```
6. **Capability budget** — demonstrate overlay-path allows gitignored writes (pip install persists), which git-workspace cannot.

---

## 10. Open policy decisions

| Decision | Default proposal | Why |
|---|---|---|
| `.git/*` writes in upperdir | Skip entirely (never emit, never direct-merge) | Commands shouldn't mutate `.git` during CodeAct. If they do, the overlay is the wrong layer to handle it. Failure-closed. |
| Symlinks in upperdir for tracked paths | Reject (`OverlayUnsupportedChangeError`) | Matches current V1 OCC policy. Defer until OCC can represent them. |
| Mode-only changes (chmod) on tracked paths | Ignore | Overlay captures as full copy-up; OCC doesn't track mode. Accept divergence or fail-closed. |
| Opaque-dir entries on tracked paths | Reject | Same as symlinks — V1 policy. |
| Non-UTF-8 content on tracked paths | Reject | Same as current OCC policy. |
| Non-UTF-8 content on ignored paths | Direct-merge as bytes | Binary deps (.whl, .so, .pyc) must pass through. |

---

## 11. Migration strategy

```
  Phase 0: Capability verification                              [DONE]
    - Probe privileged mount: BLOCKED at fsconfig
    - Probe unshare + bind mount: WORKS
    - Reflink: NO (not needed with bind mount)

  Phase 1: Land the auditor behind a flag
    - Restore overlay_exec.py (with bind-mount change)
    - Restore overlay_probe.py
    - Write overlay_sandbox_diff.py (sandbox-side walker)
    - Restore + modify overlay_auditor.py
    - Add CI_CODEACT_AUDITOR=overlay|git-workspace env var
    - Default: git-workspace (current)

  Phase 2: Parity test suite
    - Run full E2E suite under both flags
    - Assert behavioral parity for source-edit workloads
    - Add new tests for gitignored-write persistence (overlay-only)

  Phase 3: Bench + promote
    - Bench overlay at 10/30/50/100 ops
    - If ≤ git-workspace at all points: flip default
    - If significantly better: deprecate git-workspace

  Phase 4: Delete git-workspace
    - Once overlay is default for 2 weeks with no regressions
    - Remove git_workspace_* modules, unify on overlay
```
