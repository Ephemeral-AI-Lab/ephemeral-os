# Daemon HTTP

Each ready sandbox has a `daemon_http` endpoint in addition to its
authenticated daemon RPC endpoint. The HTTP endpoint supports health checks,
bounded file listing, and forwarding HTTP or WebSocket traffic to an
application running inside the sandbox.

The Docker provider publishes this endpoint on a random host port bound to
`127.0.0.1`. The daemon HTTP surface has no application authentication, so do
not publish or reverse-proxy that port to another machine without adding a
separate authentication boundary.

## Routes

| Request | Purpose |
|---|---|
| `GET /health` | Check that the daemon HTTP listener is alive. |
| `POST /files/list` | List files with optional `path`, `workspace_session_id`, and `limit` JSON fields. |
| `ANY /forward/shared/<port>/...` | Forward to `127.0.0.1:<port>` in the sandbox's shared network namespace. |
| `ANY /forward/isolated=<workspace_session_id>/<port>/...` | Forward to `<port>` on a live isolated workspace session. |

Everything else returns `404`. File reads, writes, edits, blame,
observability, and export remain on the authenticated CLI, MCP, or RPC
surfaces.

## Access a web server from the host

This example starts Python's static file server in a shared sandbox session and
opens it through the daemon HTTP endpoint. It assumes the gateway is running,
the sandbox image contains `python3`, and `jq` is installed on the host.

First choose a ready sandbox and discover its current host-loopback endpoint:

```sh
export SANDBOX_ID=eos-abc

DAEMON_HTTP=$(
  sandbox-manager-cli inspect_sandbox --sandbox-id "$SANDBOX_ID" |
    jq -er '.daemon_http | select(. != null) | "http://\(.host):\(.port)"'
)

curl --fail --show-error "$DAEMON_HTTP/health"
```

Start a server on port `8000` inside the sandbox. Omitting
`--workspace-session-id` creates an automatic session with the shared network
profile, so the shared forwarding route is the correct one:

```sh
SERVER=$(
  sandbox-runtime-cli --sandbox-id "$SANDBOX_ID" \
    exec_command --yield-time-ms 1000 \
    "python3 -m http.server 8000 --bind 127.0.0.1 --directory ."
)

printf '%s\n' "$SERVER"
COMMAND_SESSION_ID=$(
  printf '%s\n' "$SERVER" |
    jq -er '.command_session_id // error("server exited during the initial wait")'
)
```

From the host, use the published daemon HTTP port and the forwarding prefix:

```sh
APP_URL="$DAEMON_HTTP/forward/shared/8000/"

curl --fail --show-error "$APP_URL"
printf 'Open in a browser: %s\n' "$APP_URL"
```

The address is not `http://127.0.0.1:8000/`: that would target port `8000` on
the host. The request path is:

```text
host 127.0.0.1:<daemon-http-port>
  -> sandbox daemon /forward/shared/8000/
  -> sandbox 127.0.0.1:8000 /
```

Stop the server by sending Ctrl-C to its command session (the `$'...'` quoting
works in Bash and zsh):

```sh
sandbox-runtime-cli --sandbox-id "$SANDBOX_ID" \
  write_command_stdin --command-session-id "$COMMAND_SESSION_ID" \
  --yield-time-ms 1000 $'\003'
```

For a development server, replace the Python command with the server's normal
launch command and port. A shared server may bind to `127.0.0.1` or
`0.0.0.0`; binding to `127.0.0.1` keeps it reachable only through the sandbox
network namespace. A server in an isolated workspace must bind to `0.0.0.0`
so the daemon can reach it through the session's veth IP, then use:

```text
<daemon-http>/forward/isolated=<workspace_session_id>/<port>/
```

## Forwarding behavior

The forwarding prefix is stripped before the request reaches the application.
For example:

```text
GET /forward/shared/8000/assets/app.js?v=1
 -> GET /assets/app.js?v=1 on sandbox 127.0.0.1:8000
```

The daemon preserves the method, query, request body, application status, and
streaming response. It also supports HTTP upgrade tunnels such as WebSockets.
The application receives `X-Forwarded-Host`, `X-Forwarded-Proto`, and
`X-Forwarded-Prefix`.

Applications must work below a path prefix. Prefer relative asset URLs, or
configure the framework's base path from `X-Forwarded-Prefix`. The daemon does
not rewrite application-generated absolute URLs or `Location` headers.

## File-list example

`/files/list` accepts a JSON object containing only operation arguments; it
does not accept a caller-supplied operation name, request ID, or sandbox scope.

```sh
curl --fail --show-error \
  -H 'Content-Type: application/json' \
  --data '{"path":".","limit":100}' \
  "$DAEMON_HTTP/files/list"
```

Malformed HTTP input uses a non-2xx status. Once dispatch occurs, an operation
failure is returned as an error envelope with HTTP `200`, so callers must also
check the JSON body for an `error` field.

## Security notes

- The daemon HTTP endpoint is unauthenticated. Any host-local process that
  discovers the random port can call its allowlisted routes.
- Keep the Docker host publication bound to loopback. Treat any broader bind,
  tunnel, or reverse proxy as a new authenticated exposure.
- The forwarding route derives the target host, but the caller selects the
  port. Do not run unintended admin services on reachable sandbox addresses.
- Do not cache the endpoint across sandbox recreation. Re-run `inspect_sandbox`
  whenever the sandbox lifecycle changes because the published port can change.
