# Daemon CLI

`sandbox-daemon` is a private runtime binary. Public operator commands use
`sandbox-manager-cli`, `sandbox-runtime-cli`, or `sandbox-observability-cli`;
these subcommands are for the gateway, manager, and namespace runtime process
chain.

## Top-Level

```text
sandbox-daemon --version
sandbox-daemon -V

sandbox-daemon serve ...
sandbox-daemon ns-runner ...
sandbox-daemon ns-holder ...
```

## `serve`

```text
sandbox-daemon serve
  --config-yaml PATH          required
  --workspace-root PATH       required; must be absolute
  --socket PATH
  --pid-file PATH
  --tcp-host HOST
  --tcp-port PORT
  --http-host HOST
  --http-port PORT
  --auth-token TOKEN
  --sandbox-id ID
  --spawn
  --help | -h
```

Notes:

- `--config-yaml` is read before the full `serve` config is parsed.
- `--workspace-root` is always required, including `--spawn`.
- TCP JSON-line RPC binds only when both `--tcp-host` and `--tcp-port` are set.
  When TCP is enabled, an auth token must come from `--auth-token` or
  `SANDBOX_DAEMON_AUTH_TOKEN`.
- HTTP binds only when both `--http-host` and `--http-port` are set.
- `--spawn` starts a detached foreground child and returns.

## `ns-runner`

Target shape for the file-operation work:

```text
sandbox-daemon ns-runner (--shell | --mount-overlay | --file-op) --request-fd FD --result-fd FD
```

Mode flags:

```text
--shell          setns, then run the interactive shell/PTY path
--mount-overlay  setns, then perform the overlay mount request
--file-op        setns, then perform one read/write/edit file operation
```

The mode flag is required and exactly one mode flag is valid. Do not keep the
current implicit no-flag shell default when adding `--file-op`; rename the
internal `Run` operation to `Shell`.

`--request-fd FD` and `--result-fd FD` are required for every mode:

- `--request-fd` is the inherited file descriptor carrying the serialized
  `NamespaceRunnerRequest`.
- `--result-fd` is the inherited file descriptor carrying serialized
  `RunResult`.
- Shell still needs these because shell stdin/stdout/stderr are the PTY, not
  the control protocol.
- `--mount-overlay` and `--file-op` need them because their stdio is not the
  result channel.

Implementation mapping:

```text
--shell          crates/sandbox-daemon/src/runner/shell.rs
--mount-overlay  crates/sandbox-daemon/src/runner/mount_overlay.rs
--file-op        crates/sandbox-daemon/src/runner/file_op.rs
```

## `ns-holder`

```text
sandbox-daemon ns-holder readiness_fd control_fd (shared | isolated)
```

Arguments:

- `readiness_fd`: inherited file descriptor used for the namespace holder
  readiness handshake.
- `control_fd`: inherited file descriptor kept open to pin the holder lifetime.
- `shared | isolated`: namespace network mode.

`ns-holder` uses positional fd arguments because it is a narrow internal child
process adapter. `ns-runner` uses named fd flags because it has multiple runner
modes and additional flags.
