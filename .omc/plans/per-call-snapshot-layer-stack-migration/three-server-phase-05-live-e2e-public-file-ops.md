# Phase 05 Live E2E Plan - Public File Ops Over Workspace-Replaced FS

**Status:** implemented 2026-05-07
**Related plans:**

- `three-server-phase-05-occ-mutation-gate.md`
- `three-server-phase-05-occ-mutation-gate-implementation-report.md`
- `three-server-phase-05-5-occ-backend-factory-consolidation.md`

**Implementation artifacts:**

- `backend/tests/live_e2e_test/sandbox/_harness/phase05_public_file_ops.py`
- `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_correctness.py`
- `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_full_filesystem_view.py`
- `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_edge_cases.py`
- `backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py`
- `backend/tests/live_e2e_test/sandbox/phase-05-public-file-ops-report.md`

**Latest verification:** focused Phase 05 live run `7 passed` in `226.96 s`
against `registry:6000/daytona/sweevo-psf-requests-3738:v1`, plus sandbox unit
guardrail `364 passed, 1 skipped` on 2026-05-07 UTC. The load run used the
documented Phase 05 budget override environment variables to collect the full
matrix after the draft default c20 redlines missed for write/edit/shell.

## 1. Purpose

Phase 05 unit coverage proves the command-exec/OCC mutation gate shape, but it
does not prove the same contract against a real Daytona sandbox filesystem.
This live E2E phase must prove that the public file-operation surface works
when `/testbed` is not the mutable workspace truth. The runtime view is:

```text
full sandbox filesystem
|-- /testbed                        replaced per request by:
|   `-- base repo imported into layer-stack + published layers
|-- /tmp, /root, /home, ...          real provider filesystem passthrough
`-- /tmp/eos-sandbox-runtime/
    `-- layer-stack/                durable layer-stack state
```

The public API verbs under test are:

```text
sandbox.api.tool.read_file
sandbox.api.tool.write_file
sandbox.api.tool.edit_file
sandbox.api.tool.shell
```

The test suite must be correctness-first and performance-driven. Every load
case runs independent public API calls at concurrency `1, 5, 10, 20`, records
per-call and batch metrics, and verifies the final filesystem view through the
public API/shell view rather than trusting host-local state.

## 2. Assumptions

- The live suite runs against a real Daytona sandbox created by the existing
  `backend/tests/live_e2e_test/sandbox/_harness/sandbox_fixture.py` fixtures.
- The configured image remains:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1
```

- `/testbed` is the workspace root. The runtime layer stack root is
  `/tmp/eos-sandbox-runtime/layer-stack`.
- Base-repo setup uses the existing `workspace_base_sandbox` fixture and
  `seed_imported_base(...)`: raw `/testbed` is seeded only before
  `api.build_workspace_base`, then public API calls operate against the
  imported base plus later layer-stack commits.
- Runtime code is treated as the current handler layout:

```text
backend/src/sandbox/runtime/handlers/
|-- read_handler.py
|-- write_handler.py
|-- edit_handler.py
`-- shell_handler.py

backend/src/sandbox/runtime/command_exec_server.py
backend/src/sandbox/runtime/occ_server.py
```

- `raw_exec` is allowed only for fixture setup and side-channel observation:
  raw `/testbed` mutation after base import is useful isolation evidence, but
  not the source of truth for public API correctness.
- Public API load tests do not use `shell_batch` and do not batch multiple
  operations into one shell script when measuring API concurrency. They launch
  independent public calls behind a barrier.

## 3. Non-Goals

- No host-local imports of `sandbox.layer_stack`, `sandbox.occ`, or
  `sandbox.overlay` from live test modules.
- No direct Daytona `process.exec` for behavior under test. Use it only for
  setup/inspection that public APIs intentionally do not expose.
- No multi-sandbox coordination. The load target is one real sandbox with many
  concurrent public calls.
- No benchmark-only suite that skips correctness reconciliation. Every load row
  must be tied to expected final filesystem state.
- No Phase 06 supervision-transport work.

## 4. Target Invariants

### 4.1 Public API Boundary

```text
host test
  -> sandbox.api.tool.{read_file, write_file, edit_file, shell}
  -> runtime op api.{read_file, write_file, edit_file, shell}
  -> current runtime handlers
  -> OccBackend from occ_server.build_occ_backend(layer_stack_root)
