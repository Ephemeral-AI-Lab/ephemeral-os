---
title: "Sandbox Subsystem"
tags: ["sandbox", "occ", "overlay", "layer-stack", "daemon", "daytona", "plugin", "lsp", "live-e2e", "see-also"]
created: 2026-05-10T11:26:24.973Z
updated: 2026-05-10T11:58:07.394Z
sources: []
links: ["live-e2e-testing-framework-design.md", "engine-query-loop-llm-seam.md", "task-center-pipeline.md"]
category: architecture
confidence: medium
schemaVersion: 1
---

# Sandbox Subsystem

_Source: explore agent draft, 2026-05-10. See `.omc/wiki-draft/sandbox.md`._

## Top-level surface

`sandbox/api/__init__.py` re-exports module-level lifecycle and tool verbs from
`sandbox/api/default.py`; there is no `SandboxClient` facade layer.

- Sync lifecycle: `create_sandbox`, `start_sandbox`, `stop_sandbox`, `delete_sandbox`, `ensure_sandbox_running`, `get_sandbox`, `list_sandboxes`, `get_health`
- Async tool verbs: `shell`, `raw_exec`, `read_file`, `write_file`, `edit_file`

Sync lifecycle/discovery calls route through `api/lifecycle.py`,
`api/discovery.py`, and `api/preview_urls.py`. Tool verbs route through
`api/_tool_verbs/{shell,raw_exec,read,write,edit}.py`.

## Subsystem map

**occ** — Optimistic concurrency control.
- `occ/service.py` `OccService.apply_changeset` — prepare + commit through layer stack.
- `occ/client.py` `OccClient` — validates workspace binding, forwards to `OccService`.
- `occ/ports.py` — `OccLayerStackPort`; implemented by `LayerStackManager`.
- `occ/stage/transaction.py` `CommitTransaction` — holds commit lock, calls `publish_layer`.

**overlay** — Runs commands in a prepared snapshot workspace, captures diffs.
- `overlay/worker.py` `execute_request` — prepare workspace -> exec -> `capture_changes` -> `OverlayCapture` JSON.
- `overlay/mounts.py` `mount_snapshot` — builds a copy-backed lower/merged workspace from manifest layers.
- `overlay/capture.py` `capture_changes` — diffs upperdir or copy-backed workspace changes against the snapshot.

**layer_stack** — Content-addressed layered FS inside sandbox.
- `layer_stack/manager.py:58` `LayerStackManager` — manifest I/O, leases, reads, publishes; implements `OccLayerStackPort`.
- `layer_stack/manifest/model.py:37` `Manifest` — ordered `LayerRef` list + version + root_hash.
- `layer_stack/layer/change.py:34` `LayerChange` ADT — Write/Delete/Symlink/OpaqueDir → `LayerDelta`.
- `layer_stack/layer/publisher.py:42` `LayerPublisher` — writes delta as tar layer, updates manifest atomically.
- `layer_stack/lease/registry.py:23` `LeaseRegistry` — snapshot leases during in-flight commits.
- `layer_stack/workspace/base.py:82` `build_workspace_base` — content-addressed base layer from host workspace.
- `layer_stack/maintenance/squash.py` `SquashService` — collapses layers when depth > 32.

**command_exec** — Namespace stub; logic in `occ/routing/orchestrator.py`.

**runtime/daemon** — AF_UNIX daemon inside sandbox; all host→guest calls.
- `runtime/daemon/__main__.py:14` — `asyncio.run(serve(socket, pid_file))`.
- `runtime/daemon/rpc/dispatcher.py:23` `OP_TABLE` + `dispatch_envelope_async` — `{"op","args"}` → handler → JSON.
- `host/daemon_client.py:140` `call_daemon_api` — thin Python client via `provider.exec`; `_daemon_spawn_command` spawns `nohup python3 -m sandbox.runtime.daemon --socket <sock>`.

**plugin runtime (LSP)** — Dynamically-loaded in-sandbox plugin ops.
- `plugin/session.py` `call_plugin` — `ensure_installed` → `api.plugin.ensure` → `call_daemon_api("plugin.<n>.<op>")`.
- `plugin/handler.py:49` `plugin_ensure` (daemon) — imports plugin server module, flushes ops into `OP_TABLE`.
- `plugin/install.py:84` `ensure_installed` — uploads plugin tar; idempotent via marker file.

**provider/daytona** — Daytona container backend.
- `provider/protocol.py:21` `ProviderAdapter` — `create/get/list/start/stop/delete/exec/set_labels/get_health/list_snapshots`.
- `provider/daytona/adapter.py:99` `DaytonaProviderAdapter` — wraps `exec` in bash exit-code protocol.
- `provider/daytona/bootstrap.py:15` `bootstrap_daytona_provider` — instantiates adapter, calls `set_default_provider`.
- `provider/registry.py` — `set_default_provider`, `register_adapter`, `get_adapter`, `dispose_adapter`.

