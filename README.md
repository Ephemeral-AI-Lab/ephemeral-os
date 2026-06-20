# EphemeralOS Sandbox

EphemeralOS is now centered on the in-container daemon and its runtime crates.
The former fleet manager and sandbox gateway crates have been removed.

```
RPC caller
   | newline-delimited JSON over sandbox-daemon serve transport
   v
sandbox-daemon
   | dispatch_operation
   v
sandbox-runtime
   | command, workspace session, remount orchestration
   v
workspace / command / layerstack / namespace-process / overlay
```

| Component | Kind | Job | Must never |
|---|---|---|---|
| `sandbox-daemon` | bin+lib | bind daemon transport and dispatch runtime requests | know about Docker fleets |
| `sandbox-runtime` | lib | command operation surface plus internal workspace session/remount orchestration | own low-level runtime primitives |
| `workspace` | lib | workspace runtime lifecycle, namespace handles, capture, destroy, remount | own command process state |
| `command` | lib | PTY, transcript, process, process-group primitives | own workspace lifecycle |
| `layerstack` | lib | content hashes, manifest/layer types, storage, leases, compaction | own command execution |

**Boundary law:** daemon transport vocabulary lives in
`crates/sandbox-protocol`; daemon request dispatch lives in
`crates/sandbox-daemon`; runtime operation dispatch and concrete operation specs
live in `crates/sandbox-runtime/operation`; CAS fixtures live with `layerstack`.

## The pieces

- `crates/daemon/layerstack/tests/fixtures/` - daemon-owned CAS fixtures.
- `crates/` - the workspace: `sandbox-daemon`, `sandbox-protocol`,
  `sandbox-runtime/operation`, `daemon/layerstack`, `daemon/overlay`,
  `daemon/namespace-process`, `daemon/command`, `daemon/workspace`, and
  `daemon/config`.
- `config/prd.yml` — the single daemon config baseline (see `config/README.md`).
- `dist/` — packaged static `eosd` binaries uploaded into sandbox containers.

## Common tasks

```sh
# package the in-container daemon binary for Docker/E2E iteration
cargo run -p xtask -- package

# final fat-LTO package
cargo run -p xtask -- package --profile release

# run focused daemon checks
cargo test -p sandbox-runtime
cargo test -p sandbox-daemon
```

## Contract owners

The shared daemon JSON-line RPC protocol is owned by `crates/sandbox-protocol`.
LayerStack manifest schema and CAS fixtures are owned by `crates/daemon/layerstack`.