```

Expected ownership:

| Verb | In-workspace behavior | Out-of-workspace behavior |
|---|---|---|
| `read_file` | acquire layer-stack lease and read snapshot/layer view | direct host-FS read |
| `write_file` | build `WriteChange`, publish through `OCCClient.apply_changeset` | direct host-FS write |
| `edit_file` | read bytes from leased snapshot, derive final bytes, publish `WriteChange` through `OCCClient.apply_changeset` | direct host-FS read/edit/write |
| `shell` | replace `/testbed` with leased lowerdir, capture upperdir, publish capture through `OCCClient.apply_changeset` | normal sandbox FS passthrough outside `/testbed` |

### 4.2 Full Filesystem View

The command filesystem view must be tested as one coherent filesystem:

```text
public shell:
  /testbed/...    sees base repo + layer-stack commits
  /tmp/...        sees direct sandbox FS
  /root/.cache    sees direct sandbox FS

public read/write/edit:
  relative paths and /testbed/... classify in-workspace
  /tmp/... and other outside paths classify out-of-workspace
  symlink escapes from /testbed fail closed or classify outside according to
  the single classifier contract
```

Required isolation proof:

```text
1. Seed /testbed/raw.txt = "base" and import workspace base.
2. Mutate raw /testbed/raw.txt = "dirty" through raw_exec.
3. public read_file("raw.txt") still returns "base".
4. public shell("cat raw.txt") still sees "base" from the replaced workspace.
5. public write/edit commits create a new layer-stack view visible to later
   public read_file and shell calls.
```

## 5. Test Layout

Add a focused Phase 05 package under the existing integrated live suite:

```text
backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/
|-- test_phase05_public_file_ops_correctness.py
|-- test_phase05_public_file_ops_edge_cases.py
|-- test_phase05_public_file_ops_load.py
`-- test_phase05_full_filesystem_view.py
```

Shared helpers, only if the test files start duplicating setup/artifact code:

```text
backend/tests/live_e2e_test/sandbox/_harness/
`-- phase05_public_file_ops.py
```

The helper should reuse existing harness functions where possible:

- `gather_with_barrier`
- `timed_call`
- `assert_committed`
- `assert_read`
- `seed_imported_base`
- `selected_runtime_ms`
- `write_jsonl_artifact`

Implementation report target:

```text
backend/tests/live_e2e_test/sandbox/phase-05-public-file-ops-report.md
```

JSONL artifact target:

```text
.omc/results/live-e2e-phase05-public-file-ops-<case>-<utc>.jsonl
```

## 6. Coverage Matrix

### A. Base Repo And Public View Correctness

File:

```text
test_phase05_public_file_ops_correctness.py
```

Fixture shape:

```text
workspace_base_sandbox
  -> seed realistic /testbed tree
  -> api.build_workspace_base(workspace_root="/testbed")
  -> public API operations only