**host** — Local-side orchestration.
- `host/bootstrap.py` `setup_after_create/start` — concurrent `ensure_git` + bundle upload → `call_daemon_api("api.ensure_workspace_base")`.
- `host/runtime_bundle.py:261` `ensure_runtime_uploaded` — tars `sandbox/runtime/`, uploads via `provider.exec`.
- `host/bootstrap.py` `ensure_running` — probe → restart + `setup_after_start` on failure.
- `runtime/async_bridge.py` `run_sync` / `run_sync_in_executor` — loop-aware sync-from-async bridge.

## Key data structures

| Name | File:line | Role |
|---|---|---|
| `SandboxCaller` | `models.py:13` | agent_id + run_id + task_id on every audited request |
| `SearchReplaceEdit` | `models.py:86` | old_text → new_text unit |
| `EditFileRequest` | `models.py:94` | path + `tuple[SearchReplaceEdit]` + caller |
| `WriteFileRequest` | `models.py:71` | path + content + caller |
| `ShellRequest` | `models.py:107` | command + caller + cwd + timeout |
| `ShellResult` | `models.py:116` | `GuardedResultBase` + exit_code + stdout/stderr |
| `ProviderAdapter` | `provider/protocol.py:21` | Protocol every backend implements |
| `Manifest` | `layer_stack/manifest/model.py:37` | Ordered LayerRef list + version + root_hash |
| `LayerChange` | `layer_stack/layer/change.py:34` | Write/Delete/Symlink/OpaqueDir change |

## Lifecycle

1. **Register** — `bootstrap_daytona_provider()` at app startup.
2. **Create** — `api/status.py:70` → `provider.create` → `register_adapter` → `setup_after_create`.
3. **Post-create** — concurrent `ensure_git` + bundle upload → `call_daemon_api("api.ensure_workspace_base")`.
4. **Daemon** — `_daemon_spawn_command` via `provider.exec` → AF_UNIX socket open, `OP_TABLE` populated.
5. **Tool call** — `edit_file` → `call_daemon_api("api.v1.edit_file")` → `OccService.apply_changeset` → `LayerPublisher` writes layer.
6. **Shell** — `shell` -> `call_daemon_api("overlay.run")` -> `overlay/worker.py` -> prepared workspace + exec + capture -> OCC commits delta.
7. **Plugin** — `call_plugin` → `ensure_installed` → `api.plugin.ensure` → `call_daemon_api("plugin.<n>.<op>")`.
8. **Recovery** — `ensure_sandbox_running` → probe → restart + `setup_after_start`.
9. **Teardown** — `delete_sandbox` → `adapter.delete` → `dispose_adapter`.

## What the live-e2e framework needs

### Public API calls

| Call | File | Purpose |
|---|---|---|
| `bootstrap_daytona_provider()` | `provider/daytona/bootstrap.py:15` | One-time setup |
| `create_sandbox(name, snapshot)` | `api/__init__.py:27` | Provision sandbox |
| `delete_sandbox(id)` | `api/__init__.py:29` | Teardown |
| `ensure_sandbox_running(id)` | `api/__init__.py:30` | Pre-assertion health check |
| `shell / write_file / edit_file` | `api/__init__.py:37-40` | Drive tool calls |
| `read_file / raw_exec` | `api/__init__.py:36,38` | Assert state |

### Data structures the framework constructs

- `SandboxCaller(agent_id, run_id, task_id)` — `models.py:13`
- `EditFileRequest(path, edits=tuple[SearchReplaceEdit(old, new)], caller)` — `models.py:94`
- `WriteFileRequest(path, content, caller)` — `models.py:71`
- `ShellRequest(command, caller, cwd, timeout)` — `models.py:107`

### Real-vs-mock

| Component | Real / Mock |
|---|---|
| `DaytonaProviderAdapter` | **REAL** — must hit actual Daytona |
| `host/bootstrap.py` bootstrap | **REAL** — daemon needs bundle + workspace base |
| `runtime/daemon` in-sandbox | **REAL** — all tool calls traverse it |
| `occ` + `layer_stack` | **REAL** — correctness under test |
| `overlay/worker.py` execution | **REAL** — shell path needs the snapshot workspace and capture pipeline |
| plugin install + handler | **REAL** for LSP tests only |
| `stream_message` | **MOCK** — sole replaced seam |

### Coverage per "What to Test" bullet

- **setup**: `host/bootstrap.py` bootstrap, bundle upload, `ensure_workspace_base`.
- **daemon**: AF_UNIX lifecycle, `call_daemon_api` round-trip, `OP_TABLE` dispatch.
- **occ**: `OccService.apply_changeset`, conflict detection, `CommitQueue`.
- **overlay**: `overlay/worker.py` workspace preparation + capture via `shell` tool calls.
- **layerstack**: `LayerStackManager` manifest read/write, `LayerPublisher`, `SquashService`.
- **command_exec**: guarded exec via `occ/router.py`.
- **lsp plugin server**: `call_plugin` 5-step, `ensure_installed`, `plugin_ensure`.
- **tool call impact**: assert `ShellResult.changed_paths`, `EditFileResult.applied_edits`; verify via `read_file`/`raw_exec`.

---

## Update (2026-05-10T11:58:07.394Z)

## See also

- [[role-generator]] — executor-profile generators are the only role that mutates sandbox state
- [[engine-query-loop-llm-seam]] — the LLM API seam
- [[task-center-pipeline]] — what consumes the sandbox
