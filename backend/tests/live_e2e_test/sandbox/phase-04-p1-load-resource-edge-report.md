# Phase 4 P1 Load, Resource, and Edge-Case Report

Date: 2026-05-06 local / 2026-05-05 UTC run stamps.

Plan: `backend/tests/live_e2e_test/sandbox/IMPLEMENTATION_PLAN.md`, Phase 4.

## Scope

Implemented the P1 Phase 4 live tests:

- `overlay/native/test_namespace_command.py`
- `overlay/native/test_namespace_mounts.py`
- `overlay/native/test_daemon_invoker.py`
- `overlay/native/test_overlay_edge_cases.py`
- `overlay/native/test_overlay_resource.py`
- `overlay/native/test_overlay_runner_load.py`
- `layer_stack/test_layer_stack_edge_cases.py`
- `layer_stack/test_layer_stack_resource.py`
- `layer_stack/test_layer_stack_load.py`
- `occ/test_patching.py`
- `occ/test_changeset_model.py`
- `occ/test_occ_edge_cases.py`
- `occ/test_occ_resource.py`
- `occ/test_occ_load.py`
- `layer_stack_overlay_occ/test_load_profiles.py`

The P2 stress tests and Phase 5 soak remain out of scope.

## Verification

Environment:

```bash
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1
```

Commands run:

```bash
uv run python -m py_compile <phase-4 files>
uv run ruff check <phase-4 files>
.venv/bin/pytest --collect-only backend/tests/live_e2e_test/sandbox -q
EPHEMERALOS_SANDBOX_DEFAULT_IMAGE=registry:6000/daytona/sweevo-psf-requests-3738:v1 \
  .venv/bin/pytest <phase-4 target files> \
  --deselect backend/tests/live_e2e_test/sandbox/layer_stack_overlay_occ/test_load_profiles.py::test_soak_profile_no_regression_over_15_min \
  -v -rs -s --tb=short
```

Results:

| Check | Result |
|---|---:|
| `py_compile` | passed |
| targeted `ruff` | passed |
| sandbox collect-only | 77 collected |
| Phase 4 live target | 17 passed, 1 deselected, 142.67 s |

## Overlay Metrics

| Probe | Workload | p50 | p99 | Max | Result |
|---|---|---:|---:|---:|---|
| `overlay_resource` | 8 sequential snapshot shell calls | 290.637 ms | 297.509 ms | 297.560 ms | no fd/mount leak |
| `overlay_runner_load` | 20 concurrent snapshot runners | 842.863 ms | 1064.589 ms | 1072.865 ms | no fd/mount leak, no lease leak |

Overlay runner load missed the original 1000 ms diagnostic budget by about
6.5% in the final run, but stayed under the 5 s live redline. An earlier
same-command run measured 716.662 ms p99, so this is currently a variable live
tail, not a deterministic correctness failure.

Stage p99s from the final run:

| Probe | Mount p99 | Command p99 | Capture p99 | Overlay total p99 |
|---|---:|---:|---:|---:|
| `overlay_resource` | 1.527 ms | 295.258 ms | 1.658 ms | 296.860 ms |
| `overlay_runner_load` | 63.379 ms | 981.402 ms | 13.935 ms | 1023.302 ms |

Edge handling covered:

- invalid cwd escaping the mounted workspace is rejected
- signal propagation returns non-zero (`SIGTERM` observed as `-15`)
- environment propagation works inside the command wrapper
- repeated mount into a dirty run directory clears orphan upper/work content
- exec failure, stdout overflow, non-UTF8 stdout, and timeout exceptions are handled
- depth 0, depth 1, and depth 26 materialization works
- missing layer storage is detected
- injected `ENOSPC`, `EBUSY`, and `ENOMEM` errors propagate

## Layer-Stack Metrics

| Probe | Workload | Metric | Result |
|---|---|---|---:|
| `layer_stack_resource` | depth 100 | list/read | 5.233 ms |
| `layer_stack_resource` | depth 200 | list/read | 10.193 ms |
| `layer_stack_resource` | 200 publishes | publish p99 | 2.455 ms |
| `layer_stack_load` | 128 publishes, concurrency 32 | append p99 | 37.609 ms |
| `layer_stack_load` | same | publish p99 | 1.588 ms |
| `layer_stack_load` | squash 128 -> 20 layers | squash wall | 27.690 ms |

The load probe met the 50 ms publish p99 target. Squash coalesced 108 layers in
27.690 ms, which is fast for the synthetic native case. If the plan's
`coalesce <= 20 layers/s` wording is intended as a throttle rather than a
capacity diagnostic, that throttle is not implemented in this runtime path.

Edge handling covered:

- empty publish returns depth 0
- single whiteout hides the deleted file while preserving siblings
- unicode plus long paths round-trip
- symlink loops remain symlink metadata and are not followed
- a manually staged sparse file larger than 1 GiB is visible without reading it
- depth 100 and depth 200 manifests remain readable without fd/mount growth

## OCC Metrics

| Probe | Workload | p50 | p99 | Max | Result |
|---|---|---:|---:|---:|---|
| `occ_resource` | 12 mixed 5-path batches | 7.810 ms | 10.349 ms | 10.414 ms | accepted |
| `occ_load` | 80 commits, concurrency 16, 5-path, overlap | 36.935 ms | 56.822 ms | 58.174 ms | 0 starvation |

OCC load stage p99s:

| Stage | p99 |
|---|---:|
| `occ.apply.total_s` | 56.806 ms |
| `occ.prepare.total_s` | 30.267 ms |
| `occ.commit.total_s` | 11.727 ms |
| `occ.serial.queue_wait_s` | 31.081 ms |
| `layer_stack.transaction.lock_wait_s` | 8.396 ms |

Complex/edge behavior covered:

- exact edit success, missing-anchor reject, whitespace-only edit, and EOF-no-newline edit
- empty changeset, mixed add/delete/reject/drop/direct/gated routing, unicode path
- 2000-path model preparation and 10000-path huge changeset preparation
- conflicting concurrent commits produce exactly one accepted tracked write
- gitignored partial commit is dropped when shell-captured tracked conflict rejects the request layer
- UTF-8 boundary content round-trips
- 80-commit load produced 25 accepted files, 177 rejected files, and 0 starvation events

## Integrated Load Profiles

Artifacts from the final passing run:

| Profile | Artifact | Calls | Batch wall | Wall p99 | Runtime p99 | Runtime budget | Runtime budget | Wall budget |
|---|---|---:|---:|---:|---:|---:|---|---|
| `smoke` | `.omc/results/live-e2e-integrated-smoke-20260505T165416Z.jsonl` | 4 | 6072.301 ms | 1403.727 ms | 471.456 ms | 500 ms | met | missed |
| `sustained` | `.omc/results/live-e2e-integrated-sustained-20260505T165446Z.jsonl` | 12 | 12791.638 ms | 3107.266 ms | 973.279 ms | 1000 ms | met | missed |
| `burst` | `.omc/results/live-e2e-integrated-burst-20260505T165544Z.jsonl` | 24 | 23622.075 ms | 5382.904 ms | 2151.089 ms | 2500 ms | met | missed |

The integrated profile tests assert correctness, visibility, no drift, no
reject leakage, artifact emission, and a 5x/5s runtime redline. The original
profile p99 budgets are recorded as `runtime_budget_met`; wall-clock budgets are
reported separately because host/provider dispatch dominates live wall tails.

Integrated shell stage p99s:

| Profile | Shell runtime p99 | Overlay p99 | Shell OCC apply p99 | Command p99 | Capture p99 |
|---|---:|---:|---:|---:|---:|
| `smoke` | 479.125 ms | 407.068 ms | 71.558 ms | 396.064 ms | 2.868 ms |
| `sustained` | 975.886 ms | 564.667 ms | 474.411 ms | 540.476 ms | 4.362 ms |
| `burst` | 2155.439 ms | 1006.821 ms | 1391.766 ms | 962.739 ms | 6.912 ms |

Integrated edit stage p99s:

| Profile | Edit runtime p99 | OCC apply p99 | OCC prepare p99 | OCC commit p99 | Serial queue p99 |
|---|---:|---:|---:|---:|---:|
| `smoke` | 222.105 ms | 79.117 ms | 71.118 ms | 3.749 ms | 4.771 ms |
| `sustained` | 686.806 ms | 173.859 ms | 156.380 ms | 10.459 ms | 4.208 ms |
| `burst` | 1188.539 ms | 371.438 ms | 344.612 ms | 18.359 ms | 4.271 ms |

Correctness result for all three integrated profiles:

- all public-tool calls succeeded
- every accepted path was visible in the final public read
- no conflicts were emitted
- drift was 0
- all profile JSONL artifacts were written

## Interpretation

The native layer-stack and OCC load paths are comfortably within the current
budgets. Overlay runner p99 is close to the 1 s target and can miss under live
variance. Integrated runtime p99 meets the original smoke/sustained/burst
budgets in the final run, but integrated host wall p99 misses all three
budgets. The dominant wall gap is outside the in-sandbox commit primitives:
provider dispatch plus shell runtime command execution are much larger than OCC
commit or layer publication.

Residual Phase 4 risks:

- strict wall-clock budgets are not currently a stable live gate for Daytona runs
- overlay runner p99 should be watched over multiple runs before promoting the 1 s budget to a hard failure
- squash throughput is measured but not throttled
- Phase 5 soak is still required before sign-off
