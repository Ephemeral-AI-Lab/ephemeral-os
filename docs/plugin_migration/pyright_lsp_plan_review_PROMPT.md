# Plan Review Prompt: Pyright LSP Plugin Migration

You are reviewing the migration plan, not the implementation result.

Primary artifact:

```text
/Users/yifanxu/machine_learning/LoVC/ephemeral-ai/ephemeral-os/docs/plugin_migration/pyright_lsp_migration_SPEC.md
```

Objective: decide whether the plan is sufficient to aggressively remove dynamic
plugin loading and replace it with a fixed first-party plugin family whose first
provider is `sandbox.plugin.pyright_lsp`.

## Review Scope

Check whether the plan clearly removes or retires:

- arbitrary end-user plugin upload;
- public `sandbox.plugin.ensure` accepting user manifests, staged package roots,
  setup commands, or arbitrary package paths;
- manifest-defined setup commands;
- manifest-defined operation names;
- runtime dynamic operation registration;
- dynamic plugin dispatch fallback for LSP and any first-party plugin provider;
- global `plugin.*` fallback through gateway or daemon unknown-op dispatch;
- user-controlled `PluginOperationIntent`;
- fake LSP setup through staged plugin packages;
- old `plugin.lsp.*` operation names;
- old `sandbox.plugin.lsp.*` operation names;
- tests and live e2e coverage that preserve dynamic plugin behavior only for
  the old model.

Check whether the plan preserves only the parts that are still justified:

- process lifecycle helpers;
- health probe concepts;
- snapshot or LayerStack projection helpers;
- refresh locks;
- transport code only if it is simpler than replacing it for real LSP JSON-RPC.

## Questions To Answer

1. Does the cleanup phase list all dynamic plugin-load surfaces that must be
   removed before the new model is credible?
2. Does the plan correctly keep the product category as `plugin` while moving
   the concrete provider from legacy dynamic `plugin.lsp.*` or retired
   `sandbox.plugin.lsp.*` to `sandbox.plugin.pyright_lsp.*`?
3. Are all proposed public operations under `sandbox.plugin.*`?
4. Is the proposed structure correct?

```text
crates/daemon/src/op_adapter/plugin/
  mod.rs
  admin.rs
  pyright_lsp.rs

crates/operation/src/plugin/
  admin/
  pyright_lsp/
```

5. Does `admin` own provider-neutral operations such as `sandbox.plugin.list`
   and `sandbox.plugin.health`, while `pyright_lsp` owns only Pyright-specific
   LSP behavior?
6. Does the plan distinguish trusted first-party provider provisioning from
   arbitrary user plugin setup?
7. Does the plan define enough freshness semantics for LayerStack updates,
   watcher notifications, index refresh, target-file sync, and stale-result
   avoidance?
8. Are the four initial LSP operations enough and correctly scoped?

```text
sandbox.plugin.pyright_lsp.query_symbols
sandbox.plugin.pyright_lsp.definition
sandbox.plugin.pyright_lsp.references
sandbox.plugin.pyright_lsp.diagnostics
```

9. Are any write-capable operations accidentally included before the write/OCC
   policy is designed?
10. Does the e2e plan prove real pyright setup, health, operation behavior, and
    refresh after code update for both diagnostics and at least one navigation
    op?
11. Does the plan require health/list output that proves real pyright
    initialization instead of path-existence checks against fake node or fake
    langserver fixtures?

## Expected Output

Return findings first, ordered by severity.

Use this shape:

```text
Blocking plan gaps
- [P0/P1] <issue> - <why this blocks migration>
  Evidence: <file:line or spec section>
  Required change: <specific spec change>

Non-blocking improvements
- [P2/P3] <issue>
  Evidence: <file:line or spec section>
  Suggested change: <specific improvement>

Confirmed decisions
- <decision that is correctly captured by the plan>

Missing cleanup candidates
- <dynamic plugin-load item that should be explicitly removed or quarantined>
```

Do not invent issues. If evidence is weak, say so and skip it. Prefer concrete
file paths, line references, and exact operation names.

Concrete legacy names to verify include `plugin.lsp.query_symbols`,
`plugin_id: "lsp"`, `sandbox.plugin.ensure`, `PluginEnsureInput.manifest`,
`PluginPackageInput.staged_package_root`, `PluginManifest.setup`,
`PluginManifest.operations`, `PluginOperationIntent`, `DYNAMIC_PLUGIN_POLICY`,
gateway `plugin.*` forwarding, and daemon `dispatch_registered_op`.