```

Seed tree:

```text
.gitignore                         "dist/\n.tmp/\n"
README.md                          base markdown content
src/app.py                         tracked Python source
src/config/settings.json           tracked JSON
frontend/src/App.tsx               tracked TSX
tracked/edit-target.txt            multi-line edit target
tracked/large.txt                  deterministic 1-2 MiB text file
dist/existing-ignored.txt          ignored base file
links/inside -> ../src/app.py       symlink resolving inside workspace
links/outside -> /tmp/eos-outside   symlink resolving outside workspace
```

Assertions:

- `read_file` sees imported base content for representative tracked files.
- `shell("cat ...")` sees the same imported base content.
- Raw mutation to `/testbed` after import does not leak into public read/shell
  until published through public API.
- `write_file` of a new tracked file is visible to later `read_file` and
  `shell`.
- `edit_file` of a base file is visible to later `read_file` and `shell`.
- `shell` writes one tracked file and one ignored file; tracked output follows
  OCC conflict rules and ignored output follows gitignore routing.
- Final reconciliation reads every expected in-workspace path via
  `read_file`, not raw `/testbed`.

### B. Full Filesystem Boundary

File:

```text
test_phase05_full_filesystem_view.py
```

Cases:

| Case | Expected result |
|---|---|
| relative `src/app.py` and absolute `/testbed/src/app.py` | same in-workspace content |
| `/testbed/../tmp/escape.txt` | hard reject, not silent outside write |
| symlink inside workspace | classified in-workspace and reads target content |
| symlink escaping to `/tmp` | public read/write/edit classify outside the workspace; shell-side writes through the symlink are not published as workspace layer data |
| public `write_file("/tmp/phase05.txt")` then `read_file("/tmp/phase05.txt")` | direct host-FS passthrough, absolute changed path |
| public `edit_file("/tmp/phase05.txt")` | direct host-FS edit, no OCC timing required |
| shell writes `/tmp/phase05-shell.txt` and `/root/.cache/eos-phase05.txt` | visible to raw/public outside-FS reads, absent from layer-stack changed paths |
| shell writes both `/testbed/tracked.txt` and `/tmp/outside.txt` | workspace change goes through OCC; outside change persists as provider FS side effect |

Final proof for each case:

- `api.layer_metrics` manifest version changes only for in-workspace mutations.
- Public `read_file` confirms the in-workspace layer view.
- Raw side-channel inspection confirms outside-FS files exist where expected.

### C. Complex Correctness And Conflicts

File:

```text
test_phase05_public_file_ops_edge_cases.py
```

Required cases:

| Case | Workload | Pass bar |
|---|---|---|
| concurrent same-path writes | two `write_file` calls to the same tracked base file | exactly one commit, one conflict; final content is winner |
| concurrent disjoint writes | 20 `write_file` calls to unique tracked paths | all commit; all visible via public read |
| disjoint edits same file | two `edit_file` calls editing different anchors in one file | both commit or deterministic retry success; final file contains both changes |
| overlapping edits same file | two `edit_file` calls replacing same anchor | exactly one commit, one conflict |
| shell stale tracked conflict | shell leases snapshot, public write wins before shell publish | shell result rejected; no partial tracked or ignored workspace output published |
| shell delete vs public write | shell deletes base file while public write advances manifest | stale shell delete conflicts; winning content remains |
| create-only tracked write | `overwrite=False` against existing base file | rejected before OCC publish |
| large text edit | edit deterministic 1-2 MiB file near the end | commit succeeds; content hash matches expected |
| non-UTF8 edit | edit a binary base file | fail closed with text/UTF-8 error; no manifest advance |
| missing read | read absent in-workspace path | `success=True`, `exists=False`, `content=""` |
| missing outside read | read absent `/tmp/...` path | same absent-read shape |
| shell timeout before workspace write | shell times out before writing tracked output | no workspace publication; active leases and staging dirs return to zero |

The shell conflict cases must keep the boundary:

```text
command_exec_server capture -> workspace_changes_to_occ_changes
  -> OCCClient.apply_changeset
  -> OccService
```

They must not use raw provider exec as a replacement for public shell behavior.

### D. Load And Performance

File:

```text
test_phase05_public_file_ops_load.py
```

Run matrix:

```text
verbs:        read_file, write_file, edit_file, shell, mixed
concurrency: 1, 5, 10, 20
shape:       independent barrier-launched public calls
workspace:   imported base repo + prior layer-stack commits
```

Single-verb load shapes:

| Verb | Workload |
|---|---|
| `read_file` | read unique imported base files and newly committed files |
| `write_file` | write unique tracked files under `tracked/load/write/` |
| `edit_file` | edit unique imported base files under `tracked/load/edit/` |
| `shell` | each shell writes one unique tracked file and reads one base file; no `shell_batch` |

Mixed load shape:

```text
read-heavy target:
  40% read_file
  30% edit_file
  20% write_file
  10% shell
