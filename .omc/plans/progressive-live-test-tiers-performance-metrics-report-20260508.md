# Progressive Live-Test Tiers - Performance Metrics Report

**Date:** 2026-05-08
**Run id:** `20260508TIER4FIX-FULL-002`
**Source:** reduced JSONL artifacts under `.omc/results/`
**Scope:** Daytona-backed live sandbox tiers 2-6

---

## 1 Source Artifacts Read

| Artifact | Rows | Data rows | Summary |
|---|---:|---:|---|
| `phase06-k1000-spot-check-20260508TIER4FIX-FULL-002.jsonl` | 3 | 2 | `phase06.k1000_spot_check.summary.v1` |
| `phase07-size-matrix-20260508TIER4FIX-FULL-002.jsonl` | 17 | 16 | `phase07.size_matrix.summary.v1` |
| `phase07-kind-matrix-20260508TIER4FIX-FULL-002.jsonl` | 17 | 16 | `phase07.kind_matrix.summary.v1` |
| `phase07-mixed-routing-20260508TIER4FIX-FULL-002.jsonl` | 4 | 3 | `phase07.mixed_routing.summary.v1` |
| `phase08-dev-shm-bounded-20260508TIER4FIX-FULL-002.jsonl` | 5 | 5 | probe rows only |
| `phase09-size-x-kind-20260508TIER4FIX-FULL-002.jsonl` | 17 | 16 | `phase09.live_e2e.summary.v1` |
| `phase09-size-x-concurrency-20260508TIER4FIX-FULL-002.jsonl` | 13 | 12 | `phase09.size_x_concurrency.summary.v1` |
| `phase09-kind-x-concurrency-20260508TIER4FIX-FULL-002.jsonl` | 10 | 9 | `phase09.kind_x_concurrency.summary.v1` |
| `phase09-adversarial-20260508TIER4FIX-FULL-002.jsonl` | 8 | 7 | `phase09.live_e2e.summary.v1` |
| `progressive-test-summary-20260508TIER4FIX-FULL-002.jsonl` | 5 | 5 | tier outcomes |

No final artifact row had `passed=false`, `failed_cells>0`, or tier
`status="failed"`.

---

## 2 Run Summary

| Tier | Name | Status | Elapsed s | Failed cells |
|---:|---|---|---:|---:|
| 2 | `k_scaling_spot_check` | passed | 10.799 | 0 |
| 3 | `single_axis_matrices` | passed | 122.631 | 0 |
| 4 | `cross_axis_matrices` | passed | 135.533 | 0 |
| 5 | `soak` | passed | 172.875 | 0 |
| 6 | `adversarial` | passed | 18.504 | 0 |
| **Total** |  |  | **460.342** | **0** |

Across the 81 data rows with `wall_ms`, median wall time was **1045.352 ms**
and max wall time was **6624.700 ms**. Across the 81 data rows with OCC commit
timings, median commit time was **13.895 ms** and max commit time was
**140.294 ms**. Across the 60 data rows with capture timings, median
`capture_upperdir_s` was **3.536 ms** and max was **42.326 ms**.

Interpretation: for small and medium cells, the live public sandbox call floor
dominates wall time. OCC commit work is usually tens of milliseconds or less;
compare `commit_s`, `capture_upperdir_s`, and `publish_layer_s` for internal
performance rather than treating the roughly 0.9-1.1 s live call floor as OCC
cost.

---

## 3 Artifact-Level Timing Summary

