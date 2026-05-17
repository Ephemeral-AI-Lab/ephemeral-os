# Phase 00 Request Snapshot Probe Report

**Status:** measured
**Date:** 2026-05-06 12:34 UTC
**Command:** `.venv/bin/pytest backend/tests/live_e2e_test/sandbox/request_snapshot -q -s`
**Result:** `3 passed, 1 warning in 12.73 s`

JSONL artifacts:

- `.omc/results/live-e2e-request-snapshot-probe-20260506T123412Z.jsonl`
- `.omc/results/live-e2e-request-snapshot-probe-20260506T123415Z.jsonl`
- `.omc/results/live-e2e-request-snapshot-probe-20260506T123418Z.jsonl`

## Decision

Phase 1 should implement `copy_cp` first. It is available in the Daytona image,
passes the frozen-snapshot check, is faster than `tar_copy` on every default
shape, and scales to 10 concurrent baseline snapshots without serializing.

Use `tar_copy` as the fallback backend. It also freezes correctly, but its
baseline create p99 is about 2x slower than `copy_cp` at concurrency 10.

Do not use `reflink_cp` or `hardlink_cp` for Phase 1:

- `reflink_cp` failed with `Operation not supported`.
- `hardlink_cp` created snapshots but failed the frozen-sentinel check, so it is
  only useful as the negative control.

## Backend Summary

| Backend | Available | Frozen | Decision |
|---|---:|---:|---|
| `copy_cp` | yes | yes | recommended |
| `tar_copy` | yes | yes | fallback |
| `reflink_cp` | no | no | unavailable on this image/filesystem |
| `hardlink_cp` | yes | no | rejected; shared inode mutation is visible |

## Shape Results

Single-request create/destroy p99 for the recommended backend:

| Shape | Files | Bytes | Largest file | Create p99 | Destroy p99 |
|---|---:|---:|---:|---:|---:|
| `baseline_repo` | 175 | 19,270,480 | 13,338,626 | 34.069 ms | 19.420 ms |
| `many_small` | 1,175 | 23,366,482 | 13,338,626 | 49.199 ms | 22.956 ms |
| `large_files` | 179 | 86,379,347 | 16,777,216 | 69.686 ms | 21.637 ms |
| `mixed_generated` | 2,177 | 44,239,703 | 13,338,626 | 72.069 ms | 30.142 ms |

The baseline repo includes `.git`; `.git` accounts for about 14.1 MB across 25
files, so it dominates baseline bytes. Phase 1 should keep explicit workspace
size budgets and revisit neutral git metadata if this changes on larger images.

## Concurrency

Baseline repo, frozen snapshots, leftover snapshot dirs `0`:

| Backend | Concurrency | Create p99 | Create factor | Create efficiency | Destroy p99 | Destroy factor |
|---|---:|---:|---:|---:|---:|---:|
| `copy_cp` | 1 | 32.058 ms | 0.995 | 0.995 | 20.254 ms | 0.991 |
| `copy_cp` | 5 | 37.628 ms | 4.212 | 0.842 | 21.837 ms | 4.021 |
| `copy_cp` | 10 | 52.309 ms | 6.007 | 0.601 | 32.633 ms | 5.186 |
| `tar_copy` | 1 | 63.160 ms | 0.996 | 0.996 | 21.072 ms | 0.990 |
| `tar_copy` | 5 | 66.315 ms | 4.452 | 0.890 | 23.280 ms | 4.069 |
| `tar_copy` | 10 | 125.614 ms | 4.881 | 0.488 | 38.105 ms | 5.405 |

## Phase 1 Budgets

Initial production budgets for the measured default image:

| Budget | Value |
|---|---:|
| `snapshot.create_s` p50 | 0.050 s |
| `snapshot.create_s` p95 | 0.080 s |
| `snapshot.create_s` p99 | 0.100 s |
| `snapshot.destroy_s` p50 | 0.025 s |
| `snapshot.destroy_s` p95 | 0.040 s |
| `snapshot.destroy_s` p99 | 0.050 s |
| max workspace file count | 2,200 files |
| max workspace bytes | 90 MB |
| max single file bytes | 16 MB |
| recommended backend | `copy_cp` |
| fallback backend | `tar_copy` |
| cleanup mode | synchronous for these budgets; switch to async above 100 ms destroy p99 |

Full copy is acceptable for the default image and these bounded workspace
shapes. The first production implementation should fail closed or degrade to
async cleanup when a workspace exceeds the measured file, byte, or destroy-time
budget.
