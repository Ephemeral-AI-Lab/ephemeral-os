# Load Testing Standard — Live E2E Suite

Realizes §6 of `.omc/plans/per-call-snapshot-layer-stack-migration/live-e2e-test-suite-plan.md`.

## Profiles

Defined in `_harness/load_profiles.py`. Each `LoadProfile` is a frozen
dataclass; tests reference them by name.

| Profile     | Shells/s | Edits/s | Duration | Overlap | Gitignored | p99 budget | Drift | Emerg.-depth |
|-------------|---------:|--------:|---------:|--------:|-----------:|-----------:|------:|-------------:|
| `smoke`     |        2 |       4 |     30 s |    25 % |       40 % |     500 ms |     0 |            0 |
| `sustained` |        8 |      16 |     60 s |    50 % |       40 % |   1 000 ms |     0 |            0 |
| `burst`     |       30 |      60 |     20 s |    50 % |       40 % |   2 500 ms |     0 |            0 |
| `soak`      |        4 |       8 |     15 m |    35 % |       40 % |   1 200 ms |     0 |            0 |

`burst.max_emergency_depth_events` is held at 0 to match E5's pass bar
(plan §8 question 5; resolved here in favour of the stricter reading).

## Pass bars (apply to all profiles unless overridden)

- **Correctness** (mandatory): zero drift; every accepted write visible
  in the final merged view; every rejected write absent. Driven by
  `assertions.assert_accepts_visible_rejects_invisible` (lands with the
  integrated suite).
- **Latency**: per-call wall time p99 ≤ profile's `max_p99_ms`.
- **Depth**: stack depth stays in `[SQUASH_TARGET-1, EMERGENCY_DEPTH-5]`
  except in `burst`, which may touch `EMERGENCY_DEPTH` ≤ 0 times.
- **Squash**: coalesce ratio ≤ 20 layers/s under `sustained` or `burst`.
- **Lease budget**: zero forced kills in `smoke` / `sustained` / `soak`;
  `burst` kills permitted only if `MAX_LEASE_AGE` is overridden in the
  profile.
- **Telemetry**: `manifest_lag` and `shell_age_seconds` present on every
  committed result; histogram emitted to the run JSONL.

## Telemetry contract

Every load run emits one JSONL record per call to
`.omc/results/live-e2e-<profile>-<utc>.jsonl`, matching the schema in
plan §6 (mirrors the existing `stack-overlay-live-*.jsonl` shape).

## Drift definition under load

Realtime + replay (plan §8 q4):
1. **Realtime**: every commit asserts `assert_no_torn_reads(captures)`
   in-flight.
2. **Replay**: after the run, replay every captured upperdir against
   the final manifest and confirm accept/reject decisions match.

Both checks must pass for the run to count toward the promotion window.

## Promotion criteria

A profile is "passing" only when the last three runs on the same
image+kernel meet every bar. One failing run = re-run; two-of-three
failing = red, blocks the migration cutover step (Phase 06).
