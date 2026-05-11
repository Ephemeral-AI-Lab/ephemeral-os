# Pyright LSP Performance Report

Date: 2026-05-12

Sandbox: `0a217165-8c80-46e7-98a1-44e1ba62b9e9`

Runtime under test:

- LSP server: `pyright-langserver --stdio`
- Diagnostics mode: Pyright `textDocument/diagnostic` pull response only
- Plugin path: host tool -> `sandbox.plugin.call_plugin` -> sandbox daemon ->
  in-sandbox LSP plugin runtime -> Pyright process
- Workspace view: layer-stack manifest projected into a sandbox-local stable
  root

This report reused an existing Daytona sandbox. "Sandbox setup speed" below
means the setup phases needed by the LSP test harness inside that sandbox:
runtime bundle availability, daemon restart/readiness, and layer-stack workspace
base rebuild. It does not include fresh Daytona sandbox provisioning.

## Summary

The current bottleneck is no longer Pyright diagnostics. Warm end-to-end LSP
tool calls through the full plugin/daemon path are about `0.42-0.90s`, with the
common no-edit path clustered around `0.42-0.62s`. The larger values are after
manifest-changing edits where the stable projection is retargeted and open
documents are synchronized.

Cold plugin setup from a sandbox with `/tmp/eos-node22` removed took `23.70s`.
After that, plugin marker-hit setup was `0.004s`. Raw Pyright protocol calls
inside one already-initialized process are much faster: definition, references,
symbols, and pull diagnostics were all below `0.03s` in the direct probe. Hover
was the expensive raw method in that probe at `0.83s`.

## Setup Metrics

Sandbox setup phases on the reused sandbox:

| Phase | Wall time |
| --- | ---: |
| Runtime bundle upload, warm marker path | `1.012s` |
| Daemon restart to `api.runtime.ready` | `2.343s` |
| Workspace base rebuild, host-observed | `1.350s` |
| Workspace base rebuild, daemon internal | `0.935s` |

Workspace base rebuild input size:

| Metric | Value |
| --- | ---: |
| Inventory bytes | `135,088,891` |
| Inventory files | `520` |
| Inventory dirs | `55` |
| Inventory symlinks | `0` |
| Collect | `0.268s` |
| Rescan | `0.264s` |
| Write layer | `0.318s` |

Plugin setup phases:

| Phase | Wall time |
| --- | ---: |
| Cold `lsp` plugin install with `/tmp/eos-node22` removed | `23.701s` |
| `api.plugin.ensure` runtime import/register | `0.430s` |
| Marker-hit `ensure_installed` | `0.004s` |

Cold plugin setup includes bundle upload/extract, `setup.sh`, Node 22 install,
and npm Pyright install. The measured plugin digest was
`1a670d29284714e3b511879798096cfe22cf69052facb6822032ebd8d4606a92`.

## Raw Pyright LSP Speed

Direct in-sandbox Pyright protocol probe, one Pyright process, root
`/testbed/eos_lsp_perf_direct`:

| Operation | Time |
| --- | ---: |
| `initialize` | `0.941s` |
| `didOpen` for 3 files | `0.000s` |
| `textDocument/hover` | `0.830s` |
| `textDocument/definition` | `0.025s` |
| `textDocument/references` | `0.006s` |
| `textDocument/documentSymbol` | `0.006s` |
| `workspace/symbol` | `0.006s` |
| `textDocument/diagnostic` | `0.006s` |
| `shutdown` | `0.007s` |

Correctness signals from the same probe:

| Result | Count |
| --- | ---: |
| References | `4` |
| Document symbols | `5` |
| Workspace symbols | `1` |
| Diagnostics | `1` |

The diagnostic was `"missing_value" is not defined` with code
`reportUndefinedVariable`.

## End-to-End LSP Tool Speed

Verification command:

```sh
EOS_LSP_SANDBOX_ID=0a217165-8c80-46e7-98a1-44e1ba62b9e9 \
  uv run pytest backend/src/benchmarks/lsp_live_test/tests/test_lsp_scenarios.py -q -s
```

Result: `7 passed, 1 warning in 31.78s`.

Per-scenario timings from that pytest run:

| Scenario | Warmup | Tool calls |
| --- | ---: | --- |
| `hover_returns_signature` | `2.38s` | `lsp.hover=0.624s` |
| `find_definitions_resolves_local_def` | `0.99s` | `lsp.find_definitions=0.610s` |
| `find_references_returns_call_sites` | `0.91s` | `lsp.find_references=0.519s` |
| `diagnostics_flags_undefined_name` | `0.92s` | `lsp.diagnostics=0.543s` |
| `query_symbols_lists_module_symbols` | `0.98s` | `lsp.query_symbols=0.568s` |
| `hover_reflects_edit` | `0.94s` | `lsp.hover=0.580s`, `lsp.hover=1.076s` |
| `complex_all_tools_layerstack_write_edit_cycle` | `0.62s` | see below |