| Artifact | Data rows | Passed / total | Median wall ms | Max wall ms | Median commit ms | Max commit ms |
|---|---:|---:|---:|---:|---:|---:|
| phase06 K=1000 spot | 2 | 2 / 2 | 1091.812 | 1097.193 | 109.167 | 116.323 |
| phase07 size matrix | 16 | 16 / 16 | 936.576 | 1003.672 | 8.305 | 40.291 |
| phase07 kind matrix | 16 | 16 / 16 | 1224.006 | 1543.660 | 40.432 | 140.294 |
| phase07 mixed routing | 3 | 3 / 3 | 1200.915 | 1217.735 | 127.680 | 130.992 |
| phase08 dev-shm bounded | 5 | 5 / 5 | n/a | n/a | n/a | n/a |
| phase09 size x kind | 16 | 16 / 16 | 974.245 | 1098.803 | 13.315 | 54.596 |
| phase09 size x concurrency | 12 | 12 / 12 | 1860.809 | 6624.700 | 25.967 | 43.050 |
| phase09 kind x concurrency | 9 | 9 / 9 | 2106.288 | 5367.425 | 12.509 | 24.803 |
| phase09 adversarial | 7 | 7 / 7 | 904.070 | 946.387 | 1.104 | 2.480 |

---

## 4 Phase 06 - K=1000 Spot Check

| Prefix | K | Route | Wall ms | Commit ms | Commit us/file | Capture ms | Capture us/file | Stager ms |
|---|---:|---|---:|---:|---:|---:|---:|---:|
| `tracked/load/k_capture` | 1000 | gated | 1097.193 | 102.010 | 102.010 | 31.592 | 31.592 | 26.033 |
| `dist/k_capture` | 1000 | direct | 1086.430 | 116.323 | 116.323 | 33.355 | 33.355 | 28.579 |

Tracked/gated and dist/direct are close at K=1000: direct was 10.763 ms faster
on wall time, while gated was 14.313 ms faster on OCC commit time.

---

## 5 Phase 07 - Size Matrix

| Prefix | File size | K | Paths | Wall ms | Commit ms | Commit us/file | Capture ms | Publish ms | Validate ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| tracked | 64 B | 16 | 16 | 924.612 | 4.692 | 293.229 | 1.391 | 2.176 | 1.483 |
| tracked | 64 B | 256 | 256 | 927.265 | 27.984 | 109.313 | 8.701 | 13.604 | 13.933 |
| tracked | 4 KiB | 16 | 16 | 868.316 | 3.161 | 197.544 | 1.126 | 1.658 | 1.158 |
| tracked | 4 KiB | 64 | 64 | 905.855 | 9.390 | 146.715 | 3.018 | 5.108 | 3.919 |
| tracked | 64 KiB | 8 | 8 | 930.407 | 4.745 | 593.104 | 1.743 | 3.272 | 1.081 |
| tracked | 64 KiB | 32 | 32 | 937.403 | 13.944 | 435.753 | 5.352 | 10.256 | 3.342 |
| tracked | 1 MiB | 1 | 1 | 935.749 | 7.220 | 7219.916 | 2.713 | 5.724 | 0.907 |
| tracked | 1 MiB | 8 | 8 | 958.762 | 40.291 | 5036.432 | 16.843 | 35.285 | 4.600 |
| dist | 64 B | 16 | 16 | 922.560 | 3.302 | 206.401 | 1.047 | 1.576 | 1.319 |
| dist | 64 B | 256 | 256 | 956.523 | 37.125 | 145.020 | 9.517 | 15.335 | 21.133 |
| dist | 4 KiB | 16 | 16 | 910.131 | 3.842 | 240.120 | 1.160 | 1.880 | 1.510 |
| dist | 4 KiB | 64 | 64 | 998.227 | 11.392 | 177.993 | 3.554 | 5.249 | 5.663 |
| dist | 64 KiB | 8 | 8 | 996.006 | 5.097 | 637.078 | 1.798 | 3.585 | 1.145 |
| dist | 64 KiB | 32 | 32 | 998.216 | 16.321 | 510.026 | 5.897 | 11.438 | 4.334 |
| dist | 1 MiB | 1 | 1 | 1003.672 | 6.776 | 6775.666 | 3.053 | 5.013 | 1.036 |
| dist | 1 MiB | 8 | 8 | 970.589 | 39.616 | 4952.031 | 16.545 | 34.951 | 4.247 |

The highest size-matrix commit time was `tracked_size1048576_k8` at
40.291 ms. The 1 MiB x 8 cells are dominated by capture and publish costs, not
validation or stager write time.

---

## 6 Phase 07 - Kind Matrix

