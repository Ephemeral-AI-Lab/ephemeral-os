# Rust parity audit — Plugins (install, PPC, refresh, registry, OCC callbacks, projection)

Domain: sandbox. Audited against Python ground truth under `/tmp/oldpy/backend/src/sandbox/ephemeral_workspace/plugin/` and `docs/architecture/sandbox/plugins.html` + `plugin-setup.html`.

Rust under audit:
- `sandbox/crates/eos-plugin/src/` (contract crate: manifest, ppc, refresh, registry, service, service_registry)
- `sandbox/crates/eos-daemon/src/plugin/` (live daemon: mod.rs, occ_callbacks.rs, ppc_router.rs, process.rs)
- `agent-core/crates/eos-plugin-catalog/src/` (host-side static catalog: manifest, names, discovery, tool_specs)
- `agent-core/crates/eos-runtime/src/plugin_tools.rs` (model-facing `lsp.*` facade)
- `agent-core/crates/eos-sandbox-host/src/runtime_artifact.rs` (trusted staged LSP PPC runtime)

## Ground truth

The Python plugin subsystem has a **host side** and a **daemon side**:

- **Host `call_plugin` (host_dispatch.py:77-225)** is the single entrypoint and drives a 5-step flow: resolve sandbox/layer-stack/caller → `ensure_installed(sandbox_id, manifest)` (install.py) → `api.plugin.ensure` only when the digest changed (guarded by `_RUNTIME_DIGEST_BY_SANDBOX_PLUGIN` + per-key `_PLUGIN_SETUP_LOCKS`) → dispatch `plugin.<name>.<op>` with `caller`/`workspace_root`/`intent` metadata → wrap response. Unknown-op replies trigger one cache-busting retry (`_is_unknown_plugin_op_error`, host_dispatch.py:436-439, 197-215). Response is capped at `_MAX_RESPONSE_BYTES = 8 * 1024 * 1024` (host_dispatch.py:60, 394-400).
- **Install (install.py)** bundles `plugin.md`+`tools/`+`runtime/`+`setup.sh` into a gzip tar, uploads to `/eos/daemon/plugins/catalog/<name>/`, runs `setup.sh` **once** under a path-based trust allowlist (`_TRUSTED_SETUP_ROOTS`, `EOS_PLUGIN_TRUSTED_SETUP_ROOTS`, install.py:77-113, 425-434), writes a `.installed-<digest>` marker (install.py:146-147, 463-468), and uses a 600-iteration `mkdir` lock with a 600s setup timeout (`_DEFAULT_SETUP_TIMEOUT = 600`, install.py:61, 337-343). For the `lsp` plugin it host-downloads Node 22.13.1 + pyright 1.1.409 (`_LSP_NODE_VERSION`/`_LSP_PYRIGHT_VERSION`, install.py:63-64, 571-612).
- **Daemon `plugin_ensure` (runtime_api.py:78-164)** imports `plugins.catalog.<plugin>.runtime.server`, flushes pending registrations into `OP_TABLE`, warms the runtime, records loaded digest state under a per-plugin `asyncio.Lock` (WR-01, runtime_api.py:71-86). On warm failure it rolls back the registered ops and evicts modules (BL-01, runtime_api.py:133-146).
- **Op registry (op_registry.py)**: `register_plugin_op` records `(plugin, op, handler, intent, auto_workspace_overlay)`; `_PLUGIN_NAME_RE = ^[A-Za-z_][A-Za-z0-9_]*$` (line 78), op_name only non-empty (line 109), `Intent.LIFECYCLE` rejected (117-120), caller-namespace gate walks live frames `_MAX_CALLER_FRAMES = 16` (77, 263-284). `flush_plugin_registrations` publishes under `plugin.<plugin>.<op>` and selects the dispatch runner by intent (168-237).
- **Op classes**: READ_ONLY runs in-process querying a long-lived service; WRITE_ALLOWED runs through `run_plugin_op_with_workspace_overlay` (overlay_dispatch.py:29-90) which acquires an operation overlay, runs a child in an `unshare -Urm` namespace (overlay_child.py), then `publish_cycle(...)` publishes the upperdir through OCC and `_attach_publish_result` records `changed_paths`/`published_manifest_version` and fails the op if the changeset failed (overlay_dispatch.py:176-203). `auto_workspace_overlay=False` opts into a self-managed OCC path (LSP `apply.py`).
- **OCC callback (ppc_service.py)** is only used by the daemon-managed PPC service harness: `publish_mounted_workspace_changes` builds `daemon.occ.apply_changeset` request frames with body `{layer_stack_root, changes[, parent_message_id]}` where each change is `_change_for_path` → `{kind: write|delete|symlink, path, content_utf8|content_bytes|source_path}` (ppc_service.py:290-327, 393-421). Frame cap `_MAX_FRAME_BYTES = 16 * 1024 * 1024` (line 30). The PPC wire is newline-delimited JSON with `{op, invocation_id, args:{direction, body}}` (ppc_service.py:186-207, 369-390).
- **Projection (projection.py)**: `WorkspaceProjection` is lease-backed; `build_manifest_key(root_hash, version) = f"{root_hash}@{version}"` (line 21-23); daemon caches projections per layer-stack root (`_MAX_WORKSPACE_PROJECTIONS = 256`, runtime_api.py:68).
- **Refresh dynamics** for stateful services (LSP/Pyright) live in `plugins/catalog/lsp/runtime/session_manager.py` + `pyright_session.py` + the daemon dispatcher, per `plugins.html` §8.3 — **these files were NOT materialized in `/tmp/oldpy`**. The only refresh primitive present in the materialized Python is `_ServiceState.ack_refresh` (ppc_service.py:245-257): a single `daemon.workspace_snapshot_refresh` op that updates `manifest_key`/`workspace_root` and returns `{manifest_key, accepted: True}`.
- **Audit field caps (runtime_api.py:352-360)**: `_audit_field` rejects NUL bytes and caps each caller field at `_MAX_AUDIT_FIELD_CHARS = 256`.

