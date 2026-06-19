# LayerStack Hard-Protection Compact vs Live Remount Performance Report

Date: 2026-06-17

This report compares hard-protection compaction against verified remount
normalization and records the decision to remove blocked-path
hard-protection compact fallback from live-remount handling. The numbers are
local wall-clock measurements from this worktree. Treat them as directional
performance evidence for policy decisions, not a portable SLA.

The direct LayerStack storage numbers below are retained mutable layer payload
bytes under `storage_root/layers`, with the base workspace snapshot `B`
subtracted. This is the disk-space signal we care about for compaction policy:
how much additional storage LayerStack retains above the base repo snapshot. If
the base workspace snapshot is `B`, a numeric table cell `X` corresponds to
total layer payload `B + X`.

All percentage improvements intentionally exclude `B` and are computed over the
base-subtracted mutable layer payload only:

```text
base_subtracted_reduction =
    (hard_protection_retained_payload - remount_retained_payload)
    / hard_protection_retained_payload
```

If a reader wants whole-disk reduction including the base repo, the denominator
would be `B + hard_protection_retained_payload`, so the percentage would depend
on repo/base snapshot size and would hide the true LayerStack overhead signal.

## Commands

LayerStack real-storage benchmark:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -q -p layerstack --release --example bench_layerstack_gap_reclaim
```

Fresh run captured on 2026-06-17 from this worktree:

```text
base_dir,/var/folders/s4/xpkmz7wn6yq97w1ls_4f_dfc0000gn/T/layerstack-gap-reclaim-32680-1781647728074219000
```

Live isolated workspace E2E runs used for namespace remount timings:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id hard-vs-remount-report-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-many-file-tree-2 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-three-lease-two-command-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-process-fanout-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-hard-batch-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-matrix-batch-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-complex-integrity-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-multi-command-2 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo run -p e2e-test --bin e2e-runner -- --run-id live-remount-mixed-tree-1 --suites workspace-runtime-isolated --max-parallel 1 --container-weight-cap 10 --heavy-test-threads 4
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount_preserves_concurrent_pip_style_install_tree -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated coverage_goal2 -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated coverage_goal3 -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated coverage_goal4 --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated coverage_goal4 -- --nocapture --test-threads 1
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --test workspace-runtime-isolated compact_remount_live_remount --no-default-features -- --nocapture
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount -- --nocapture --test-threads 1
```

Current calibration run:

| Run | Result | Runner | Suite | Prebuild | Settings |
| --- | --- | ---: | ---: | ---: | --- |
| `hard-vs-remount-report-1` | 35/35 passed | 59,595 ms | 59,115 ms | 118 ms | `max_parallel=1`, `container_weight_cap=10` |

Additional direct Cargo filtered proofs:

| Command Filter | Result | Wall Time | Scope |
| --- | ---: | ---: | --- |
| `compact_remount_live_remount_preserves_concurrent_pip_style_install_tree` | 1/1 passed | 4.56s | private upperdir pip-style integrity under live remount |
| `compact_remount_live_remount` | 34/34 passed | 202.32s | broad compact-remount live filter including pip-style, expanded matrix, and pinned-history cases |
| `coverage_goal2` | 16/16 passed | 143.09s | additional easy/medium/hard matrix and pinned-history live proof |
| `compact_remount_live_remount` no-feature count | 50/50 passed | 0.00s test time | current direct compact-remount inventory compile/filter check |
| `coverage_goal3` | 20/20 passed | 184.79s | additional sparse, max-file-size, high-command, and pinned-history live proof |
| `compact_remount_live_remount` no-feature count | 70/70 passed | 0.00s test time | current direct compact-remount inventory compile/filter check |
| `compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree` | 1/1 passed | 7.10s | real concurrent local `pip install --target` correctness proof under live remount |
| `compact_remount_live_remount` no-feature count | 71/71 passed | 0.00s test time | current direct compact-remount inventory compile/filter check |
| `coverage_goal4` no-feature count | 30/30 passed | 0.00s test time | final easy/medium/hard batch compile/filter check |
| `coverage_goal4` | 30/30 passed | 262.59s | final live proof batch with real pip, large files, high command count, and pinned history |
| `compact_remount_live_remount` no-feature count | 100/100 passed | 0.00s test time | current direct compact-remount inventory compile/filter check |
| `compact_remount_live_remount` | 100/100 passed | 802.03s | broad direct live proof over the full compact-remount inventory |
| `compact_remount_live_remount` after fallback removal | 100/100 passed | 789.36s | final broad live proof over the full 40 easy / 30 medium / 30 hard inventory against the current report-only blocked-path package |