| Prefix | Kind | K | Paths | Wall ms | Commit ms | Commit us/file | Capture ms | Stager ms | Validate ms |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| tracked | new_files | 100 | 100 | 934.154 | 12.684 | 126.837 | 4.828 | 2.778 | 5.368 |
| tracked | new_files | 1000 | 1000 | 1144.473 | 118.276 | 118.276 | 34.536 | 28.109 | 61.100 |
| tracked | modify_files | 100 | 100 | 1089.143 | 15.055 | 150.552 | 4.481 | 2.842 | 7.333 |
| tracked | modify_files | 1000 | 1000 | 1279.616 | 140.294 | 140.294 | 42.326 | 30.204 | 79.596 |
| tracked | delete_files | 100 | 100 | 963.765 | 7.225 | 72.249 | 2.040 | 0.000 | 3.553 |
| tracked | delete_files | 1000 | 1000 | 1215.842 | 65.809 | 65.809 | 15.809 | 0.000 | 36.112 |
| tracked | mixed_kinds | 100 | 100 | 1089.960 | 12.865 | 128.646 | 3.518 | 1.994 | 6.758 |
| tracked | mixed_kinds | 1000 | 1000 | 1319.902 | 108.080 | 108.080 | 28.678 | 17.258 | 63.404 |
| dist | new_files | 100 | 100 | 1135.705 | 15.012 | 150.124 | 3.885 | 2.850 | 8.522 |
| dist | new_files | 1000 | 1000 | 1374.137 | 135.222 | 135.222 | 32.362 | 25.229 | 83.602 |
| dist | modify_files | 100 | 100 | 1215.204 | 12.418 | 124.176 | 3.615 | 2.528 | 6.480 |
| dist | modify_files | 1000 | 1000 | 1496.785 | 120.120 | 120.120 | 31.646 | 25.448 | 65.802 |
| dist | delete_files | 100 | 100 | 1232.170 | 7.191 | 71.906 | 1.950 | 0.000 | 3.393 |
| dist | delete_files | 1000 | 1000 | 1460.285 | 66.346 | 66.346 | 16.658 | 0.000 | 38.269 |
| dist | mixed_kinds | 100 | 100 | 1292.036 | 12.557 | 125.568 | 3.251 | 1.827 | 7.177 |
| dist | mixed_kinds | 1000 | 1000 | 1543.660 | 123.962 | 123.962 | 27.145 | 18.299 | 74.397 |

At K=1000, delete workloads are fastest by commit time
(65.809-66.346 ms). Modify workloads are the most expensive on tracked paths
(140.294 ms), while dist new_files has the highest validation time
(83.602 ms).

---

## 7 Phase 07 - Mixed Routing

| Cell | Gated paths | Direct paths | Total paths | Wall ms | Commit ms | Commit us/file | Capture ms | Stager ms |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `gated500_dist500` | 500 | 500 | 1000 | 1099.457 | 104.444 | 104.444 | 32.299 | 25.156 |
| `gated1000_dist100` | 1000 | 100 | 1100 | 1217.735 | 127.680 | 116.072 | 38.962 | 32.253 |
| `gated100_dist1000` | 100 | 1000 | 1100 | 1200.915 | 130.992 | 119.084 | 41.489 | 31.367 |

Mixed routing stayed within 104.444-130.992 ms commit time for 1000-1100
paths. The balanced 500/500 case was fastest.

---

## 8 Phase 09 - Size x Kind