## Rust mapping

The Rust split is intentional and matches the contract-crate docstring (`eos-plugin/src/lib.rs:1-34`): the importlib in-process handler is replaced by **daemon-owned service processes over a bidirectional `AF_UNIX` PPC channel**, and `eos-plugin` owns only pure contract types.

| Python concept | Rust anchor |
|---|---|
| host `call_plugin` 5-step flow | `plugin_tools.rs` ensures the built-in LSP manifest before each `lsp.*` dispatch and then calls `plugin.<plugin>.<op>` through the daemon. Unknown-op cache-bust retry is not ported; the built-in route is source-covered, live proof pending. |
| `install.py` (bundle upload, setup.sh, marker, node/pyright) | Replaced for the built-in LSP plugin by trusted runtime-artifact staging: `runtime_artifact.rs::ensure_builtin_lsp_plugin_runtime_uploaded` uploads a fixed sandbox-Python PPC bridge to `/eos/daemon`, writes `.builtin-lsp-runtime-sha256`, and points the manifest command at `/eos/daemon/plugins/catalog/lsp/runtime/ppc_service.sh`. No catalog `setup.sh` is executed. |
| `plugin_ensure` import + flush + record | `op_ensure` (mod.rs:120-204), `ParsedEnsure::from_manifest` (mod.rs:390-413) — manifest-driven, NOT importlib |
| `register_plugin_op` / `flush_plugin_registrations` | `OpRegistry` (registry.rs:108-173), `public_op_name` (registry.rs:28-30) |
| `_PLUGIN_NAME_RE` | `eos-plugin` manifest/service validation uses the Python name rule for daemon manifests; `op_name` is only non-empty at the manifest boundary. `eos-plugin-catalog` remains a stricter host catalog contract for bundled plugin ids. |
| PPC frame `{op,invocation_id,args:{direction,body}}` | `PpcEnvelope` (ppc.rs:43-137); transport `PpcClient` (ppc_router.rs) |
| `publish_mounted_workspace_changes` → `daemon.occ.apply_changeset` | self-managed callback `occ_callbacks.rs:21-97`; routed by `dispatch_connected_self_managed_route` (mod.rs:1477-1514) |
| WRITE_ALLOWED overlay + `publish_cycle` | `dispatch_oneshot_overlay_route` (mod.rs:1408-1475) → `run_plugin_overlay_command`/`run_plugin_overlay_once` (dispatcher.rs:884-954) |
| `WorkspaceProjection` / `build_manifest_key` | `PluginServiceSnapshot` + `manifest_key` (mod.rs:736-744, 812-814); `PluginServiceKey` (service.rs) |
| `ack_refresh` single op | 7-step `send_refresh_sequence` (mod.rs:1270-1321) — redesign (Disparity D5) |
| `_MAX_FRAME_BYTES = 16 MiB` | `MAX_PPC_FRAME_BYTES = MAX_REQUEST_BYTES = 16 * 1024 * 1024` (ppc_router.rs:27, version.rs:34) — MATCH |
| `_MAX_RESPONSE_BYTES = 8 MiB` | `MAX_PLUGIN_RESPONSE_BYTES = 8 * 1024 * 1024`; `response_payload_from_reply` rejects larger bodies before JSON parse. |
| audit/caller field caps | daemon plugin boundary validates `agent_id`, `request_id`, `task_id`, `workflow_id`, and nested `caller.*` as strings with no NUL and <=256 chars. |
| isolated-mode blocks plugin ops | `ensure_plugin_family_allowed` (mod.rs:325-337) |

