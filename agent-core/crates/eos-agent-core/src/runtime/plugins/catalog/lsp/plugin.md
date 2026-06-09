# LSP Plugin Package

The built-in LSP plugin is a normal sandbox plugin package. The catalog owns
the package metadata, setup script, service entrypoint, operation list, and
model-facing tool schemas. The daemon consumes only the neutral manifest sent by
the runtime bridge.

The package installs dependency material under the digest-scoped dependency
root provided by the daemon:

- `node22/`
- `pyright/`
- `npm-cache/`

The service computes the Pyright process argv from
`EOS_PLUGIN_DEPENDENCY_ROOT`, using the package-scoped Node executable and the
package-scoped `langserver.index.js` entrypoint. It does not rely on global
Node aliases or shell path mutation.

Model operations:

- `lsp.hover`
- `lsp.find_definitions`
- `lsp.find_references`
- `lsp.diagnostics`
- `lsp.query_symbols`
- `lsp.rename`
- `lsp.format`
- `lsp.code_actions`
- `lsp.apply_code_action`
- `lsp.apply_workspace_edit`
