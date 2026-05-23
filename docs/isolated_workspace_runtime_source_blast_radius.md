# Isolated Workspace Runtime/Source Blast Radius

Comparison range:

- Baseline without `isolated_workspace`: `561b7a4e0dc551d10fa4135c748de2cf02fa5a23`
- First introducing commit: `57c788e4e56d22fdb806dfe6290ca50b02376064`
- Compared target: `HEAD`

Scope:

- Included: `backend/scripts/**`, `backend/src/**`
- Excluded: tests, planning artifacts, skill artifacts, docs/reports/wiki/plans, and binary test assets
- Effective command:

```bash
git diff --numstat \
  561b7a4e0dc551d10fa4135c748de2cf02fa5a23..HEAD \
  -- backend/scripts backend/src \
  ':(exclude)backend/src/task_center_runner/tests/**'
```

Summary:

- 21 runtime/source files changed
- 2,978 insertions
- 13 deletions
- 13 added files
- 8 modified existing files

## Modified Existing Runtime/Source Files

| Status | Insertions | Deletions | Path | Runtime impact |
| --- | ---: | ---: | --- | --- |
| M | 23 | 6 | `backend/scripts/preflight_docker_a2_caps.sh` | Extends Docker capability preflight from overlay-only checks to include `CAP_NET_ADMIN` bridge and nftables probes. |
| M | 12 | 0 | `backend/src/sandbox/daemon/rpc/dispatcher.py` | Registers daemon RPC routes for `api.isolated_workspace.*`. |
| M | 31 | 2 | `backend/src/sandbox/daemon/scripts/launch_daemon.sh` | Hardens daemon liveness checks so zombie PIDs do not block daemon respawn. |
| M | 13 | 2 | `backend/src/sandbox/host/daemon_client.py` | Sources `/etc/environment` before daemon spawn so container-written feature flags reach the daemon. |
| M | 36 | 0 | `backend/src/sandbox/host/runtime_bundle.py` | Adds top-level `audit/` and `sandbox/isolated_workspace/` to the extracted daemon runtime bundle. |
| M | 5 | 1 | `backend/src/sandbox/provider/docker/adapter.py` | Wraps `cwd` command execution with newline-delimited subshells to preserve multiline heredocs. |
| M | 10 | 2 | `backend/src/sandbox/provider/docker/client.py` | Adds `NET_ADMIN` to default Docker run flags for bridge, veth, and nftables operations. |
| M | 32 | 0 | `backend/src/task_center_runner/audit/events.py` | Adds isolated-workspace audit event types and the phase-timing contract notes. |

## Added Runtime/Source Files

| Status | Insertions | Deletions | Path | Runtime impact |
| --- | ---: | ---: | --- | --- |
| A | 81 | 0 | `backend/scripts/cache_iws_apt_debs.sh` | Helper to cache apt dependency `.deb` files needed by isolated-workspace live fixtures. |
| A | 30 | 0 | `backend/src/sandbox/isolated_workspace/__init__.py` | Declares the isolated-workspace package contract and import-boundary expectations. |
| A | 200 | 0 | `backend/src/sandbox/isolated_workspace/handlers.py` | Daemon RPC handlers for enter, exit, status, list, reset, and audit JSONL emission. |
| A | 1,624 | 0 | `backend/src/sandbox/isolated_workspace/manager.py` | Core manager for handle lifecycle, namespace setup, layer-stack snapshot pinning, cgroup/freezer handling, TTL, GC, and tool-call execution. |
| A | 296 | 0 | `backend/src/sandbox/isolated_workspace/network.py` | Network isolation support: bridge, veth, IP pool, nftables, DNS, IMDS/RFC1918 policy. |
| A | 98 | 0 | `backend/src/sandbox/isolated_workspace/ops_handlers.py` | Bounded file/shell/search operation handlers for open isolated-workspace handles. |
| A | 12 | 0 | `backend/src/sandbox/isolated_workspace/scripts/__init__.py` | Package marker for subprocess helper scripts. |
| A | 35 | 0 | `backend/src/sandbox/isolated_workspace/scripts/_setns_libc.py` | Low-level libc/setns helper bindings. |
| A | 120 | 0 | `backend/src/sandbox/isolated_workspace/scripts/configure_dns_in_ns.py` | Helper for DNS setup inside an isolated namespace. |
| A | 34 | 0 | `backend/src/sandbox/isolated_workspace/scripts/in_ns_write.py` | Helper for writing file content from inside the isolated namespace. |
| A | 103 | 0 | `backend/src/sandbox/isolated_workspace/scripts/ns_holder.py` | Namespace holder process for persistent isolated-workspace namespace lifetime. |
| A | 99 | 0 | `backend/src/sandbox/isolated_workspace/scripts/setns_exec.py` | Executes commands after joining the isolated workspace namespaces. |
| A | 84 | 0 | `backend/src/sandbox/isolated_workspace/scripts/setns_overlay_mount.py` | Mounts the pinned lower snapshot plus isolated upper/work dirs inside the namespace. |

## Concentration

- `backend/src/sandbox/isolated_workspace/**`: 12 files, 2,735 insertions.
- Existing runtime integration points: 8 files, 162 insertions, 13 deletions.
- Non-core helper script: 1 file, 81 insertions.

Highest-impact existing-file changes:

1. `backend/src/sandbox/provider/docker/client.py`: expands Docker default capabilities with `NET_ADMIN`.
2. `backend/src/sandbox/daemon/rpc/dispatcher.py`: makes isolated workspace RPCs part of daemon startup routing.
3. `backend/src/sandbox/host/runtime_bundle.py`: makes the isolated-workspace package importable inside the daemon bundle.
4. `backend/src/sandbox/host/daemon_client.py` and `backend/src/sandbox/daemon/scripts/launch_daemon.sh`: change daemon spawn and liveness behavior.