## Invariant table

| # | invariant | status | severity | python file:line | rust file:line | note |
|---|---|---|---|---|---|---|
| 1 | Plugin install/setup flow (manifest parse, bundle upload, setup.sh, marker) | **source-covered; live pending** | high | install.py:155-194, 425-468; host_dispatch.py:77-225 | `plugin_tools.rs` manifest ensure + `runtime_artifact.rs::ensure_builtin_lsp_plugin_runtime_uploaded` + `BUILTIN_LSP_PPC_SERVICE_PATH` | The built-in LSP plugin deliberately does not port Python `install.py` or execute catalog `setup.sh`; it stages trusted runtime files beside `eosd` and relies on an image-provided `pyright-langserver` or trusted `/eos/plugin-packages/lsp/{node.tar.xz,pyright.tgz}` archives. Source tests cover the staging/manifest path; existing Docker plugin benchmark proof is still pending. See D1. |
| 2 | PPC routing preserved | **match** | medium | ppc_service.py:45-208, 186-207 | ppc.rs:43-137 (framing), ppc_router.rs:37-409 (transport, multiplex by message_id, callback routing by `parent_message_id`) | Wire shape `{op,invocation_id,args:{direction,body}}` preserved; bidirectional + out-of-order multiplexing tested (ppc_router.rs:487-557). |
| 3 | Op registry + host dispatch preserved | **partial** | medium | op_registry.py:81-237 | registry.rs:49-173 | Registry semantics (public op name, idempotent re-reg, conflict, lifecycle reject, flush) match. BUT registry is a standalone pure type with **no caller-namespace frame walk** (`_validate_plugin_caller`, op_registry.py:263-284) and is not wired into the daemon ensure path (daemon parses a manifest instead). See D2/D3. |
| 4 | OCC callbacks publish through OCC-gated path | **match** | high | ppc_service.py:290-327 | occ_callbacks.rs:37-97 → `apply_occ_changeset` (dispatcher.rs:1761-1776) → `occ_service_for_root` (same per-root writer) | Callback validates `layer_stack_root == service root` (occ_callbacks.rs:43-51) and routes through the shared OCC writer, never a second writer. MF-1 honored (lib.rs:16-28). |
| 5 | Projection of plugin overlay-child changes back to parent overlay | **match** | high | overlay_dispatch.py:64-90, 176-203; overlay_child.py:48-74 | `run_plugin_overlay_once` (dispatcher.rs:919-954): ns-runner child + `capture_upperdir_for_occ` (line 929) + `apply_occ_changeset` (line 936); guarded changeset response (dispatcher.rs:989) | WRITE_ALLOWED oneshot overlay captures upperdir diff and publishes through OCC; only reachable via `OneshotOverlay` service_mode (mod.rs:1413). |
| 6 | Refresh + registry lifecycle preserved | **divergent** | medium | ppc_service.py:245-257 (`ack_refresh`); real LSP refresh in session_manager.py/pyright_session.py (NOT in /tmp/oldpy) | mod.rs:1129-1406 (singleflight gate, 7-step `send_refresh_sequence`, restart strategy) | Rust invents a structured 7-message refresh handshake + per-service refresh lock. No Python counterpart in materialized sources to compare against. See D5. |

