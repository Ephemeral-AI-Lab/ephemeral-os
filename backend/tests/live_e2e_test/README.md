# live_e2e_test — per-call snapshot layer-stack live suite

Implementation of `.omc/plans/per-call-snapshot-layer-stack-migration/live-e2e-test-suite-plan.md`.

## Status

This package is being built incrementally from that plan.

- **Step 1 (landed):** `_harness/`, `conftest.py`, README, load-testing
  standard.
- **Step 2 (landed):** `layer_stack/test_manifest_atomicity.py` —
  vertical slice that validates the harness contract end-to-end.
- **Step 3a (landed):** layer-stack harness scaffolding —
  `_harness.with_thresholds()` (plan §3.4) plus lease/squash workload
  helpers in `_harness.workload` (`commit_layer`, `commit_layers`,
  `acquire_lease`, `release_lease`, `squash_to`, `make_write_change`).
  Unblocks the `layer_stack/` test bodies.
- **Step 3b (pending):** test bodies for the 16 skeleton files in
  §4.1–§4.4 of the plan, plus the three remaining sandbox fixtures
  (`overlay_sandbox`, `occ_sandbox`, `integrated_sandbox`). See
  *What's left* below.

Current run: `3 passed, 54 skipped, 0 failed`. Every skip is an
intentional `pending:` skeleton — see the *What's left* table.

## What's left

The 54 skips break down as follows:

| Bucket | Files | Tests | Blocker |
|---|---|---:|---|
| `layer_stack/test_squash_throughput.py` | 1 | 3 | test bodies (harness ready) |
| `layer_stack/test_layer_gc.py` | 1 | 3 | test bodies (harness ready) |
| `layer_stack/test_lease_budget.py` | 1 | 4 | test bodies (3/4 ready; the `MAX_PINNED_OLD_MANIFESTS` case needs a new knob in `LeaseBudgetWorker`) |
| `overlay/*` | 4 | 10 | `overlay_sandbox` fixture + `OverlayClient` registration path |
| `occ/*` | 4 | 17 | `occ_sandbox` fixture + `OccApplyService` registration path |
| `layer_stack_overlay_occ/*` | 5 | 17 | `integrated_sandbox` fixture + `sandbox.api.tool` end-to-end through registered overlay/occ |

Notes on the harder buckets:

- **overlay/** — needs in-sandbox overlayfs mount probes via `raw_exec`
  and direct `OverlayClient.shell` calls; baseline data lives in
  `.omc/results/stack-overlay-live-*.jsonl`. The `overlay_sandbox`
  fixture is the natural unit.
- **occ/** — runs the changeset pipeline (`WriteChange`, `EditChange`,
  `DeleteChange`, `BinaryChange`, `SymlinkChange`, `OpaqueDirChange`)
  against a synthetic layer-stack base view; needs `register_occ_service`
  wiring inside the fixture.
- **layer_stack_overlay_occ/** — only allowed to import `sandbox.api.tool`
  (per the import fence in `conftest.py`). Validates the per-call flow
  end-to-end and runs the four named load profiles. Depends on the two
  fixtures above plus a real Daytona sandbox configured per
  `tests/unit_test/test_sandbox/test_live_setup_api.py`.

Recommended landing order: layer-stack test bodies (smallest, harness
in place) → `overlay_sandbox` + overlay tests → `occ_sandbox` + occ
tests → integrated suite. The plan suggests fanning Step 3b out via
ultrawork.

## How to run

The suite is opt-in **by directory**: pyproject's
`[tool.pytest.ini_options].norecursedirs` keeps the default
`pytest backend/tests` invocation from walking into it. Run by pointing
pytest at the directory:

```bash
# Whole suite
.venv/bin/pytest backend/tests/live_e2e_test

# Single suite (layer_stack | overlay | occ | layer_stack_overlay_occ)
.venv/bin/pytest backend/tests/live_e2e_test/layer_stack

# Single file
.venv/bin/pytest backend/tests/live_e2e_test/layer_stack/test_manifest_atomicity.py

# Verbose, show skip reasons (handy while skeletons skip with "pending: ...")
.venv/bin/pytest backend/tests/live_e2e_test -v -rs

# Only the cases that already have real implementations
.venv/bin/pytest backend/tests/live_e2e_test -v -k manifest_atomicity
```

All tests still carry the `live` marker, so `pytest -m "not live"` from
anywhere also excludes them.

### Prerequisites

The session fixture brings up a real Daytona sandbox via
`setup_after_create`, so before running you need:

- Daytona credentials configured for the environment (same path as
  `tests/unit_test/test_sandbox/test_live_setup_api.py`)
- `settings.sandbox.default_image` populated; if it isn't, the session
  fixture calls `pytest.skip` and every test is skipped

Bring-up takes ~7 s once per pytest run; per-test fixtures reset
`/testbed` instead of recreating the sandbox.

The session-scoped `live_sandbox` fixture brings up a Daytona sandbox via
`setup_after_create` exactly once per pytest run (~7 s) and tears it down
in `finally`. Per-test fixtures reset `/testbed` with `git reset --hard
HEAD && git clean -fdx`.

## Defaults adopted from plan §8

The plan left six items "confirm before implementing"; this
implementation adopts the recommended defaults:

| §8 question                  | Adopted default                                          |
|------------------------------|----------------------------------------------------------|
| sandbox lifecycle scope      | session-scoped sandbox + per-test `/testbed` reset       |
| load JSONL location          | `.omc/results/live-e2e-<profile>-<utc>.jsonl`            |
| import fence enforcement     | `pytest_collection_modifyitems` hook in `conftest.py`    |
| drift definition under load  | realtime check **and** post-run replay reconciliation    |
| burst emergency-depth budget | tightened to 0 (matches E5)                              |
| provider neutrality          | facade-first; only `live_sandbox` mentions Daytona       |

If any of these need to change, edit the harness — the layer/overlay/occ
suites consume the harness contract, not the live-sandbox bring-up.

## Layer-stack scope note

`LayerStackManager` is host-side Python with no remote-storage variant.
The plan's "tmpfs root inside the sandbox via raw_exec" framing is
realized by giving the host-side manager a *local* `tmp_path` storage
root while the sandbox stays up to satisfy the gate. Tests that genuinely
need remote shell access reach for `handle.raw_exec` directly.

## Directory layout

See plan §2.

## Pass bars and load profiles

See `load_testing_standard.md`.
