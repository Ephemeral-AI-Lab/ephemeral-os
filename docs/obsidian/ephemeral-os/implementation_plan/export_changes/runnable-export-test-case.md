---
title: Manager Export Changes — Runnable-Project Round-Trip Test Spec (build → export → run)
tags:
  - ephemeral-os
  - layerstack
  - manager
  - export
  - testing
  - runnable
status: draft
updated: 2026-07-07
---

# Runnable-project export round-trip (5 projects)

Companion to `spec.md` (design truth) and `test-case.md` (the 30-case delta
catalog). That catalog proves the export **delta** equals the sandbox's
`MergedView` — winners, deletions, opaque cuts, modes, symlinks — and that the
host boundary holds. It never builds a real toolchain project inside the
sandbox and **runs** it back on the host. This spec closes that gap: it pins
B1's headline promise — "applied onto the host directory the base was seeded
from, the result **is** the sandbox's full merged view — a *workable tree*" —
by building five real Python/Node projects in-sandbox (`npm ci`, `pip install`,
`tsc`, native addons), exporting the built tree, and **executing** it.

Existing coverage (as of the 30-case run `export-20260707-214503`): **none of
the 30 cases run anything** — deltas are `printf`/`file_write`/`ln -s`
synthetics and assertions read the on-disk tree. RUN-01…05 below are the
missing end-to-end proof.

## 0. What "still runnable" has to mean (the portability boundary)

Export carries the **fidelity set** of invariant 10 — file content, file mode
(second-granular mtime), and symlink targets — and nothing else. It does **not**
carry uid/gid, xattrs, cross-winner hardlinks, or directory mode, and it cannot
change the fact that the bytes were produced by a **Linux** toolchain. Three
consequences make a naïve "export then `node app.js` on my Mac" test
either dishonest or flaky, and this spec confronts each:

1. **Native ABI.** `node-gyp`/prebuilt addons (`*.node`) and Python
   C-extension wheels (`numpy`, `pydantic-core`) are compiled for the
   sandbox's `linux-<arch>`. They export byte-perfectly but **will not load**
   on a macOS host, and load on a Linux host only at matching arch. Export
   faithfully carried the binary; the binary is simply not host-portable. This
   is a documented boundary, not a defect (inv 10, B4).
2. **Absolute paths baked at build time.** A Python venv writes the interpreter
   path into `pyvenv.cfg`, `.venv/bin/python` (a symlink to the build-time
   interpreter), and every console-script **shebang** — all rooted at the
   sandbox workspace path (`/workspace`). Exported to a host dir at a *different*
   path, the venv breaks. npm's `node_modules/.bin` uses **relative** symlinks
   and relocates cleanly; a venv does not.
3. **Hardlinks → duplicate content.** npm/pnpm may hardlink store entries;
   export emits hardlinked winners as duplicate content (inv 10). Runnable is
   unaffected (content is identical); on-disk size is larger than the source.

### Verification strategy — two checks, stated honestly

- **Primary — remount-and-run (platform-honest, load-bearing).** Mount the
  exported **host** directory at `/workspace` in a **fresh throwaway container
  of the same base image** (no build toolchain assumed beyond the language
  runtime) and run the app/test-suite there:
  `docker run --rm -v <dest>:/workspace -w /workspace <image> <run-argv>`.
  This proves the exported host tree is a **complete, self-contained, runnable
  project** — deps included — independent of the developer's host OS, and it
  resolves the venv/shebang absolute paths (they were baked at `/workspace`, and
  that is exactly where the tree is mounted). This is the definitive "still
  runnable" proof and the axis a failure fails on. It is also precisely how a
  Linux CI/prod consumer would run the exported tree.
- **Secondary — direct host run (developer smoke, best-effort).** For
  **pure-interpreted, path-relocatable** projects only (no native deps, no
  baked absolute paths), additionally run the exported tree with the host's
  own `node`/`python3` to demonstrate B1's in-place-workable promise for the
  common case. Explicitly `skip` (with a recorded reason, not a failure) when
  the host lacks the runtime, or when the project carries native/venv artifacts
  that the boundary above says are not host-portable.

A case passes on **primary**; **secondary** contributes `pass`/`skip`, never a
hard fail — its job is to document the boundary, not to punish it.