## Disparities

### D1 — Built-in LSP plugin runtime/ensure path is source-covered; live proof pending (status: partial, severity: high)

- **Python**: host_dispatch.py:77-225 is the single entrypoint; install.py:155-468 performs idempotent bundle upload, `setup.sh` execution gated by `_TRUSTED_SETUP_ROOTS` (install.py:425-434), `.installed-<digest>` marker (install.py:463-468), a 600-iteration `mkdir` lock + `_DEFAULT_SETUP_TIMEOUT = 600`, and host-side Node 22.13.1 / pyright 1.1.409 download for `lsp` (install.py:571-612).
- **Rust update**: `agent-core/crates/eos-runtime/src/plugin_tools.rs` now registers `lsp.*` tools from the catalog, sends a manifest ensure through `SandboxTransport::plugin_ensure`, and dispatches the dynamic daemon op. The manifest service command is `BUILTIN_LSP_PPC_SERVICE_PATH`, exported by `eos-sandbox-host`.
- **Runtime artifact update**: `runtime_artifact.rs::ensure_builtin_lsp_plugin_runtime_uploaded` stages a trusted sandbox-Python PPC bridge/runtime under `/eos/daemon`, writes `/eos/daemon/.builtin-lsp-runtime-sha256`, and validates the wrapper path. The wrapper runs `python3 -m plugins.runtime_bridge.ppc_service` from `/eos/daemon` with no `PYTHONPATH`. It uses an image-provided `pyright-langserver` or the pre-existing trusted artifact contract `/eos/plugin-packages/lsp/{node.tar.xz,pyright.tgz}`.
- **Trust decision**: Rust does **not** execute catalog `setup.sh`, so the Python path-based `setup.sh` trust allowlist is replaced by a smaller trusted-runtime staging path for the built-in LSP plugin. This avoids porting Python `install.py` wholesale and keeps untrusted setup scripts out of the production path.
- **What remains**: Source proof exists; live proof does not. The existing Docker plugin benchmark still needs to run through the repo's normal Docker Python environment with `EOS_LIVE_E2E_IMAGE=sweevo-dask__dask-10042`.
- **Verification**: `cargo test -p eos-sandbox-host runtime_artifact`, `cargo test -p eos-runtime lsp_executor_ensures_manifest_before_dispatch`, and Python compile for the staged bridge modules.

### D2 — Daemon ensure is manifest-driven, not importlib runtime-load (status: divergent, severity: medium — likely intentional)

- **Python**: `plugin_ensure` does `importlib.import_module("plugins.catalog.<plugin>.runtime.server")`, flushes the decorator-populated `_PENDING` into `OP_TABLE`, and **warms** the runtime (runtime_api.py:109-164). It holds a per-plugin `asyncio.Lock` (WR-01) and rolls back registered ops on warm failure (BL-01).
- **Rust**: `op_ensure` reads a `manifest` object out of `args`, validates it, and builds routes from the declared operations/services (mod.rs:357-413). There is no import, no warm hook (`"runtime_warmed": false` hardcoded, mod.rs:193), and no per-plugin async lock — concurrency is serialized by the global `state_cell()` `Mutex` instead.
- **Why it matters**: This is the core "importlib replaced by PPC service process" redesign and is internally coherent. But two Python safety behaviors have no analogue: (a) the **warm-then-record** ordering that prevents a failed warm from wedging the registry (BL-01), and (b) `OP_TABLE` rollback on warm failure. In Rust a service that fails to start is handled at dispatch time (`ensure_tracked_service_process_running`, mod.rs:1538-1560) rather than ensure time, so a "loaded but unstartable" plugin records routes that fail on first dispatch instead of failing `ensure`. Confirm this is acceptable.
- **Suggested fix**: Document the redesign explicitly (the lib.rs docstring does); optionally fail `op_ensure` when `start_services=true` and a declared service cannot spawn, to preserve the Python "ensure fails loudly" contract.

### D3 — Caller-namespace registration gate (`_validate_plugin_caller`) not ported (status: missing, severity: low — likely N/A)