## Real Pip Space/Time Bench

Focused command:

```bash
CARGO_TARGET_DIR=/tmp/ephemeral-os-remount-target cargo test -p e2e-test --features e2e --test workspace-runtime-isolated compact_remount_live_remount_coverage_goal4_hard_concurrent_real_pip_install_tree -- --nocapture --test-threads 1
```

Latest run:

| Metric | Value |
| --- | ---: |
| Result | 1/1 passed, 128 filtered |
| Test runtime | 6.96s |
| Test-binary compile before run | 2.21s |
| Installed files | 786 |
| Install-ready time | 2,759 ms |
| Live remount operation time | 75 ms |
| Post-remount verification time | 385 ms |
| Layer dirs | 19 -> 2 |
| Manifest depth | 19 -> 1 |
| Remounted lowerdir count | 1 |
| Compacted snapshot layers | 19 |
| Process/quiesced count | 2 / 2 |
| LayerStack storage bytes | 1,772,814 -> 197,168 |
| Saved LayerStack bytes | 1,575,646 |
| Storage reduction | 88.88% |

This row is a mixed correctness/performance bench. It includes package
generation, two concurrent local `pip install --target` processes, hash/import
verification, a verified live remount, and post-remount integrity checks. The
75 ms remount value is the direct `sandbox.isolation.test_compact_remount`
operation latency inside that broader 6.96s live E2E row.

## Policy Paths Compared

Hard-protection compact means the running lease is not retargeted. The planner
may compact only unleased gaps outside the protected mounted lease manifest:

```text
before: [n6, n5, l4, n3, n2, n1]
after:  [C(n6,n5), l4, n3, n2, n1]
```

Verified remount normalization means the running lease is quiesced, inspected,
remounted onto a compact parent, verified, retargeted, and then unleased gaps
can be reclaimed:

```text
before: [n6, n5, l4, n3, n2, n1]
after:  [C(n6,n5), l4, C(n3,n2,n1)]
```

The important correctness boundary is unchanged:

```text
Never delete old lowerdirs from a running lease unless mount switch and lease
retarget are verified.
```

## Direct LayerStack Comparison

These rows use base-subtracted mutable layer payload bytes under
`storage_root/layers`. `B` is measured as the compact single-snapshot payload
after all leases release. They measure LayerStack compaction and lease-retarget
work, not the Linux namespace mount switch itself. Add `B` to any space value
only when translating the row back to whole-workspace disk usage.

| Scenario | Policy | Leases | B | Before - B | After While Leased - B | After Release - B | Depth | Time |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 6 x same 1 MiB, mounted l4 | Hard protection | 1 | 1,048,576 | 5,242,880 | 4,194,304 | 0 | 6 -> 5 | 0.011754917s |
| 6 x same 1 MiB, mounted l4 | Remount normalized | 1 | 1,048,576 | 5,242,880 | 2,097,152 | 0 | 6 -> 3 | 0.044615000s |
| 6 x same 16 MiB, mounted l4 | Hard protection | 1 | 16,777,216 | 83,886,080 | 67,108,864 | 0 | 6 -> 5 | 0.011886500s |
| 6 x same 16 MiB, mounted l4 | Remount normalized | 1 | 16,777,216 | 83,886,080 | 33,554,432 | 0 | 6 -> 3 | 0.043966166s |
| 12 x same 1 MiB, old lease pins mid parent | Hard protection | 2 | 1,048,576 | 11,534,336 | 8,388,608 | 0 | 12 -> 9 | 0.014396541s |
| 12 x same 1 MiB, old lease pins mid parent | Remount normalized | 2 | 1,048,576 | 11,534,336 | 6,291,456 | 0 | 12 -> 3 | 0.053167334s |
| 20 x same 1 MiB, historical readers v4/v8/v12 | Hard top-gap reclaim | 3 | 1,048,576 | 19,922,944 | 12,582,912 | 0 | 20 -> 13 | 0.016175792s |

