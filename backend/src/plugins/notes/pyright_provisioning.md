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

Pyright is pointed at the active layer-stack projection lowerdir, not the
mutable provider workspace. Sessions are keyed by active manifest, so a
manifest change evicts the previous Pyright process and starts a new one against
the new snapshot.
