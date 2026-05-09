---
name: lsp
description: Pyright-backed LSP tools for Python - hover, find_definitions, find_references, diagnostics, query_symbols. Read-only in v1.
tools:
  - name: lsp.hover
    module: tools/hover.py
  - name: lsp.find_definitions
    module: tools/find_definitions.py
  - name: lsp.find_references
    module: tools/find_references.py
  - name: lsp.diagnostics
    module: tools/diagnostics.py
  - name: lsp.query_symbols
    module: tools/query_symbols.py
setup: setup.sh
runtime: runtime/server.py
---

# LSP Plugin

Provides Python language-server tools backed by `pyright-langserver --stdio`.
The plugin runs inside the sandbox; the host calls into it through
`call_plugin`. The plugin keeps a long-lived Pyright child keyed by the active
layer-stack manifest, so edits flush through automatically and the language
server never sees stale snapshots.

## Tools

- `lsp.hover` — symbol info / type info at a `(file_path, line, character)`
  cursor.
- `lsp.find_definitions` — definition locations for a symbol cursor.
- `lsp.find_references` — references to the symbol at a cursor.
- `lsp.diagnostics` — diagnostics for a file (errors, warnings, hints).
- `lsp.query_symbols` — workspace symbol search by name fragment.

## Setup

`setup.sh` installs Node 22 into `/tmp/eos-node22` when needed, then installs
`pyright` with npm and writes a marker so re-runs are cheap.

## Constraints

- Read-only in v1. WorkspaceEdit-producing features (rename, code actions
  that modify files) are deferred.
- Document URIs are mapped onto the layer-stack snapshot lowerdir, so Pyright
  never sees the mutable provider workspace.
