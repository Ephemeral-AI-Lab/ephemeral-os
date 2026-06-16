# EphemeralOS Sandbox — Target Architecture Spec

Status: **implemented** (live crate map; supersedes the old shared-protocol-crate layout).
Scope: the sandbox system only — the host-side API service, the host engine, the
in-container daemon, and the contract artifact that binds them. Client
implementations (agent-core or otherwise) are out of scope; they are defined
by `crates/daemon/operation/ops.json`,
`crates/shared/protocol/PROTOCOL.md`, and owner-local fixtures.

---

## 1. Goals

1. **One entry point.** External callers reach exactly one socket, served by
   `gateway`. The per-sandbox daemons are unreachable from outside the host.
2. **Complete isolation, loose coupling.** Host/gateway code and daemon
   implementation code do not cross runtime boundaries. The shared artifacts
   are the shared protocol/trace crates,
   `eos-sandbox/crates/daemon/operation/ops.json`, and owner-local fixtures.
   Drift is caught by conformance tests and catalog generation.
3. **Client-first vocabulary.** The public op catalog is derived from what a
   caller needs (acquire a sandbox, use files/commands/isolation/plugins, end a
   run), not from the historical daemon inventory. Internal and operator ops
   exist but are not part of the public surface.

## 2. Components

```
caller (out of scope)
   │  UDS, newline-delimited JSON, one request per connection
   ▼
gateway (bin, host) receive → gate → route → return. No fleet logic.
   │ in-process calls
   ▼
host   (lib, host)   owns and reaches sandboxes: host engine,
   │                             protocol, runtime.
   │  loopback TCP (docker-published port) + auth token; `docker exec` fallback
   ▼
eosd / daemon  (bin+lib, in-container)   executes in-box ops: files (layer
                                 stack + OCC), commands (PTY), isolated
                                 workspaces, static plugin providers,
                                 checkpoint.
```

| Component | Kind | Job | Must never |
|---|---|---|---|
| `gateway` | bin | decode requests, enforce visibility, route by catalog, return response | contain fleet logic or per-op branches |
| `host` | lib | host engine, duplicated protocol client, Docker runtime | parse op semantics beyond catalog metadata |
| `eosd` / `daemon` | bin+lib | dispatch and execute the in-box op catalog | know about Docker, sandbox_ids, or the fleet |
| `crates/daemon/operation/ops.json` | data | reviewed static op catalog | drift from `eosd dump-ops` |
| `crates/shared/protocol/` | shared contract | op catalog, envelope/fault vocabulary, wire protocol prose and fixtures | depend on host/gateway/daemon implementation crates |
| `layerstack` | lib (in-box) | the two frozen content hashes + manifest/layer types, storage, leases, checkpoint squashing | be depended on by host-side crates |

Dependency law: shared crates are leaves; `gateway` may depend on `host` and
shared crates; `host` may depend on shared crates but not daemon implementation
crates; daemon crates may depend on shared crates and other daemon crates but
not host/gateway. Both sides conform to `crates/daemon/operation/ops.json`,
`crates/shared/protocol/PROTOCOL.md`, and owner-local fixtures via tests.

## 3. Wire protocol

### 3.1 Client hop (caller → gateway)

- Transport: Unix domain socket (path from `--listen`). Access control =
  filesystem permissions. No auth field on this hop.
- Framing: one UTF-8 compact-JSON object terminated by `\n` per connection;
  response is one JSON line, then the server half-closes.
- Request:

```json
{"op":"sandbox.file.read","sandbox_id":"sb-…","invocation_id":"<uuid4hex>","args":{…}}
```

| Field | Required | Notes |
|---|---|---|
| `op` | yes | canonical name from `crates/daemon/operation/ops.json` |
| `sandbox_id` | for daemon-bound ops; for host ops only when targeting an existing managed sandbox record | absent on host fleet-list/profile ops |
| `invocation_id` | yes | uuid4 hex; canonical request identity; echoed back as `meta.request_id` / `meta.trace.request_id` |
| `args` | yes (may be `{}`) | op-specific |

- Top-level `request_id` is not a request field; clients send `invocation_id`
  and read `request_id` only from response metadata or trace/audit APIs.
- Response: for forwarded ops, the daemon's response verbatim. For host ops,
  a host-built envelope with the same `status`/`result`/`error`/`meta` shape.