## Delta Summary

| Scenario | Remount Saves While Leased Over Base | Base-Subtracted Retained Payload Reduction | Time Cost vs Hard Protection |
| --- | ---: | ---: | ---: |
| 6 x same 1 MiB | 2,097,152 bytes | 50.0% less mutable layer payload over `B` | 3.80x slower |
| 6 x same 16 MiB | 33,554,432 bytes | 50.0% less mutable layer payload over `B` | 3.70x slower |
| 12 x same 1 MiB, 2 leases | 2,097,152 bytes | 25.0% less mutable layer payload over `B` | 3.69x slower |

Interpretation:

- Remount normalization is consistently slower in LayerStack-only time because
  it builds a compact parent for the mounted lease and then reclaims the active
  top gap.
- The storage improvement is substantial for mounted-current same-file rewrites:
  50% less retained payload over `B` while the lease is active.
- When older historical leases still pin part of the parent prefix, remount
  helps but cannot reach the ideal bound. The two-lease case still retains 7 MiB
  while leased because the older lease pins 4 MiB of old parent layers.
- For large same-file rewrites, the extra remount-normalization time was still
  tens of milliseconds in this local benchmark while saving 32 MiB of retained
  payload relative to hard protection.

## Historical Lease Pressure

The many-lease benchmark is not a direct remount comparison; it is a hard
retention-boundary measurement for historical leases:

| Scenario | Leases | Protected Layers | B | Before - B | After Top-Gap Reclaim - B | After Release - B | Depth | Time |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 20 x same 1 MiB, historical readers at v4/v8/v12 | 3 | 12 | 1,048,576 | 19,922,944 | 12,582,912 | 0 | 20 -> 13 | 0.016175792s |

This proves hard-protection top-gap reclaim is useful when old leases pin lower
history, but it does not solve the historical lease storage class. Storage stays
proportional to the pinned historical set until those leases release or are
explicitly retired.

## Live Namespace Remount Timing

These timings come from Docker live E2E traces. They include the operation
request path, command quiesce/inspection, staged overlay switch, mountinfo
verification, lease retarget, active squash cleanup, and process resume.

The latest runner-based suite source of truth is `live-remount-matrix-batch-1`,
which passed 47/47 tests and includes the first generated matrix batch. Its
runner duration was 122,101 ms and the isolated workspace suite duration was
121,656 ms. The latest broad direct Cargo compact-remount live filter passed
34/34 matching tests in 202.32s, including the expanded matrix, pinned-history,
and concurrent pip-style install-tree case. After that, the focused
`coverage_goal2` live filter added 16/16 more passing cases in 143.09s, and the
`coverage_goal3` live filter added 20/20 more passing cases in 184.79s. The
real concurrent local-pip test then added one hard correctness row, passing
1/1 live in 7.10s. The final `coverage_goal4` batch added 29 more rows and
passed 30/30 live in 262.59s when including that real-pip row. The current
broad no-feature inventory compiles 100/100 compact-remount matching tests, and
the broad direct live proof passes 100/100 in 802.03s.

`before_storage_bytes` and `after_storage_bytes` in live traces are
`LayerStack::storage_metrics()` values, so they include the whole storage root
and small manifest/digest overhead, not just file payloads under `layers/`.
For rows that end at two layer dirs with one active lease, `B` is inferred as
half of the after-remount storage root bytes because the measured after state
contains one compact public checkpoint plus one compact leased checkpoint. For
multi-lease rows that intentionally retain historical layers, `B` is taken from
the matching compact/released row when available or from the scenario payload
model. The table is therefore the best current base-subtracted trace estimate;
future telemetry should emit `compact_base_bytes` directly.

