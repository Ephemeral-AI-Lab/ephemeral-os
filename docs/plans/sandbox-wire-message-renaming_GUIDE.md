# Sandbox Wire Message Renaming Guide

## Scope

This guide covers the sandbox protocol naming cleanup for the Rust crates under
`sandbox/crates`. It is a vocabulary guide only; it does not change the wire
JSON contract by itself.

The current code uses "envelope" for multiple concepts:

- the decoded wire-message enum,
- host-to-daemon request JSON,
- daemon error responses,
- request or argument validation failures.

The target vocabulary should make those concepts explicit without introducing
"frame" or daemon-owned names for protocol-generic types.

## Naming Principles

- Use `WireMessage` for the transport-level enum that can decode request,
  error-response, and success-response shapes.
- Use `Request` for host-to-sandbox request payloads when the module context is
  already protocol-specific. Use a narrower prefix only when needed to avoid a
  local collision.
- Use `Response` for daemon/gateway replies. Error replies are responses, not a
  separate "envelope" concept.
- Avoid `Frame` in new names.
- Avoid `DaemonMessage` unless the type specifically models daemon ownership
  rather than the wire protocol.
- Keep public serialized error kinds stable unless a contract migration is
  planned with fixture and E2E updates.

## Rename Map

| Current name | Preferred name | Notes |
| --- | --- | --- |
| `Envelope` | `WireMessage` | The umbrella enum for decode/encode. |
| `Request` | `Request` | Keep if it is already scoped by module path. |
| `ErrorEnvelope` | `ErrorResponse` | It is a response with `success: false`. |
| `error_envelope` | `error_response` | Builder for structured error responses. |
| `raw_envelope_bytes` | `encode_request` | Encodes the request exactly as provided. |
| `stamped_envelope_bytes` | `encode_request_with_metadata` | Adds protocol and invocation metadata before encoding. |
| `InvalidEnvelope` | `InvalidRequest` | Rust-facing error for malformed request shape or invalid request arguments. |
| `invalid_envelope` | keep serialized for compatibility, or migrate to `invalid_request` deliberately | This is wire-visible and fixture-visible. |

## Semantic Boundaries

### Wire Message

`WireMessage` should be the only broad protocol wrapper name. It describes the
set of top-level JSON message shapes accepted by the wire codec:

- `Request`
- `ErrorResponse`
- arbitrary success `Response` JSON

The type is about decoding and encoding the protocol. It should not leak into
operation handlers unless the handler genuinely needs to reason about multiple
message variants.

### Request

`Request` means the host-to-sandbox operation object:

```json
{"op":"...", "invocation_id":"...", "args":{}}
```

Request helpers should use request verbs, for example `encode_request` and
`encode_request_with_metadata`.

### Response

`Response` means any sandbox-to-host reply. `ErrorResponse` is the structured
failure response:

```json
{"success":false, "warnings":[], "timings":{}, "error":{"kind":"...", "message":"...", "details":{}}}
```

The builder should be named `error_response`, not `error_envelope`.

### Invalid Request

Rust-facing validation errors should use `InvalidRequest` when the problem is a
missing field, wrong field type, invalid `args` shape, or operation argument
validation failure.

If the JSON cannot be parsed at all, keep using a parse/JSON error such as
`BadJson`.

If the top-level decoded value is not an object or cannot be classified as a
request/response shape, `InvalidRequest` is still clearer than
`InvalidEnvelope` for Rust code.

## Compatibility Policy

The wire string `invalid_envelope` appears in tests and client-visible error
payloads. A safe first pass can rename Rust items while preserving serialized
output:

```rust
#[serde(rename_all = "snake_case")]
pub enum ErrorKind {
    #[serde(rename = "invalid_envelope")]
    InvalidRequest,
}
```

Only rename the serialized kind to `invalid_request` in a deliberate contract
migration that updates fixtures, E2E tests, gateway behavior, host/client
expectations, and documentation together.

## Suggested Migration Order

1. Rename Rust-only helpers and comments:
   `error_envelope` -> `error_response`,
   `raw_envelope_bytes` -> `encode_request`,
   `stamped_envelope_bytes` -> `encode_request_with_metadata`.
2. Rename protocol DTOs:
   `Envelope` -> `WireMessage`,
   `ErrorEnvelope` -> `ErrorResponse`.
3. Rename Rust-facing validation variants:
   `InvalidEnvelope` -> `InvalidRequest` while preserving the serialized
   `invalid_envelope` string.
4. Update tests and generated docs that refer to Rust names.
5. Run focused protocol checks before broader sandbox gates.

## Verification Checklist

- `rg -n "Envelope|envelope|InvalidEnvelope|error_envelope|raw_envelope_bytes|stamped_envelope_bytes" sandbox/crates`
- `cargo test -p eos-daemon --test contract`
- `cargo test -p eos-daemon --test phase2_read_paths`
- `cargo test -p eos-daemon --test phase3_write_paths`
- `cargo test -p eos-sandbox-host --test contract`
- `cargo test -p eos-sandbox-gateway`
