# LayerStack Space/Time Benchmark Results

Date: 2026-06-16

This is a single local release-mode run of the LayerStack benchmark example.
Treat timings as directional wall-clock measurements for this machine, not a
portable SLA. The storage columns use logical file lengths under
`storage_root/layers`; they are not allocated-block accounting.

Command:

```sh
CARGO_TARGET_DIR=/tmp/ephemeral-os-target cargo run -p layerstack --release --example bench_layerstack
```

Benchmark harness:

- `crates/daemon/layerstack/examples/bench_layerstack.rs`
- Temp root:
  `/var/folders/s4/xpkmz7wn6yq97w1ls_4f_dfc0000gn/T/layerstack-bench-15562-1781622141132139000`

## Summary

| Scenario | Input | Before payload | After payload | Peak payload | Layer dirs before -> after | Time |
|---|---:|---:|---:|---:|---:|---:|
| Retained edits | 50 x same 1 MiB file | 50 MiB | 50 MiB | n/a | 50 -> 50 | publish 1.771s |
| Same-snapshot leases | 200 leases on 10 x 1 MiB stack | 10 MiB | 10 MiB | n/a | 10 -> 10 | acquire 0.006s |
| Versioned leases | 50 leases over 50 x 1 MiB rewrites | 50 MiB | 50 MiB | n/a | 50 -> 50 | publish+lease 1.711s |
| Squash, same file | 50 x same 1 MiB file | 50 MiB | 1 MiB | 51 MiB | 50 -> 1 | squash 0.033s |
| Squash while lease held | 50 x same 1 MiB file | 50 MiB | 51 MiB | 51 MiB | 50 -> 51 | squash 0.024s |
| Release after lease-blocked squash | same case | 51 MiB | 2 MiB | n/a | 51 -> 2 | release 0.012s |
| Deferred squash after release | 50 x same 1 MiB file | 50 MiB | 1 MiB | 51 MiB | 50 -> 1 | squash 0.033s |
| Squash, many files | 5,000 x 1 KiB files | 5.12 MB | 5.12 MB | 9.84 MB | 10 -> 1 | squash 1.279s |
| Squash, large rewrite | 4 x same 64 MiB file | 256 MiB | 64 MiB | 320 MiB | 4 -> 1 | squash 0.014s |
| Remount compaction | 50 x same 1 MiB file, open lease | 50 MiB | 2 MiB | 52 MiB | 50 -> 2 | total 0.065s |
| Remount compaction | 5,000 x 1 KiB files, open lease | 5.12 MB | 10.24 MB | 10.24 MB | 10 -> 2 | total 2.410s |
| Remount compaction | 4 x same 64 MiB file, open lease | 256 MiB | 128 MiB | 384 MiB | 4 -> 2 | total 0.036s |
| Exhaustive remount | 50 x same 1 MiB, 5 current leases | 50 MiB | 2 MiB | 52 MiB | 50 -> 2 | total 0.069s |
| Exhaustive remount | 50 rewrites rotating 5 x 1 MiB files, 5 current leases | 50 MiB | 10 MiB | 60 MiB | 50 -> 2 | total 0.078s |
| Exhaustive remount | 50 x hot 1 MiB + unique 64 KiB side files, 3 current leases | 53.13 MiB | 8.25 MiB | 61.38 MiB | 50 -> 2 | total 0.111s |
| Exhaustive remount | 50 layers rewriting 5 x 256 KiB files, 2 current leases | 62.5 MiB | 2.5 MiB | 65 MiB | 50 -> 2 | total 0.185s |
| Exhaustive remount | 50 x same 1 MiB, current lease plus 4 historical leases | 50 MiB | 46 MiB | 56 MiB | 50 -> 46 | total 0.061s |

For remount compaction, `total` is LayerStack compaction plus lease retarget
plus active-head squash cleanup. It does not include the live namespace remount
syscall because Docker live E2E is unavailable in this environment.

## Analysis

1. Retained same-file rewrites are linear in retained layer payload:
   `O(L * file_size)`. The 50 x 1 MiB case retained exactly 50 MiB across 50
   layer directories.

2. Borrowing many leases for the same snapshot does not multiply lowerdir
   storage. The benchmark acquired 0, 1, 10, 50, and 200 leases against the same
   10-layer stack; payload and layer directory count stayed constant at 10 MiB
   and 10 dirs. Lease acquisition itself is small metadata work and grows with
   lease count.

3. Versioned leases are different from duplicate lease handles. If each lease
   pins a different historical version, storage remains proportional to the
   accumulated bytes needed by those versions. The 50-version 1 MiB rewrite case
   retained 50 MiB.

4. Normal squash collapses same-file rewrite history to the latest live file.
   In the 50 x 1 MiB case, retained payload dropped from 50 MiB to 1 MiB. Peak
   payload was about old payload plus the new checkpoint.

5. Lease-blocked squash cannot delete layers still referenced by the old lease.
   The measured same-file case went from 50 MiB to 51 MiB while the lease was
   held, then to 2 MiB after release. A later active-head squash can reduce that
   to 1 MiB.

6. Remount compaction solves the long-running isolated session case in terms of
   retained history: same-file rewrite history dropped from 50 MiB to 2 MiB while
   the lease remained active. This is bounded in retained layer count and no
   longer scales with `L`.

7. Remount compaction currently materializes two compact checkpoints when the
   lease snapshot and active public head both need compact representations: one
   for the mounted lease and one for the active head. That is why the many-file
   case grows from 5.12 MB to 10.24 MB while the lease remains open. The storage
   class is still `O(live_snapshot_bytes)`, but the constant is currently two
   live snapshots.

8. Squash and remount compaction time scale with live projection work, not with
   every overwritten historical byte. Same-file large rewrites are fast because
   the live projection contains one file. Many-file snapshots are slower: 5,000
   files took 1.279s for normal squash and 2.594s for remount compaction because
   the current remount path projects two checkpoints.

9. Multiple current leases for the same snapshot do not multiply compacted
   storage when they are retargeted to one compact manifest. The 5-current-lease
   same-file case still ended at 2 MiB and two layer directories.

10. Historical leases are a hard retention boundary. In the
    current-plus-4-historical-leases case, remount compaction reduced the latest
    mounted lease but could not reclaim the historical heads. The stack ended at
    46 MiB and 46 layer directories, which is the storage pressure that policy
    should report as historical/lease-blocked rather than treating as reclaimed.

## Policy Implication

The byte-aware policy should trigger on retained unsquashed bytes, not only
depth. For long-running isolated sessions, remount compaction gives bounded
storage in retained history, but the next optimization target is checkpoint
sharing or active-manifest adoption when the mounted snapshot and active head are
the same logical root. That would reduce the constant from two compact
checkpoints to one. Separately, historical leases need explicit bounds or
operator-visible pressure reporting because mounted remount of the current
session does not make old versions disposable.
