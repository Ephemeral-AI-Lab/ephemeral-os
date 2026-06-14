# plugin

## Overview

This module owns live E2E coverage for the static first-party Pyright LSP provider. It exercises daemon ops `sandbox.plugin.list`, `sandbox.plugin.health`, `sandbox.plugin.pyright_lsp.query_symbols`, `sandbox.plugin.pyright_lsp.definition`, `sandbox.plugin.pyright_lsp.references`, `sandbox.plugin.pyright_lsp.diagnostics`, and `sandbox.file.write` for fixture setup and workspace refresh. Module config lives at `crates/e2e-test/tests/plugin/config/default.test.yml`.

## Checklist

- [ ] plugin-pyright-health: Static provider listing and health report Pyright as enabled, initialized, running, and bound to a projected LayerStack manifest.
- [ ] plugin-pyright-symbols: Query-symbol requests return live document symbols from the projected workspace.
- [ ] plugin-pyright-navigation: Definition and references requests return locations with repo-relative file paths.
- [ ] plugin-pyright-refresh: Workspace edits refresh the projection and Pyright process before returning navigation results.
- [ ] plugin-pyright-diagnostics: Diagnostics reflect broken Python files and clear after a workspace update fixes the file.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `pyright_lsp_setup_and_health` | Verifies `sandbox.plugin.list` and `sandbox.plugin.health` expose the static `pyright_lsp` provider, initialized process metadata, capabilities, projection root, and active manifest key. | `cargo run -p e2e-test --bin e2e-runner -- --suites plugin --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `plugin-pyright-health` |
| `pyright_lsp_query_symbols` | Writes a Python file through `sandbox.file.write`, queries `sandbox.plugin.pyright_lsp.query_symbols`, and asserts the live function symbol is returned with fresh analyzer state. | `cargo run -p e2e-test --bin e2e-runner -- --suites plugin --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `plugin-pyright-symbols`, `plugin-pyright-refresh` |
| `pyright_lsp_definition_and_references` | Seeds a small Python package and validates `definition` and `references` return repo-relative locations for the target symbol. | `cargo run -p e2e-test --bin e2e-runner -- --suites plugin --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `plugin-pyright-navigation` |
| `pyright_lsp_navigation_refreshes_after_update` | Moves a referenced symbol to another module and proves the static provider refreshes before returning the new definition. | `cargo run -p e2e-test --bin e2e-runner -- --suites plugin --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `plugin-pyright-navigation`, `plugin-pyright-refresh` |
| `pyright_lsp_diagnostics_refreshes_after_update` | Creates a syntax error, verifies diagnostics are returned, rewrites the file, and verifies diagnostics clear with fresh analyzer state. | `cargo run -p e2e-test --bin e2e-runner -- --suites plugin --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `plugin-pyright-diagnostics`, `plugin-pyright-refresh` |
