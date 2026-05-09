# LSP server provisioning strategy

**Current contract.** The LSP plugin uses `pyright-langserver --stdio`.
The older `pylsp` fallback is intentionally not used.

## Install Path

`setup.sh` installs Node 22 under `/tmp/eos-node22` from the official Node
tarball when `node` is not already available. It then runs:

```sh
npm config set prefix /tmp/eos-node22
npm install -g pyright
```

The marker is `.pyright_installed`, but setup also verifies
`pyright-langserver` is on `PATH` before short-circuiting.

## Spawn Path

`runtime/pyright_session.py` owns the subprocess command:

```sh
pyright-langserver --stdio
```

When the SWE-EVO conda hook is available, the runtime activates `testbed` first
and prepends `/tmp/eos-node22/bin` to `PATH`.

## Layer-Stack Contract

Pyright is pointed at a stable sandbox-local symlink, not the mutable provider
workspace. That symlink targets the active layer-stack projection lowerdir.
Sessions are cached by layer-stack root and reconciled when the active manifest
changes; Pyright is restarted only if the refresh cannot be applied safely.