- **Python**: op_registry.py:263-284 walks up to `_MAX_CALLER_FRAMES = 16` live stack frames to enforce that `register_plugin_op('lsp', ...)` is only called from a module under `plugins.catalog.lsp.*`. Wrapper functions cannot hide a foreign caller.
- **Rust**: `OpRegistry::register` (registry.rs:128-139) has no caller check. `PluginOpRegistration::new` validates name/op/intent only.
- **Why it matters**: The frame-walk is a Python-import-time defense against a plugin registering ops under another plugin's namespace. In the Rust model plugins do not `import` and decorate inside the daemon — the daemon builds routes from a manifest it received over RPC — so the frame-walk is structurally inapplicable. Low severity / likely intentionally N/A, but it means the manifest sender is trusted to scope op names correctly; the only remaining guard is `public_op_name` formatting + identifier validation.
- **Suggested fix**: None required if manifests are daemon-trusted; note the dropped defense in the migration doc.

### D4 — OCC callback body accepts `snapshot_version` + `base_hashes` that the Python harness never sends (status: divergent, severity: low)

- **Python**: `publish_mounted_workspace_changes` sends `{layer_stack_root, changes[, parent_message_id]}` only (ppc_service.py:312-317). No `snapshot_version`, no `base_hashes` — so the callback publishes at "head" with no optimistic-version guard.
- **Rust**: `ApplyChangesetRequest` reads optional `snapshot_version` (defaulting `None`) and optional `base_hashes` (occ_callbacks.rs:99-114), forwarding both to `apply_occ_changeset` (occ_callbacks.rs:67-72). The Rust *daemon test* harness sends neither, so behavior matches Python at runtime (publishes at head). The new fields are an *additive* capability for a Rust harness to pass a snapshot version / base hashes for OCC conflict detection.
- **Why it matters**: Forward-compatible, not a bug. But it is a real wire-contract widening: a Rust plugin harness *could* pass a stale `snapshot_version` and get an `aborted_version` (occ_callbacks.rs:198-208 maps the status) where Python always head-published. Worth noting as an intentional protocol extension.
- **Suggested fix**: None; document that the Rust callback supports optional OCC-version guarding the Python harness omitted.

### D5 — Refresh protocol is a redesign with no materialized Python counterpart (status: divergent/unverifiable, severity: medium)

- **Python (materialized)**: `_ServiceState.ack_refresh` (ppc_service.py:245-257) is a single op updating `manifest_key`/`workspace_root`, returning `{accepted: True}`. The real LSP refresh dynamics (remount via nsenter, `didChangeWatchedFiles`, document re-sync) live in `pyright_session.py:374-650` and `session_manager.py:34-360` per `plugins.html` §8.3 — **not present in `/tmp/oldpy`**.
- **Rust**: `ensure_connected_service_current` (mod.rs:1129-1189) implements a freshness gate: check `active_manifest_key` vs the service's ready manifest, then under a per-service refresh `Mutex` run `send_refresh_sequence` — a 7-message handshake PrepareRefresh→Quiesce→remount(nsenter)→SwapWorkspace→[NotifyRefresh]→Resume→Health (mod.rs:1270-1321), each acked via `RefreshAck::require_manifest` (refresh.rs:77-92). `RestartService` strategy restarts the process instead (mod.rs:1177-1179, 1380-1406).
- **Why it matters**: The Rust handshake is more elaborate and well-tested (mod.rs:2366-2697), and the **singleflight refresh + multiplexed dispatch** invariant (refresh mutates the namespace, ops stay concurrent after the gate) is exactly the right shape. But it cannot be validated against ground truth because the authoritative Python refresh code was deleted/not materialized. The `manifest_key` format also diverges (`version:root_hash`, mod.rs:812 vs Python `root_hash@version`, projection.py:23) — internally consistent within Rust (daemon + harness both use the Rust format) so harmless, but it confirms this is a fresh design, not a port.
- **Suggested fix**: Re-materialize `pyright_session.py`/`session_manager.py` for a focused refresh-parity pass; until then treat #6 as divergent-by-design.

### D6 — Manifest identifier validator now matches the daemon-side Python rule (status: closed)

