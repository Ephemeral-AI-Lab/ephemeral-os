# E3 — MergedView aggregate-index spike

**Status:** Spike (2-day-boxed per plan §6).
**Hotspot attacked:** `layer_stack.shell_pre_mount_squash.total_s` p99=204ms — *via the structural path of making reads cheap so squash becomes optional*.
**Hypothesis (per plan §6):** A manifest-wide `path → (layer_id, kind)` index, invalidated incrementally on publish, lets shell mount skip squash entirely without slowing reads.

## Load-bearing assumption (advisor-flagged)

E3 measures `read_bytes` latency, not squash latency. The chain "faster reads → skip squash → 204ms tail disappears" assumes **read perf is the dominant reason for squashing**. If the squash exists primarily for the overlay-mount layer cap (per `overlay_depth_cap_root_cause.md` memory: util-linux 2.41 mount(8) caps at 16; mount(2) syscall takes 199+), E3 does not subsume E1 even if it scales beautifully. The report must surface this honestly.

## Thresholds (all required)

1. **Sublinear scaling:** median `read_bytes` latency growth from L=10 to L=200 ≤ 2× (current implementation is O(L); reported growth is the operational proxy for ≥5× throughput at L=100).
2. **Bounded incremental publish cost:** index update cost added to publish p99 ≤ 20ms (apply only the new layer's changes, *not* full rebuild).
3. **Baseline realism gate:** baseline L=200 median must be ≥ 5× baseline L=10 median. If the synthetic workload doesn't reproduce the documented O(L) shape, the experiment is INCONCLUSIVE — neither promoted nor killed, plan §6 falsifiability principle.

## Decision tree

- Both pass → spike succeeds; **drops E1** (subsumed); write `sandbox_merged_index_PLAN.md`.
- Only read-throughput passes (invalidation cost too high) → keep E1, kill E3.
- Both fail → kill E3, keep E1.
- Realism gate fails → INCONCLUSIVE; re-design synthetic workload before re-running.

## Workload design

- **L ∈ {10, 50, 100, 200}** layers per condition.
- Each layer holds **~50 random files** under nested directories (mimics typical commit shape per `occ.commit.stager_write_count` mean=1.0 but spread for fixture variety).
- **Path mix:** 50% absent lookups (force full O(L) walk — the worst case for baseline), 50% present (sampled uniformly across all layers — average walk = L/2).
- Per iteration: 1000 lookups. Per condition: 30 iterations + 5 warmup. Stats are computed over per-iteration *mean per-lookup* latency.
- Caches kept warm between iterations (production state). Cold-start cost is not in scope.

## Capability gate

None (pure Python).

## How to run

```sh
uv run python backend/scripts/perf_experiments/E3_indexed_merged_view/bench.py \
  --output backend/scripts/perf_experiments/E3_indexed_merged_view/report.md
```

The script self-contained — no daemon, no Docker, no provider. Tears down its temp storage on exit.
