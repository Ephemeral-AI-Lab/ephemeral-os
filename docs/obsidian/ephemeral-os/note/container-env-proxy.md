---
title: Sandbox container_env proxy passthrough
tags:
  - ephemeral-os
  - sandbox
  - networking
  - proxy
status: draft
---

# Sandbox `container_env` proxy passthrough

How a sandbox running on a proxied Docker Desktop host gets working egress
(e.g. `npm install`) via [[docs/ephemeral-os|EphemeralOS]] config, without a
per-workspace `.npmrc`.

## Symptom

`exec_command` running `npm install …` inside a sandbox hangs ~80s then fails:

```
npm error code ENOTFOUND
npm error network request to https://registry.npmjs.org/... failed,
  reason: getaddrinfo ENOTFOUND registry.npmjs.org
```

A plain `docker run node:24-bookworm-slim npm install …` on the same host
**succeeds**.

## Root cause

On this host, Docker Desktop sits behind a local proxy. Two things follow:

1. **Direct DNS/egress from containers is broken** — only Docker Desktop's HTTP
   proxy at `http.docker.internal:3128` (→ `192.168.65.1`) actually reaches the
   internet. Raw TCP to `8.8.8.8:53` connects, but UDP/getaddrinfo DNS fails
   (`EAI_AGAIN` / `ENOTFOUND`).
2. **The proxy is injected by the Docker CLI, not the Engine.** `docker run`
   reads `~/.docker/config.json` → `proxies.default` and injects
   `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` into the container. EphemeralOS creates
   containers via the **Engine API (bollard)**, which does *no* such injection,
   so the sandbox container had no proxy env.

There is also a **second gate**: even with the proxy on the container, the
namespace runner rebuilds an **allowlisted** environment for exec'd commands
(`command_environment` / `HOST_KEYS` in
`sandbox-runtime/namespace-process/.../shell_exec/request.rs`). Anything not in
`HOST_KEYS` is stripped — so an injected `HTTP_PROXY` would never reach `npm`.

```text
container_env config ──▶ Engine API Config.Env ──▶ daemon process env
                                                        │  HOST_KEYS allowlist
                                                        ▼
                                              exec'd command (npm) env
```

Both layers are required; either one alone leaves npm broken.

## Fix (config-driven, no per-workspace files)

`manager.docker.container_env` — a `name → value` map injected into every
sandbox container by the Docker provider, plus `HOST_KEYS` forwarding the proxy
family into exec'd commands.

`config/prd.yml`:

```yaml
manager:
  docker:
    container_env:
      HTTP_PROXY: http://http.docker.internal:3128
      HTTPS_PROXY: http://http.docker.internal:3128
      NO_PROXY: localhost,127.0.0.1,::1
```

Touch points:

- `sandbox-config` `DockerRuntimeConfig.container_env: BTreeMap<String,String>`
  (validated: non-empty names, no `=` in names).
- `sandbox-provider-docker` renders the map to the Engine's `NAME=VALUE` list.
- `namespace-process` `HOST_KEYS` forwards `HTTP_PROXY/HTTPS_PROXY/NO_PROXY/`
  `ALL_PROXY` (+ lowercase) from the daemon env into commands.

Commit: `2d1623bd8`.

## Verification

Fresh `node:24-bookworm-slim` sandbox, **no `.npmrc` anywhere**:

```
npm install is-number@7.0.0 --ignore-scripts --no-audit --no-fund
→ status ok, exit 0, "added 1 package in 3s"

npm config get proxy        → null   # proxy comes from env, not a file
npm config get https-proxy  → null
HTTP_PROXY                  → http://http.docker.internal:3128
```

## Caveats

- `prd.yml` sets only **uppercase** vars. `npm` honors these, but lowercase-only
  tools (`curl`, `git`, `wget`) won't pick up the proxy — add
  `http_proxy`/`https_proxy`/`no_proxy` keys to the map if needed (`HOST_KEYS`
  already forwards them).
- This is a **host-specific** need. On a host without a local proxy, direct
  egress works and `container_env` can stay empty.
- `container_env` is general-purpose env injection; proxy is just the first use.

## See also

- [[docs/ephemeral-os|EphemeralOS overview]]
- [[docs/cli-gateway-manager-runtime|CLI / gateway / manager / runtime]]
- [[docs/comparision/anthropic-managed-container|Anthropic managed container]]