| Shape | Time | Dirs | Commands | Processes | B Used | Before - B | After - B | Delta Over B | Reduction Over B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Historical leases still pinned, first remount | 49 ms | 17 -> 18 | 1 | 2 | 131,353 | 1,968,832 | 2,098,810 | -129,978 | -6.6% |
| Historical leases released, same command running | 46 ms | 9 -> 2 | 1 | 2 | 131,353 | 1,049,749 | 131,352 | 918,397 | 87.5% |
| Large same-file rewrite, 9 x 1 MiB | 54 ms | 10 -> 2 | 1 | 2 | 1,048,857 | 8,390,275 | 1,048,856 | 7,341,419 | 87.5% |
| Matrix deep tree, 18 files x 3 rewrites | 46 ms | 56 -> 2 | 2 | 4 | 296,871 | 598,621 | 296,870 | 301,751 | 50.4% |
| Matrix many tiny files, 36 files x 2 rewrites | 48 ms | 74 -> 2 | 3 | 6 | 151,201 | 159,044 | 151,201 | 7,843 | 4.9% |
| Matrix medium-large, 10 files x 5 rewrites | 53 ms | 52 -> 2 | 4 | 8 | 656,623 | 2,629,618 | 656,623 | 1,972,995 | 75.0% |
| Matrix nested rewrite, 16 files x 4 rewrites | 50 ms | 66 -> 2 | 4 | 8 | 526,172 | 1,583,212 | 526,172 | 1,057,040 | 66.8% |
| Matrix single hot file, 12 x 512 KiB | 49 ms | 14 -> 2 | 1 | 2 | 524,664 | 5,769,456 | 524,664 | 5,244,792 | 90.9% |
| Matrix wide sparse tree, 48 files x 1 rewrite | 46 ms | 50 -> 2 | 2 | 4 | 398,125 | 7,868 | 398,125 | -390,257 | -4960.1% |
| Complex command integrity | 42 ms | 11 -> 2 | 1 | 2 | 131,373 | 526,191 | 131,373 | 394,818 | 75.0% |
| Many-file tree, 32 files x 3 rewrites | 50 ms | 98 -> 2 | 1 | 2 | 526,873 | 1,063,884 | 526,873 | 537,011 | 50.5% |
| Process tree plus private state | 46 ms | 19 -> 2 | 1 | 3 | 786,761 | 3,935,453 | 786,761 | 3,148,692 | 80.0% |
| Process fanout, 10 child loops | 49 ms | 25 -> 2 | 1 | 22 | 393,497 | 4,329,369 | 393,497 | 3,935,872 | 90.9% |
| Repeated remount cycle 1 | 55 ms | 13 -> 2 | 1 | 2 | 131,353 | 1,443,925 | 131,353 | 1,312,572 | 90.9% |
| Repeated remount cycle 2 | 42 ms | 3 -> 2 | 1 | 2 | 131,353 | 262,580 | 131,353 | 131,227 | 50.0% |
| Repeated remount cycle 3 | 42 ms | 3 -> 2 | 1 | 2 | 131,353 | 262,580 | 131,353 | 131,227 | 50.0% |
| Three commands over 12-file x 4-rewrite tree | 43 ms | 50 -> 2 | 3 | 6 | 296,189 | 892,604 | 296,189 | 596,415 | 66.8% |
| Newer lease remount with older pinned reader | 63 ms | 13 -> 8 | 1 | 2 | 262,144 | 2,885,998 | 1,573,928 | 1,312,070 | 45.5% |
| Three open leases, two newest-lease commands | 46 ms | 25 -> 21 | 2 | 4 | 262,144 | 2,887,858 | 2,885,598 | 2,260 | 0.1% |
| Tiny explicit remountable command | 38 ms | 7 -> 2 | 1 | 2 | 295 | 1,272 | 294 | 978 | 76.9% |
| Two remountable commands | 42 ms | 13 -> 2 | 2 | 4 | 196,923 | 985,333 | 196,923 | 788,410 | 80.0% |

