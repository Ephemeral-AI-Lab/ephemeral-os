# Result Review Prompt: Pyright LSP Plugin Migration

You are reviewing the implementation result after the migration work has landed.
Do not treat the spec as proof. Verify the actual repository state.

Primary spec:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/plugin_migration/pyright_lsp_migration_SPEC.md
```

Objective: determine whether the implementation actually removed dynamic plugin
loading aggressively and replaced the LSP path with the fixed
`sandbox.plugin.pyright_lsp` provider.

## Required Checks

Verify that no LSP path still depends on:

- arbitrary end-user plugin upload;
- public `sandbox.plugin.ensure` accepting user manifests, staged package roots,
  setup commands, or arbitrary package paths;
- user-provided setup scripts;
- staged fake plugin packages;
- manifest-defined operations;
- dynamic operation registration;
- dynamic plugin dispatch fallback;
- global `plugin.*` forwarding through gateway or daemon unknown-op dispatch for
  any first-party plugin provider;
- user-controlled `PluginOperationIntent`;
- old `plugin.lsp.*` names;
- old `sandbox.plugin.lsp.*` names;
- fake Python AST service behavior standing in for pyright;
- fake node or fake pyright fixtures in live e2e.

Verify that the implemented public operation surface is static:

```text
sandbox.plugin.list
sandbox.plugin.health
sandbox.plugin.pyright_lsp.query_symbols
sandbox.plugin.pyright_lsp.definition
sandbox.plugin.pyright_lsp.references
sandbox.plugin.pyright_lsp.diagnostics
```

Verify that disabled plugins return a typed disabled-provider error, not an
unknown-op fallback.

Verify that `sandbox.plugin.ensure` is removed from the public catalog/API or is
clearly internal-only first-party provisioning with no user-supplied manifest,
setup, staged package, or arbitrary package-root fields.

## Suggested Searches

Run focused searches and inspect each hit before deciding it is a problem:

```bash
rg -n 'sandbox\.plugin\.lsp|plugin\.lsp|plugin_id\s*=\s*"lsp"|PluginOperationIntent|manifest-defined|setup\.sh|langserver\.index\.js|fake pyright|fake node|query_symbols' crates docs
rg -n 'sandbox\.plugin\.ensure|PluginEnsureInput|staged_package_root|PluginManifest|DYNAMIC_PLUGIN_POLICY|dispatch_registered_op' crates docs
rg -n 'dynamic plugin|dynamic.*op|register.*plugin|unknown.*plugin|plugin.*fallback|manifest.*op|user.*plugin.*upload|plugin\.\*' crates docs
rg -n 'sandbox\.plugin\.(pyright_lsp|list|health|node_lsp)' crates docs
```

For every hit, classify it as:

```text
removed correctly
kept intentionally
leftover dynamic behavior
unclear and needs follow-up
```

## Structure Review

Confirm the implementation uses the intended structure:

```text
crates/daemon/src/op_adapter/plugin/
  mod.rs
  admin.rs
  pyright_lsp.rs

crates/operation/src/plugin/
  admin/
  pyright_lsp/
```

Check ownership boundaries:

- `admin` owns `sandbox.plugin.list` and `sandbox.plugin.health`;
- `pyright_lsp` owns Pyright-specific setup, LSP JSON-RPC, request shaping,
  refresh, diagnostics, definitions, references, and symbol queries;
- reusable process, health, projection, and watcher helpers are static
  infrastructure, not a reintroduced arbitrary plugin runtime;
- there is no `crates/daemon/src/op_adapter/plugin_lsp.rs`.

## Functional Review

Confirm real pyright behavior:

- pyright starts as a monitored service when `pyright_lsp` is enabled;
- health/list report enabled provider state;
- health proves real LSP initialization with executable paths, process id,
  initialize success, capabilities/server info where available, active manifest
  key, projection root, and last init/analysis errors;
- the implementation uses real LSP JSON-RPC framing;
- each public op maps to the intended LSP behavior:
  `textDocument/documentSymbol`, `workspace/symbol` if workspace symbol search is
  implemented, `textDocument/definition`, `textDocument/references`, and cached
  `textDocument/publishDiagnostics`;
- LayerStack workspace updates are projected into the pyright workspace;
- every initial Pyright op waits for current analysis or returns a bounded
  stale/timeout status with `manifest_key` and `freshness`;
- diagnostics update after code changes;
- definition and references do not answer from stale indexes.

## E2E Review

Confirm live e2e tests cover:

- setup and health for enabled `pyright_lsp`;
- `sandbox.plugin.pyright_lsp.query_symbols`;
- `sandbox.plugin.pyright_lsp.definition`;
- `sandbox.plugin.pyright_lsp.references`;
- `sandbox.plugin.pyright_lsp.diagnostics`;
- diagnostics refresh after code update;
- at least one navigation op after code update;
- disabled-provider rejection.

Confirm old e2e coverage was removed or rewritten:

- no staged `/eos/scratch/uploads/plugins/lsp/...` package;
- no fake AST service;
- no fake node shim;
- no fake pyright langserver fixture;
- no assertions against `plugin.lsp.query_symbols` or
  `sandbox.plugin.lsp.query_symbols`;
- no generated e2e readmes/indexes still advertise `plugin.lsp.*` as active
  coverage.

## Expected Output

Return findings first, ordered by severity.

Use this shape:

```text
Findings
- [P0/P1/P2/P3] <issue>
  Evidence: <file:line>
  Impact: <why this matters>
  Required change: <specific fix>

Dynamic-load removal map
- <surface>: <removed / intentionally kept / leftover / unclear>

Structure verdict
- <correct / partially correct / incorrect>
  Reason: <short explanation>

Verification
- Commands run:
  - <command>
- Tests run:
  - <command or not run with reason>
```

Do not invent issues. Do not accept the spec as proof. Cite concrete files,
lines, diffs, test results, or command output.