Complex all-tools scenario:

| Step | Tool | Time |
| ---: | --- | ---: |
| 1 | `lsp.hover` | `0.598s` |
| 2 | `lsp.find_definitions` | `0.492s` |
| 3 | `lsp.find_references` | `0.449s` |
| 4 | `lsp.query_symbols` | `0.430s` |
| 5 | `lsp.diagnostics` | `0.429s` |
| 6 | `lsp.diagnostics` after edit | `0.805s` |
| 7 | `lsp.hover` after edit | `0.898s` |
| 8 | `lsp.find_definitions` after edit | `0.430s` |
| 9 | `lsp.diagnostics` final clean file | `0.423s` |

Tool summary from the same pytest run:

| Tool | Count | Min | Median | Mean | Max |
| --- | ---: | ---: | ---: | ---: | ---: |
| `lsp.hover` | 5 | `0.580s` | `0.624s` | `0.755s` | `1.076s` |
| `lsp.find_definitions` | 3 | `0.430s` | `0.492s` | `0.510s` | `0.610s` |
| `lsp.find_references` | 2 | `0.449s` | `0.484s` | `0.484s` | `0.519s` |
| `lsp.diagnostics` | 4 | `0.423s` | `0.486s` | `0.550s` | `0.805s` |
| `lsp.query_symbols` | 2 | `0.430s` | `0.499s` | `0.499s` | `0.568s` |

## Live E2E Scenario Verification

Exact scenario file:
`backend/src/live_e2e/scenarios/sandbox/complex_project_build_shell_edit_lsp.py`.

Verification command:

```sh
EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1 \
  uv run pytest backend/src/live_e2e/tests/sweevo/test_complex_project_build_shell_edit_lsp.py::test_complex_project_build_shell_edit_lsp_full -q -s
```

Result: `1 passed, 4 warnings in 1482.44s (0:24:42)`.

Run artifact:
`.sweevo_runs/scenario_logs/sandbox.complex_project_build_shell_edit_lsp/20260511T183612Z_cd4b04785a01`.

Run metadata:

| Metric | Value |
| --- | ---: |
| Sandbox id | `c8cb8aa4-b5fb-4ea4-a81c-291d52734079` |
| TaskCenter run id | `352b545a-7a5a-40eb-9cd0-ecc6f14ce9f0` |
| `run.json` duration | `1450.46s` |
| In-sandbox probe duration | `1448.66s` |
| Pytest wall time | `1482.44s` |

In-sandbox `summary.json` correctness:

| Metric | Value |
| --- | ---: |
| Logical edits | `653` |
| `edit_file` routed edits | `435` |
| Shell routed edits | `218` |
| Shell edit ratio | `0.3338` |
| Shell edit errors | `0` |
| LSP semantic correctness checks | `336` |
| LSP failed checks | `0` |
| Diagnostic probe checks | `10` |
| Diagnostic error detected and repaired | `true` |
| In-sandbox pytest | `115 passed, 2 warnings in 0.17s` |

Semantic LSP correctness by tool:

| Tool | Passed checks |
| --- | ---: |
| `lsp.diagnostics` | `77` |
| `lsp.find_definitions` | `64` |
| `lsp.find_references` | `67` |
| `lsp.hover` | `64` |
| `lsp.query_symbols` | `64` |

Host-observed tool-call timing from `.sweevo_runs`:

| Tool | Count | Errors | P50 | P95 |
| --- | ---: | ---: | ---: | ---: |
| `lsp.diagnostics` | `85` | `0` | `0.430s` | `1.047s` |
| `lsp.find_definitions` | `64` | `0` | `0.427s` | `0.445s` |
| `lsp.find_references` | `67` | `0` | `0.451s` | `0.564s` |
| `lsp.hover` | `64` | `0` | `1.133s` | `1.214s` |
| `lsp.query_symbols` | `64` | `0` | `0.469s` | `0.504s` |
| `edit_file` | `436` | `1` | `0.436s` | `0.458s` |
| `read_file` | `83` | `0` | `0.427s` | `0.438s` |
| `write_file` | `43` | `0` | `0.433s` | `0.448s` |
| `shell` | `256` | `2` | `1.063s` | `1.103s` |

The recorded `edit_file` and shell errors are expected scenario probes, not
unhandled failures: the scenario intentionally exercises overlap/conflict and
diagnostic repair paths, and the test asserts `failed_checks == 0`,
`shell_edit.errors == 0`, and no unexpected conflicts.