The measured live remount window is 38 ms to 63 ms in the latest full-suite
run. The largest live test here compacted 98 layers in a 32-file tree; the
process-tree test separately preserved a private upperdir state file created by
the running command before remount.

The multi-lease row intentionally does not compact to two layer dirs. The older
open lease remains a correctness boundary, so verified remount of the newer
lease reduces the unpinned suffix and retargets that lease while retaining the
older reader's pinned historical layers.

The three-lease row intentionally shows limited storage reduction: two
historical leases pin most of the lower chain, so verified remount can retarget
the newest lease and verify the running commands without pretending that pinned
history is reclaimable.

The fanout row is the current high-process-count proof. It shows the quiesce
protocol stopping and resuming 22 processes in one remountable session while
still completing mount verification, lease retarget, and active cleanup in
tens of milliseconds.

The historical-release row is the current proof for reclaim after old leases go
away while the newest command keeps running. The first remount intentionally
adds a compact checkpoint while the three older leases still pin their lower
layers, so retained mutable payload can temporarily increase. After those leases
release, a second verified remount on the still-running command reclaims the
same shape to two layer dirs.

The matrix rows prove that layer count is not a storage proxy. Hot-file rewrite
pressure is a strong byte win because many old versions collapse to one current
file. After subtracting `B`, the three-command 12-file x 4-rewrite tree reduces
retained overhead by 66.8%, not the raw 50% that includes an unchanged base
snapshot. Wide sparse trees with only one version per file can collapse many
layer directories but increase retained mutable bytes while the lease is open:
after subtracting `B`, that case moved from only 7,868 bytes over base to
398,125 bytes over base. A production policy should therefore make byte
pressure and rewrite density first-class inputs rather than triggering remount
normalization from depth alone.

The repeated-cycle row verifies idempotence: after the first remount compacts
the pinned snapshot, later public writes move the active public head, but the
isolated lease stays pinned and subsequent verified remounts keep the command on
the compact snapshot while preserving private upperdir state.

The concurrent pip-style row is intentionally not folded into the
base-subtracted storage table. Its stress signal is command correctness, not
LayerStack retained payload: the command creates hundreds of private upperdir
files in a `site-packages`-style tree, waits as a live remountable session,
then recomputes the install-tree SHA-256 after remount. The exact focused live
test passed in 4.56s, and the broad compact-remount filter including that case
passed in 202.32s.

The real concurrent pip row is also correctness-oriented rather than a
base-subtracted storage row. It generates two local Python packages, runs two
`python3 -m pip install --no-index --target ...` processes concurrently, and
asserts the resulting private upperdir contains at least 500 installed files.
After verified live remount, the still-running command recomputes the installed
tree SHA-256 and imports both packages from the remounted private tree. The
exact focused live test passed in 7.10s, and the broad no-feature inventory now
compiled 71/71 compact-remount rows before the final `coverage_goal4`
expansion.

The final `coverage_goal4` rows close the requested 100-test inventory. The
batch contributes 12 easy rows, 10 medium rows, and 8 hard rows when counting
the real concurrent pip row as the first hard `coverage_goal4` case. It covers
small/sparse/nested easy trees, 64/128-file medium trees, 20 same-file rewrites,
large 256 KiB groups, 1 MiB multi-file rewrites, one 4 MiB file rewritten eight
times, 256 sparse files with eight commands, 96 files x 4 rewrites with eight
commands, and pinned-history cases with up to four historical readers. The
batch passed live in 262.59s, and the broad no-feature inventory now compiles
100/100 compact-remount rows. The broad direct live proof also passes 100/100
in 802.03s. The tracked distribution is 40 easy, 30 medium, and 30 hard across
those 100 rows.