| Size | Kind | K | Wall ms | Commit ms | Capture ms | Stager ms | Publish ms |
|---:|---|---:|---:|---:|---:|---:|---:|
| 64 B | new_files | 64 | 923.953 | 8.296 | 2.637 | 1.816 | 4.170 |
| 64 B | modify_files | 64 | 958.343 | 10.071 | 2.715 | 2.022 | 4.449 |
| 64 B | delete_files | 64 | 912.282 | 6.310 | 1.543 | 0.000 | 2.861 |
| 64 B | mixed_kinds | 64 | 897.555 | 7.733 | 2.427 | 1.260 | 3.503 |
| 4 KiB | new_files | 64 | 951.908 | 11.207 | 3.353 | 2.067 | 5.647 |
| 4 KiB | modify_files | 64 | 968.944 | 11.982 | 3.458 | 1.862 | 6.211 |
| 4 KiB | delete_files | 64 | 919.312 | 6.069 | 1.716 | 0.000 | 2.727 |
| 4 KiB | mixed_kinds | 64 | 934.155 | 9.495 | 2.499 | 1.212 | 4.329 |
| 64 KiB | new_files | 64 | 1029.639 | 31.964 | 10.826 | 3.896 | 23.076 |
| 64 KiB | modify_files | 64 | 1042.763 | 41.350 | 12.363 | 4.542 | 23.311 |
| 64 KiB | delete_files | 64 | 1052.994 | 14.648 | 1.811 | 0.000 | 2.762 |
| 64 KiB | mixed_kinds | 64 | 1049.534 | 31.045 | 8.871 | 3.146 | 16.879 |
| 1 MiB | new_files | 8 | 979.545 | 44.016 | 16.332 | 2.583 | 38.883 |
| 1 MiB | modify_files | 8 | 1098.803 | 54.596 | 17.058 | 2.630 | 34.013 |
| 1 MiB | delete_files | 8 | 1045.352 | 18.563 | 0.526 | 0.000 | 1.368 |
| 1 MiB | mixed_kinds | 8 | 993.082 | 39.961 | 13.544 | 1.834 | 26.863 |

Delete cells remain the cheapest commit path because they avoid stager writes.
The heaviest size x kind cell was `size1048576_modify_files_k8` at 54.596 ms
commit and 1098.803 ms wall.

---

## 9 Phase 09 - Size x Concurrency

| Size | K | C | Batch wall ms | Median call wall ms | P99 call wall ms | Median commit ms | Calls succeeded |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 64 B | 64 | 1 | 927.612 | 926.640 | 926.640 | 8.018 | 1 / 1 |
| 64 B | 64 | 5 | 1076.369 | 1052.497 | 1073.871 | 13.895 | 5 / 5 |
| 64 B | 64 | 10 | 1403.774 | 1219.716 | 1397.015 | 13.046 | 10 / 10 |
| 64 B | 64 | 20 | 2822.567 | 2577.758 | 2810.501 | 27.645 | 20 / 20 |
| 4 KiB | 64 | 1 | 1040.006 | 1039.235 | 1039.235 | 19.202 | 1 / 1 |
| 4 KiB | 64 | 5 | 1565.003 | 1376.798 | 1562.873 | 22.211 | 5 / 5 |
| 4 KiB | 64 | 10 | 2351.874 | 2146.714 | 2349.213 | 32.498 | 10 / 10 |
| 4 KiB | 64 | 20 | 5280.020 | 4787.614 | 5269.072 | 43.050 | 20 / 20 |
| 64 KiB | 32 | 1 | 1170.307 | 1169.399 | 1169.399 | 24.288 | 1 / 1 |
| 64 KiB | 32 | 5 | 2156.616 | 1732.605 | 2154.968 | 27.931 | 5 / 5 |
| 64 KiB | 32 | 10 | 3265.179 | 3121.827 | 3258.503 | 32.110 | 10 / 10 |
| 64 KiB | 32 | 20 | 6624.700 | 6425.524 | 6623.840 | 33.115 | 20 / 20 |

All concurrent calls succeeded. The 64 KiB axis uses K=32 by design for the live
`/dev/shm` ceiling. Even at C=20, median commit time stayed under 44 ms; batch
wall time increased mostly with concurrent live call pressure.

---

## 10 Phase 09 - Kind x Concurrency