## 1. The contract under test

| Promise | Spec anchor | How RUN-0x pins it |
| --- | --- | --- |
| The exported tree is a workable project, not just an equal byte set | B1 "workable result", cost table | build in-sandbox → export → run |
| Deps/build artifacts (`node_modules`, `dist/`, `site-packages`) cross intact | inv 2 (merged-delta equivalence) | run needs them; missing → run fails |
| Modes + symlinks carry (executables, `.bin`, `venv/bin/python`) | inv 10 | `.bin/tsc`, `.venv/bin/python` used at run time |
| The native/venv portability boundary is honest | inv 10, B4 | RUN-03/04 document it, don't hide it |
| Incremental re-export of a built tree is O(source change) | inv 4, B2 | re-export after a 1-line edit skips the dep tree |
| Host boundary holds even for a large real tree | inv 9 | teardown: nothing outside dest; no markers |

## 2. Environment & harness additions

Reuses the `export/helpers.py` machinery (`create_sandbox`, `publish_exec`,
`export_changes`, `read_tree`, `record_case`, three-axis `verdict.json`,
`teardown`). New, project-specific helpers (thin wrappers, no new engine):

| Helper | Job |
| --- | --- |
| `create_sandbox(rec, root, image=…)` | image override per project (node/python base, not `ubuntu:24.04`) — the build toolchain must exist in-sandbox |
| `build_in_sandbox(rec, sid, cmd, timeout=900)` | a `publish_exec` with a long timeout; publishes the built tree as the delta (network pull allowed) |
| `run_in_image(rec, dest, image, argv, *, timeout=180, mount_at="/workspace", ports=None)` | `docker run --rm -v dest:mount_at -w mount_at [ -p … ] image argv`; returns exit code + captured stdout |
| `run_on_host(rec, dest, argv, *, timeout=120)` | best-effort subprocess in `dest`; returns `skip` if the runtime/binary is absent |
| `assert_runnable(result, *, expect_exit=0, expect_out=None)` | exit-code + stdout-substring assertion, feeds the `runnable` axis |

Each project ships a **`verify` entrypoint in its source seed** (a
self-terminating script: start → probe → exit non-zero on failure) so the
run-check is one deterministic command, not an orchestrated server+client dance
in the harness. The seed (source + `verify`) is the base; the **build output**
(`node_modules`/`dist`/`.venv`) is the published delta the export carries.

Markers: `export and runnable` (and `slow` — these pull packages over the
network). Serial. Per-project image pinned so the sandbox build runtime and the
verification runtime match.

## 3. Verdict schema (extends test-case.md §2)

The three axes (correctness / host-safety / incremental) plus a fourth,
load-bearing **runnable** axis:

```json
{
  "case_id": "RUN-01",
  "runnable": {
    "pass": true,
    "container_run": { "pass": true, "exit_code": 0, "output_match": true, "image": "node:22-slim" },
    "host_run":      { "status": "pass|skip|xfail", "exit_code": 0, "reason": "" }
  }
}
```

`runnable.pass == container_run.pass`. `host_run` is informational
(`skip`/`xfail` never fails the case). The `correctness` axis additionally
asserts the build artifacts are present in the export result
(`files_written` > the source-file count; specific dep paths on disk);
`incremental` (where run) asserts a source-only re-export skips the dep tree.

## 4. The five projects

Matrix (each ✓ is what the case uniquely exercises):

| Case | Stack | Pure deps | Build artifact | `.bin`/venv symlinks | Native/compiled | Test-suite proof | Boundary documented |
| --- | --- | :-: | :-: | :-: | :-: | :-: | :-: |
| RUN-01 | Node/Express | ✓ | | ✓ | | | |
| RUN-02 | Node/TypeScript | | ✓ (`dist/`) | ✓ | | | |
| RUN-03 | Node/native addon | | | ✓ | ✓ (`*.node`) | | ✓ ABI |
| RUN-04 | Python/Flask venv | ✓ | | ✓ | | | ✓ venv relocation |
| RUN-05 | Python/pytest+wheel | | | ✓ | ✓ (wheel) | ✓ (`pytest`) | |

