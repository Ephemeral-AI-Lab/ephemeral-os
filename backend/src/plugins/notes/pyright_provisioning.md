# Pyright LSP Provisioning

**Current contract.** The LSP plugin uses `pyright-langserver --stdio` inside
the sandbox. There is no alternate language-server fallback.

## Setup Path

`sandbox.plugin.install` uploads only the LSP plugin bundle. It does not upload
Node archives, npm packages, or language-server bundles.

`setup.sh` installs Node into `/tmp/eos-node22` only when `node`/`npm` are not
already available. The default Node download list tries the npmmirror binary
endpoint first, then the official Node tarball. After Node is available, setup
installs pinned Pyright with npm:

```bash
npm install -g --omit=optional "pyright@${EOS_PYRIGHT_VERSION:-1.1.409}"
```

The marker is `.pyright_installed`, but setup also verifies
`pyright-langserver` is on `PATH` before short-circuiting.

## Runtime Path

`runtime/pyright_session.py` owns the subprocess command:

```bash
pyright-langserver --stdio
```

If the sandbox has the standard `testbed` conda environment, the session starts
through that environment and prepends `/tmp/eos-node22/bin` to `PATH`. Missing
setup fails closed rather than falling back to a different language server.

## Diagnostics

Diagnostics use Pyright's synchronous `textDocument/diagnostic` request. Normal
calls return the current diagnostic report directly. `wait_for_diagnostics=true`
is reserved for scenarios that intentionally expect a diagnostic and will poll
for a non-empty report up to the session timeout.
