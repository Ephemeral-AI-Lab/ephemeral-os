# EphemeralOS Sandbox

One host-side API process fronting a fleet of Docker sandboxes, each running
one in-container daemon. External callers reach exactly one socket; the
per-sandbox daemons are unreachable from outside the host. The full target
architecture is `docs/SPEC.md`.

```
caller
   │  UDS, newline-delimited JSON, one request per connection
   ▼
gateway (bin, host) receive → gate → route → return. No fleet logic.
   │ in-process calls
   ▼
host   (lib, host)   owns and reaches sandboxes: host engine,
   │                             protocol, runtime.
   │  loopback TCP (docker-published port) + auth token; `docker exec` fallback
   ▼
eosd / daemon  (bin+lib, in-container)   executes in-box commands and
                                 isolated workspace lifecycle.
```

| Component | Kind | Job | Must never |
|---|---|---|---|
| `gateway` | bin | decode requests, enforce visibility, route by local table, return response | contain fleet logic |
| `host` | lib | host engine, protocol client, Docker runtime | depend on daemon implementation crates |
| `eosd` / `daemon` | bin+lib | dispatch in-box daemon requests | know about Docker, sandbox_ids, or the fleet |
| `crates/protocol/` | cross-boundary contract | envelope/fault vocabulary, wire protocol prose and fixtures | depend on host/gateway/daemon implementation crates |
| `layerstack` | lib (in-box) | the two frozen content hashes + manifest/layer types, storage, leases, and compaction | be depended on by host-side crates |

**Boundary law:** host/gateway crates do not depend on daemon implementation
crates, and daemon crates do not depend on host/gateway crates. Cross-boundary
schemas live in `crates/protocol`. Wire and
CAS fixtures live with their owning crates. `cargo run -p xtask -- check-contract`
is the drift gate.

## The pieces

- `crates/protocol/PROTOCOL.md` — framing/auth/errors/canonicalization
  plus immutable wire fixtures in `crates/protocol/fixtures/`.
- `crates/daemon/layerstack/tests/fixtures/` — daemon-owned CAS fixtures.
- `crates/` — the workspace. Contract: `protocol`.
  Gateway: `gateway`. Host: `host`. Daemon side:
  `daemon/eosd`, `daemon/core`, `daemon/layerstack`, `daemon/overlay`,
  `daemon/namespace`, `daemon/command`, `daemon/operation_service`,
  `daemon/workspace`, and `daemon/config`.
- `docs/contract/` — the frozen historical wire/CAS contracts.
- `config/prd.yml` — the single daemon config baseline (see `config/README.md`).
- `dist/` — packaged static `eosd` binaries uploaded into sandbox containers.

## Common tasks

```sh
# the contract drift gate (CI-required)
cargo run -p xtask -- check-contract

# package the in-container daemon binary for Docker/E2E iteration
cargo run -p xtask -- package

# final fat-LTO package
cargo run -p xtask -- package --profile release

# optional: set a shared custom socket once instead of passing --listen/--socket
# export EOS_GATEWAY_SOCKET=/tmp/eos-sandbox.sock

# repo-local gateway CLI
bin/ephai-sandbox-gateway --help

# optional: install the CLI binary once for global `ephai-sandbox-gateway ...`
cargo install --path crates/gateway --locked

# serve the sandbox gateway (one client socket + one operator socket beside it)
bin/ephai-sandbox-gateway host serve

# inspect through the gateway client mode
bin/ephai-sandbox-gateway host images profiles
bin/ephai-sandbox-gateway host images list
bin/ephai-sandbox-gateway host containers list
bin/ephai-sandbox-gateway host sandboxes list
bin/ephai-sandbox-gateway host containers start <docker-image>

# acquire a sandbox, then operate inside its daemon
SID=$(bin/ephai-sandbox-gateway host sandboxes acquire | jq -r .sandbox_id)
WSID=<workspace-session-id>
bin/ephai-sandbox-gateway daemon --sandbox-id "$SID" commands exec --workspace-session-id "$WSID" -- pwd
bin/ephai-sandbox-gateway host sandboxes release "$SID"
```

## Version pins

`CONTRACT.md` pins the wire protocol version and the on-disk manifest schema
version, and documents the bump procedure. Golden fixtures are immutable
ground truth — never regenerate them to match code.