| Kind | K | C | Batch wall ms | Median call wall ms | P99 call wall ms | Median commit ms | Calls succeeded |
|---|---:|---:|---:|---:|---:|---:|---:|
| new_files | 64 | 1 | 910.586 | 909.724 | 909.724 | 8.093 | 1 / 1 |
| new_files | 64 | 5 | 1113.371 | 1070.168 | 1110.138 | 14.254 | 5 / 5 |
| new_files | 64 | 10 | 1387.324 | 1209.353 | 1377.818 | 12.509 | 10 / 10 |
| modify_files | 64 | 1 | 1963.546 | 990.406 | 990.406 | 10.075 | 1 / 1 |
| modify_files | 64 | 5 | 2402.601 | 1084.253 | 1108.151 | 12.560 | 5 / 5 |
| modify_files | 64 | 10 | 3743.728 | 1824.340 | 1880.950 | 24.803 | 10 / 10 |
| delete_files | 64 | 1 | 2106.288 | 1037.439 | 1037.439 | 5.936 | 1 / 1 |
| delete_files | 64 | 5 | 3262.897 | 1254.511 | 1341.484 | 8.063 | 5 / 5 |
| delete_files | 64 | 10 | 5367.425 | 2523.780 | 2573.819 | 13.005 | 10 / 10 |

Modify/delete batch wall includes untimed per-call seeding before the measured
mutating shell call. Commit medians are still small: max 24.803 ms.

---

## 11 Phase 08 - Dev-shm Soak

| Call index | Run dirs | Total bytes | Run-dir limit | Byte limit |
|---:|---:|---:|---:|---:|
| 0 | 0 | 0 | 5 | 5242880 |
| 50 | 0 | 100 | 5 | 5242880 |
| 100 | 0 | 100 | 5 | 5242880 |
| 150 | 0 | 100 | 5 | 5242880 |
| 200 | 0 | 100 | 5 | 5242880 |

The command-exec `/dev/shm` working directory stayed bounded for all 200 noop
shell calls. The observed maximum was 0 run dirs and 100 bytes, well below the
limits of 5 run dirs and 5 MiB.

---

## 12 Phase 09 - Adversarial

| Cell | Kind | Wall ms | Commit ms | Capture ms | Gated paths | Direct paths |
|---|---|---:|---:|---:|---:|---:|
| `deeply_nested_d20` | deeply_nested | 919.429 | 2.480 | 0.997 | 1 | 0 |
| `symlink_target_inside_workspace` | symlink_inside | 904.070 | 1.334 | 0.492 | 0 | 1 |
| `symlink_target_outside_workspace` | symlink_outside | 946.387 | 1.058 | 0.385 | 0 | 1 |
| `whiteout_collision_same_commit` | whiteout_collision | 900.654 | 1.557 | 0.584 | 1 | 0 |
| `special_bash_chars_filename` | special_chars | 906.500 | 1.104 | 0.454 | 1 | 0 |
| `long_filename_250` | long_filename | 852.361 | 1.102 | 0.479 | 1 | 0 |
| `empty_commit_no_changes` | empty_commit | 757.714 | 0.000 | 0.120 | 0 | 0 |

All adversarial cells passed. Commit time stayed at or below 2.480 ms for every
adversarial case.

---

## 13 Conclusions

1. **Tier 2-6 are green on the final Daytona-backed run.** All tier summaries
   report `status="passed"` and `failed_cells=0`.
2. **OCC commit cost is low relative to live wall time.** Median wall time is
   1045.352 ms, while median commit time is 13.895 ms.
3. **K=1000 file-count workloads are stable in the 100-140 ms commit range.**
   The heaviest K=1000 commit was tracked modify at 140.294 ms.
4. **Large-file workloads are dominated by capture/publish.** The 1 MiB x 8
   size cells commit in roughly 39.6-40.3 ms in phase07, and 1 MiB x 8
   size/kind modify reaches 54.596 ms in phase09.
5. **Routing is not the bottleneck at this scale.** Mixed gated/direct routing
   handled 1000-1100 paths in 104.444-130.992 ms commit time.
6. **Concurrency correctness is solid after workload calibration.** All C=20
   size-concurrency calls succeeded, including the calibrated 64 KiB K=32 cell.
7. **The `/dev/shm` cleanup invariant holds.** The soak observed no retained run
   directories and only 100 bytes at probe points after shell calls began.
