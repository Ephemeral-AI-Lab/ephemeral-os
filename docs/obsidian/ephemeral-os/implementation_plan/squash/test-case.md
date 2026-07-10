---
title: LayerStack Squash + Live Remount — Live-Docker Test Catalog & Measurement Harness
tags:
  - ephemeral-os
  - layerstack
  - testing
  - observability
status: verified
updated: 2026-07-11
---

# Squash Test Catalog — 50 cases, 3 axes each

Companion to `spec.md` (design truth), `acceptance_criteria.md` (definition
of done), and `impl_plan_and_progress_tracker.md` (measured phase-0
baselines). This document defines the **live Docker sandbox** test catalog:
10 smoke (SMK), 20 medium (MED), 20 very difficult (HRD) — every case
verified on three axes:

1. **Correctness** — merged-view equivalence, result-contract exactness,
   lease/registry hygiene, recovery behavior.
2. **Space** — disk on the layer-stack root before/peak/after, checked
   against the §D complexity formulas with the case's concrete fixture
   sizes.
3. **Time** — `T_squash` (storage commit), `T_quiesce` (freeze → inspect),
   `T_remount` (staged switch, per session), plus end-to-end CLI wall
   clock, each against a measured budget (§3).

Relationship to the spec's required tests: unit/integration tests 1–22 run
under `cargo test` and are not repeated here; this catalog is the
live-environment suite. It **subsumes G1–G3 and E1–E10** (traceability in
§5) and extends them with scale, soak, and measurement cases.

**Runnability by phase** (tracker): phases 0–3 are done — storage squash
exists behind `LayerStack::squash()` but is not yet wired to an operation.
Every case below drives the product surface
(`sandbox-manager-cli squash_layerstacks`), so the catalog becomes runnable
after phase 9 (CLI) and fully green only at phase 10 (enablement). Cases
marked ⛔gate additionally require live remount enabled (G1–G3 proven).

**Where the tests land.** The suite is implemented in the existing
CLI-driven pytest harness, one folder per family:

```text
e2e/manager/management/squash/
├── helpers.py               squash-family CLI wrappers + fixture toolkit (§1.2)
├── measure.py               timers, disk snapshots, mountinfo poller, verdict writer (§2)
├── test_squash_smoke.py     SMK-01…10   (pytest -m "squash and smoke")
├── test_squash_medium.py    MED-01…20   (pytest -m "squash and medium")
├── test_squash_hard.py      HRD-01…20   (pytest -m "squash and hard")
├── test_spec.md             this catalog, mirrored (family convention)
└── test-reports/<RUN_ID>/   artifact bundles + SUMMARY.md (§2.5–§2.6)
```

Conventions inherited from the harness: every operation goes through
the public CLI binaries and asserts on structured JSON (never log scraping);
sandbox lifecycle lives in `conftest.py` fixtures so teardown runs on
failure; markers `smoke` / `medium` / `hard` gate the tiers.

---

## 1. Environment & fixture toolkit

### 1.1 Bring-up

```sh
export PATH="$PWD/bin:$PATH"
bin/start-sandbox-docker-gateway --rebuild-binary        # rebuild + start gateway

RUN_ID=sq-$(date +%Y%m%d-%H%M%S)                         # one run id per suite run
SEED=$(mktemp -d /tmp/$RUN_ID-seed.XXXX); echo seed > "$SEED/seed.txt"
bin/sandbox-manager-cli create_sandbox --image ubuntu:24.04 \
  --workspace-bind-root "$SEED" > /tmp/$RUN_ID-create.json
SID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["id"])' /tmp/$RUN_ID-create.json)
```

**Environment preconditions — asserted once per suite, hard-fail (never
skip)**, mirroring the spec's harness ground rules:

| # | Precondition | Check | Expected (measured 2026-07-02) |
| --- | --- | --- | --- |
| P1 | kernel ≥ 5.8 (`syncfs` error reporting); supported ≥ 6.0 | `docker exec <ctr> uname -r` | `6.12.76-linuxkit` |
| P2 | layer-stack root not on overlayfs | `findmnt -no FSTYPE` on `/eos/layer-stack` | `ext4` (named volume `254:1`) |
| P3 | unprivileged `userxattr` overlay works in the sandbox userns | probe mount | pass |
| P4 | outside observation channel | `/proc/<holder>/mountinfo` readable from daemon side | pass (mount-id change 135→138 observable) |
| P5 | disk headroom for the scale cases | `df -B1 /eos/layer-stack` | ≥ 8 GiB free |

One sandbox per case unless stated; cases never share a layerstack root.
Suite is **serial by default**; only cases that explicitly test concurrency
run parallel invocations *inside* one case.

### 1.2 Fixture vocabulary

Layers are published by one-shot `exec_command` (each one-shot with
changes publishes exactly one `L` layer — X1.1 verified). Sessions are
long-lived `create_workspace_session` leases. Named fixtures used
throughout the catalog:

| Fixture | Meaning | Construction |
| --- | --- | --- |
| `stack(N×distinct m)` | N layers, each a new m-byte file | loop: `runtime exec_command "head -c m /dev/urandom > /workspace/f$i"` |
| `stack(N×churn m)` | N layers rewriting the **same** m-byte file (F ≪ P) | loop over `/workspace/hot` |
| `stack(delete-heavy)` | create-then-delete: whiteout/opaque winners | `touch`+`rm`, `mkdir -p`+`rm -rf`+`mkdir` sequences |
| `witness layer Li` | spec witness convention | `wit/only-in-Li`, one file deleted-in-`Li+1`, one dir created-then-emptied, one mode-0640 file |
| `ws-idle` | session with a lease, no running command | `create_workspace_session` |
| `ws-batch` | zero-pin live command | `exec_command --workspace-session-id WS "sh -c 'cd /; exec sleep 600'"` — cwd `/`, no workspace fd/mmap |
| `ws-pty` | interactive PTY bash at prompt, cwd in workspace | `exec_command` interactive + `write_command_stdin`/`read_command_lines` |
| `pin(fd)` | task holding `exec 3</workspace/f` then sleeping | inside `ws-*` |
| `pin(mnt-escape)` | `unshare -m sleep inf`, zero workspace fds | inside `ws-*` |
| `pin(child-mount)` | bind mount created under `/workspace` by a task that already exited | `mount --bind` then exit |
| `pin(scm-fd)` | SCM_RIGHTS-parked fd (invisible to `/proc/*/fd`) | fd sent to self over socketpair, local copy closed (X0.5 probe) |
| `kill-point(staged)` / `kill-point(moved)` | external runner kill triggered by mountinfo observation | poll `/proc/<holder>/mountinfo`: staging mount id appears / workspace root mount id changes |

### 1.3 Teardown contract (part of every case's assertions)

After each case, in order — a teardown failure **fails the case loudly**:

1. Destroy every session; destroy the sandbox last.
2. Lease registry empty: `observability layerstack` shows
   `active_lease_count == 0` on every layer.
3. Holder mountinfo (pre-destroy) contains no `.remount-staging-*` /
   `.remount-rollback-*` entries; `staging/` is empty.
4. Teardown uses **strict unmount only** — a lazy detach here would mask
   exactly the leak class this suite exists to catch.
5. Artifact bundle (§2.5) written even on failure — especially on failure.

---

## 2. Observability, audit, and the test report — the measurement design

Design rule inherited from the simplicity review: the feature adds
**exactly three** observability records and nothing else — no progress
streaming, no byte totals in the result JSON, no persisted remount state.
Therefore everything below is split into (a) the three in-src spans and
their attrs, and (b) a **harness-side measurement kit** that composes
existing surfaces. The report never requires src instrumentation beyond
the three records.

### 2.1 Signal inventory

| Signal | Source | What it answers |
| --- | --- | --- |
| Result JSON (stdout, 1 line) | `squash_layerstacks` | the contract: `manifest_version`, per-block `reclaimed/leased` + `blocked_reasons`, `faulty_sessions` |
| `layerstack.squash` span | NDJSON log / `observability trace` | storage-commit duration + phase decomposition (attrs, §2.4) |
| `workspace_session.remount` span (per session) | same | per-session sweep outcome + quiesce/switch decomposition |
| `namespace.exec.remount_overlay` span (per session) | same | runner staged-switch duration; two-boolean report facts |
| `lease.acquired` / `lease.released` events | `observability events` | pin-overlap ordering, replacement-lease leak detection, GC instants |
| `observability layerstack` view | live runtime | per-layer bytes (S sized by walking), per-layer `active_lease_count`, stack series — **the** byte-accounting surface |
| `observability trace --trace-id last` | reader | the whole squash invocation as one waterfall (dispatch → op → per-session spans) |
| Disk truth | `du -sb layers/ staging/ .layer-metadata/`, `find … | wc -l`, `df -B1` inside the container | ground truth the view must agree with |
| Outside observation | poll `/proc/<holder>/mountinfo` from the daemon side | staging-mount appearance, workspace mount-id change (PONR), rollback residue — kill-point triggers, zero src hooks |
| Process truth | `/proc/<pid>/stat` states, `/proc/<pid>/{cwd,root,fd,maps}` | freeze/resume proof, pin ground truth |
| Harness clocks | `/usr/bin/time -p`, monotonic timestamps around CLI calls | end-to-end wall clock; cross-check span durations |

### 2.2 Timing model — the three clocks and their decomposition

One squash invocation has a fixed timeline; every timer below is named
once and used by that name in every case:

```text
T_e2e ──────────────────────────────────────────────────────────────────────┐
 CLI → manager forward → daemon dispatch                                    │
 ┌─ T_squash = layerstack.squash dur_ms ────────────────┐                   │
 │  t_plan   (plan lock, boundaries, blocks)            │                   │
 │  t_build  (flatten: walk + hardlink + re-encode)     │                   │
 │  t_commit (recheck → promote → syncfs → rename → GC) │                   │
 │    └ t_syncfs (the one durability barrier)           │                   │
 └──────────────────────────────────────────────────────┘                   │
 per session i (sweep loop):                                                │
 ┌─ T_remount(i) = workspace_session.remount dur_ms ────────────────┐       │
 │  t_rewrite  (map contraction + acquire_rewritten_lease)          │       │
 │  T_quiesce(i) = t_freeze (SIGSTOP→all-T) + t_inspect (/proc + 1  │       │
 │                 mountinfo read)                                  │       │
 │  t_switch(i) = namespace.exec.remount_overlay dur_ms             │       │
 │               (stage → remask → probe → moves → probe → strict   │       │
 │                unmount)                                          │       │
 │  t_resume + t_release (SIGCONT + old-lease release/GC)           │       │
 └──────────────────────────────────────────────────────────────────┘       │
─────────────────────────────────────────────────────────────────────────────
```

Measurement sources, in preference order:

1. **Span `dur_ms`** for `T_squash`, `T_remount(i)`, `t_switch(i)` — these
   exist by contract (three records).