In-sandbox performance attribution from `perf.json`:

| Layer | Metric | Value |
| --- | --- | ---: |
| Layer stack | materialize count | `254` |
| Layer stack | materialize p95 | `0.0062s` |
| Layer stack | squash p95 | `0.0042s` |
| Overlay | capture p95 | `0.00048s` |
| OCC | commit p95 | `0.00143s` |
| OCC | total commit time | `0.831s` |
| Shell edit | p50 | `1.067s` |
| Shell edit | p95 | `1.107s` |

## Direct Layer-Stack Probe

Verification command:

```sh
EOS_LSP_SANDBOX_ID=0a217165-8c80-46e7-98a1-44e1ba62b9e9 \
  uv run pytest backend/src/benchmarks/lsp_live_test/tests/test_pyright_layerstack.py -q -s
```

Result: `1 passed, 1 warning in 22.75s`.

Top-level metrics:

| Metric | Value |
| --- | ---: |
| Scenario wall time | `21.41s` |
| Warm `ensure_node_pyright` | `2.00s` |
| Public write/edit host wall | `0.427-0.484s` |
| Public write/edit daemon internal | `0.005-0.008s` |
| Snapshot materialization | `0.022-0.025s` |
| Direct Pyright LSP probe per stage | `1.72-1.85s` |

The host-observed public write/edit time is dominated by the provider exec
round trip. The daemon-side mutation work itself is single-digit milliseconds
for these small files.

## Interpretation

Current speed split:

| Layer | Current behavior |
| --- | --- |
| Sandbox setup | `1-2s` for reused-sandbox runtime/base phases, excluding fresh Daytona provisioning |
| Cold plugin setup | `23.70s` when Node/Pyright are absent |
| Warm plugin setup | `0.004s` marker-hit path |
| Raw Pyright diagnostics | `0.006s` in direct pull probe |
| End-to-end `lsp.diagnostics` | `0.423-0.805s` through tool/plugin/daemon/projection path |
| End-to-end all LSP tools | Mostly `0.42-0.62s`, edit-refresh tail up to `1.08s` |

The main remaining cost in ordinary tool calls is the host-to-sandbox
provider/daemon/plugin envelope and projection/document synchronization. Raw
Pyright pull diagnostics are not the bottleneck.

## Verification Commands

```sh
uv run ruff check \
  backend/src/benchmarks/lsp_live_test/pyright_layerstack.py \
  backend/src/benchmarks/lsp_live_test/runner.py \
  backend/src/benchmarks/lsp_live_test/tests/test_lsp_scenarios.py \
  backend/src/plugins/catalog/lsp/runtime/pyright_session.py \
  backend/src/sandbox/plugin/handler.py \
  backend/src/sandbox/plugin/install.py \
  backend/src/sandbox/plugin/runtime/registry.py \
  backend/src/sandbox/plugin/session.py \
  backend/tests/unit_test/test_plugins/test_lsp_catalog.py \
  backend/tests/unit_test/test_plugins/test_lsp_session_refresh.py \
  backend/tests/unit_test/test_sandbox/test_plugin_handler.py \
  backend/tests/unit_test/test_sandbox/test_plugin_install.py \
  backend/tests/unit_test/test_sandbox/test_plugin_session.py \
  backend/tests/unit_test/test_benchmarks/test_lsp_runner_workspace_base.py

bash -n backend/src/plugins/catalog/lsp/setup.sh

uv run pytest \
  backend/tests/unit_test/test_plugins/test_lsp_catalog.py \
  backend/tests/unit_test/test_plugins/test_lsp_session_refresh.py \
  backend/tests/unit_test/test_sandbox/test_plugin_install.py \
  backend/tests/unit_test/test_sandbox/test_plugin_handler.py \
  backend/tests/unit_test/test_sandbox/test_plugin_session.py \
  backend/tests/unit_test/test_benchmarks/test_lsp_runner_workspace_base.py -q

EOS_LSP_SANDBOX_ID=0a217165-8c80-46e7-98a1-44e1ba62b9e9 \
  uv run pytest backend/src/benchmarks/lsp_live_test/tests/test_lsp_scenarios.py -q -s

EOS_LSP_SANDBOX_ID=0a217165-8c80-46e7-98a1-44e1ba62b9e9 \
  uv run pytest backend/src/benchmarks/lsp_live_test/tests/test_pyright_layerstack.py -q -s

EPHEMERALOS_RUN_HEAVY_LIVE_E2E=1 \
  uv run pytest backend/src/live_e2e/tests/sweevo/test_complex_project_build_shell_edit_lsp.py::test_complex_project_build_shell_edit_lsp_full -q -s
```