### RUN-01 — Node/Express HTTP server (pure JS, the happy path)
- **Image**: `node:22-slim`. **Seed**: `package.json` (`express`), `server.js`
  (`GET /health → {"status":"ok"}`, port from `$PORT`), `verify.sh`
  (`node server.js & … curl -fs localhost:$PORT/health | grep '"status":"ok"' ; kill %1`).
- **Build**: `build_in_sandbox(… "npm ci")` → `node_modules/express` + relative
  `node_modules/.bin` symlinks, published as the delta.
- **Export**: `dir` onto `dest_seed`.
- **Correctness**: `dest/node_modules/express/package.json` present;
  `symlinks_written > 0` (the `.bin` symlinks carried); result
  `files_written` ≈ the dependency file count.
- **Runnable (primary)**: `run_in_image(dest, "node:22-slim", ["sh","verify.sh"], ports={PORT})` → exit 0, stdout has `"status":"ok"`.
- **Runnable (secondary)**: `run_on_host(dest, ["sh","verify.sh"])` → `pass`
  if host `node` exists (pure JS relocates), else `skip`.
- **Host-safety**: no `.wh.` on host; nothing outside `dest_seed`.
- **Incremental**: edit `server.js` (1 line), re-export → `node_modules`
  entries `skipped_unchanged`; only `server.js` rewritten.

### RUN-02 — Node/TypeScript CLI (a real build step)
- **Image**: `node:22-slim`. **Seed**: `package.json` (`typescript` devDep,
  `"build":"tsc"`), `tsconfig.json` (`outDir: dist`), `src/index.ts`
  (prints `sum(2,3)=5`), `verify.sh` (`node dist/index.js | grep '=5'`).
- **Build**: `npm ci && npm run build` → `dist/index.js` **and** `node_modules`
  (incl. dev `typescript`), published.
- **Export**: `dir` onto `dest_fresh` (a no-base dest — the sparse built tree).
- **Correctness**: `dest/dist/index.js` present (the **compiled** artifact
  crossed, not just `src/`); `node_modules/.bin/tsc` symlink carried.
- **Runnable (primary)**: run the **compiled** output — `run_in_image(dest, "node:22-slim", ["node","dist/index.js"])` → stdout `…=5`.
- **Secondary**: host `node dist/index.js` → `pass`/`skip`.
- **Incremental**: n/a.

### RUN-03 — Node/native addon (the ABI boundary, load-bearing document)
- **Image**: `node:22-slim`. **Seed**: `package.json` (`better-sqlite3`),
  `app.js` (open `:memory:`, `SELECT 1+1 AS v` → print `v=2`), `verify.sh`.
- **Build**: `npm ci` → `node_modules/better-sqlite3/build/Release/*.node`
  (a compiled Linux native addon), published.
- **Export**: `dir` onto `dest_fresh`.
- **Correctness**: the `*.node` binary is present in the export and
  **byte-identical** to the in-sandbox source (content fidelity of a binary);
  mode carried (executable bits).
- **Runnable (primary, same platform)**: `run_in_image(dest, "node:22-slim", ["node","app.js"])` → `v=2`. The Linux addon loads in the Linux container —
  proof the native artifact exported and executes on its own platform.
- **Boundary (documented, `xfail` on macOS host)**: `run_on_host` of the same
  tree on a macOS/arch-mismatched host **fails to load** the `.node`
  (`xfail`, reason `native ABI: linux binary on non-linux host`). This is inv
  10 / B4 stated as a fact: content carried, runtime portability not promised.
- **Host-safety / Incremental**: standard / n/a.

### RUN-04 — Python/Flask venv (the venv relocation boundary)
- **Image**: `python:3.12-slim`. **Seed**: `app.py` (Flask `GET /ping →
  "pong"`), `requirements.txt` (`flask`), `verify.sh`
  (`.venv/bin/python -m flask --app app run … & curl -fs localhost:$PORT/ping | grep pong`).
- **Build**: `python -m venv .venv && .venv/bin/pip install -r requirements.txt`
  → `.venv/` (site-packages, `bin/python` symlink, shebang scripts baked at
  `/workspace/.venv`), published.
- **Export**: `dir` onto `dest_seed` **and** the run mounts at `/workspace`, so
  the venv's absolute paths resolve.