2. **Span attrs** for the sub-phase decomposition (§2.4 proposal). If a
   sub-timing attr is absent, the harness degrades to derivation:
   `T_quiesce(i) ≈ T_remount(i) − t_switch(i) − t_rewrite − t_resume/release`
   with the residual labeled `derived`, never silently mixed with measured
   values (`"quiesce_ms_source": "attr" | "derived"` in the verdict).
3. **Harness clocks** for `T_e2e` and as a sanity envelope:
   `T_e2e ≥ T_squash + Σ T_remount(i)` must hold; violation flags a
   broken trace.
4. **Mountinfo polling timestamps** give externally-observed
   `t_stage_appear` and `t_ponr` (mount-id change) for kill-point cases —
   the only timers that exist even when the runner dies.

### 2.3 Space model — what is measured and against which formula

Notation from spec §D: `B` base, `P0` candidate-run bytes, `F` flatten
(surviving) bytes, `U` Σ upperdirs, `Π` still-pinned candidate bytes.

Four disk snapshots per case, all taken as
`{du -sb layers staging .layer-metadata, find layers -maxdepth 1 | wc -l, df -B1}`
on the layer-stack root **plus** the `observability layerstack` view (the
two must agree within 5% or the case fails with `view_drift`):

| Snapshot | When | Expected (general form) |
| --- | --- | --- |
| `S0` | after fixture build, before squash | `B + P0 + U` |
| `S1` | peak during build (poll at 250 ms while squash runs) | `≤ B + P0 + F + U + ε` — the transient commit peak; hardlinked winners keep `F_bytes ≈ re-encoded only` |
| `S2` | after the invocation returns (commit + sweep done) | migrated: `B + F + U + Π + pins/singletons + publish tail`; commit-only: `B + P0 + F + U` |
| `S3` | settled (after stated follow-up: session exit / next squash / destroy) | `B + F + U'` |

