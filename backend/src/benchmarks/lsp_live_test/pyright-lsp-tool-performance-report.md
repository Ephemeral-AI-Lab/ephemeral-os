# Pyright LSP Tool Performance Report

Date: 2026-05-10

Sandbox: `956b2dea-e3c9-436e-adbc-906485f34564`

Scope:

- Runtime under test: `pyright-langserver --stdio`
- Tool path: `lsp.hover`, `lsp.find_definitions`, `lsp.find_references`,
  `lsp.diagnostics`, `lsp.query_symbols`
- Mutation path: public `sandbox.api.write_file` and `sandbox.api.edit_file`
- Filesystem view: stable sandbox-local symlink to the active layer-stack
  projection lowerdir

## Summary

The LSP tool operations are not inherently slow once Pyright is warm. The
pre-fix slow case was a manifest-changing call path: each setup write or edit
published a new layer-stack manifest, the LSP session cache evicted the old
Pyright process, and the next tool call paid for projection acquisition,
Pyright spawn/init, document open, and initial analysis.

The runtime now keeps one Pyright process per layer-stack root, points Pyright
at a stable sandbox-local projection root, and retargets that root when the
active manifest changes. If the refresh path cannot reconcile a manifest
change, it fails closed by evicting and restarting the session.

In the measured pre-fix complex scenario, calls that reused the same active
Pyright session were sub-second for definition, references, and symbols. Calls
that followed a manifest change were around `3-4s` because they included cold
session startup.

## Evidence

Final live run of all plugin-tool scenarios:

| Scenario | Tool calls | Result |
| --- | ---: | --- |
| `hover_returns_signature` | 1 | passed |
| `find_definitions_resolves_local_def` | 1 | passed |
| `find_references_returns_call_sites` | 1 | passed |
| `diagnostics_flags_undefined_name` | 1 | passed |
| `query_symbols_lists_module_symbols` | 1 | passed |
| `hover_reflects_edit` | 2 | passed |
| `complex_all_tools_layerstack_write_edit_cycle` | 9 | passed |

Single-tool scenario timings after Pyright was already installed:

| Scenario | Tool | Tool time |
| --- | --- | ---: |
| `hover_returns_signature` | `lsp.hover` | `7.96s` |
| `find_definitions_resolves_local_def` | `lsp.find_definitions` | `3.09s` |
| `find_references_returns_call_sites` | `lsp.find_references` | `3.31s` |
| `diagnostics_flags_undefined_name` | `lsp.diagnostics` | `3.32s` |
| `query_symbols_lists_module_symbols` | `lsp.query_symbols` | `2.92s` |

Complex all-tools scenario timings:

| Step | Tool | Tool time | Notes |
| --- | --- | ---: | --- |
| 1 | `lsp.hover` | `3.59s` | First call after setup writes/new manifest |
| 2 | `lsp.find_definitions` | `0.69s` | Same Pyright session |
| 3 | `lsp.find_references` | `0.61s` | Same Pyright session |
| 4 | `lsp.query_symbols` | `0.53s` | Same Pyright session |
| 5 | `lsp.diagnostics` | `1.08s` | Same Pyright session |
| 6 | `lsp.diagnostics` | `3.22s` | After edit/new manifest |
| 7 | `lsp.hover` | `3.68s` | After edit/new manifest |
| 8 | `lsp.find_definitions` | `0.57s` | Same Pyright session as step 7 |
| 9 | `lsp.diagnostics` | `1.01s` | Same Pyright session as step 7 |

Post-cleanup live pytest rerun against the same Daytona sandbox with
`EOS_LSP_SANDBOX_ID=956b2dea-e3c9-436e-adbc-906485f34564`, after restarting
the in-sandbox daemon so the patched plugin bundle was loaded. The first hover
below includes the one intentional cold Pyright spawn after daemon restart:

| Scenario | Tool | Pre-fix tool time | Post-fix tool time | Change |
| --- | --- | ---: | ---: | ---: |
| `hover_returns_signature` | `lsp.hover` | `7.96s` | `7.35s` | `-8%` |
| `find_definitions_resolves_local_def` | `lsp.find_definitions` | `3.09s` | `0.59s` | `-81%` |
| `find_references_returns_call_sites` | `lsp.find_references` | `3.31s` | `0.79s` | `-76%` |
| `diagnostics_flags_undefined_name` | `lsp.diagnostics` | `3.32s` | `0.67s` | `-80%` |
| `query_symbols_lists_module_symbols` | `lsp.query_symbols` | `2.92s` | `0.69s` | `-76%` |

Post-cleanup complex scenario timings:

| Step | Tool | Pre-fix tool time | Post-fix tool time | Notes |
| --- | --- | ---: | ---: | --- |
| 1 | `lsp.hover` | `3.59s` | `0.79s` | First call after setup writes; no Pyright restart |
| 2 | `lsp.find_definitions` | `0.69s` | `1.04s` | Same Pyright session |
| 3 | `lsp.find_references` | `0.61s` | `0.63s` | Same Pyright session |
| 4 | `lsp.query_symbols` | `0.53s` | `0.59s` | Same Pyright session |
| 5 | `lsp.diagnostics` | `1.08s` | `0.68s` | Same Pyright session |
| 6 | `lsp.diagnostics` | `3.22s` | `0.93s` | After edit/new manifest; session refreshed |
| 7 | `lsp.hover` | `3.68s` | `0.86s` | After edit/new manifest; session refreshed |
| 8 | `lsp.find_definitions` | `0.57s` | `0.62s` | Same Pyright session as step 7 |
| 9 | `lsp.diagnostics` | `1.01s` | `2.72s` | No diagnostics response arrived; bounded wait returned empty |