- Error response (same shape as the daemon's):

```json
{"success":false,"error":{"kind":"…","message":"…","details":{…}}}
```

API-level error kinds (in addition to daemon kinds passed through):

| kind | Raised when |
|---|---|
| `forbidden` | op exists but `visibility != public` on this socket |
| `unknown_op` | op not in catalog |
| `unknown_sandbox` | `sandbox_id` not in registry |
| `sandbox_unavailable` | recovery exhausted (connect/respawn failed) |
| `uncertain_outcome` | mutating op sent, daemon outcome unknowable after a failure; NOT retried (see §6) |

### 3.2 Box hop (host → daemon)

Unchanged from the frozen daemon protocol
(`crates/shared/protocol/PROTOCOL.md`, distilled from
`docs/contract/01-wire-protocol.md`):

- Loopback TCP to the docker-published port; one request per connection;
  compact JSON + `\n`; response read to EOF.
- `_eos_daemon_auth_token` stamped top-level by the host (popped by the daemon
  before dispatch). AF_UNIX path inside the container carries no auth.
- `_eos_daemon_protocol_version` carried inside `args`; the daemon requires
  integer version `1` before dispatch.
- Limits: `MAX_REQUEST_BYTES = 16 MiB`, request read timeout 30 s.
- `sandbox_id` is stripped before forwarding; the daemon request is
  byte-compatible with the frozen fixtures.
- Fallback transport: `docker exec <container> eosd daemon --client <socket>
  <payload>` (the daemon binary as its own thin client over its AF_UNIX socket).

## 4. Op catalog

Canonical grammar: `host.<service>.<verb>` for host/fleet ops and
`sandbox.<service>.<verb>` for daemon ops, including static first-party plugin
providers under `sandbox.plugin.*`. Host-served `sandbox.*` aliases and legacy
`api.*` aliases are retired.
The token `v1` is dead: protocol versioning lives in `args`/`ops.json`, never
in names.

### 4.1 Host ops (`served_by: host`, `visibility: public`)

| Op | Effect |
|---|---|
| `host.sandbox.acquire` | provision container + daemon (see §5); returns `sandbox_id` |
| `host.sandbox.release` | destroy container, drop registry entry |
| `host.sandbox.status` | host view (container/endpoint/recovery state) + embedded daemon readiness |
| `host.sandbox.list` | enumerate registry |
| `host.image_profiles.list` | list public, operator-approved image profiles |

### 4.2 Daemon ops (`served_by: daemon`, `visibility: public`)

| Service | Op |
|---|---|
| file | `sandbox.file.read` |
| | `sandbox.file.write` |
| | `sandbox.file.edit` |
| command | `sandbox.command.exec` |
| | `sandbox.command.poll` |
| | `sandbox.command.write_stdin` |
| | `sandbox.command.cancel` |
| | `sandbox.command.collect_completed` |
| | `sandbox.command.count` |
| isolation | `sandbox.isolation.enter` |
| | `sandbox.isolation.exit` |
| | `sandbox.isolation.status` |
| plugin | `sandbox.plugin.list` |
| | `sandbox.plugin.health` |
| | `sandbox.plugin.pyright_lsp.query_symbols` |
| | `sandbox.plugin.pyright_lsp.definition` |
| | `sandbox.plugin.pyright_lsp.references` |
| | `sandbox.plugin.pyright_lsp.diagnostics` |
| run | `sandbox.run.end` |
| call | `sandbox.call.heartbeat` |
| | `sandbox.call.cancel` |
| | `sandbox.call.count` |

### 4.3 Non-public ops

| Visibility | Ops | Caller |
|---|---|---|
| `internal` | `sandbox.runtime.ready` | host recovery machine only |
| `operator` | `host.trace.{requests, show, verify}` · `host.image.{list, pull}` · `host.container.{list, start, adopt, stop, remove}` · `sandbox.checkpoint.{layer_metrics, build_base, commit_to_workspace, commit_to_git, binding}` · `sandbox.run.cancel_all` · `sandbox.isolation.list_open` | `<listen>.operator` socket; never the client socket |
| `test` | `sandbox.isolation.test_reset` | test builds only |

### 4.4 `crates/daemon/operation/ops.json` schema

```json
{
  "protocol_version": 1,
  "ops": [
    {
      "name": "sandbox.file.read",
      "served_by": "daemon",          // "host" | "daemon"
      "visibility": "public",         // "public" | "operator" | "internal" | "test"
      "family": "Files",
      "mutates_state": false,
      "summary": "Read one file from the layer stack or isolated workspace."
    }
  ]
}
```

Generated by `eosd dump-ops` from `protocol::catalog`, checked in, and
reviewed like code. Arg/response JSON schemas are optional per-op fields, added
incrementally; fixtures cover the hot paths first.

## 5. Lifecycle (host engine)

**Provision** (`host.sandbox.acquire`):

1. `docker run` with labels `eos.sandbox_id`, `eos.tcp_port`, `eos.created_by`.
2. Copy the `eosd` binary and merged config into the container.
3. `docker exec -d eosd daemon --spawn --socket … --pid-file … --log-file …
   --tcp-host 0.0.0.0 --tcp-port <port> --auth-token <fresh random>`.
4. Resolve published port via `docker port` (retry ≤ 15 s).
5. Ready-gate: poll `sandbox.runtime.ready` until `ready: true` (bounded).
6. Insert the host registry record and cache the resolved endpoint.
7. Create the requested `workspace_root` (default `/testbed`) and sandbox
   overlay scratch directory, then build the LayerStack base binding.
8. Return `sandbox_id`.

`workspace_root` is a `host.sandbox.acquire` request arg, surfaced by
`sandbox-gateway host sandboxes acquire --workspace-root PATH`. It is not read
from `isolated_workspace.workspace_root`.

**Destroy** (`host.sandbox.release`): `docker rm -f`, drop record. No daemon-side
courtesy calls — container teardown *is* the cleanup.

**Registry**: in-memory map `sandbox_id → {container, endpoint, token, state}`.
On `gateway` startup the registry is **rebuilt from docker labels**
(`docker ps --filter label=eos.sandbox_id`); tokens are recovered from a
host-private state dir keyed by sandbox_id. A host restart MUST NOT orphan
running sandboxes.

## 6. Recovery (normative)

For a forwarded request that fails:

```
connect refused/reset ─► invalidate cached endpoint ─► re-resolve ─► retry once
        │ still failing
        ▼
docker exec thin-client fallback (eosd daemon --client)
        │ still failing
        ▼
respawn daemon in-place (docker exec --spawn …) ─► ready-gate
        │
        ├─ op.mutates_state == false  ─► replay original request
        └─ op.mutates_state == true   ─► return error kind "uncertain_outcome"
```

Empty response on a mutating op fails closed (`uncertain_outcome`) — a write is
never replayed after an ambiguous outcome. Connect-retry backoff:
0.25 / 0.5 / 1.0 / 2.0 s, then one final attempt (inherited from the frozen
host behavior).

## 7. Routing and visibility (normative)

`gateway` routes purely by catalog lookup:

```
visibility != public                  → forbidden            (client socket)
served_by == host                     → host call
served_by == daemon (incl. static sandbox.plugin.* providers) -> host::forward(sandbox_id, request)
op not in catalog                     → unknown_op
```

`gateway` MUST NOT branch on specific op names; the only per-op data it reads
is `served_by`, `visibility`, and `mutates_state`.

## 8. File/folder structure

```
eos-sandbox/
├── README.md                       entry point
├── CONTRACT.md                     version-pin pointers
├── crates/
│   ├── shared/
│   │   ├── protocol/              op catalog, envelope, wire protocol fixtures/prose
│   │   └── trace/                 trace ids, records, batches, codec/constants
│   ├── gateway/                   bin: main, serve, gateway
│   │   └── tests/contract/mod.rs
│   ├── host/                      lib: host, protocol, runtime
│   │   └── tests/
│   ├── daemon/
│   │   ├── eosd/                  + dump-ops subcommand
│   │   ├── core/                  daemon package: wire, dispatch, op adapters
│   │   ├── operation/             daemon operation DTOs, handlers, ops.json
│   │   ├── plugin/                static provider runtime implementation
│   │   ├── layerstack/            CAS hashes, storage, leases, commit queue
│   │   ├── overlay/               overlayfs mount/capture leaf
│   │   ├── namespace/             holder + runner namespace child support
│   │   ├── command/               PTY-backed commands
│   │   ├── workspace/             ephemeral + isolated workspace policy
│   │   └── config/                daemon/runtime config
│   └── e2e-test/                  live protocol tests
├── docs/
│   ├── README.md                   index
│   ├── API.md                      GENERATED from ops.json
│   └── contract/ …                 frozen historical contracts (unchanged)
└── xtask/                          + check-contract, + gen-docs
```

## 9. Conformance (the drift defense)

`cargo run -p xtask -- check-contract` is a REQUIRED CI gate:

1. `eosd dump-ops` output must equal the committed
   `crates/daemon/operation/ops.json`.
2. Daemon conformance: decodes request fixtures byte-exactly; operation
   responses, including errors, match fixture shapes after documented
   canonicalization.
3. Host conformance: `host` encodes requests that reproduce the
   request fixtures; `gateway` refuses non-public ops; router covers every
   catalog entry.
4. Name integrity: canonical names are unique across the catalog.

CAS byte-identity remains governed by `docs/contract/02-cas-byte-identity.md`
and the 18 golden cases — `layerstack` carries the implementation and
fixture tests; host-side crates never depend on it.

## 10. Drift cleanup

When crate boundaries move, clean these surfaces in the same change:

- `crates/daemon/operation/ops.json` via `cargo run -p eosd -- dump-ops` and
  `docs/API.md` via `cargo run -p xtask -- gen-docs`.
- Stale prose in `README.md`, `docs/SPEC.md`, and `docs/RUST-GUIDANCE.md`.
- Ignored local junk such as `.DS_Store`, `.omc/`, and `target/` leftovers.

## 11. Out of scope

Client implementations (generated or hand-written), warm pooling, multi-host
fleets, quotas/rate limits, remote operator access. All extend the host side
without changing this spec's component boundaries.
