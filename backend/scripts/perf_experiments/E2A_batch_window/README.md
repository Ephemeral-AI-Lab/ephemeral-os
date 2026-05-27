# E2/A — OCC batch-window sweep

**Status:** Microbench experiment.
**Hotspot attacked:** `occ.apply.commit_queue_wait_s` max=804ms on heavy_io_zoned_concurrent, max=332ms on full_case_user_input. p99=47.5ms globally.
**Hypothesis (per plan §6):** `CommitQueue.batch_window_s=0.002` may be starving the batcher under steady write pressure — sweeping the window from 10µs to 10ms reveals whether tuning it materially reduces queue wait p99.

## Advisor-flagged ceiling

Per `commit_queue.py:132-167`, the batch window adds at most one `time.sleep(batch_window_s)` per batch — i.e. **≤ batch_window_s overhead per batch**, not per item. The 332-804ms production tail therefore **cannot** be sourced from the current 2ms batch window alone — the arithmetic ceiling is ~2ms per batch. Tuning batch_window_s can shave at most a few ms.

Build the bench expecting:
- Best case: small but measurable reduction (a few ms).
- Likely case: INCONCLUSIVE — the workstation SSD's publisher is fast enough that queue wait is dominated by per-commit publish time, not by the batch window.

## Threshold

`commit_queue_wait_s` p99 reduction **≥50ms** vs baseline (batch_window_s=0.002) for the best non-default window on N=8 disjoint workload. Below 50ms = killed.

## Realism gate

Baseline `commit_queue_wait_s` p99 must be ≥ 20ms (half of production p99=47.5ms). If a workstation can't reproduce that, the bench is INCONCLUSIVE — the experiment cannot prove the optimization will move the production tail.

## Sub-experiment plan

- **N ∈ {8}** primary; we don't sweep N for this experiment — the threshold targets N=8.
- **batch_window_s sweep**: `{0.00001, 0.0001, 0.0005, 0.001, 0.002, 0.005, 0.01, 0.0}`.
  - `0.0` = "no sleep" — pure non-blocking drain.
  - `0.002` = current default (baseline).
- Each window: 30 timed iterations; each iteration = N=8 threads each submitting 10 commits to disjoint paths simultaneously; record `commit_queue_wait_s` (TimingKey.COMMIT_QUEUE_WAIT) from each commit's result timings.
- Stats are over the 30 × 8 × 10 = 2400 per-commit wait samples, then per-window p99 is the metric.

## Capability gate

None (pure Python).

## How to run

```sh
uv run python backend/scripts/perf_experiments/E2A_batch_window/bench.py \
  --output backend/scripts/perf_experiments/E2A_batch_window/report.md
```