The live pytest run passed all seven scenarios in `38.85s`. The meaningful
manifest-change calls dropped from `3-4s` to roughly `0.6-1.0s`. The remaining
tail is no-diagnostics polling, not process restart.

Direct Pyright/layer-stack benchmark evidence:

| Component | Observed cost |
| --- | ---: |
| Layer-stack snapshot materialization | about `27-32ms` per checkpoint |
| Public write/edit internal API time | mostly about `0.16-0.19s` in a deep stack |
| Auto-squash contribution during deep-stack writes | about `0.13-0.15s` |
| Pyright initialize | about `0.46-0.55s` |
| Hover after init/open | about `0.90-1.00s` |
| Definition after warm session | about `4-38ms` in direct probe, `0.57-0.69s` through plugin tool path |
| Fixed diagnostics wait in direct probe | `3.0s` |

## Why Single-Tool Scenarios Look Slow

The measured single-tool scenarios were not measuring a warm LSP operation.
Each scenario first wrote setup files into `/testbed`. That write published a
new layer-stack manifest. Before this implementation, the session manager keyed
Pyright sessions by active manifest, so the next tool call had to:

1. Acquire a projection for the new manifest.
2. Start `pyright-langserver --stdio`.
3. Send `initialize` and `initialized`.
4. Open the requested document.
5. Wait for Pyright to parse/analyze enough state.
6. Execute the requested LSP operation.

That startup path explains the `~3s` calls after setup. The tool operation
itself is much faster once the Pyright process is already running. The current
implementation removes that startup from ordinary manifest changes by
refreshing Pyright's stable root and notifying opened documents.

## Layer-Stack Attribution

The layer stack is not the main source of the LSP tool latency in this run.
Projection materialization was tens of milliseconds in the direct benchmark.
The measurable write/edit internal cost rose when auto-squash triggered in a
deep stack, but that was still roughly `0.13-0.15s`, not the `3s` LSP tool
latency.

The dominant pre-fix LSP latency source was lifecycle churn: every
manifest-changing operation forced a new Pyright process and fresh analysis.
The implementation replaces that with stable-root retargeting plus explicit
session eviction only when refresh fails.

## Current Runtime Behavior

The LSP plugin uses `pyright-langserver --stdio`.

Relevant implementation points:

- `backend/src/plugins/catalog/lsp/runtime/pyright_session.py` owns the Pyright
  subprocess, stable projection root, manifest refresh notification, and
  fail-closed eviction behavior.
- `backend/src/plugins/catalog/lsp/runtime/session_manager.py` keys sessions by
  layer-stack root and refreshes the existing session when the active manifest
  changes.
- `backend/src/plugins/catalog/lsp/runtime/paths.py` maps returned locations
  through the stable projection root and back to repo paths.
- `backend/src/plugins/catalog/lsp/setup.sh` installs Node 22 and npm Pyright.
- `backend/src/benchmarks/lsp_live_test/scenarios.py` includes the complex
  all-tools scenario.

Pyright-specific protocol fixes were required:

- The client must advertise `workspace.workspaceFolders` when sending
  `workspaceFolders`; otherwise Pyright can hang during initialize.
- The JSON-RPC client must answer server-to-client requests such as
  `workspace/configuration`.
- LSP framing must be byte-based because diagnostics can contain non-ASCII
  text.

## Recommendation

The main optimization target was to stop restarting Pyright on every manifest
change.

Implemented architecture:

1. Keep a sandbox-local Pyright sidecar alive for the workspace.
2. Retarget a stable workspace symlink to the latest layer-stack projection
   after public write/edit operations.
3. Apply file updates via `textDocument/didChange` or close/reopen documents
   after manifest refresh.
4. Use layer-stack manifests for correctness and recovery, but avoid tying every
   semantic query to a brand-new Pyright process.
5. Keep fail-closed behavior: if the sidecar cannot reconcile a change, evict
   and restart explicitly rather than silently reading stale files.

Expected impact:

- Definition/references/symbol calls should stay in the sub-second range for
  repeated operations.
- Hover should avoid repeated `3-4s` cold-start cost after edits.
- Diagnostics latency should be bounded by Pyright analysis, not process
  startup plus projection setup.

## Verification Commands

```sh
uv run ruff check backend/src/plugins/catalog/lsp backend/src/benchmarks/lsp_live_test backend/tests/unit_test/test_plugins/test_lsp_catalog.py
uv run python -m py_compile backend/src/plugins/catalog/lsp/runtime/pyright_session.py backend/src/plugins/catalog/lsp/runtime/session_manager.py backend/src/plugins/catalog/lsp/runtime/lsp_jsonrpc.py backend/src/benchmarks/lsp_live_test/runner.py backend/src/benchmarks/lsp_live_test/scenarios.py backend/src/benchmarks/lsp_live_test/tests/test_lsp_scenarios.py
uv run pytest -q backend/tests/unit_test/test_plugins/test_lsp_catalog.py backend/tests/unit_test/test_plugins/test_lsp_runtime_paths.py backend/tests/unit_test/test_plugins/test_lsp_session_refresh.py
uv run pytest --collect-only -q backend/src/benchmarks/lsp_live_test/tests/test_lsp_scenarios.py backend/src/benchmarks/lsp_live_test/tests/test_pyright_layerstack.py
```

Live verification used a direct runner invocation against the existing Daytona
sandbox `956b2dea-e3c9-436e-adbc-906485f34564` to avoid measuring fresh sandbox
provisioning time as LSP latency.
