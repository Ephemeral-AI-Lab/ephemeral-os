# Daemon HTTP Adversarial Review Prompt

Use this prompt to review the current daemon HTTP allowlist and implementation.
The review is intentionally skeptical and scoped to:

1. design, implementation cleanness, and simplicity;
2. file and folder structure;
3. naming convention.

Paste this into a fresh reviewer with repository access.

## Role

You are an adversarial design reviewer. Your job is to find where the daemon
HTTP work is larger, less clear, less canonical, or less consistently named than
it needs to be. Bias toward subtraction. A finding is useful only if it cites the
spec or code and proposes a smaller or clearer replacement.

Do not validate the implementation because it works. Working code can still be
too tangled, too spread out, or misleadingly named.

## Source of Truth

Binding contract:

```text
docs/obsidian/ephemeral-os/implementation_plan/mcp_cli_surface/http.md
```

Current user-facing reference:

```text
docs/daemon-http/README.md
```

Primary implementation areas:

```text
crates/sandbox-daemon/src/rpc/
crates/sandbox-daemon/src/http/
crates/sandbox-daemon/src/serve.rs
crates/sandbox-runtime/operation/src/operations/registry/
crates/sandbox-manager/src/operations/management/service/impls/export_changes.rs
crates/sandbox-protocol/src/
crates/sandbox-console/src/
crates/sandbox-config/src/configs/manager.rs
crates/sandbox-manager/src/model.rs
crates/sandbox-provider-docker/src/engine.rs
crates/sandbox-provider-docker/src/installer.rs
crates/sandbox-provider-docker/src/launch.rs
crates/sandbox-provider-docker/src/runtime.rs
crates/sandbox-provider-docker/src/labels.rs
e2e/runtime/daemon_http/test_daemon_http.py
```

Review daemon RPC dispatch under `crates/sandbox-daemon/src/rpc/` against the
separate HTTP listener under `crates/sandbox-daemon/src/http/`.

## Fixed Intent

Do not relitigate these decisions unless the implementation makes them
impossible or clearly expensive:

- The JSON-line daemon RPC listener and daemon HTTP listener are separate.
- `daemon_http` is a real daemon surface, not a "preview" feature.
- Public forwarding routes live under `/forward`.
- The exact public HTTP surface is:

```text
GET /health
ANY /forward/shared/<port>/...
ANY /forward/isolated=<workspace_id>/<port>/...
POST /files/list
```

- `POST /files/list` is the sole HTTP operation endpoint and remains absent
  from runtime CLI and MCP.
- `/files/read`, `/files/write`, `/files/edit`, `/files/blame`,
  `/observability/*`, `/export/*`, and every other unlisted path return `404`.
- Management, runtime, and observability operations use authenticated gateway
  RPC through CLI, MCP, or the console `/api/rpc` bridge.

- Docker publishes a single daemon HTTP container port to one random host
  loopback port.
- Gateway-level sandbox routing is outside the daemon HTTP contract.
- Isolated loopback forwarding through setns is outside the current contract.
- HTTP capabilities may use dedicated folders.

## Review Lenses

### L1: Design, Cleanness, and Simplicity

Walk the request path end to end:

```text
host HTTP client
  -> daemon_http listener
  -> http router
  -> health, forward, or file-list handler
  -> fixed response, forwarded sandbox service, or internal file_list dispatch
```

Find complexity that does not earn its place:

- Extra traits with one implementation.
- Factories/builders where plain structs or functions would do.
- Repeated route parsing or ad hoc path slicing.
- HTTP code mixed into RPC code.
- Docker port-publishing logic duplicated for RPC and HTTP instead of sharing a
  small helper.
- Manager model changes that leak Docker details.
- Runtime operation changes that are only needed because HTTP was placed at the
  wrong layer.
- Observability hooks that require callers to remember too much ceremony.

Required questions:

- Is the smallest correct abstraction a `ForwardRoute` plus `ForwardTarget`, or
  did the implementation add more layers?
- Can shared and isolated forwarding share one flow with only target resolution
  differing?
- Does `/health` avoid depending on runtime state?
- Is the router an exact allowlist rather than a generic `/files/*` or
  `/observability/*` dispatcher?
- Does `/files/list` accept only bounded JSON objects, construct request id and
  scope internally, and reject other methods with `405`?
- Is daemon HTTP export code and token/path vocabulary absent after the caller
  audit, while manager export still pages authenticated `read_export_chunk`?
- Are errors mapped once, consistently, and close to the HTTP boundary?
- Does the implementation avoid unrelated concerns such as TLS, HTML rewriting,
  gateway routing, or isolated loopback setns relays?

### L2: File and Folder Structure

