# Sandbox Protocol — framing, wire messages, auth, errors, canonicalization

This file plus `../crates/operation/ops.json` and `fixtures/` is the
**complete shared artifact** between the host side (`gateway`,
`host`) and the box side (`eosd` / `daemon`). No compiled code
crosses that boundary; both sides prove conformance with
`cargo xtask check-contract`.

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
{"op":"sandbox.file.read","sandbox_id":"sb-…","invocation_id":"<uuid4hex>","args":{}}
```

| Field | Required | Notes |
|---|---|---|
| `op` | yes | canonical name from `crates/operation/ops.json` |
| `sandbox_id` | for daemon-bound ops | absent on `sandbox.acquire` / `sandbox.list` |
| `invocation_id` | yes | uuid4 hex; canonical request identity; echoed back as `meta.request_id` / `meta.trace.request_id` |
| `args` | yes (may be `{}`) | op-specific |

Top-level `request_id` is not a request field; clients send `invocation_id` and
read `request_id` only from response metadata or trace/audit APIs.

- **Response:** for forwarded ops, the daemon's operation envelope verbatim;
  for host ops, a host-built operation envelope with the same
  `status`/`result`/`error`/`meta` shape.
- **Routing is pure catalog lookup** (`crates/operation/ops.json`):
  `visibility != public` → `forbidden`; `served_by == "host"` → host engine; `served_by == "daemon"`
  (and dynamic `plugin.*`) → forward to the sandbox daemon; unknown op →
  `unknown_op`. `gateway` never branches on specific op names.
- **API-level error kinds** (same response shape as §4, in addition to daemon
  kinds passed through):

| kind | Raised when |
|---|---|
| `forbidden` | op exists but `visibility != public` on this socket |
| `unknown_op` | op not in catalog |
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
2. **`result.status`** — the *domain* outcome, present only for command and file
   ops:
   - Command ops: `running | ok | cancelled | error | timed_out`.
   - Mutation ops: `accepted | committed | rejected | aborted_version |
     aborted_overlap | dropped | failed`.

Foot-gun: a backgrounded command and even `command_not_found` come back as
envelope `status: "ok"` (the *transport* succeeded) with the real outcome nested
at `result.status`. Branch the envelope `status` first, then `result.status`.

```jsonc
// Backgrounded command still running — envelope ok, domain running.
{"status":"ok","result":{"status":"running","command_id":"cmd-7f3a","output":""},"meta":{"envelope_version":2,"op":"sandbox.command.exec","…":"…"}}
// command_not_found — transport ok, domain error + exit_code 127.
{"status":"ok","result":{"status":"error","exit_code":127,"output":"bash: nosuchcmd: command not found"},"meta":{"envelope_version":2,"op":"sandbox.command.exec","…":"…"}}
```

Error envelope (both hops):

```json
{"status":"error","error":{"kind":"…","message":"…","details":{}},"meta":{"envelope_version":2,"op":"…","request_id":"…","trace":{"trace_id":"…","store":"pending_host_ingest","event_count":0,"degraded":false},"workspace_route":{"kind":"none"},"duration_ms":0.0,"modules_touched":[],"steps":[],"resource_summary":{"fields":{}},"warnings":[]}}
```

Daemon error kinds: `invalid_request`, `bad_json`, `request_too_large`,
`unauthorized`, `unknown_op`, `internal_error`, `forbidden`,
`forbidden_in_isolated_workspace`, `lifecycle_in_progress`. `internal_error`
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
golden cases in `fixtures/cas/cases.json`. Fixtures are immutable ground
truth — never regenerate them to match code.

## 7. Catalog pins

- `../crates/operation/ops.json` is generated by `eosd dump-ops`, checked
  in, and reviewed like code. `cargo xtask check-contract` fails on drift.
- Each op has exactly one wire spelling: its canonical `sandbox.*` name. The
  legacy `api.*` aliases were retired (2026-06); the three request fixtures
  were rewritten to canonical spellings in the same change.
- Canonical names are unique across the catalog.