- **Original gap**: `PluginManifest::validate` used the looser service identifier rule for `plugin_id` and required identifier shape for `op_name`, accepting names Python rejected and rejecting op names Python accepted.
- **Fix**: daemon manifests now validate `plugin_id` with the faithful Python plugin-name rule (`^[A-Za-z_][A-Za-z0-9_]*$`), and `op_name` is only required to be non-empty at the manifest boundary.
- **Scope note**: `eos-plugin-catalog` still enforces its stricter host catalog naming contract for bundled plugin ids. The closed parity gap is the daemon manifest path that `api.plugin.ensure` actually consumes.
- **Verification**: `cargo test -p eos-plugin`, including `plugin_id_matches_python_name_rule`, `op_name_is_only_non_empty_at_manifest_boundary`, and `plugin_id_uses_python_name_rule`.

### D7 — 8 MiB plugin response cap and caller-field caps restored (status: closed)

- **Python**: host `_wrap_response` rejects a serialized plugin response over `_MAX_RESPONSE_BYTES = 8 MiB` (host_dispatch.py:60, 394-400). Daemon `_audit_field` rejects NUL bytes and caps caller fields at `_MAX_AUDIT_FIELD_CHARS = 256` (runtime_api.py:352-360).
- **Rust fix**: `MAX_PLUGIN_RESPONSE_BYTES` rejects PPC reply bodies above 8 MiB before JSON parse; the daemon plugin boundary validates `agent_id`, `request_id`, `task_id`, `workflow_id`, and nested `caller.*` fields as strings, NUL-free, and <=256 chars.
- **Verification**: `cargo test -p eos-daemon plugin`, including `plugin_response_payload_rejects_over_8_mib_body` and `plugin_caller_fields_reject_nul_long_and_non_string_values`.

## Extra findings

- **Manifest-key format divergence (low)**: Rust `manifest_key = "{version}:{root_hash}"` (mod.rs:812) vs Python `build_manifest_key = "{root_hash}@{version}"` (projection.py:21-23). Harmless because the key is produced and consumed entirely within the Rust daemon+harness (mod.rs:740, 804-810; `RefreshAck::require_manifest` compares Rust-format keys), never crossing to a Python peer. Stated explicitly so a future reader does not "fix" one side into incompatibility.
- **`runtime_warmed` always false (low)**: Rust `op_ensure` hardcodes `"runtime_warmed": false` (mod.rs:193). Python's warm hook (`warm_plugin_runtime`, runtime_api.py:185-209) ran an optional plugin-defined warm and surfaced `warm_result`. No Rust plugin warm mechanism exists — consistent with the no-importlib redesign, but any Python plugin relying on a warm hook (e.g. pre-spawning a session) loses it.
- **OCC callback adds `OpaqueDir` change kind (info)**: `CallbackLayerChange` includes an `OpaqueDir` variant (occ_callbacks.rs:133-135) that Python's `_change_for_path` never emits (it only produces write/delete/symlink). Additive; not a regression.
- **`OneshotOverlay` is the only WRITE projection path (closed misconfiguration risk)**: The generic WRITE_ALLOWED overlay+OCC path still requires `service_mode == OneshotOverlay`, but the manifest validator now rejects `WRITE_ALLOWED + auto_workspace_overlay=true` unless the referenced service is `OneshotOverlay`. LSP write ops remain self-managed with `auto_workspace_overlay=false`.
- **Strong PPC concurrency tests (positive)**: ppc_router.rs has thorough tests for out-of-order replies (487-557), multiple callbacks (617-685), and concurrent callbacks routed by `parent_message_id` (687-787). The callback routing has a legacy `prefix:suffix` message-id fallback (ppc_router.rs:282-288) plus a single-in-flight fallback (299-310) — robust and beyond what the materialized Python provides.

## Open questions

1. Can the existing Docker plugin benchmark run through the repo's normal Docker Python environment and prove the staged LSP/Pyright runtime path live? (D1 live gate.)
2. Is the Python LSP refresh code (`session_manager.py`, `pyright_session.py`) intended to be re-materialized for a refresh-parity pass, or is the Rust 7-step handshake the new authoritative design? (D5.)
3. Should unknown-op cache-bust retry be reintroduced for non-built-in dynamic plugins, or is manifest digest re-ensure before dispatch the accepted Rust contract?
