# sandbox-console

The web console's HTTP server: one same-origin surface the browser talks to
for the operations plane, app preview, daemon health, and the SPA assets.
It is a **client peer** of the three `sandbox-cli` binaries, built on
`sandbox_cli::core::GatewayClient`.

Boundary law: never define operation vocabulary, never contact the daemon
RPC endpoint directly, never expose the gateway auth token to the browser.

## Routes

```text
POST /api/rpc                                  one-shot operation dispatch
POST /api/rpc   (Accept: text/event-stream)    same, streaming _stream_logs as SSE
GET  /api/catalog                              management+runtime+observability catalogs
GET  /api/sandboxes/<id>/health                daemon_http /health probe
POST /api/sandboxes/<id>/files/list            daemon_http directory listing
ANY  /s/<id>/shared/<port>/...                 preview proxy (prefix swap to /forward)
ANY  /s/<id>/isolated=<ws-id>/<port>/...       preview proxy, isolated workspace
GET  /*                                        SPA assets + client-route fallback
```

Protocol errors return in the body with HTTP 200; transport failures map to
400 (bad body) / 502 (gateway or daemon unreachable) / 504 (timeout).
Preview requests stream bodies, tunnel WebSocket/HTTP upgrades, append
`X-Forwarded-For`, and resolve `daemon_http` endpoints through a 3s cache.

## Running

One-command bootstrap (gateway → SPA build → console, in token order):

```sh
bin/start-sandbox-console-stack             # then open http://127.0.0.1:7880
bin/start-sandbox-console-stack --skip-gateway   # keep the running gateway
```

Or piece by piece:

```sh
cargo run -p xtask -- package-console       # build SPA into dist/console
bin/start-sandbox-console                   # reads /tmp/eos-gateway.token
# or explicitly:
cargo run -p sandbox-console -- \
  --bind 127.0.0.1:7880 \
  --gateway-socket 127.0.0.1:7878 \
  --gateway-auth-token TOKEN \
  --assets dist/console
```

Config discovery: flags > `SANDBOX_GATEWAY_SOCKET` / `SANDBOX_GATEWAY_AUTH_TOKEN`
/ `SANDBOX_CONSOLE_BIND` / `SANDBOX_CONSOLE_ASSETS` env > defaults
(`127.0.0.1:7880`, gateway `127.0.0.1:7878`, assets from `dist/console` or
`web/console/dist`). Bind stays loopback in v0 — browser auth is out of
scope, matching the gateway and `daemon_http` posture.

SPA development: `cd web/console && npm run dev` proxies `/api` and `/s`
to the running console.