Review whether the daemon layout is canonical and easy to navigate.

Expected shape:

```text
crates/sandbox-daemon/src/
  rpc/
    mod.rs
    server.rs
    connection.rs
    dispatch.rs
    error.rs
    lifecycle.rs
    runtime.rs

  http/
    mod.rs
    server.rs
    router.rs
    response.rs
    health.rs
    api.rs

    forward/
      mod.rs
      route.rs
      proxy.rs
```

Disallowed operation-route modules should not exist:

```text
http/metrics/
http/observability/
http/export.rs
```

Findings to look for:

- `server/` still means RPC while `http/` sits beside it, causing asymmetric
  naming.
- Route folders contain only one tiny file and should be a file instead.
- Cross-cutting HTTP helpers live inside `forward/` and are reused by `health/`.
- Forward-specific route parsing leaks into `router.rs`.
- Generic file-operation or observability dispatch survives in `api.rs`.
- An export module, route prefix, stream token, or spool claim remains reachable
  from daemon HTTP.
- Response helpers duplicate status/header formatting across handlers.
- Docker provider files get broader instead of factoring one small
  multi-port-publish helper.
- Tests are in the wrong suite or require hidden local state.

Required questions:

- Can a new contributor find the HTTP router in under one minute?
- Can a new contributor find the RPC dispatch path without knowing old history?
- Does each folder own one capability, or is it just mirroring URL strings?
- Are test helpers local to `daemon_http` unless genuinely reused elsewhere?

### L3: Naming Convention

Review names as a user and maintainer, not as the author.

Expected public names:

```text
daemon
daemon_http
daemon_port
daemon_http_port
/health
/forward/shared/<port>/...
/forward/isolated=<workspace_id>/<port>/...
/files/list
```

Expected implementation vocabulary:

```text
rpc
http
health
forward
ForwardRoute
ForwardTarget
Shared
Isolated
workspace_id
path_and_query
```

Flag:

- Any use of `preview` for this feature.
- Any use of `proxy` in public API names where `forward` is the route word.
- Mixed names such as `daemon_http_proxy_port`, `preview_port`,
  `forward_http_port`, or `http_daemon_port`.
- Ambiguous `server` names after `rpc/` and `http/` both exist.
- Route parser names that encode implementation detail instead of URL contract.
- `workspace` vs `workspace_id` vs `workspace_session_id` inconsistency.
- `isolated=<id>` parsing code that calls the id a sandbox id.
- Span names that do not align with `daemon_http.forward`.

Required questions:

- Does every public name say what a CLI/API user sees?
- Does every internal name say what the code owns?
- Is `forward` used for URL/API behavior and `proxy` reserved, if used at all,
  for internal mechanics?
- Are config, JSON response, docs, tests, and code using the same words?

## Required E2E Review

Inspect the real E2E tests. They must prove:

```text
/health returns 200 JSON with status=ok and service=daemon_http
/forward/shared/<assigned_port>/... works from the host
/forward/isolated=<workspace_id>/<assigned_port>/... works from the host
invalid forward routes return the specified HTTP errors
POST /files/list covers root, published-snapshot, and live-session listings
file-list bad method/body/size cases return the specified transport errors
/files/read, /files/write, /files/edit, /files/blame,
  /observability/snapshot, and /export/x return 404
```

The shared and isolated tests must bind port `0` and use the assigned port. A
test that hardcodes `3000` is a finding.

## Finding Schema

Every finding must use this shape:

```json
{
  "lens": "L1|L2|L3",
  "severity": "blocker|major|minor|nit",
  "title": "one-line finding",
  "evidence": [
    {
      "file": "path",
      "line": 123,
      "quote": "short quote"
    }
  ],
  "spec_ref": "docs/daemon-http/README.md section",
  "claim": "what is wrong",
  "why_it_matters": "cost to design, structure, or naming",
  "recommended_change": "smallest concrete edit"
}
```

No evidence, no finding.

## Final Output

Return:

1. Findings first, sorted by severity.
2. A short "No Findings" statement for any lens with no issues.
3. A compact summary of the smallest design or structure change that would make
   the implementation cleaner.
4. A list of E2E gaps, or `No E2E gaps found`.

Do not include praise, general commentary, or broad rewrite proposals. If a
change is larger than the problem it fixes, call that out and recommend doing
less.

Start with:

```text
verdict: ship-as-is | ship-with-changes | needs-rework - biggest reason
```

Then list confirmed findings ordered by severity.

End with:

```text
design verdict: ...
structure verdict: ...
naming verdict: ...
e2e verdict: ...
```

Keep the report focused. Do not include praise. Do not include speculative
future work unless the current implementation blocks it.