Every case states its **expected S2/S3 with concrete numbers** derived
from its fixture (e.g. `stack(6×churn 100 MiB)` ⇒ `P0=600 MiB, F≈100 MiB`
⇒ migrated S2 ≈ `B + 100 MiB + U`, i.e. the §D "17%" row). Layer-count
and lowerdir-chain-length assertions ride the same snapshots
(chain length read from the session's `observability layerstack
--workspace-id` slice, verified behaviorally by witness reads — never by
mount-option introspection).

### 2.4 Span-attrs vocabulary (the only in-src surface — proposal)

Attrs are the record model's open domain facts (`span.attr(k, v)` —
`publish_changes.rs:50` precedent: flat scalars only). Proposed minimal
vocabulary, to land with phases 3/5–8; anything already derivable from
existing surfaces stays out:

| Span | Attrs | Notes |
| --- | --- | --- |
| `layerstack.squash` | `manifest_version`, `blocks`, `blocks_reclaimed`, `blocks_leased`, `plan_ms`, `build_ms`, `commit_ms`, `syncfs_ms`, `entries_walked` | no byte fields — bytes belong to the layerstack view |
| `workspace_session.remount` | `workspace_id`, `outcome` (`migrated`\|`leased`\|`parked`\|`faulty`\|`identity`), `class_detail`, `tasks_frozen`, `pins`, `rewrite_ms`, `freeze_ms`, `inspect_ms`, `resume_release_ms` | `status=completed` even for `leased` (a clean skip is not an error); `status=error` only for `faulty` |
| `namespace.exec.remount_overlay` | `first_move_succeeded`, `mount_verified`, `detail` | mirrors the two-boolean runner report exactly — no third fact |

If review trims this list, the harness derivation rules in §2.2 keep every
test case runnable — cases assert on *timers by name*, not on attrs
directly.

### 2.5 Artifact bundle & per-case verdict (audit trail)

Every case writes one directory —
`e2e/manager/management/squash/test-reports/<RUN_ID>/<CASE_ID>/`
— capturing enough to re-diagnose without re-running:

```text
<CASE_ID>/
├── cmd.log                 every CLI command, verbatim, in order (the audit log)
├── result.json             the one-line result (or stderr error line)
├── trace.txt               observability trace --trace-id last (waterfall)
├── events.json             observability events slice for the case window
├── layerstack.S0.json …    the four view snapshots
├── disk.S0.txt …           the four du/find/df snapshots
├── mountinfo.log           timestamped holder-mountinfo poll (remount cases)
├── proc/                   /proc dumps at decision points (pin cases)
├── timings.json            every named timer + source (attr|derived|harness)
└── verdict.json            the machine verdict (schema below)
```

`verdict.json` — one schema for all 50 cases; a case passes only when all
three axes pass:

```json
{
  "case_id": "MED-08",
  "run_id": "sq-20260702-210000",
  "status": "pass",
  "axes": {
    "correctness": { "pass": true, "assertions": 14, "failed": [] },
    "space": {
      "pass": true,
      "formula": "B + F + U (all sessions migrated)",
      "expected_bytes": 115343360, "measured_bytes": 114966528,
      "tolerance_pct": 5,
      "layer_count": {"before": 8, "after": 3},
      "chain_length": {"before": 8, "after": 3}
    },
    "time": {
      "pass": true,
      "t_squash_ms":   {"value": 210, "budget": 2000, "source": "span"},
      "t_quiesce_ms":  {"value": 41,  "budget": 500,  "source": "attr"},
      "t_remount_ms":  {"value": 187, "budget": 2000, "source": "span"},
      "t_e2e_ms":      {"value": 934, "budget": 10000, "source": "harness"}
    }
  },
  "teardown": { "pass": true, "lease_registry_empty": true,
                "staging_empty": true, "no_rollback_mounts": true },
  "defects": []
}
```

### 2.6 Suite report

`…/squash/test-reports/<RUN_ID>/SUMMARY.md`, generated from the verdicts —
one row per case, read top-to-bottom in one screen per tier:

```markdown
| Case | Title | C | Space (meas/exp) | T_squash | T_quiesce | T_remount | Verdict |
| SMK-01 | idle block reclaims | ✓ | 4.1/4.0 MiB | 180 ms | — | — | PASS |
| MED-12 | EBUSY park | ✓ | +Π (parked, expected) | 195 ms | 38 ms | 240 ms | PASS |
| HRD-06 | crash matrix | ✗ 3/4 legs | leg-2 leaked S dir | … | … | … | **FAIL → defects** |
```

plus three rollups: (a) axis totals per tier; (b) a timing distribution
table (min/p50/p95/max per named timer across all cases — regression
sentinel between runs); (c) a defect list in the repo's existing
`Command / Good / Defect / Fix` iteration format
(`test-reports/TEST-REPORT.md` precedent) so failures feed the tracker
directly.

### 2.7 Diagnosis playbook (symptom → signal → cause)

| Symptom | First signal to read | Typical cause |
| --- | --- | --- |
| block `leased`, expected `reclaimed` | `blocked_reasons` string; `events.json` for a `lease.acquired` between plan and commit | legitimate late lease (test race) or a pin the fixture didn't intend — check `proc/` dump |
| `leased(quiesce_failed:freeze_timeout)` | `timings.json` `freeze_ms` vs budget; straggler pid state in `proc/` | D-state task (nested fs, NFS); raise budget only if the straggler is the fixture's point |
| `faulty_sessions` unexpectedly non-empty | `mountinfo.log` (did mount id change?), then runner span presence | runner died post-PONR — if the kill-point fired early, the case's trigger is wrong |
| space S2 over formula | `layerstack.S2.json` per-layer bytes; `active_lease_count` per layer | park/leased sessions retaining Π (expected?) vs leaked replacement lease (bug) |
| view vs `du` drift > 5% | both snapshots side by side | S-layer walk cache self-heal pending, or hardlink double-count (documented) — re-read after 1 s before failing |
| `T_e2e ≫ T_squash + ΣT_remount` | `trace.txt` waterfall gaps | forward/dispatch stall, gate contention (HRD-08 territory) |
| teardown `staging/` non-empty | `cmd.log` tail + `disk.S3.txt` | commit abort path didn't clean staging → real defect, file it |
| strict unmount EBUSY where clean expected | `mountinfo.log` rollback entry + `proc/` fd tables | leaked O_PATH dirfd (the X0.2 self-EBUSY class) or a genuine parked-fd fixture leak |

---

## 3. Performance budgets

Budgets derive from phase-0 measured baselines (Experiment log,
2026-07-02, `6.12.76-linuxkit`, ext4 named volume) with ~10× headroom for
CI noise; a case failing only its time axis reports `SLOW`, not `FAIL`,
unless the case is explicitly a performance case (marked ⏱).

| Timer | Measured baseline | Budget |
| --- | --- | --- |
| `t_freeze` 1 / 10 / 100 tasks | 76–152 µs / 250–600 µs / 1.3–2.6 ms | freeze budget 500 ms (spec default; ≥ 100× headroom) |
| `t_inspect` per session (few tasks, few fds) | O(procs × fds); ns-scan 74 µs | 250 ms |
| `T_quiesce` | ≈ freeze + inspect | **500 ms** |
| `t_switch` (stage→moves→probes→unmount) | moves are µs-scale; probe I/O-bound | 1 000 ms |
| `T_remount(i)` per session | — | **2 000 ms** |
| `t_syncfs` clean / 256 MiB foreign dirty | 33 ms / 197 ms | 500 ms / 2 000 ms (dirty cases say so) |
| `t_build` 1k-entry hardlink block | 4.0–6.4 ms per 1 000 links | 1 000 ms per 1 000 entries |
| promote rename | 24 µs | (inside `t_commit`) |
| `T_squash` small stacks (≤ 10 layers, ≤ 10 MiB) | — | **2 000 ms** |
| `T_squash` scale cases (5 k files / 500 layers / GiB-class) | — | per-case, stated inline |
| `T_e2e` per invocation (CLI+forward+op) | create_sandbox ≈ 0.5 s precedent | 10 000 ms |

---

## 4. Test catalog

Per-case format: **Spec** (sections / required tests / AC) · **Fixture** ·
**Steps** · **Correctness** · **Space** · **Time**. Snapshot names
(`S0…S3`), timer names, and fixture names are §1–§3 vocabulary. Every case
ends with the §1.3 teardown contract; it is not repeated. Cases needing
live remount enabled are marked ⛔gate; pure performance cases ⏱.

### 4.1 Smoke — SMK-01…10

Fast, single-feature, happy-path. Whole tier ≤ 5 min. These are the
`pytest -m "squash and smoke"` gate for every gateway rebuild
(`bin/start-sandbox-docker-gateway --rebuild-binary`).

#### SMK-01 — idle block reclaims at commit (B1)
- **Spec**: §B1; unit 1, 20 · AC §2. **Fixture**: `stack(3×distinct 1 MiB)`, no sessions.
- **Steps**: snapshot `S0` → `squash_layerstacks` under `/usr/bin/time` → `S2` → fresh `ws-idle` reads all three files → destroy.
- **Correctness**: result = one block, `replaced_layers:"reclaimed"`, `replaced_layer_ids` = `[L3,L2,L1]` newest-first, `manifest_version` = old+1, no `faulty_sessions` key; exit 0, exactly one stdout line. `layers/` = `{B, S…}` exactly; `staging/` empty; all three file contents byte-identical via the fresh session.
- **Space**: distinct content ⇒ byte-neutral: `S2.layers_bytes ≈ S0.layers_bytes` (±5%), layer count 4→2, sources gone at commit (no sweep needed).
- **Time**: `T_squash` ≤ 2 000 ms; assert **zero** `workspace_session.remount` spans (no sessions ⇒ `T_quiesce`/`T_remount` n/a); `T_e2e` ≤ 10 s.

#### SMK-02 — nothing to squash is a clean no-op
- **Spec**: §CLI output ("nothing to squash"); unit 17. **Fixture**: fresh stack, `B` + 1 layer.
- **Steps**: `S0` → `squash_layerstacks` → `S2` → repeat once.
- **Correctness**: `squashed_blocks: []`, exit 0; **no** `no_op` field, no `layers`/`leases` fields; manifest untouched (same version, same file mtime); no `S*` dir, `staging/` empty. Second run identical.
- **Space**: `S2 == S0` exactly (byte-for-byte `du` equality, not tolerance).
- **Time**: `T_squash` ≤ 500 ms (plan-only path); `T_e2e` ≤ 10 s.

#### SMK-03 — singleton run below a boundary is never touched
- **Spec**: vocabulary `SquashBlock` (runs ≥ 2); unit 1. **Fixture**: `stack(2×distinct 1 MiB)` + `ws-idle` leased at newest (its lease head `L2` is the boundary; below it only `[L1]`, a singleton).
- **Steps**: `S0` → `squash_layerstacks` → `S2` → session still reads both files → destroy → final squash.
- **Correctness**: `squashed_blocks: []`; `L1`,`L2` dirs untouched (same inode/mtime); session lease intact (`active_lease_count` unchanged); after destroy, the follow-up squash still reports empty (2-layer stack: `[L2,L1]` becomes one block only when no boundary splits it — assert exactly the block arithmetic the plan produces with zero leases: one block `[L2,L1]` → now `reclaimed`).
- **Space**: unchanged until the final squash; after it, layer count 3→2, bytes ±5% of `S0`.
- **Time**: first run `T_squash` ≤ 500 ms; final run ≤ 2 000 ms.

#### SMK-04 — result contract shape, success and fault
- **Spec**: §Output contract; unit 17; AC §6. **Fixture**: `stack(3×distinct 1 MiB)`; fault leg: invoke against a nonexistent sandbox id.
- **Steps**: success run; then `squash_layerstacks --sandbox-id eos-nonexistent`.
- **Correctness**: success = one stdout JSON line, key set exactly `{manifest_version, squashed_blocks}` (+`faulty_sessions` only when non-empty — assert absent), block keys exactly `{squashed_layer_id, replaced_layer_ids, replaced_layers}` (+`blocked_reasons` only when `leased`); fault = one stderr `{"error":{"kind":…}}` line, empty stdout, exit 1. No byte totals anywhere in the result.
- **Space**: n/a (SMK-01 covers) — assert only that the fault leg wrote nothing (`S` snapshot equality on the healthy sandbox).
- **Time**: fault leg `T_e2e` ≤ 5 s (fast rejection, no daemon work).

#### SMK-05 — CLI catalog placement
- **Spec**: §CLI surface; unit 18; AC §6. **Fixture**: none (catalog only).
- **Steps**: `sandbox-manager-cli --help` / catalog dump; runtime catalog dump.
- **Correctness**: `squash_layerstacks` appears under the existing `management` family with exactly one arg `--sandbox-id`; `squash_layerstack` appears in **no** CLI catalog (`cli: None`); grep of catalogs finds no `--progress`/trigger/policy options for squash.
- **Space/Time**: n/a — this case is the one pure-inspection smoke; it still writes a verdict (axes marked `n/a`, not `pass`).

#### SMK-06 — ⛔gate idle session migrates via plain staged switch (B2)
- **Spec**: §B2; C1 "no observable tasks" leg. **Fixture**: build order 3 layers → `ws-idle` (leases `[L3,L2,L1,B]`; its head `L3` is the boundary) → publish `L4`. Plan: `[L4]` singleton kept; block `[L2,L1]` below the boundary → `S1`; ws chain rewrites `[L3,L2,L1,B] → [L3,S1,B]`.
- **Steps**: `S0` → `squash_layerstacks` → `S2` → witness reads through the session (pre-created marker files) → destroy → `S3`.
- **Correctness**: block reports `reclaimed` (idle session migrated in-sweep, old run deleted on old-lease release); result has no `faulty_sessions`; session's reads unchanged post-switch; `observability layerstack --workspace-id` shows chain length 4→3; `lease.acquired`(replacement) precedes `lease.released`(old) in `events.json` (pin-overlap order); no rollback/staging residue in holder mountinfo.
- **Space**: `S2 = B + F + U + L4` with `F ≈ 2 MiB` (distinct content byte-neutral for the block, old run reclaimed): layer count 5→4; `S2.layers_bytes ≈ S0 ± 5%` (distinct-content case), old `L2,L1` dirs gone.
- **Time**: `T_squash` ≤ 2 000 ms; `T_remount` ≤ 2 000 ms; `T_quiesce` ≤ 500 ms (allowlist-only discovery: freeze may be skipped — accept `quiesce_ms ≤ 50` or absent with `outcome=migrated`); `T_e2e` ≤ 10 s.

#### SMK-07 — interactive PTY shell blocks cleanly (leased)
- **Spec**: §C6 (cwd is physics); C1 pin leg. **Fixture**: `stack(3×distinct 1 MiB)` + `ws-pty` (bash at prompt, cwd `/workspace`).
- **Steps**: `S0` → `squash_layerstacks` → `S2` → type a command into the PTY (`write_command_stdin "pwd; echo alive"` → `read_command_lines`) → exit shell → second squash → `S3`.
- **Correctness**: block `leased`, `blocked_reasons` non-empty containing a `pinned:cwd_pinned_workspace` diagnostic; shell answers normally after resume (never observed the freeze beyond a stall); old lease intact; **second** squash (shell exited) migrates: block flips effect to reclaimed (`S3` confirms deletion); result classification derived from commit GC, not plan snapshot.
- **Space**: `S2 = S0 + F` (117%-shaped: old run retained by the lease, S added); `S3 = B + F + U` (old run reclaimed after convergence).
- **Time**: run-1 `T_quiesce` ≤ 500 ms (freeze+inspect on a ~2-task PTY tree: expect ≤ 50 ms); no `namespace.exec.remount_overlay` span in run-1 (blocked before stage); run-2 `T_remount` ≤ 2 000 ms.

#### SMK-08 — ⛔gate the three observability records, and only those
- **Spec**: AC §6 (exactly three records); §2.4. **Fixture**: rerun the SMK-06 shape.
- **Steps**: squash → `observability trace --trace-id last` → `observability events` → NDJSON grep for record names.
- **Correctness**: trace waterfall contains exactly one `layerstack.squash` span (status `completed`), one `workspace_session.remount` span per swept session, ≤ 1 `namespace.exec.remount_overlay` span per attempted switch, correctly parented (runner span child of remount span child of op dispatch); `lease.acquired`/`lease.released` events present with the §2.2 ordering; grep confirms **no other** new record names (no `squash.progress`, no per-phase spans); all `dur_ms > 0`; `T_e2e ≥ T_squash + Σ T_remount` sanity holds.
- **Space**: n/a (SMK-06 covers).
- **Time**: this case *validates the timing sources themselves*: span-vs-harness clock skew ≤ 20%.

#### SMK-09 — immediate idempotence
- **Spec**: convergence policy ("no retry machinery"). **Fixture**: SMK-01 state after its squash.
- **Steps**: run `squash_layerstacks` again immediately; then once more.
- **Correctness**: both reruns return `squashed_blocks: []` (an S singleton above base with no second layer forms no ≥ 2 run); no new `S*` dirs, no staging writes, manifest version stable; substitution map unchanged (indirectly: no rewrite spans).
- **Space**: `du` byte-identical across both reruns.
- **Time**: rerun `T_squash` ≤ 500 ms — idempotent runs must be plan-only cheap.

#### SMK-10 — daemon restart: boot reap-then-sweep on a healthy stack is a no-op sweep
- **Spec**: §Boot cleanup; G3-lite; AC §5. **Fixture**: SMK-01 post-squash state + one `ws-idle`.
- **Steps**: `S0` → `docker restart` the sandbox container (daemon SIGKILL + restart path) → poll daemon ready → `S2` → create a fresh session, run a command → destroy.
- **Correctness**: holder + pid-ns init provably gone before restart completes (poll `/proc` via `docker exec`); boot emits reap record(s) for the dead session **before** any sweep record; sweep deletes nothing (disk == manifest already); no session resurrects (`list` shows none); fresh session + command succeed; `B*` untouched.
- **Space**: `S2 == S0` minus the reaped session's run dir/upperdir (`U → 0`); layers byte-identical.
- **Time**: boot cleanup adds ≤ 2 s to daemon ready (harness clock around restart-to-ready).

### 4.2 Medium — MED-01…20

One interaction or fault dimension per case. Encoding cases (01–03) run
commit-only; most others are ⛔gate.

#### MED-01 — whiteout winners: re-emitted only when masking, dropped when net-nothing
- **Spec**: vocabulary `flatten`; unit 2; X1.1 encodings. **Fixture**: seed contains `seed.txt`; `L1`: `rm /workspace/seed.txt` (whiteout must mask the base); `L2`: `touch /workspace/tmp && rm /workspace/tmp` (net-nothing inside the block).
- **Steps**: squash idle → inspect `layers/S…` directly (`docker exec` `stat`/`find`) → fresh session reads → `S2`.
- **Correctness**: `S` contains a **char 0:0 device** whiteout for `seed.txt` (the X1.1-measured encoding) and **no entry at all** for `tmp` (whiteout emitted only as a masking winner); merged view: both absent in a fresh session; `file_read seed.txt` returns structured not-found through the daemon path too.
- **Space**: delete-heavy: `F ≈ 0` ⇒ `S2.layers_bytes ≈ B + ε` for the block's contribution; layer count collapses to `{B, S}`.
- **Time**: `T_squash` ≤ 2 000 ms.

#### MED-02 — opaque dir is dual-encoded and never resurrects
- **Spec**: flatten dual-encoding decision (Decision log 2026-07-02); unit 2; G2's teeth. **Fixture**: seed dir `d/{a,b}`; `L1`: `rm -rf /workspace/d && mkdir /workspace/d && touch /workspace/d/new`; `L2`: any distinct file (makes the run ≥ 2).
- **Steps**: squash idle → inspect `layers/S…/d` → fresh session `ls /workspace/d` → capture-path read (`file_read`).
- **Correctness**: `S…/d` carries **both** encodings: `.wh..wh..opq` marker file **and** `user.overlay.opaque=y` xattr (`getfattr`); kernel view (fresh session): `d` contains exactly `{new}` — seed `a,b` never resurface; daemon `MergedView` path agrees (marker honored). Negative-control inspection: assert the marker alone would be insufficient is **not** re-proven here (G2/HRD-18 owns it) — this case pins the dual write.
- **Space**: `F` ≈ bytes of `new` + dir entries; sources reclaimed.
- **Time**: `T_squash` ≤ 2 000 ms.

#### MED-03 — witness matrix: dir-created-then-emptied, modes, shadowed subtrees
- **Spec**: invariant 1; unit 2; witness-file convention. **Fixture**: 3 witness layers per §1.2 (`wit/only-in-Li`, deleted-in-`Li+1`, dir-created-then-emptied `wit/dce/`, mode-0640 file), plus a subtree fully shadowed by `L3` (newest-wins drop).
- **Steps**: squash idle → fresh-session witness reads → direct `S` inspection.
- **Correctness**: every witness read exact — presence, absence, **`wit/dce/` exists and is empty** (explicit dir entry survived flatten), `stat -c %a` = 640; the shadowed subtree's old versions absent from `S` (single winner per path); no symlink was followed during the walk (plant a symlink pointing outside the layer tree; assert its *target* content is not in `S`, the link itself is).
- **Space**: `S` byte-size ≈ Σ winners only (measure; shadowed bytes gone).
- **Time**: `T_squash` ≤ 2 000 ms.

#### MED-04 — ⏱ hardlink flatten is metadata-bound and byte-neutral at peak
- **Spec**: §D re-squash cost; unit 2 hardlink assertions; X1.3/X0.8 baselines. **Fixture**: `stack(2 layers × 500 distinct 100 KiB files)` = 1 000 entries, `P0 ≈ 100 MiB`.
- **Steps**: `S0` + `df` free-bytes → squash with 250 ms `S1` peak polling (`du staging/`, `df`) → `S2`.
- **Correctness**: sampled file contents intact post-squash; whole-file winners were hardlinked: **fs free space never drops by more than ε = 10 MiB during build** (link(2) copies zero bytes; only re-encoded content + metadata may write); after commit GC the sources are gone and `S` files show `nlink == 1` with content intact.
- **Space**: `S2.layers_bytes ≈ S0 ± 5%` (distinct content, byte-neutral); peak `S1 ≤ S0 + ε`, **not** `S0 + P0`.
- **Time** (the point of the case): `t_build` ≤ 1 000 ms for 1 000 entries (measured 4–6.4 ms/1 000 links + walk); `T_squash` ≤ 3 000 ms; record `entries_walked`, `build_ms` into the timing distribution.

#### MED-05 — racing publishes: run-presence recheck, tail preserved, no starvation
- **Spec**: `commit recheck`; unit 4; §Storage phase table. **Fixture**: `stack(6×churn 10 MiB)`; a background publisher loop firing 10 one-shot `exec_command`s (~1 layer each) for the whole squash window.
- **Steps**: start publisher loop → `squash_layerstacks` → join loop → `S2`.
- **Correctness**: exit 0; every racing publish also succeeded (10 new `L` layers exist); final active manifest = `[tail…, S, B]` with the tail **above** `S` in publish order; `manifest_version` = latest-at-commit + 1; block classification unaffected by the race; no `operation_failed` (recheck is run-presence, publishes only prepend — a conflict abort here is a defect).
- **Space**: `S2 = B + F + tail bytes + U`; sources reclaimed.
- **Time**: `T_squash` ≤ 5 000 ms under publish load; record `commit_ms` (the exclusive section must stay small — flag > 500 ms).

#### MED-06 — singleflight per root under concurrent invocations
- **Spec**: unit 5; §Storage singleflight. **Fixture**: `stack(6×distinct 5 MiB)`; two `squash_layerstacks` processes launched simultaneously (`&` + `wait`).
- **Steps**: launch both → collect both results → `S2`.
- **Correctness**: both exit cleanly — one commits the block, the other waits and reports `squashed_blocks: []` (or the documented clean failure; either way **no** interleaved build); exactly **one** `S` layer exists for the block; peak polling never observes two staging trees; combined stdout parses as two valid single-line results.
- **Space**: identical to a single SMK-01-style run — peak = one builder's staging.
- **Time**: `T_e2e(slow invocation)` ≈ `T_e2e(fast) + wait` — serialized, not corrupted; both ≤ 15 s.

#### MED-07 — lease acquired between plan and commit keeps sources (GC decides, not the plan)
- **Spec**: unit 3, 20; policy "reclaimed-vs-leased decided at commit instant". **Fixture**: `stack(2 layers × 2 500 small files)` (5 000 entries stretch `t_build` into a visible window); mid-build, create `ws-pty` (cwd-pinned so the sweep cannot migrate it away).
- **Steps**: start squash → poll `staging/` appearance → create `ws-pty` during build → squash returns → `S2` → destroy session → follow-up squash → `S3`. Timing race is real: if the session's lease landed after the commit (detectable — the block reports `reclaimed`), rebuild with a 2× larger fixture and retry, cap 3, attempts logged.
- **Correctness**: block reports `leased` with non-empty `blocked_reasons`; **source dirs survive on disk** (commit GC saw the new lease); the late session is fully healthy (reads via its chain); after destroy + follow-up squash the old run reclaims (`S3`).
- **Space**: `S2 = S0 + F` (117%-shaped); `S3 = B + F + U'`.
- **Time**: `T_squash` ≤ 10 000 ms (5 k entries); record `build_ms` vs MED-04 scaling (should be ≈ 5× the 1 k-entry number, linear O(E)).

#### MED-08 — ⛔gate live migration under a running batch command (E5)
- **Spec**: E5; B5 ws-3; unit 22 core. **Fixture**: `stack(6×churn 20 MiB)` (`P0 = 120 MiB`, `F ≈ 20 MiB`); `ws-batch` running `sh -c 'cd /; echo start >> /workspace/hb; sleep 45; echo done >> /workspace/hb'`; a `file_write` upperdir write lands **before** the squash.
- **Steps**: `S0` → `squash_layerstacks` mid-sleep → `S2` → wait for command exit → read `hb` + witnesses → destroy → `S3`.
- **Correctness**: outcome `migrated`; the command never errors — after natural wake it appends `done` (both lines present, exit 0: the fd was reopened by `>>` post-migration on NEW); pre-freeze upperdir writes visible post-resume; absolute-path witness reads land on NEW (block content via `S`); chain shortened (view `--workspace-id` + witness reads, never mount options); old source dirs deleted from disk **within the invocation**; block reports `reclaimed` (this was the only pinning session).
- **Space**: the §D "17%" row: `S2 ≈ B + 20 MiB + U` vs `S0 ≈ B + 120 MiB + U` — assert ≥ 75% reduction of candidate-run bytes.
- **Time**: `T_quiesce` ≤ 500 ms (record `freeze_ms`, ~2 tasks ⇒ expect ≪ 50 ms); `t_switch` ≤ 1 000 ms; `T_remount` ≤ 2 000 ms; `T_e2e` ≤ 10 s.

#### MED-09 — ⛔gate escaped-pgid child with an fd pin blocks, then converges (E1)
- **Spec**: E1; quiesce discovery union. **Fixture**: `stack(3×distinct 1 MiB)`; `ws-batch` running `sh -c 'cd /; setsid sh -c "exec 3</workspace/f0; sleep 300" & sleep 300'` — the fd holder escapes the command pgid via `setsid`.
- **Steps**: squash → assert block leased → kill the escapee (in-session `pkill -f "sleep 300"` scoped to the child, or destroy-and-recreate shape) → second squash → `S3`.
- **Correctness**: discovery (cgroup ∪ ns-scan) finds the escapee — it reaches `T` within budget (observe `/proc/<pid>/stat` from outside); `blocked_reasons` carries `pinned:fd_pinned_workspace`; **both** parent and child resume (states `S`/`R` post-sweep) and the parent command completes; old lease intact; second squash (escapee gone) migrates and reclaims.
- **Space**: run-1 `S2 = S0 + F`; run-2 `S3 = B + F + U`.
- **Time**: `T_quiesce` run-1 ≤ 500 ms including the full `/proc` ns-scan (X0.9: scan 74 µs — record actual).

#### MED-10 — ⛔gate child mount pins even after its creator exited (one mountinfo read)
- **Spec**: E4 child-mount sub-case; §C4 holder-mountinfo rule. **Fixture**: `stack(3×distinct 1 MiB)`; session runs `sh -c 'mkdir -p /workspace/m && mount --bind /workspace/m /workspace/m'` — the task **exits**, the vfsmount stays.
- **Steps**: squash (session now has no observable tasks) → assert leased → in-session `umount /workspace/m` → second squash → `S3`.
- **Correctness**: despite the "no observable tasks" fast path, the **one holder-mountinfo read** still blocks: `blocked_reasons` = `pinned:child_mount_pinned_workspace`; **zero `MS_MOVE` attempted** (workspace root mount id unchanged in `mountinfo.log` for the whole window); old lease intact; after the unmount the second squash migrates.
- **Space**: run-1 retains sources; run-2 reclaims (`S3 = B + F + U`).
- **Time**: `T_remount` run-1 ≤ 500 ms (blocked at inspect — cheap); run-2 normal budgets.

#### MED-11 — ⛔gate nested mount namespace blocks remount (E2)
- **Spec**: E2 (EBUSY does **not** subsume escape detection). **Fixture**: `stack(3×distinct 1 MiB)`; `ws-batch` running `sh -c 'cd /; unshare -m sleep 300'` — zero workspace fds; the copied vfsmount is the only pin.
- **Steps**: squash → assert leased → kill the escapee in-session → second squash → `S3`.
- **Correctness**: `blocked_reasons` = `pinned:mount_namespace_escaped` (ns-scan leg found a task whose `ns/mnt` differs); **no `MS_MOVE` ever attempted** (mount id unchanged — outside observation); old layers retained; after the kill, the next run migrates. This case exists because a copied vfsmount pins layers *without* making the rollback unmount busy.
- **Space**: run-1 `S2 = S0 + F`; run-2 reclaims.
- **Time**: `T_quiesce` ≤ 500 ms (ns-scan cost recorded); run-1 `T_remount` ≤ 500 ms (blocked pre-stage).

#### MED-12 — ⛔gate strict-unmount EBUSY parks with both leases (E6, park half)
- **Spec**: E6; C5 park row; X0.5. **Fixture**: `stack(3×distinct 1 MiB)`; session runs the SCM_RIGHTS probe (static binary `file_write`-installed into `/workspace`, made executable): opens `/workspace/f0`, sends the fd to itself over a socketpair, closes the local copy, blocks — the fd lives only in the socket queue, invisible to `/proc/*/fd`.
- **Steps**: `S0` → squash → `S2` → post-park liveness: `file_read` + `file_write` through the session → hold for HRD-07 (convergence) or destroy → `S3`.
- **Correctness**: freeze finds **zero** pins; switch verifies; strict `umount2(rollback, 0)` returns EBUSY → **park**: report `leased(pinned:rollback_unmount_busy)`; session resumed **on NEW** (witness read of `S` content through the session); registry holds **both** leases (view: old-run layers and new chain all `active_lease_count ≥ 1`); old run **not** deleted; holder mountinfo shows the old mount parked at the masked rollback point; runner span reports `first_move_succeeded=true, mount_verified=true`.
- **Space**: `S2 = S0 + F` — parked Π retained **by design**; verdict formula says so (`parked, expected`).
- **Time**: `T_remount` ≤ 2 000 ms including the EBUSY leg; no retry loop (exactly one switch attempt in the trace).

#### MED-13 — ⛔gate post-PONR runner death is faulty: reported, then destroyed (E7)
- **Spec**: E7; C5 faulty row; vocabulary `faulty remount`. **Fixture**: `stack(3×distinct 1 MiB)`; two sessions — `ws-batch` (the victim, zero pins) and `ws-idle` (control); mountinfo poller armed with `kill-point(moved)`.
- **Steps**: squash → poller SIGKILLs the runner the instant the workspace root's mount id changes (report never arrives) → result → verify control session migrated → destroy-side assertions → `S3`.
- **Correctness**: stdout result carries `faulty_sessions` = `[{session_id, class_detail, lease_errors}]` (free-form detail ≈ `mount_uncertain:runner_report_missing`; **no** byte totals, no phase enum); **exit 0** — the squash committed; the victim was destroyed through the ordinary path (absent from session list; `workspace_session.destroy` span present); namespace death released both leases (registry counts return to baseline); layers only the victim pinned reclaim; committed manifest untouched; the control session in the same sweep migrated normally.
- **Space**: `S3 = B + F + U(control)` — victim's retained bytes fully reclaimed after destroy.
- **Time**: record `t_ponr` (harness) and the gap to destroy completion; budget: destroy-side cleanup ≤ 5 s.

#### MED-14 — daemon crash between promote and manifest rename (E10, single leg)
- **Spec**: E10 point 4; unit 6; X3.3 rehearsal. **Fixture**: `stack(2 layers × 2 500 small files)` (the 5 k-entry build + ~33 ms syncfs stretch the promote→rename window); an outside poller watching `layers/S*` appear.
- **Steps**: start squash → the instant `layers/S…` exists while `manifest.json` is still old, `SIGKILL` the daemon (`docker exec pkill -9`) → if the rename already landed, destroy/rebuild and retry (cap 5 attempts, log attempts) → restart daemon → `S2` → fresh squash.
- **Correctness**: post-restart: **old manifest intact**, orphan `S` dir swept by boot (it has no sidecars to leak), `staging/` empty, layers on disk == old manifest exactly; no session state resurrects; a fresh `squash_layerstacks` then commits normally.
- **Space**: post-boot `S2` == pre-squash `S0` byte-identical; post-resquash == SMK-01 shape.
- **Time**: boot reap+sweep ≤ 2 s added to ready; record which attempt hit the window (flakiness telemetry).

#### MED-15 — boot reap-then-sweep with planted orphans and live sessions (G3 core)
- **Spec**: G3; §Boot cleanup 1–3; unit 14. **Fixture**: two sessions (one `ws-idle`, one `ws-pty` with a live command); hand-planted orphan staging tree `staging/S999-fake.staging/` and orphan promoted dir `layers/S999-fake/` not in the manifest (`docker exec mkdir/cp`).
- **Steps**: `SIGKILL` the daemon → poll `/proc` until holder **and** pid-ns init are gone (bounded wait — this asserts PDEATHSIG, not deployment luck) → restart → read observability log order → `S2` → create fresh session + run command.
- **Correctness**: every reap record precedes the first sweep deletion record; both sessions' run dirs and handles gone; `staging/` empty; orphan `S999` dirs gone; layers on disk == active manifest **exactly**; `B*` untouched; fresh session + command succeed.
- **Space**: `S2 = B + manifest layers` exactly (`U → 0`).
- **Time**: restart-to-ready ≤ 5 s with reap+sweep included.

#### MED-16 — sidecar hygiene: S has none; lease GC removes `.digest` + `.bytes` (leak regression)
- **Spec**: unit 21, 14; AC §2/§5. **Fixture**: `stack(2×distinct 1 MiB)` + `ws-idle` leased over both; squash (migrates, old run reclaims); then one more publish on top of `S`.
- **Steps**: squash → inspect `.layer-metadata/` → publish-on-S (one-shot exec) → view check → `S2`.
- **Correctness**: no `.digest`/`.bytes`/ledger entry exists for the `S` id (find on `.layer-metadata/`); the reclaimed `L` layers' `.digest` **and** `.bytes` were deleted with their dirs (regression for the pre-existing `.bytes` leak); the follow-up publish succeeds with S below it (dedup miss is silent — no error, no warning record); `observability layerstack` sizes `S` by walking (bytes field ≈ `du` of the S dir ± 5%).
- **Space**: `.layer-metadata/` entry count == L-layer count exactly.
- **Time**: standard budgets; the publish-on-S must not regress publish latency (compare to a baseline publish span).

#### MED-17 — ⛔gate persist failure still migrates; restart reaps the stale handle (test 12 e2e)
- **Spec**: unit 12; C5 verified-switch row; environment fact 3. **Fixture**: `stack(3×distinct 1 MiB)`; `ws-batch` zero-pin; before squash, `docker exec chattr +i` on `manager.json` (external, src-free persist-failure injection).
- **Steps**: squash → assert migrated → `chattr -i` → `SIGKILL` daemon + restart → `S2` → fresh session.
- **Correctness**: outcome `migrated` despite `persist_handles()` failing (best-effort by design); tasks resumed on NEW; old lease released and old run reclaimed **in the same invocation**; after restart, boot reap destroys the run dir from the **stale** handle with nothing leaked (no orphan run dirs, no orphan retired workdirs); fresh session + squash succeed.
- **Space**: post-sweep `S2 = B + F + U`; post-restart `U → 0`, byte-exact manifest match.
- **Time**: standard; record that the persist failure added no retry latency (single `persist` attempt in trace).

#### MED-18 — ⏱ OVL_MAX_STACK creation boundary: 500 mounts, 501 fails with the documented error
- **Spec**: §D chain-length; E9 creation half; X0.7. **Fixture**: publish 500 tiny distinct layers (batched one-shot execs; ⏱ slow case — budget the build loop, not just the squash).
- **Steps**: at 500 layers `create_workspace_session` → succeeds (500 lowerdirs mount) → destroy it → publish layer 501 → `create_workspace_session` → must fail → `squash_layerstacks` (idle) → create again → `S2`.
- **Correctness**: the 501-layer creation fails with the **distinct documented error** (fsconfig `lowerdir+` EINVAL surfaced as the mount-build error shape — assert kind/message, exit 1); after squash the stack is `{B, S, (tail)}` and creation succeeds again; repeated squash runs stable (idempotent, no side effects).
- **Space**: 501 tiny layers → `{B,S}`; layer-dir count 502 → 2.
- **Time**: `T_squash` for a 501-source block ≤ 15 s (walk-dominated; record `build_ms` for the scaling table); creation-attempt failure fast (≤ 5 s).

#### MED-19 — ⛔gate masks never observable; mask-restore failure is a clean pre-PONR skip (E3)
- **Spec**: E3 both legs; C3 steps 1–3; AC §4. **Fixture**: (a) SMK-06 shape + a daemon-side observer loop stat'ing the hidden daemon paths through `/proc/<holder>/root/<hidden>` for the whole sweep; (b) same shape with the test-controlled **read-only mask source** arranged before the sweep so remask (C3 step 3) must fail.
- **Steps**: leg (a) squash-with-observer → migrated; leg (b) squash → clean skip → in-session stat of the hidden paths → second squash after clearing the injection.
- **Correctness**: (a) migration succeeds; post-resume in-session stats return the **masked** view; the observer never correlates an unmasked window with a resumable (non-frozen) task — every non-allowlisted task was frozen for the entire unmask window (freeze span covers the runner's build window in the trace timeline). (b) `leased(stage_failed:mask_restore_failed)`; masks verifiably restored (in-session stat post-resume); **no move attempted** (mount id unchanged) — pinning remask-before-moves as pre-PONR; old lease intact; recovery run migrates.
- **Space**: leg (b) retains sources until the recovery run.
- **Time**: standard budgets; leg (b) `T_remount` ≤ 1 000 ms (fails at step 3).

#### MED-20 — ⏱⛔gate quiesce at 100 tasks: freeze cost, membership stability, total resume
- **Spec**: §D sweep budget; X0.9 baseline; unit 22's freeze leg. **Fixture**: `stack(3×distinct 1 MiB)`; `ws-batch` running `sh -c 'cd /; for i in $(seq 100); do sleep 300 & done; sleep 300'` — 101 freezable tasks, zero pins.
- **Steps**: `S0` → squash → poll all task states from outside during the window → `S2` → wait/kill command → destroy.
- **Correctness**: all 101 tasks reach `T` within the freeze budget; membership stable (no `quiesce_failed:membership_changed` — the fixture forks *before* the sweep); outcome `migrated`; **every** task resumes (`S`/`R` states post-sweep, count exact); no straggler left `T` (post-sweep scan).
- **Space**: standard migrate reclaim.
- **Time** (the point): `freeze_ms` recorded and ≤ 500 ms budget — expect ≤ 50 ms (X0.9: 100 tasks all-`T` in 1.3–2.6 ms; the budget check guards regression, the raw number feeds the distribution table); `inspect_ms` scales O(tasks × fds) — record; `T_remount` ≤ 2 000 ms.

### 4.3 Very difficult — HRD-01…20

Multi-dimensional shapes, kill/crash matrices, adversarial floors, scale.
All ⛔gate unless commit-only is stated. These cases are the reason the
artifact bundle exists — every one has failed-run forensics designed in.

#### HRD-01 — B3 replay: multi-block plan, mixed classification, reclaim cascade
- **Spec**: §B3 exactly; units 1, 3, 13. **Fixture**: 5 MiB distinct layers, built in lease-interleaved order: `L0–L3` → `ws-C` → `L4,L5` → `ws-B`,`ws-D` → `L6–L9` → `ws-A` → `L10–L12`; `ws-D` gets a PTY (cwd pin); A/B/C idle.
- **Steps**: `S0` → one `squash_layerstacks` → `S2` → per-session chain checks → exit ws-D's shell → second squash → `S3` → destroy all → final assertions.
- **Correctness** (spec B3's squashed layers, written `Sa`/`Sb`/`Sc` here to avoid colliding with snapshot names): plan = boundaries `{L9,L5,L3}`, blocks `[L11,L10]→Sa`, `[L8,L7,L6]→Sb`, `[L2,L1,L0]→Sc`; result: `Sa` `reclaimed` (commit GC — no lease), `Sb` `reclaimed` (ws-A migrated in-sweep), `Sc` `leased` + `blocked_reasons` = cwd diagnostic (ws-D); singleton `L4` and boundary layers untouched byte-identical; post-sweep chains (view + witness reads): ws-A `[L9 Sb L5 L4 L3 Sc B]` (11→7), ws-B `[L5 L4 L3 Sc B]` (7→5), ws-C `[L3 Sc B]` (5→3); disk after run-1: `L11,L10,L8,L7,L6` gone, `L2,L1,L0` present (the cascade's pinned tail); run-2 (shell exited) migrates ws-D and reclaims `L2,L1,L0`.
- **Space**: distinct content is byte-neutral overall (`F ≈ P0 = 40 MiB` across the three blocks), so the assertion is the Π ledger: after run-1 disk = `S0 + 15 MiB` (the `Sc` sources ws-D still pins), after run-2 disk = `S0 ± 5%`; on-disk layer dirs 14 → 12 (run-1: 9 manifest layers + 3 pinned sources) → 9 (run-2); manifest layers 14 → 9.
- **Time**: run-1 sweeps 4 sessions serially — `Σ T_remount ≤ 8 s`, each ≤ 2 s; `T_quiesce(ws-D)` ≤ 500 ms; `T_e2e` ≤ 20 s.

#### HRD-02 — B4 replay: two generations, re-squash of S, generation-crossing rewrite, identity short-circuit
- **Spec**: §B4; unit 8; X2.2. **Fixture**: gen-1: `L1–L8` churn content, `ws-1` = PTY @head `L8`, `ws-2` idle @head `L4`. Then: squash #1 → `v10 [L8 Sa L4 Sb B]`; ws-1 shell exits + destroy; publish `L10,L11`; create `ws-3` (head `L10`) running `ws-batch`; background publisher drops `L12` mid-build of squash #2.
- **Steps**: squash #1 → assertions → mutate world → squash #2 → `S2` → convergence checks → destroy all.
- **Correctness**: squash #1: blocks `[L7,L6,L5]→Sa`, `[L3,L2,L1]→Sb`; ws-2 migrates to `[L4 Sb B]`, ws-1 leased. Squash #2: blocks `[L11]` singleton kept, `[L8 Sa]→Sc` (**re-squash of a prior S** — S layers are not boundaries), `[Sb]` singleton kept; racing `L12` rides the recheck (commit `v13 = [L12 L11 L10 Sc L4 Sb B]`). Sweep #2: ws-3's rewrite is the **generation-crossing contraction** `[L10 L8 Sa L4 Sb B] → [L10 Sc L4 Sb B]` (raw run `Sc→[L8,Sa]` applied oldest-first, no expansion) — migrates **live** under the running command; `L8` and `Sa` deleted on old-lease release; ws-2's rewrite is **Identity** — short-circuits before any freeze (assert: no freeze window on ws-2's tasks — state samples stay `S` throughout; remount span outcome `identity` or absent). Witness reads through ws-3 span both generations (gen-1 content reachable through `Sc` ∘ `Sa` flatten) — byte-exact.
- **Space**: churn fixture makes it visible: gen-1 `P0 = 8s`, `F ≈ s`; after squash #2 + sweep, candidate bytes ≈ `s` (+tail `L10–L12`); assert `L8`,`Sa` dirs gone, `Sb` alive (singleton pinned by ws-2's chain).
- **Time**: standard budgets; record squash #2 `t_build` (re-flatten of `[L8,Sa]` should be hardlink-cheap — compare to gen-1).

#### HRD-03 — B5 replay: every hard path in one sweep, zero faulty, full convergence
- **Spec**: §B5; unit 22. **Fixture**: one stack, four sessions in one sweep: `ws-3` = `ws-batch` (live protocol), `ws-2` = created post-squash-#0 so its chain contains no map runs (identity), `ws-5` = PTY (cwd pin), `ws-6` = `pin(scm-fd)` (EBUSY park).
- **Steps**: prep squash #0 (records map history for ws-2's identity), build the four sessions → `S0` → squash #1 → `S2` → per-session assertions → clear pins (exit shell; destroy parked ws-6) → squash #2 → `S3`.
- **Correctness**: one invocation yields simultaneously: `migrated(live)` (ws-3 — command never notices, pre-freeze upperdir writes visible), `identity-untouched` (ws-2 — no freeze), `leased(pinned:cwd…)` (ws-5 — resumes ≤ budget), `leased(pinned:rollback_unmount_busy)` (ws-6 — parked on NEW, both leases); **zero `faulty_sessions`** — "faulty" stays narrow under stress; run-2 converges: ws-5 migrates (plain switch), parked ws-6's destroy released both leases; no persisted sweep state existed between runs (nothing on disk names remount — grep the root).
- **Space**: `S2 = B + F + U + Π(ws-5 ∪ ws-6 old runs)`; `S3 = B + F + U'` — assert the two-step Π ledger concretely.
- **Time**: 4-session sweep `Σ T_remount ≤ 8 s`; ws-2 costs ≈ 0 (identity — assert `T_remount(ws-2) ≤ 50 ms` or span absent); distribution recorded.

#### HRD-04 — E4 full pin matrix: eleven classes, one sweep, zero moves, zero leaks
- **Spec**: E4; §C4 map; blocked-class table. **Fixture**: eleven sessions on one stack, one pin each: (1) PTY cwd; (2) root pin — static busybox planted in workspace, `chroot /workspace /busybox sleep 300`; (3) fd pin; (4) mmap of `/workspace/a b.txt` (space in path — probe binary); (5) mmap then unlink (`(deleted)` suffix); (6) child mount by exited task; (7) `io_uring` anon fd (probe); (8) ptrace tracer outside the frozen set (`docker exec strace -p <tid>` from outside → task in `t`); (9) unreadable `/proc` entry — **best-effort** (env-sensitive, verdict may record `skipped:not_constructible`); (10) fork loop → `membership_changed`; (11) D-state freeze straggler via a test-controlled `fsfreeze`-frozen nested fs → `freeze_timeout` — **best-effort** per spec.
- **Steps**: `S0` → one `squash_layerstacks` → per-session classification → follow-up command in **every** session → `S2` → teardown.
- **Correctness**: each session yields its exact expected `class:detail` (matrix table in the test source mirrors §C4); **zero `MS_MOVE`s** across the entire sweep (every session's mount id unchanged in `mountinfo.log`); every session resumes and runs a follow-up command successfully; all old leases intact; replacement-lease accounting returns to baseline — `lease.acquired`/`lease.released` events pair exactly (none leaked); classes (9)/(11) may be skipped-with-reason but never mis-classified.
- **Space**: everything retained: `S2 = S0 + F` exactly (the sweep must not delete a single source byte).
- **Time**: per-session `T_quiesce` ≤ 500 ms (except (11): `freeze_timeout` fires **at** the budget — assert it, and that the sweep proceeds to the remaining sessions); `T_e2e` ≤ 30 s for 11 sessions.

#### HRD-05 — E8 PONR boundary: three induced points, outcomes are a pure function of the report
- **Spec**: E8; vocabulary `point of no return`; unit 19. **Fixture**: three sequential runs on rebuilt stacks, one `ws-batch` each: (i) **failed first move** — an in-session `mount --make-rshared /` turns the workspace parent's propagation shared, so `MS_MOVE` out of it fails `EINVAL`; (ii) runner SIGKILLed at `kill-point(staged)` (staged build visible, no mount-id change); (iii) runner SIGKILLed at `kill-point(moved)`+after visible probe (mount id changed, staging gone — report suppressed by the kill).
- **Steps**: per leg: arm trigger → squash → classify → verify session state → converge or destroy.
- **Correctness**: (i) clean skip — `first_move_succeeded=false` in the runner span, session untouched, `leased(…)`, old mount intact and usable; (ii) clean skip — report present/pre-PONR or missing *before* first-move-success ⇒ still clean, session untouched; (iii) **faulty** — missing report at/past first-move-success ⇒ `faulty_sessions` + ordinary destroy. Each outcome must be reproducible as a pure function of `first_move_succeeded` + `mount_verified` + report presence — the harness recomputes the C5 row from the runner span alone and asserts it matches what the daemon did.
- **Space**: legs (i)/(ii): zero reclaim, zero loss; leg (iii): victim's bytes reclaim after destroy.
- **Time**: record `t_ponr` alignment: leg (ii) must show **no** mount-id change ever; leg (iii)'s kill lands ≤ 50 ms after the change (poller resolution — telemetry for trigger quality).

#### HRD-06 — E10 crash matrix: four kill points, one recovery path
- **Spec**: E10; boot cleanup; environment facts 1–2. **Fixture**: rebuilt per leg: (a) daemon SIGKILL **mid-freeze** (victim tasks observed in `T`); (b) **mid-switch** (workspace mount-id change observed, kill immediately); (c) **post-switch, pre-release** (staging gone + id changed, kill in the release window — accept retry loop, cap 5); (d) **promote→rename** (MED-14's leg, rerun inside the matrix for one-suite completeness).
- **Steps**: per leg: arm → squash → kill daemon at point → poll holder + pid-ns init death (bounded — PDEATHSIG assertion; leg (a) additionally proves SIGKILL works on stopped tasks) → restart → `S2` → fresh session + fresh `squash_layerstacks`.
- **Correctness**: every leg: no session state resurrects; restart runs reap-then-sweep once before serving; disk == active manifest **exactly** (old manifest for leg (d)); no orphan staging/rollback mounts anywhere; fresh session and fresh squash both succeed. **No remount-specific recovery branch exists to test — that absence is the assertion** (the same three boot steps handle all four legs identically; the harness asserts the boot records are shape-identical across legs).
- **Space**: post-restart byte-exact manifest match per leg.
- **Time**: restart-to-ready ≤ 5 s every leg; retry telemetry for legs (b)/(c) windows.

#### HRD-07 — EBUSY park convergence: Identity next run, both leases to destroy, reclaim at death
- **Spec**: E6 second half; unit 15; C5 park row. **Fixture**: MED-12's parked end-state (scm-fd park, both leases held).
- **Steps**: parked `S2` → **second** `squash_layerstacks` → no-op assertions → exercise session on NEW (reads + copy-up `file_write`) → `destroy_workspace_session` → `S3` → final squash.
- **Correctness**: second squash sees **Identity**: no second freeze (task states sampled at 10 ms never leave `S`/`R` during run-2), no second switch (mount id unchanged), result `squashed_blocks: []`; session fully usable on NEW throughout; destroy: namespace death releases **both** leases (registry → baseline), the parked rollback mount vanishes with the namespace, old run reclaims on disk. Pins the restore-ladder deletion end to end — any second freeze/switch in run-2 is a defect.
- **Space**: `S2` holds `F + Π(parked)`; `S3 = B + F` — the park's Π reclaims exactly at destroy, not before.
- **Time**: run-2 `T_e2e` ≤ 5 s (plan-only + identity sweep); destroy ≤ 5 s.

#### HRD-08 — admission-gate storm: per-session serialization without a global bottleneck
- **Spec**: unit 9; lock discipline; AC §4 gate criterion. **Fixture**: two sessions: `ws-A` = MED-20's 100-task batch (long quiesce+switch window), `ws-B` = idle control. During ws-A's sweep window, fire concurrently against **ws-A**: `exec_command`, `file_write`, `file_read`, `destroy_workspace_session` (last), plus a one-shot `exec_command` on the sandbox; and against **ws-B**: a steady file-op loop.
- **Steps**: squash → concurrent barrage (launched at `kill-point(staged)` observation) → join all → trace-order assertions → `S2`.
- **Correctness**: no deadlock (every call completes ≤ 30 s global timeout); ws-A calls **wait**: their spans start after the remount span resolves (trace ordering), destroy waited for the attempt to resolve; no call ever observed a half-switched mount (`file_read` results consistent with either fully-old or fully-new view, never mixed); **ws-B's loop never stalls** — per-session gate, not a global lock: ws-B file-op latency during the sweep ≈ baseline (p95 within 2×), the load-bearing proof that deleting `session_lifecycle_lock` didn't just relocate the bottleneck.
- **Space**: standard; destroy-at-end reclaims ws-A.
- **Time**: the case's product is the ws-B latency histogram during ws-A's sweep — attach to the distribution table.

#### HRD-09 — one-shot finalize/timeout hook firing mid-switch never interleaves
- **Spec**: unit 9 (completion hooks); C1 gate list ("finalize_one_shot must acquire it"). **Fixture**: a session whose one-shot command has a deadline chosen to expire inside the switch window (staged-build observation arms the overlap; retry until the deadline timestamp ∈ [stage_appear, switch_end], cap 10 — log attempts).
- **Steps**: launch deadline'd one-shot → squash → observe overlap → join → forensic trace read → `S2`.
- **Correctness**: the deadline SIGKILL of the frozen pgid is benign (task death only sheds pins); the finalize hook's capture/destroy **blocked on the per-session gate** until the switch attempt resolved — the trace shows `workspace_session.remount` and the finalize spans strictly serialized, never overlapping the MS_MOVE window; final state is exactly one of the legal serializations (migrated-then-finalized, or attempt-resolved-then-finalized); captured content (if the finalize captured) is consistent — no half-switched reads; no leaked leases; session ends destroyed via its ordinary one-shot path.
- **Space**: post-case baseline exact (one-shot reclaimed everything).
- **Time**: overlap-hit telemetry (which attempt landed); finalize wait ≤ switch duration + 1 s.

#### HRD-10 — dense-pinning adversarial floor: blocks vanish, then mass convergence
- **Spec**: §D "dense pinning is the adversarial floor"; unit 1. **Fixture**: publish→lease interleave ×6: after every 5 MiB publish, create a `ws-idle` (every layer becomes a boundary or singleton — zero blocks ≥ 2 exist).
- **Steps**: `S0` → squash #1 (expect no-op) → repeat squash (stability) → destroy all six sessions → squash #2 → `S3`.
- **Correctness**: squash #1: `squashed_blocks: []` — the plan correctly finds zero blocks under maximal boundary density; repeated runs stable (no S dirs ever minted, no staging writes, manifest version unchanged); sessions all healthy; after mass destroy, squash #2 forms **one** block over the whole run and reclaims it; the degenerate disk state before destroy matches the documented no-squash floor — the design's honesty clause, asserted, not hidden.
- **Space**: pre-destroy: `Θ(B + P(t) + U)` (assert ≈ `S0`, no reduction — expected); post-squash-#2: `B + F` (distinct content ⇒ `F ≈ P0`, but layer count 8 → 2).
- **Time**: squash #1 `T_squash` ≤ 500 ms **even with 6 boundaries** (plan-only; boundary computation is `lease_newest_layers()` under the plan lock — flag if it scales badly); squash #2 standard.

#### HRD-11 — ⏱ deep chain: 200-layer churn collapses to 3 lowerdirs live
- **Spec**: §D chain-length row ("the killer"); E5 at depth. **Fixture**: `stack(200×churn 1 MiB)` (`P0 = 200 MiB`, `F ≈ 1 MiB`); one `ws-batch` session created at v200 (leases all 200 layers) with its zero-pin command running when the squash fires.
- **Steps**: `S0` (+ record session-mount chain length via view) → `squash_layerstacks` → `S2` → witness reads → destroy → `S3`.
- **Correctness**: block `[L199…L1]` (199 sources — boundary `L200` kept) → one `S`; session migrates live; chain 201 → `[L200, S, B]` = 3 lowerdirs (view + witness); the hot file's final content exact; old 199 dirs deleted in-invocation.
- **Space**: the flagship reclaim: candidate-run bytes 199 MiB → ≈ 1 MiB (**< 1%**, §D churn row); layer dirs 201 → 3 (`{L200, S, B}`); `du` before/after both recorded in the verdict for the report's headline.
- **Time** (scaling probe): `t_build` walks 199 dirs (record; linear extrapolation from MED-04/MED-07 numbers); `t_switch` at 3 lowerdirs ≈ trivial; staged-mount build of the **old** 201-lowerdir chain never happens (only NEW mounts — assert). Budgets: `T_squash` ≤ 20 s, `T_remount` ≤ 3 s, `T_quiesce` ≤ 500 ms.

#### HRD-12 — E9: over-cap chains fail closed at the mount syscall, never probed
- **Spec**: E9; §D chain-length (numeric); X0.7. **Fixture**: leg (a) = MED-18's 501-layer creation failure (rerun in-suite for the matrix); leg (b) staged-remount over-cap — **reachability caveat, honestly recorded**: a rewritten chain only ever *shortens*, and creation caps at 500, so a > 500 staged chain requires the spec's 501-pinned-singletons construction (≈ 250 interleaved sessions). The leg builds 60 sessions max in CI (constructibility telemetry) and otherwise records `skipped:not_constructible_at_ci_scale` — the errno-classification path itself is pinned by leg (a) + the unit suite (same builder, same `fsconfig lowerdir+` EINVAL).
- **Steps**: leg (a) per MED-18; leg (b) attempt the construction, else skip-with-reason; both: repeat twice for stability.
- **Correctness**: any over-limit mount fails **the mount-build syscall itself** (`fsconfig lowerdir+` EINVAL — assert error shape) with zero side effects, classified as clean pre-PONR `stage_failed:<errno>`; old lease intact; **no separate limit detector, no ≈97-lowerdir machinery, no probe** — repeated runs byte-stable. The deletion of `lowerdir_limit` probing is the assertion.
- **Space**: byte-stable across repeats (no side effects is the point).
- **Time**: failure legs fast (≤ 5 s) — an over-cap attempt must not stall the sweep.

#### HRD-13 — ⏱ commit durability cost: one syncfs, measured clean and dirty
- **Spec**: §Storage phase 3; unit 7 (the syscall count itself is unit-proven — this case measures cost); X0.6 baselines. **Fixture**: `stack(2×2 500 small files)` (5 k entries); leg (dirty): a parallel session first writes 256 MiB of un-synced upperdir data on the shared ext4.
- **Steps**: leg (clean): squash, decompose `commit_ms`/`syncfs_ms`; leg (dirty): ballast write → immediate squash → decompose; then `docker restart` the container and verify content (page-cache flush proxy — **not** a power-fail proof; that's unit 7's shim territory, restated here as a limitation, per spec "explicitly not covered").
- **Correctness**: all 5 k winners + whiteouts + symlinks intact after restart; manifest valid; the dirty leg's foreign data also intact (syncfs flushed it — collateral, not corruption).
- **Space**: standard byte-neutral small-file math; `.layer-metadata` untouched by S.
- **Time** (the point): `syncfs_ms` clean ≤ 500 ms (baseline 33 ms), dirty ≤ 2 000 ms (baseline 197 ms @ 256 MiB); `commit_ms − syncfs_ms` ≤ 200 ms (recheck+promote+rename are µs–ms scale); flag any per-entry-fsync regression: `commit_ms` scaling with entry count is the sentinel (5 k entries must not cost 5 k barriers — compare against MED-04's 1 k-entry `commit_ms`, ratio must be ≈ 1×, not 5×).

#### HRD-14 — ⏱ re-squash across 5 generations: flat cost, no write amplification, rolling cross-generation rewrites
- **Spec**: §D re-squash cost ($\Theta(G·F)$ avoided); unit 8's generation shapes, live. **Fixture**: rolling pattern, g = 1…5: publish 5 churn layers (1 MiB hot file) → create `ws_g` (its head pins the new run, so this gen's sources stay individually on disk) → destroy `ws_{g-1}` (frees the *previous* run for squashing) → `squash_layerstacks`. Each gen-g block is therefore `[…prior gen's L run…, S_{g-1}]` — a raw run **containing the previous S id** — and each sweep must rewrite the surviving `ws_g` across it.
- **Steps**: per gen: `S0(g)`, squash, `S2(g)`, `df` delta, timings, `ws_g` chain + witness check; final: destroy `ws_5`, last squash, teardown.
- **Correctness**: every gen: the block re-squashes the prior S (S layers are not boundaries); the map's raw runs compose across generations (`S_g → […, S_{g-1}]`) and `ws_g`'s live rewrite applies them oldest-first in one bounded pass — chain returns to `[head, S_g, B]` shape each gen, witness reads exact; hot-file content exact after every gen; zero faulty anywhere.
- **Space**: fs free-space delta per generation ≤ ε (hardlink re-link, only re-encoded bytes written); steady-state disk after each gen ≈ `B + F + tail` — never grows with g.
- **Time** (the point): `build_ms(g)` is **flat** across g (O(E) per generation, not O(g·F)) — assert `build_ms(5) ≤ 2 × build_ms(1)`; distribution recorded per gen.

#### HRD-15 — ⏱ sweep at k=8: mixed outcomes, exact classification, additive cost
- **Spec**: §D sweep budget ($O(\sum procs + \sum fds) + k$ staged mounts); C1 tree at scale. **Fixture**: one stack, eight sessions in one sweep: 3× `ws-batch` (migrate live), 1× identity (post-squash creation), 2× PTY (cwd), 1× `pin(scm-fd)` (park), 1× `pin(child-mount)`.
- **Steps**: `S0` → one `squash_layerstacks` → 8-way classification → `S2` → clear pins → squash #2 → `S3` → destroy all.
- **Correctness**: exactly 8 `workspace_session.remount` outcomes, each the expected class (3 migrated, 1 identity, 2 `pinned:cwd…`, 1 `pinned:rollback_unmount_busy`, 1 `pinned:child_mount…`); `blocked_reasons` on the shared block = the union of the distinct still-pinning diagnostics (B5's multi-reason example); **zero faulty**; run-2 converges everything except the park (destroyed → reclaims); teardown registry exact.
- **Space**: `S2` Π = exactly the 4 non-migrated sessions' old runs; `S3` = `B + F` — the two-step ledger asserted numerically.
- **Time** (the point): total sweep ≈ Σ per-session (serial loop — no session exceeds its 2 s budget; the 3 live migrations dominate); `T_e2e` ≤ 30 s; per-outcome timing distribution attached (parked vs pinned vs migrated cost profile becomes the regression baseline).

#### HRD-16 — ENOSPC on both sides of the commit boundary
- **Spec**: C5 abort row; §Storage crash column; B5 ws-6. **Fixture**: leg (a) **commit-path abort**: `stack(6×distinct 20 MiB)`; a ballast writer fills the volume to < `F` free the moment staging appears (mountinfo/`staging/` poll trigger). Leg (b) **stage-path pre-PONR skip**: squash #1 with a PTY pin records the map and leaves the session leased; shell exits; fill the volume; squash #2 (empty blocks — the sweep still attempts the straggler) hits ENOSPC at staging/fresh-workdir creation; free space; squash #3.
- **Steps**: per leg: arm ballast → squash → classify → free ballast → recovery squash → `S3`.
- **Correctness**: (a) squash fails **cleanly**: one stderr `operation_failed` line, exit 1; `staging/` cleaned by the in-process error path; plan lease released (registry baseline — `lease.released` event); manifest untouched; recovery squash succeeds. (b) squash #2 exits 0 with `squashed_blocks: []`; the straggler reports `leased(stage_failed:…)` **via its remount span** (result-line has no block to carry the reason — the observability record is the surface here, asserted explicitly); old mount untouched (its workdir was never reused), session fully usable; squash #3 migrates it. Zero faulty in all legs.
- **Space**: (a) no partial S bytes survive the abort (byte-exact `S0` after cleanup); (b) reclaim completes only at squash #3.
- **Time**: abort path ≤ 5 s (fail fast, no retry loops); recovery runs standard.

#### HRD-17 — G1 kernel gate: coexistence proof, abort-leg durability, and the gate-failure policy
- **Spec**: G1 (incl. failure leg); environment gate 1; X0.2. **Fixture**: the full G1 sequence, driven through the product path where possible (session + squash) plus the raw builder sequence for the abort leg: OLD `[l2,l1]+U+W_old`; copy-up before; staged NEW `[S]+U+W_new` (fresh sibling); witness probe; move pair; visible probe; strict unmount; abort leg in a fresh tree (stage → unmount **without** moves → OLD copy-up again).
- **Steps**: run sequence → assertions → abort leg → durability check on `cow-after-abort` (the shared-workdir corruption sentinel) → gate-failure policy leg (below).
- **Correctness**: all witness reads exact on staged NEW and post-switch (including pre-staging copy-up content, whiteout-masked absence, dir-created-then-emptied, mode 0640); strict `umount2(rollback,0)` returns 0; the **abort leg's copy-up succeeds and survives** a sync+remount cycle — the assertion that fails if the workdir was shared. **Failure-policy leg**: in a gate-red environment squash must still commit and every session report `leased(unsupported:kernel_gate_not_proven)`; when the CI environment is gate-green (expected), this leg is exercised only where an unproven-gate configuration can be arranged (e.g. first-boot-before-proof ordering) — otherwise recorded `skipped:gate_green_env` with the wiring asserted at unit level. The gate **gates**; it never crashes.
- **Space/Time**: n/a beyond standard; gate sequence ≤ 30 s.

#### HRD-18 — G2 parity with the negative control that proves the teeth
- **Spec**: G2; X0.3 (the teeth are the opaque dir, not the plain whiteout). **Fixture**: through OLD: delete `wit/only-in-l1` (char 0:0 upperdir whiteout) and rm-and-recreate a populated lower dir (upperdir dir with `user.overlay.opaque=y`); flatten sources into `S`; staged NEW = `[S]` + same upperdir + fresh workdir via the **production builder**.
- **Steps**: positive leg probe → negative control: rebuild NEW once with a deliberately misconfigured **test-local** mount (no `userxattr`) → probe again → restore → teardown.
- **Correctness**: positive: deleted file stays absent, recreated dir stays exactly `{new content}` on NEW. Negative control: the recreated dir's **lower entries resurface** (X0.3 measured: mount succeeds, opaque xattr unread) — proving the positive assertion has teeth; the plain char-dev whiteout must **hold** even in the control (xattr-independent — asserting the *right* thing decays). No mountinfo lowerdir introspection anywhere in the test (grep the test source itself — meta-assertion).
- **Space/Time**: n/a beyond standard.

#### HRD-19 — mid-sweep daemon kill at k=6, plus the unreadable-manifest fail-closed boot
- **Spec**: E10 scaled; G3 unreadable leg; boot rule 1. **Fixture**: six sessions mid-sweep (HRD-15's mix minus park); daemon SIGKILLed the instant session #3's mount id changes (mid-switch, mid-sweep — earlier sessions already migrated, later ones untouched). Leg (b): while the daemon is down, truncate `manifest.json` to garbage; plant an orphan staging tree.
- **Steps**: squash → kill at trigger → restart → `S2` + boot-record assertions → leg (b): kill again → corrupt manifest → restart → fail-closed assertions → restore manifest from backup → restart → normal sweep.
- **Correctness**: leg (a): restart reaps every session (migrated and not — all dead by PDEATHSIG), sweeps to the active manifest exactly; the partially-swept state needs no special handling (some sessions' old runs already GC'd, others' sources still present — both covered by keep-set); fresh session + squash succeed. Leg (b): **nothing deleted** — orphan staging stays, `B*` stays, all layers stay; the daemon **still serves** (create/exec work — storage sweep skipped, not fatal); after restoring the manifest the next boot sweeps the orphan normally.
- **Space**: leg (a) disk == manifest exactly; leg (b) disk unchanged byte-for-byte (fail-closed proof).
- **Time**: restart ≤ 5 s both legs.

#### HRD-20 — ⏱ soak marathon: 20 randomized iterations, invariants after every one
- **Spec**: everything at once; the teardown contract as a *standing* invariant. **Fixture**: 20 iterations of seeded-random composition: publish batch (churn/distinct/delete-heavy, 1–8 layers), create 0–3 sessions (mixed `ws-idle`/`ws-batch`/`ws-pty`, occasionally `pin(scm-fd)`), destroy 0–2 existing sessions, `squash_layerstacks`. Seed logged for replay.
- **Steps**: per iteration: mutate → squash → **invariant sweep**; final: destroy all → final squash → full teardown.
- **Correctness (standing invariants, checked ×20)**: result JSON parses and satisfies the contract every time; `Σ active_lease_count` consistent with live sessions' chains (+parks); no `.remount-staging-*`/`.remount-rollback-*` residue; `staging/` empty after every invocation; every live session answers a probe command; zero faulty outcomes across the whole soak (nothing in the mix is post-PONR-lethal); observability log parses 100% (no malformed/truncated records at the default cap); daemon fd count stable (±16) across iterations — the leak sentinel.
- **Space**: after every iteration, disk within ±10% of the Θ formula computed from live state (`B + F_cum + U_live + Π_live + tail`); final state = `B + F` exactly.
- **Time**: per-iteration timing distributions accumulate into the **regression baseline artifact** (`test-reports/<RUN_ID>/soak-baseline.json`) — p50/p95 per named timer; the suite fails if p95 `T_squash` or `T_remount` drifts > 3× from the checked-in baseline of the previous accepted run.

---

## 5. Traceability

### 5.1 Spec e2e tests → catalog

| Spec test | Catalog case(s) |
| --- | --- |
| G1 | HRD-17 |
| G2 | HRD-18 (encodings also MED-01/02) |
| G3 | MED-15 (core) · HRD-19 (unreadable leg) · SMK-10 (healthy boot) |
| E1 | MED-09 |
| E2 | MED-11 |
| E3 | MED-19 (both legs) |
| E4 | HRD-04 (full matrix) · MED-10 (child-mount solo) |
| E5 | MED-08 (core) · HRD-11 (at depth) |
| E6 | MED-12 (park) · HRD-07 (convergence/destroy) |
| E7 | MED-13 |
| E8 | HRD-05 |
| E9 | MED-18 (creation) · HRD-12 (staged + caveat) |
| E10 | MED-14 (leg d) · HRD-06 (matrix) · HRD-19 (mid-sweep, k=6) |

### 5.2 Acceptance criteria → catalog (live-suite share)

| AC section | Catalog coverage |
| --- | --- |
| §1 scope/non-goals | SMK-04/05 (surface) · HRD-03 (no persisted sweep state) |
| §2 storage commit | SMK-01/02/03/09 · MED-01–07, 14, 16 · HRD-13, 16 |
| §3 substitution/rewrite | HRD-02, 14 (live halves; unit 8 owns the rest) |
| §4 live remount | SMK-06/07 · MED-08–13, 17, 19, 20 · HRD-01–09, 15 |
| §5 boot cleanup | SMK-10 · MED-14/15 · HRD-06, 19 |
| §6 CLI/product | SMK-04/05/08 · MED-13 (faulty surface) |
| §7 performance/space | MED-04, 18, 20 · HRD-11–15, 20 (all ⏱) |

### 5.3 Coverage caveats (recorded, not hidden)

- Power-fail durability of the one-`syncfs` commit is **unit-only** (test 7
  shim); HRD-13's restart is a cache-flush proxy and says so.
- HRD-12 leg (b) and HRD-04 sub-cases (9)/(11) are best-effort
  constructions; their verdicts distinguish `skipped:<reason>` from `pass`
  — a skip is visible in SUMMARY.md, never silent.
- HRD-17's gate-failure policy leg depends on arranging an unproven-gate
  environment; the wiring is otherwise unit-asserted.

---

## 6. Execution order & suite composition

1. **Preconditions** (§1.1 table) — once, hard-fail.
2. **SMK-01…10** serial (`-m "squash and smoke"`) — the rebuild gate;
   ≤ 5 min total.
3. **MED-01…20** serial; encoding cases (01–03) may run before gate
   enablement (commit-only), the rest need ⛔gate green.
4. **HRD-01…19** serial — each rebuilds its own sandbox; kill/crash cases
   (HRD-05/06/19) run **last within the tier** so a stuck gateway can't
   poison earlier cases.
5. **HRD-20 soak** — final, owns the run's timing baseline artifact.
6. **Suite report** (§2.6) generated even on abort; the SUMMARY table plus
   the timing-distribution diff against the previous accepted baseline is
   the review artifact for sign-off (`acceptance_criteria.md` §9).

Budget for a full run: smoke ≈ 5 min, medium ≈ 30 min (MED-18's 500-layer
build dominates), hard ≈ 60–90 min (soak ≈ 20 min). Kill-point cases carry
retry caps and log their trigger telemetry so flakes are diagnosable from
the bundle alone.
