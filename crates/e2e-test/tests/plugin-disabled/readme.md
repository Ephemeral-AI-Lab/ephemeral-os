# plugin-disabled

## Overview

This module owns live E2E coverage for disabled static plugin provider rejection. It exercises `sandbox.plugin.pyright_lsp.query_symbols` against a module-local daemon config with `daemon.plugin.enabled_plugins: []`. Module config lives at `crates/e2e-test/tests/plugin-disabled/config/default.test.yml`.

## Checklist

- [ ] plugin-disabled-pyright: Disabled Pyright LSP requests return a typed `plugin_disabled` rejection instead of falling through to dynamic dispatch.

## Test Case

| Test name | Test description | Command to run | Checklist item |
|---|---|---|---|
| `pyright_lsp_rejects_when_disabled` | Calls the static Pyright query-symbol op with the provider disabled and asserts the daemon returns a rejected envelope with `plugin_disabled`. | `cargo run -p e2e-test --bin e2e-runner -- --suites plugin-disabled --max-parallel 5 --container-weight-cap 10 --heavy-test-threads 4` | `plugin-disabled-pyright` |
