# LSP server provisioning strategy

**Current contract.** The LSP plugin uses `pyright-langserver --stdio`.
The older `pylsp` fallback is intentionally not used.

## Install Path

`sandbox.plugin.install` resolves a Linux Node archive on the host, uploads it
into the LSP plugin install directory, and passes it to `setup.sh` as
`EOS_NODE_ARCHIVE`. That keeps Node provisioning on the host-side upload path
instead of depending on in-sandbox internet. If no uploaded archive is provided,
`setup.sh` may download Node in the sandbox only when the installer explicitly
sets `EOS_LSP_ALLOW_DOWNLOAD=1`; it tries the official Node tarball first, then
the npmmirror binary endpoint because some Daytona sandboxes cannot establish
TLS to `nodejs.org`. The URL list can be overridden with
`EOS_NODE_DOWNLOAD_URLS`.

The installer also resolves a local `pyright-<version>.tgz` package with
`npm pack pyright@<version>` and uploads it as `EOS_PYRIGHT_PACKAGE`. That keeps
the Pyright install on the same host-side upload path. If the local package is
unavailable, `setup.sh` may download the pinned Pyright package only when
`EOS_LSP_ALLOW_DOWNLOAD=1`.

It then runs:

```sh
npm config set prefix /tmp/eos-node22
npm install -g --omit=optional "$EOS_PYRIGHT_PACKAGE"
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