- **Correctness**: `.venv/bin/python` symlink carried
  (`symlinks_written > 0`); `.venv/lib/python3.12/site-packages/flask` present.
- **Runnable (primary, mounted at `/workspace`)**: `run_in_image(dest, "python:3.12-slim", ["sh","verify.sh"], mount_at="/workspace", ports={PORT})` → `pong`. Mounting at the **build-time path** is what makes the venv
  valid.
- **Boundary (documented, `xfail`)**: mount the same tree at a *different*
  path (`/elsewhere`) and run → the venv shebangs/`pyvenv.cfg` point at
  `/workspace` and fail (`xfail`, reason `venv is not path-relocatable`). The
  export carried the venv faithfully; venvs are simply not relocatable — the
  escape hatch is "seed the host copy at the same path, or recreate the venv".
- **Incremental**: edit `app.py`, re-export → `.venv` skipped, only `app.py`
  written.

### RUN-05 — Python/pytest + compiled wheel (test-suite as the runnable proof)
- **Image**: `python:3.12-slim`. **Seed**: `pkg/__init__.py` +
  `pkg/stats.py` (a `mean()` over `numpy`), `test_stats.py` (pytest),
  `requirements.txt` (`numpy`, `pytest`), `verify.sh` (`.venv/bin/pytest -q`).
- **Build**: `python -m venv .venv && .venv/bin/pip install -r requirements.txt`
  → `.venv` with the **manylinux `numpy`** compiled wheel, published.
- **Export**: `dir` onto `dest_seed`.
- **Correctness**: `numpy`'s compiled `*.so` present under site-packages;
  `.venv/bin/pytest` shebang script carried.
- **Runnable (primary, mounted at `/workspace`)**: `run_in_image(dest, "python:3.12-slim", ["sh","verify.sh"], mount_at="/workspace")` → `pytest`
  exit 0 (the suite imports the compiled `numpy` and passes). A **passing test
  suite over the exported tree** is the strongest "still runnable" signal.
- **Boundary**: as RUN-03/04 (Linux wheel + venv path) — same `xfail`
  documentation for a mismatched host.
- **Incremental**: n/a.

## 5. Execution order & budget

1. RUN-01 (pure JS) — the smoke; also validates the `run_in_image`/`run_on_host`
   harness itself before native/venv cases.
2. RUN-02 (build step), RUN-04 (venv) — artifacts + symlinks + relocation.
3. RUN-03 (native), RUN-05 (wheel + pytest) — the ABI/wheel boundary and the
   test-suite proof; heaviest installs, run last.

Serial; `-m "export and runnable"`. Budget ≤ **20 min** total (network installs
dominate; `npm ci`/`pip install` each 20–120 s, plus per-case container runs).
Pre-pull the `node:22-slim` / `python:3.12-slim` images (build + verify reuse
them). Each case writes `test-reports/<RUN_ID>/RUN-0x/verdict.json` with the
four axes; the run bundles into the same `SUMMARY.md`.

## 6. Traceability

| Spec item | RUN cases |
| --- | --- |
| B1 — workable result, apply onto the seed | RUN-01, RUN-04, RUN-05 |
| inv 2 — merged-delta equivalence (deps/artifacts present) | all |
| inv 4 / B2 — incremental re-export of a built tree | RUN-01, RUN-04 |
| inv 9 — host boundary on a large real tree | all (teardown) |
| inv 10 / B4 — fidelity + portability boundary (native, venv, wheel, hardlink) | **RUN-03, RUN-04, RUN-05** |
| cost table — base never crosses; delta = the build output | RUN-02, RUN-03 (fresh dest) |

## 7. Non-goals

- **Cross-arch / cross-OS portability of native artifacts** — explicitly out of
  scope and documented as `xfail`; export is content fidelity, not a portable
  runtime. Re-running `npm ci`/`pip install` on the target platform is the
  supported path for native deps.
- **Making venvs relocatable** — not export's job; the remount-at-`/workspace`
  check is the honest proof, and re-seeding at the build path is the escape
  hatch (B1 fidelity condition).
- **A generic "run any project" harness** — five pinned projects with baked
  `verify` scripts, not a language-agnostic runner.
