# Cross-repo contract: protocol versions, envelope shape, fixture pin

The sandbox system pins several version surfaces that must move deliberately.
A careless bump silently breaks the thin-client handshake, the response
envelope, or the on-disk manifest read path. The binding host<->box artifacts
are `crates/protocol/PROTOCOL.md` and the owner-local fixtures under
`crates/protocol/fixtures/` and
`crates/daemon/layerstack/tests/fixtures/`; no daemon implementation code
crosses into host/gateway.

## Version surfaces at a glance

| Surface | Constant / field | Value | Governs |
|---|---|---|---|
| Wire protocol | `DAEMON_PROTOCOL_VERSION` | `1` | The request framing handshake |
| Envelope metadata | `meta.envelope_version` | `2` | The shape of the response envelope `meta` block |
| On-disk manifest | `MANIFEST_SCHEMA_VERSION` | `1` | The persisted layer-stack manifest schema |

These move independently: the response envelope can gain fields (bumping
`envelope_version`) without touching the wire handshake, and vice versa.

## 1. Wire Protocol Version

- `DAEMON_PROTOCOL_VERSION = 1`
- Carried as the `_eos_daemon_protocol_version` field **inside `args`** on every
  request. The daemon requires it before request dispatch and rejects missing,
  non-integer, or unsupported versions with `invalid_request`.
- Pinned in the daemon wire module and the host-side daemon wire encoder, with
  fixture conformance tests covering request framing and host stamping.

## 2. Envelope metadata version

- `meta.envelope_version = 2`
- Stamped into every response envelope's `meta` block by the shared
  `protocol::ResponseMeta` contract and the gateway's request metadata helper.
- It is **independent** of the wire version above. It is `2` because the
  envelope `meta` shape is the second iteration of the response contract; the
  wire framing it rides on is still version `1`.
- The field is named `envelope_version` precisely so it cannot be confused with
  the wire `protocol_version`. (It was renamed from `protocol_version`; see the
  bump procedure's exception note.)

## 3. Envelope-nesting rule (transport status vs. domain status)

Every daemon and gateway response carries two status layers where a command
domain status is present, and a client must branch on them in order:

1. **Envelope `status`** — the *transport* outcome of delivering the op:
   `ok | running | rejected | cancelled | timed_out | error`. `ok`/`running`/
   `cancelled`/`timed_out` carry `result`; `rejected`/`error` carry `error` (an
   `OperationFault`); `rejected` may *also* keep partial domain `result`.
2. **`result.status`** — the *domain* outcome, present for command ops:
   `running | ok | cancelled | error | timed_out`.

The foot-gun: a running command and even a `command_not_found` come back
as envelope `status: "ok"` — the *transport* succeeded — while the real outcome
is nested at `result.status`. A naive client that reads only the envelope
`status` mis-parses every command. Always branch envelope `status` first, then
`result.status` for command ops.

```jsonc
// Command still running: envelope ok, domain running.
{ "status": "ok",
  "result": { "status": "running", "command_id": "cmd-7f3a", "output": "" },
  "meta": { "envelope_version": 2, /* ... */ } }

// command_not_found: transport still succeeded (envelope ok),
// the failure is the domain status + exit code.
{ "status": "ok",
  "result": { "status": "error", "exit_code": 127,
              "output": "bash: nosuchcmd: command not found" },
  "meta": { "envelope_version": 2, /* ... */ } }

```

## 4. On-disk manifest schema version

- `MANIFEST_SCHEMA_VERSION = 1`
- Stamped into the persisted layer-stack manifest. The CAS `manifest_root_hash`
  hashes **only** the `layers` array, never `version`/`schema_version`, so the
  schema version can change without invalidating existing layer hashes — but a
  reader that does not understand a new schema version must refuse to load it.
- Source of truth: `crates/daemon/layerstack/src/model.rs` (`MANIFEST_SCHEMA_VERSION`).

## 5. Bump procedure

When any version must change:

1. Bump the constant in its owning location(s) above and update this file in
   the same change.
2. The golden fixtures are **immutable ground truth** captured from the
   original Python runtime, which has been removed — they can no longer be
   regenerated. Never edit a fixture to match code. Wire fixtures live under
   `crates/protocol/fixtures/wire_messages/`; CAS fixtures live under
   `crates/daemon/layerstack/tests/fixtures/cas/`. Two deliberate exceptions,
   each a contract change made on purpose and recorded here:
   - **2026-06 — legacy `api.*` aliases retired.** The `op` field of the three
     request fixtures was rewritten to the canonical `sandbox.*` spellings.
   - **Envelope `protocol_version` → `envelope_version` rename.** The response
     envelope `meta` field was renamed to disambiguate it from the wire
     `protocol_version` (§1 vs §2). The four response fixtures that carry
     `meta.envelope_version` (`heartbeat_response`, `readiness_response`,
     `error_unknown_op`, `error_request_too_large`) had
     that single key renamed; the value (`2`) and every other byte are
     unchanged.
   - **2026-06 — diagnostic metadata removed.** The response fixtures dropped
     the removed diagnostic fields from `meta` when the project removed the
     storage and reporting layer that produced them.

   Every other byte — args, response bodies, timing keys — remains the original
   capture.

Until such a change, the wire and manifest versions are pinned at `1`
and the envelope version at `2`.
