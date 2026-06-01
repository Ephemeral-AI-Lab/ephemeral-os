# Plugin Refresh Strategy Experiment

- run_id: `local-a586bdc28978`
- container_id: `2856103e0c53`
- runtime: `python-daemon-thin-client`
- api_transport: `tcp`
- workspace_root: `/eos/plugin/workspace`
- recommendation: `workspace_snapshot_refresh`

## Performance

| strategy | p50 refresh/materialize ms | p95 ms | max ms | correctness |
|---|---:|---:|---:|---|
| workspace_snapshot_refresh | 5.747 | 5.747 | 6.020 | current reads |
| commit_to_workspace_timer | 11.419 | 11.419 | 13.856 | raw workspace refreshed |

## Key Findings

- workspace snapshot refresh kept reads current without publishing or materializing the raw workspace
- raw filesystem watches did not observe LayerStack writes without materialization
- commit_to_workspace did not observe the held synthetic snapshot lease; periodic materialization can reset storage under a long-lived service unless the daemon adds an explicit plugin-service guard
- workspace snapshot refresh p95=5.747ms versus commit_to_workspace p95=11.419ms
- fs watch without materialization stale: `True`
- commit blocked by active snapshot refresh lease: `False`
- auto-squash then commit gate passed: `True`

## Strategy Scores

| strategy | performance | implementation simplicity | arbitrary plugin ease | note |
|---|---:|---:|---:|---|
| workspace_snapshot_refresh | 5 | 3 | 4 | requires a small harness protocol; supports remount/restart strategies generically |
| commit_to_workspace_timer | 1 | 4 | 2 | simple timer, but full materialization, active-lease refusal, and storage reset make it unsafe as steady-state refresh |
| raw_workspace_fs_watch | 2 | 2 | 3 | native watches need materialized projection; without it watchers stay stale |

## Safety Gates

- safety gate pass: `True`
- snapshot refresh samples ok: `True`
- commit timer samples ok: `True`
- concurrent commit/write readable after: `True`
- final active leases: `0`
- final orphan layers: `0`
- final missing layers: `0`