```

For concurrency levels where percentages do not divide evenly, use a stable
pattern such as:

```text
["read", "read", "edit", "write", "read", "edit", "shell", "write"]
```

Metrics per call:

```json
{
  "schema": "sandbox.live_e2e.phase05_public_file_ops.v1",
  "kind": "call",
  "case": "write_file_c20",
  "op": "write_file",
  "concurrency": 20,
  "label": "write_c20_03",
  "success": true,
  "status": "committed",
  "changed_paths": ["tracked/load/write/c20-03.txt"],
  "wall_ms": 0.0,
  "runtime_ms": 0.0,
  "timings": {
    "api.write.total_s": 0.0,
    "api.write.occ_apply_s": 0.0,
    "occ.apply.total_s": 0.0,
    "occ.prepare.total_s": 0.0,
    "occ.commit.total_s": 0.0,
    "occ.serial.queue_wait_s": 0.0,
    "layer_stack.transaction.lock_wait_s": 0.0
  }
}
```

Summary row per verb/concurrency:

```json
{
  "schema": "sandbox.live_e2e.phase05_public_file_ops.v1",
  "kind": "summary",
  "case": "write_file",
  "workspace_root": "/testbed",
  "layer_stack_root": "/tmp/eos-sandbox-runtime/layer-stack",
  "concurrency": 20,
  "calls": 20,
  "batch_wall_ms": 0.0,
  "per_call_wall_p50_ms": 0.0,
  "per_call_wall_p95_ms": 0.0,
  "per_call_wall_p99_ms": 0.0,
  "runtime_p99_ms": 0.0,
  "parallel_factor": 0.0,
  "parallel_efficiency": 0.0,
  "throughput_ops_s": 0.0,
  "correctness": {
    "all_calls_accounted": true,
    "all_expected_paths_visible": true,
    "unexpected_conflicts": 0,
    "final_reconciliation": true
  },
  "pass_bars": {}
}
```

Default performance redlines:

| Workload | c20 batch wall | c20 wall p99 | c20 runtime p99 |
|---|---:|---:|---:|
| `read_file` | `<= 5000 ms` | `<= 3000 ms` | `<= 1000 ms` |
| `write_file` | `<= 8000 ms` | `<= 5000 ms` | `<= 2500 ms` |
| `edit_file` | `<= 8000 ms` | `<= 5000 ms` | `<= 2500 ms` |
| `shell` | `<= 12000 ms` | `<= 7000 ms` | `<= 4000 ms` |
| `mixed` | `<= 12000 ms` | `<= 7000 ms` | `<= 4000 ms` |

Each redline should be overrideable with environment variables so live
infrastructure noise can be adjusted without changing test semantics:

```text
EPHEMERALOS_PHASE05_READ_C20_BATCH_WALL_BUDGET_MS
EPHEMERALOS_PHASE05_WRITE_C20_BATCH_WALL_BUDGET_MS
EPHEMERALOS_PHASE05_EDIT_C20_BATCH_WALL_BUDGET_MS
EPHEMERALOS_PHASE05_SHELL_C20_BATCH_WALL_BUDGET_MS
EPHEMERALOS_PHASE05_MIXED_C20_BATCH_WALL_BUDGET_MS
EPHEMERALOS_PHASE05_C20_WALL_P99_BUDGET_MS
EPHEMERALOS_PHASE05_C20_RUNTIME_P99_BUDGET_MS
```

The load suite should also assert weaker monotonic sanity for every level:

- `parallel_factor > 0`
- `throughput_ops_s > 0`
- no missing per-call timing row
- no unexpected conflict in disjoint load
- every expected path reconciles through public `read_file`

## 7. Artifact And Report Requirements

Every test that measures performance or complex conflict behavior writes a
JSONL artifact under:

```text
.omc/results/live-e2e-phase05-public-file-ops-<case>-<utc>.jsonl
```

After implementation, add:

```text
backend/tests/live_e2e_test/sandbox/phase-05-public-file-ops-report.md
```

The report must include:

- exact command lines and image used
- test result summary
- artifacts emitted
- 1/5/10/20 tables for read, write, edit, shell, mixed
- p50/p95/p99 wall and runtime latency
- batch wall, throughput, parallel factor, parallel efficiency
- OCC timing breakdown for write/edit/shell
- shell overlay timing breakdown
- correctness and edge-case summary
- explanation of any budget miss with attribution to provider wall time,
  runtime dispatch, overlay/capture, OCC prepare/commit, or layer-stack lock

## 8. Implementation Steps

### Step 1 - Harness Reuse And Schema

- Add `phase05_public_file_ops.py` only if the test modules need shared JSONL,
  budget, or expected-content helpers.
- Reuse existing `workspace_base_public.seed_imported_base`.
- Add a tiny helper to derive selected runtime duration from
  `api.read.total_s`, `api.write.total_s`, `api.edit.total_s`,
  `api.shell.total_s`, or `api.shell.dispatch_total_s`.
- Keep host-side test imports within public API and live harness modules.

### Step 2 - Correctness Tests

- Implement base-repo public view correctness.
- Implement raw `/testbed` mutation isolation proof.
- Implement public write/edit/shell/read reconciliation over the imported base.
- Emit JSONL for the correctness case if it records timing tables.

### Step 3 - Full Filesystem Boundary Tests

- Cover relative path, absolute `/testbed`, escape attempts, symlink-inside,
  symlink-outside, `/tmp`, and `/root/.cache`.
- Use raw exec only to create symlink/outside fixtures or inspect outside-FS
  state.
- Assert layer-stack manifest movement only for in-workspace mutations.

### Step 4 - Complex Edge Tests

- Implement conflict and failure cases from section 6.C.
- Prefer deterministic two-party races with explicit `/tmp` barriers for
  conflict cases.
- Do not use raw provider exec for the public shell mutation path.

### Step 5 - Load Tests

- Implement single-verb load matrix for `read_file`, `write_file`,
  `edit_file`, and `shell`.
- Implement mixed read/edit/write/shell load matrix.
- Use `gather_with_barrier` so `1, 5, 10, 20` are real concurrent public
  calls.
- Emit one JSONL artifact per workload or one consolidated artifact with
  per-workload summary rows.

### Step 6 - Report And README Update

- Add the Phase 05 report with live evidence.
- Update `backend/tests/live_e2e_test/sandbox/README.md` with the new Phase 05
  status and run command after the suite passes.

## 9. Decision Before Implementation

One shell policy must be made explicit before adding a pass/fail gate:

```text
shell command writes tracked output, then exits nonzero
```

The current command-exec shape applies workspace capture before projecting the
host-visible shell result. If the intended contract is "nonzero shell never
publishes workspace changes", implementation must fix that first and add a
failing live test. If the intended contract is "command side effects publish
even when exit_code != 0, but success is false", the live test must assert that
shape and the Phase 05 report must name it clearly.

Do not leave this as an implicit behavior in the live suite.

## 10. Verification Commands

Collection sanity:

```bash
.venv/bin/pytest backend/tests/live_e2e_test --collect-only -q
```

Focused Phase 05 live run:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_correctness.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_full_filesystem_view.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_edge_cases.py \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_phase05_public_file_ops_load.py \
  -v -rs -s --tb=short
```

Broader live regression, if the focused run is green:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest \
  backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ \
  -v -rs -s --tb=short
```

Unit guardrail for the runtime mutation gate:

```bash
.venv/bin/pytest backend/tests/unit_test/test_sandbox -q
```

## 11. Exit Criteria

Phase 05 live sign-off requires:

- public `read_file`, `write_file`, `edit_file`, and `shell` all pass against
  an imported `/testbed` base repo
- public shell sees `/testbed` as base repo plus layer-stack commits, not the
  mutable raw `/testbed` directory
- writes outside `/testbed` remain direct sandbox filesystem side effects
- in-workspace public writes/edits/shell captures publish through OCC and are
  visible to later public reads/shells
- conflict cases are deterministic and leave no partial workspace publication
- load matrix covers `1, 5, 10, 20` for each verb and mixed workload
- JSONL artifacts contain wall/runtime/OCC/overlay timing attribution
- Phase 05 report explains correctness, edge cases, and performance redlines
- collection passes with the live-suite import fence enabled