The `coverage_goal2` rows are also reported as batch timing rather than
base-subtracted storage rows. They add 16 focused live correctness cases across
large hot rewrites, very wide sparse trees, eight-command hashing, and
pinned-history pressure. The batch passed in 143.09s.

The `coverage_goal3` rows add 20 more focused live correctness cases. The batch
passed in 184.79s after correcting a test shape that exceeded the live
`sandbox.file.write` single-request cap. The measured cap is 8 MiB per write,
so larger-than-8-MiB stress should be modeled through multiple public files or
command-created private data rather than a single oversized tool request. The
current implemented compact-remount inventory is 71 matching tests after the
real concurrent pip row. After `coverage_goal4`, the implemented inventory is
100 matching tests, with broad no-feature compilation green for all 100. A
final broad live 100-case proof now also passes. The blocked-session replacement
is now implemented as pressure-only reporting: no lease retarget, no lowerdir
deletion, and no hard-protection fallback compaction from
`lease_remount_blocked`. After that product-code change, the final broad direct
live proof passed 100/100 in 789.36s against the current package.

## Unsafe Session Cost

Unsafe sessions must not be forced through remount. The mixed safe-plus-pinned
test proves one unsafe process blocks the entire remount attempt:

| E2E Run | Shape | Reason | Trace Duration | Storage / Dirs |
| --- | --- | --- | ---: | --- |
| `live-remount-mixed-tree-1` | 2 remountable commands, one safe and one fd-pinned | `fd_pinned_workspace` | 12 ms | `9 -> 9` dirs, `fallback_compaction_enabled=false`, `fallback_compacted_layers=0` |

The blocked path no longer calls lease-aware hard-protection reclaim. In the
current unsafe live E2E shapes, the protected lease covers the whole active
chain, and the blocked handler now leaves both layer-dir and storage metrics
unchanged while reporting pressure. The direct LayerStack benchmark remains the
useful comparison for the older hard-protection top-gap strategy:

```text
[n6, n5, l4, n3, n2, n1]
hard protection -> [C(n6,n5), l4, n3, n2, n1]
remount         -> [C(n6,n5), l4, C(n3,n2,n1)]
```

This is the critical policy point for fallback removal. Removing hard-protection
compact fallback is now implemented with explicit replacement behavior for
unsafe sessions:

1. Block live remount.
2. Report pinned reason, lease age, pinned bytes, and layer pressure.
3. Do not retarget the lease.
4. Do not delete any lowerdir referenced by the running mount.
5. Leave any unleased top-gap reclaim to a separate explicit maintenance
   operation.

## Decision

The performance evidence supports using verified remount normalization whenever
the command/session is proven remount-safe:

- Storage: saves 25% to 50% retained mutable layer payload over `B` versus hard
  protection in direct comparisons while leases remain active. This percentage
  excludes the unchanged base repo snapshot.
- Latency: costs roughly 3.7x to 3.8x more LayerStack-only time than
  hard-protection compaction in the small measured cases.
- Live wall-clock: verified live remount traces are 38 ms to 63 ms across the
  current full-suite command/process/file shapes.

The evidence does not support replacing unsafe blocked sessions with mandatory
remount. Unsafe sessions still need block-and-report behavior. Given current
data, hard-protection compact is fast but can only reclaim unleased gaps outside
the mounted lease. It must not be treated as a correctness fallback for
parent-prefix reclaim. The implemented policy therefore removes
hard-protection compact as the blocked-live-remount fallback and replaces it
with explicit `lease_remount_blocked` pressure reporting, while keeping
ordinary lease-aware top-gap reclaim as a separate maintenance operation.

Do not remove all hard-protection reclaim machinery yet. The measured policy
split is:

- Verified remount path: reclaim mounted parent prefix plus unleased top gap.
- Blocked live-remount path: do not retarget, do not delete mounted lowerdirs,
  report pressure, and do not compact as part of the blocked response.
- Historical leases: remain bounded only by lease policy; remount cannot reclaim
  versions still pinned by older live readers.
