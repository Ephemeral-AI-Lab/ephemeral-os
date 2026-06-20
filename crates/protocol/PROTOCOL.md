# Sandbox Protocol — framing, wire messages, auth, errors, canonicalization

This file plus `fixtures/` is the shared wire artifact between the host side
(`gateway`, `host`) and the box side (`eosd` / `daemon`). No compiled code
crosses that boundary; both sides prove conformance with the protocol fixture
tests.

The deep frozen contract (every field of every op, with source citations)
remains `docs/contract/01-wire-protocol.md`. This file is the distilled
normative subset both sides build against.

---

## 1. Client hop (caller → `gateway`)

- **Transport:** Unix domain socket (path from `--listen`). Access control is
  filesystem permissions; there is no auth field on this hop.
- **Framing:** one UTF-8 compact-JSON object terminated by `\n` per
  connection; the response is one JSON line, then the server half-closes.
- **Request:**

```json
{"op":"exec_command","sandbox_id":"sb-...","request_id":"<uuid4hex>","args":{"workspace_session_id":"ws-...","cmd":"pwd"}}
```

| Field | Required | Notes |
|---|---|---|
| `op` | yes | canonical host or daemon operation name |
| `sandbox_id` | for daemon-bound ops; for host ops only when targeting an existing managed sandbox record | absent on host fleet-list/profile ops |
| `request_id` | yes | uuid4 hex; canonical request identity; echoed back as `meta.request_id` |
| `args` | yes (may be `{}`) | op-specific |

Clients send top-level `request_id` on every request and read the same identity
back from response `meta.request_id`.

- **Response:** for forwarded ops, the daemon's operation envelope verbatim;
  for host ops, a host-built operation envelope with the same
  `status`/`result`/`error`/`meta` shape.
- **Routing:** the gateway applies its local route table. Host operations are
  handled by the host engine; daemon command/session operations are forwarded
  to the sandbox daemon; known operator-only or internal daemon operations are
  forbidden from the client socket; unknown operations return `unknown_op`.
- **API-level error kinds** (same response shape as §4, in addition to daemon
  kinds passed through):

| kind | Raised when |
|---|---|
| `forbidden` | op exists but `visibility != public` on this socket |
| `unknown_op` | op is not recognized by the gateway route table or daemon dispatcher |
| `unknown_sandbox` | `sandbox_id` not in registry |
| `sandbox_unavailable` | recovery exhausted (connect/respawn failed) |
| `uncertain_outcome` | mutating op sent, daemon outcome unknowable after a failure; never retried |

## 2. Box hop (`host` → daemon)

- **Transport:** loopback TCP to the docker-published port; one request per
  connection; compact JSON + `\n`; response read to EOF. Inside the container
  the daemon also serves the identical request protocol on an AF_UNIX socket
  (`/eos/runtime/daemon/runtime.sock`) with **no** auth.
- **Auth:** `_eos_daemon_auth_token` is stamped as a **top-level** request
  field by the host on the TCP hop and popped by the daemon before dispatch.
  Mismatch → `unauthorized`.
- **Protocol version:** `_eos_daemon_protocol_version` (currently `1`) is
  carried **inside `args`** and is required on the daemon side. Missing,
  non-integer, or unsupported versions return `invalid_request` before op
  dispatch.
- **`sandbox_id` is stripped** by the host before forwarding; the daemon
  request is byte-compatible with the frozen fixtures in `fixtures/wire_messages/`.
- **Fallback transport:** `docker exec <container> eosd daemon --client
  <socket> <payload>` — the daemon binary as its own thin client over its
  AF_UNIX socket. Thin-client exit codes: `97` connect failed, `98` I/O failed.

## 3. Limits and retry timing

| Constant | Value |
|---|---|
| `MAX_REQUEST_BYTES` | 16 MiB (16777216) per request frame, both hops |
| request read timeout | 30 s |
| connect-retry backoff | 0.25 / 0.5 / 1.0 / 2.0 s, then one final attempt |

## 4. Response Envelope, Status Nesting, and Errors

Every response is an `OperationEnvelope` with **two status layers**. A client
must branch on them in order:

1. **Envelope `status`** — the *transport* outcome:
   `ok | running | rejected | cancelled | timed_out | error`. `ok`/`running`/
   `cancelled`/`timed_out` carry `result`; `rejected`/`error` carry `error`;
   `rejected` may also keep a partial `result`.
2. **`result.status`** — the *domain* outcome, present for command ops:
  - Command ops: `running | completed | failed`.

Foot-gun: a running command and even `command_not_found` come back as
envelope `status: "ok"` (the *transport* succeeded) with the real outcome nested
at `result.status`. Branch the envelope `status` first, then `result.status`.

```jsonc
// Command still running — envelope running, domain running.
{"status":"running","result":{"status":"running","command_session_id":"cmd-7f3a","output":{"stdout":""}},"meta":{"envelope_version":2,"op":"exec_command","…":"…"}}
// Completed command with a non-zero exit code — envelope ok, domain failed.
{"status":"ok","result":{"status":"failed","exit_code":127,"output":{"stdout":"bash: nosuchcmd: command not found"}},"meta":{"envelope_version":2,"op":"exec_command","…":"…"}}
```

Error envelope (both hops):

```json
{"status":"error","error":{"kind":"…","message":"…","details":{}},"meta":{"envelope_version":2,"op":"…","request_id":"…","duration_ms":0.0,"resource_summary":{"fields":{}},"warnings":[]}}
```

Daemon error kinds: `invalid_request`, `bad_json`, `request_too_large`,
`unauthorized`, `unknown_op`, `internal_error`, `forbidden`,
`lifecycle_in_progress`. `internal_error`
details always carry a generated `error_id`. On the client hop, error
responses built by `gateway` use the same shape with the §1 kinds.

## 5. Canonicalization (response comparison bar)

Requests are **byte-identity**: decode → encode must reproduce the fixture
bytes exactly (compact separators, key order preserved, one trailing `\n`).

Operation responses, including error envelopes, are **canonical-equal**: sort
object keys recursively before comparison. Everything else must match the
fixtures in `fixtures/wire_messages/` exactly.

## 6. CAS byte-identity

The two frozen content hashes (`manifest_root_hash`, `layer_digest`) are
governed by `docs/contract/02-cas-byte-identity.md` and pinned by the 18
golden cases in `crates/daemon/layerstack/tests/fixtures/cas/cases.json`.
Fixtures are immutable ground truth — never regenerate them to match code.

## 7. Operation Names

Canonical names use `host.*` for host/fleet operations and `sandbox.*` for
daemon operations. Legacy host-served `sandbox.*` aliases and legacy `api.*`
aliases are retired.
