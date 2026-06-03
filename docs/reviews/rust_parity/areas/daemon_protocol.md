# Rust Parity Audit — Daemon protocol & dispatch (sandbox)

Area: wire protocol, envelopes, CAS, command session, in-flight, recovery, auth.

Source precedence: Python = ground truth; docs/architecture = corroboration; the
invariant checklist's specifics are treated as fuzzy and verified against code.

NOTE on Python ground truth: the pre-cutover Python sandbox internals were
deleted from the working tree and materialized under `/tmp/oldpy/backend/src/...`.
The host client (`backend/src/sandbox/host/daemon_client.py`) is still IN-TREE
(it is the Rust-era host that targets the `eosd` binary). Where oldpy and the
in-tree host client agree, both are cited.

---

## Ground truth

Wire contract (Python):
- One newline-delimited compact-JSON envelope per connection:
  `{"op","invocation_id","args"}` + `\n`; response is one compact-JSON object +
  `\n`. `server.py` docstring lines 11-15; `daemon_client.py:118-121`
  (`json.dumps(..., separators=(",",":"))`).
- Server caps the request at `MAX_REQUEST_BYTES = 16*1024*1024`
  (`server.py:58`), read-times `readline()` at `REQUEST_READ_TIMEOUT_S = 30.0`
  (`server.py:62`), and converts oversize/timeouts to `request_too_large` / no
  response (`server.py:74-98`).
- Auth: `DAEMON_AUTH_FIELD = "_eos_daemon_auth_token"` popped on the TCP path
  before dispatch; mismatch → `unauthorized` (`server.py:52,116-120`). AF_UNIX
  path passes `auth_token=None` and never authenticates (`server.py:183-187`).
- Protocol version: `DAEMON_PROTOCOL_VERSION = 1`, field
  `_eos_daemon_protocol_version` injected into `args` by
  `with_daemon_protocol_version()` and NEVER read by the daemon
  (`daemon_client.py:48-49,190-195`).
- Dispatcher: `register_op` is no-op on same-handler re-register, raises on a
  different handler for a claimed op (`dispatcher.py:42-57`). `OP_TABLE` lookup;
  unknown → `unknown_op`; missing/empty `op` or non-dict `args` →
  `invalid_envelope` (`dispatcher.py:147-149,162-195`). A missing
  `invocation_id` is minted as a uuid4 fallback and `args.setdefault`-ed
  (`dispatcher.py:176-194`). Handlers register in the in-flight registry, run,
  serialize, attach `runtime.boot_to_dispatch_s`/`runtime.dispatch_s`, and
  deregister in `finally`; uncaught exceptions → `internal_error` with a uuid4
  `error_id` (`dispatcher.py:79-133,198-212`).
- In-flight registry (`in_flight.py`): id→task; `register`/`deregister`,
  `cancel_task`, `heartbeat`, `count_by_agent` (background + not-done),
  `reap_stale` (background, not-yet-reaped, `now - last_seen >= ttl`), TTL
  default 300s / reaper 30s, env `EOS_INFLIGHT_TTL_S` / `EOS_INFLIGHT_REAPER_INTERVAL_S`.
- Cancel handler (`builtin_operations.py:182-198`): cancels the task, then
  `await asyncio.wait_for(asyncio.shield(task), timeout=_CANCEL_CLEANUP_WAIT_S=5.0)`,
  returns `{cancelled, already_done: not cancelled, cleanup_done: task.done()}`.
- Plugin gate (`dispatcher.py:90-114,251-282`): for `api.plugin.*`/`plugin.*`,
  under `acquire_dispatch_slot(agent_id)`, `_plugin_block_decision` returns
  `forbidden_in_isolated_workspace` if the agent has an open isolated handle;
  when no isolated pipeline is bootstrapped it emits the
  `workspace_lifecycle.plugin_check_unbootstrapped` audit event.
- Host recovery state machine (`daemon_client.py:299-485`): first send; recover
  iff `CONNECT_FAILED` (op-agnostic) OR empty-response on a retry-eligible op;
  spawn; require `layer_stack_root`; readiness probe (`api.runtime.ready`) with
  connect-retry (delays `(0.25,0.5,1.0,2.0)`); `ready is True` (with a bootstrap
  fall-through for `ensure/build_workspace_base` + `WorkspaceBindingError`);
  replay the original envelope. Empty-response is retryable EXCEPT the
  fail-closed set `{api.edit_file, api.v1.edit_file, api.write_file,
  api.v1.write_file, api.v1.exec_command, api.v1.write_stdin,
  api.v1.command.write_stdin}` and any `plugin.*` (`daemon_client.py:605-622`).
- CAS hashes (corroborated by Rust PORT comments): `manifest_root_hash` over
  `json.dumps({"layers":[...]}, sort_keys=True, ensure_ascii=True,
  separators=(",",":"))`; `layer_digest` over NUL-framed
  `kind\0path\0payload\0` of last-write-wins, path-sorted changes.

Docs: `docs/architecture/sandbox/daemon.html` §6.1-6.4 corroborate the one-
envelope contract, OP_TABLE/in-flight/plugin-gate shape, the 97/98 thin-client
codes, the spawn→readiness→retry recovery, and that the Rust daemon owns native
command sessions (Python keeps a compat stub).

---

## Rust mapping

- Wire constants: `eos-protocol/src/version.rs` (single source) +
  `eos-protocol/src/lib.rs` re-exports. Host derives `DAEMON_PROTOCOL_VERSION`
  from `eos_protocol` (`daemon_client.rs:37`); compile-time lockstep assert at
  `runtime_artifact.rs:33`.
- Envelope encode/decode + error kinds: `eos-protocol/src/envelope.rs`.
- Canonical response comparison: `eos-protocol/src/canonical.rs`.
- CAS byte-identity: `eos-protocol/src/cas.rs`.
- Server (AF_UNIX + TCP, framing, auth pop, signal shutdown):
  `eos-daemon/src/server.rs`.
- Dispatcher (op table, validation, error envelope, cancel/heartbeat/count,
  runtime-timing attach, audit emit): `eos-daemon/src/dispatcher.rs`.
- In-flight registry: `eos-daemon/src/invocation_registry.rs`.
- Command sessions (native, Rust-owned): `eos-daemon/src/command.rs`.
- Plugin gate: `eos-daemon/src/plugin/mod.rs` (`ensure_plugin_family_allowed`).
- Host transport + recovery: `eos-sandbox-host/src/daemon_client.rs`.

---

## Invariant table

| # | Invariant | Status | Severity | Python file:line | Rust file:line | Note |
|---|-----------|--------|----------|------------------|----------------|------|
| 1 | Wire protocol version lockstep host↔daemon (compile-time) | match | none | daemon_client.py:48-49,190-195 | version.rs:10-14; daemon_client.rs:37; runtime_artifact.rs:33 `const _: () = assert!(...)` | Lockstep is by single-sourcing `eos_protocol::DAEMON_PROTOCOL_VERSION` (host derives it) + host-internal const assert. Daemon never validates the field (same as Python). |
| 2a | One JSON envelope/call; compact, `\n`-framed | match | none | server.py:11-15; daemon_client.py:118-121 | envelope.rs:106-143; server.rs:432-464,247-249 | serde compact + single `\n`. |
| 2b | Canonical response compare (drop timings/pid/uptime) | match | none | (Rust-only parity bar) | canonical.rs:15-51 | Drops `timings`/`daemon_pid`/`uptime_s`, sorts keys, quantizes floats 1e-9. |
| 2c | CAS for blobs (manifest_root_hash, layer_digest byte-identity) | match | none | manifest.py:134-138; changes.py:145-165; publisher.py:144-158 | cas.rs:146-216 (ascii escape), 266-300 (NUL-framed digest) | Hand-rolled `ensure_ascii` escaping incl. surrogate pairs; last-write-wins path-sorted. Directed + proptest coverage. |
| 3 | Dispatcher routes ops; in-flight registry tracks calls (dedupe of retries) | match | low | dispatcher.py:42-57,79-133; in_flight.py:54-106 | dispatcher.rs:173-243; invocation_registry.rs:121-218; server.rs:283-350 | Routing + cancel/heartbeat/count present. NO actual dedupe on EITHER side — both overwrite the HashMap entry by id (in_flight.py:66; invocation_registry.rs:133). "dedupe" is a fuzzy checklist term: the registry correlates by id for cancel/heartbeat/TTL, it does not suppress replays. This no-dedupe is what makes Disparity D1 harmful. |
| 4 | Command-session lifecycle (PTY path) preserved | divergent (intentional) | none | builtin_operations.py:119-175 (gutted stub) | command.rs:52-181, registry internals | Python is a deliberate `command_session_not_found` stub; the real contract is docs + Rust-native sessions. NOT deeply audited (command.rs is ~55KB; only op surface + registry fn names inspected). |
| 5 | Host recovery (spawn/connect/empty-response retry w/ backoff) | bug | high | daemon_client.py:299-485,605-622 | daemon_client.rs:229-381,582-592 | State machine faithful EXCEPT the empty-response fail-closed op set is wrong — see D1. |
| 6 | Auth + protocol field handled on both sides | match | none | server.py:52,116-120; daemon_client.py:735-745 | server.rs:352-373; daemon_client.rs:812-829; version.rs:14,18 | TCP-only auth pop + mismatch→`unauthorized`; auth added only with a token; protocol field injected, never validated. |

Extra invariants verified:

| Invariant | Status | Severity | Python | Rust | Note |
|-----------|--------|----------|--------|------|------|
| `MAX_REQUEST_BYTES`=16 MiB, `REQUEST_READ_TIMEOUT_S`=30.0 | match | none | server.py:58,62 | version.rs:34,38; server.rs:48,51,446 | Literal values + `constants_match_python` test. |
| Thin-client codes 97/98 | match | none | daemon_client.py:39-40 | version.rs:26,30 | CONNECT_FAILED=97, IO_FAILED=98. |
| Connect-retry delays `(0.25,0.5,1.0,2.0)` | match | none | daemon_client.py:47 | version.rs:42; daemon_client.rs:52-57 | bit-exact test. |
| register_op collision semantics | match | none | dispatcher.py:42-57 | dispatcher.rs:173-186 | same-handler no-op (via `fn_addr_eq`), different-handler rejected (builtins assert). |
| unknown_op / invalid_envelope error kinds | match | none | dispatcher.py:147-195; server.py:104-120 | dispatcher.rs:201-232; server.rs:268-280; envelope.rs:71-90 | snake_case wire kinds verified by test. |
| internal_error w/ uuid error_id on handler panic | partial | low | dispatcher.py:121-131 | dispatcher.rs:234-243; server.rs:325-337 | Rust maps handler `Err` → `internal_error` with `to_string()` but NO `error_id` uuid in details; a cancelled/joined task error also maps to `internal_error`. |
| TTL default 300s / reaper 30s / env overrides | match | none | in_flight.py:14-17 | invocation_registry.rs:30-42,100-104 | env names reproduced. |
| Plugin isolation gate (`forbidden_in_isolated_workspace`) | partial | low | dispatcher.py:90-114,251-282 | plugin/mod.rs:120-121,206-207,253-255,325-337 | Block path wired on ensure/status/registered-op. The `plugin_check_unbootstrapped` audit emit + per-agent `acquire_dispatch_slot` are NOT reproduced — see D4 and Open Q. |

---

## Disparities

### D1 (HIGH, bug) — Host empty-response fail-closed op set is wrong; stdin-write ops can be replayed

`can_retry_empty_response` decides whether an empty daemon response (= the daemon
process died/closed AFTER accepting the connection, mid-request) may be safely
replayed after respawn. Mutating/stateful ops MUST fail closed so a replay cannot
double-apply.

- Python ground truth (oldpy AND in-tree) fail-closed set:
  `{api.edit_file, api.v1.edit_file, api.write_file, api.v1.write_file,
  api.v1.exec_command, api.v1.write_stdin, api.v1.command.write_stdin}` + any
  `plugin.*`. `backend/src/sandbox/host/daemon_client.py:614-622`.
- Rust set: `{api.edit_file, api.v1.edit_file, api.write_file, api.v1.write_file,
  api.v1.exec_command, api.v1.exec_stdin}` + any `plugin.`.
  `agent-core/crates/eos-sandbox-host/src/daemon_client.rs:582-592`.

Two divergences:
1. Rust DROPPED `api.v1.write_stdin` and `api.v1.command.write_stdin` from
   fail-closed → these stdin-write ops are now treated as RETRYABLE.
2. Rust ADDED `api.v1.exec_stdin`, which is not a registered op anywhere
   (`grep exec_stdin` → no hits in op table or Python). The phantom arm is a dead
   no-op.

Why it matters: harm mechanism is amplified by the no-dedupe property (Invariant
3). The in-flight registry does NOT suppress duplicate invocation ids — both
Python (`in_flight.py:66`) and Rust (`invocation_registry.rs:133`) overwrite the
HashMap entry. So a wrongly-permitted replay of `write_stdin` after a respawn
makes the daemon execute the stdin write TWICE → duplicated/corrupted live
command-session stdin. This is precisely the silent-data-divergence class the
audit targets. Trigger: empty TCP response (daemon died after connect) during a
`write_stdin`, recovery replays it.

False confidence: the Rust test `empty_response_gating_matches_python_set`
(`daemon_client.rs:1211-1233`) pins `api.v1.exec_stdin` and asserts the set
"matches python" while NEVER testing the two real ops — it passes because it
asserts the wrong thing.

Suggested fix: replace `api.v1.exec_stdin` with both `api.v1.write_stdin` and
`api.v1.command.write_stdin` in `can_retry_empty_response`, and fix the test to
assert both stdin ops fail closed.

### D2 (LOW, divergent) — `cancel` does not perform the bounded cleanup-wait; `cleanup_done`/`already_done` semantics differ

- Python `cancel` (`builtin_operations.py:182-198`): cancels the task, then
  `await asyncio.wait_for(asyncio.shield(task), timeout=_CANCEL_CLEANUP_WAIT_S=5.0)`,
  returns `cleanup_done = task.done()` (true once the cancelled task settles).
- Rust `op_cancel` (`dispatcher.rs:300-317`): cancels (`registry.cancel`),
  returns immediately with `already_done = !cancelled` and
  `cleanup_done = !cancelled` (so `cleanup_done` is FALSE whenever a cancel
  succeeds — inverted vs Python's true), and never waits for cleanup.

Why it matters: the synchronous up-to-5s drain that Python guarantees before
returning is gone; the inverted `cleanup_done` would mislead a consumer. Severity
is LOW because there are NO consumers of `cleanup_done`/`already_done` anywhere
in `backend/src/`, `agent-core/`, or `sandbox/crates/` (grep: only the producer
in dispatcher.rs). Also note Rust cancel runs synchronously in the in-flight
context and `terminate_process_group` provides a stronger kill than the Python
task-cancel, so the cleanup intent is partly served by other means.

Suggested fix: if any consumer ever reads these, mirror Python: best-effort wait
on the aborted task with a 5s cap and report real `cleanup_done`. Otherwise leave
a note; do not over-engineer for an unconsumed field.

### D3 (LOW, partial) — `runtime.boot_to_dispatch_s` / `runtime.dispatch_s` / `runtime.read_request_s` are stubbed to 0.0

Python measures these precisely: `boot_t0` captured before `readline`
(`server.py:71`), `dispatch_entered_at` in the dispatcher, and
`runtime.read_request_s` from `read_completed_at - boot_t0`
(`server.py:123-131`; `dispatcher.py:198-212`). The Phase-3 pass bar was
`runtime.boot_to_dispatch_s ≤ 2 ms`.

Rust `attach_runtime_timings` (`dispatcher.rs:3089-3106`) only inserts `0.0`
placeholders for all three keys; the server's `dispatch_request` measures
`started.elapsed()` solely for the audit `tool_call.finished` event
(`server.rs:293,344-347`), never feeding the response `timings`.

Why it matters: this is telemetry-only. `canonical.rs` `DROP_KEYS`
(`canonical.rs:15`) drops the entire `timings` subtree before any parity
comparison, so 0.0-vs-real is invisible to the parity bar, and there is no
functional consumer in the harness. LOW and possibly intentional (shape parity).
Raise only if a production monitor reads `runtime.dispatch_s`.

### D4 (LOW, missing) — Plugin gate drops the `plugin_check_unbootstrapped` audit emit

Python `_plugin_block_decision` calls `_emit_plugin_gate_audit` →
`workspace_lifecycle.plugin_check_unbootstrapped` when there is NO bootstrapped
isolated pipeline (`dispatcher.py:259-282`). Rust `ensure_plugin_family_allowed`
(`plugin/mod.rs:325-337`) only checks `agent_has_active_handle` and emits nothing
in the unbootstrapped case. The forbidden-block path itself IS preserved.

Why it matters: a diagnostic/audit signal is silently dropped; functional gating
is unaffected. Suggested fix: emit the equivalent workspace-lifecycle audit event
in the no-active-handle branch if that audit lane is consumed.

---

## Extra findings

- TTL comparison operator differs: Python `reap_stale` uses `>=`
  (`in_flight.py:123`: `now - entry.last_seen >= self._ttl_seconds`); Rust
  `ttl_sweep` uses `>` (`invocation_registry.rs:240`:
  `now - entry.last_seen > self.ttl_s`). Boundary-only divergence (an entry idle
  EXACTLY ttl seconds reaps in Python, not in Rust). Severity LOW.

- `count_by_agent` adds `!entry.ttl_reaped` in Rust
  (`invocation_registry.rs:214`) which Python omits (`in_flight.py:98-103`,
  filters only `background && agent && not task.done()`). Behaviorally aligned: a
  ttl_reaped task has been aborted so it is also `is_finished()`; the extra guard
  is harmless and arguably more correct. Note, not a bug.

- Envelope serialization is raw UTF-8 (`serde_json`) while Python `json.dumps`
  defaults to `ensure_ascii=True` (`\uXXXX`). The `envelope.rs` doc-comment
  "byte-stable for requests and error envelopes" holds only for ASCII / intra-
  Rust. Benign at runtime (both ends are Rust; JSON parsers accept both; the CAS
  hashes correctly hand-roll the ascii escaping in `cas.rs` rather than relying
  on the envelope path), but the doc-comment overclaims for non-ASCII. No
  byte-comparison consumer of the request envelope was found. Severity LOW/note.

- `internal_error` envelopes lack the Python `error_id` uuid in `details`
  (Python `dispatcher.py:122-131`; Rust `dispatcher.rs:234-243` and
  `server.rs:325-337`). Diagnostic correlation id dropped. LOW.

- Intentional, correctly-handled migration divergences (NOT bugs): native
  command sessions replace the Python stub; Daytona/AF_UNIX-only thin client
  collapses to the `eosd --client` branch; Python launch script replaced by
  `eosd daemon --spawn` preserving the PID/socket/log/env-signature contract
  (`daemon_client.rs:877-955` vs in-tree `daemon_client.py:640-728`); the
  `ensure_daemon_current` TCP-cache-invalidation in the stale Python doc-comment
  is correctly NOT reproduced (documented at `daemon_client.rs:159-163`).

---

## Open questions

1. Plugin gate TOCTOU: Python runs `_plugin_block_decision` under
   `acquire_dispatch_slot(agent_id)` so `exit_pending` cannot flip mid-call
   (`dispatcher.py:90-114,251-258`). Rust `ensure_plugin_family_allowed` does a
   bare `agent_has_active_handle` check with no per-agent serialization against a
   concurrent isolated enter/exit. Whether `crate::isolated`'s own locking closes
   this race was NOT traced — flag for a dedicated isolated-workspace concurrency
   review, do not assume a bug.

2. Native command-session internals (`command.rs`, ~55KB) were inspected only at
   the op-surface + registry-fn-name level (start/write_stdin/cancel/collect/
   count, background flag, process-group registration). The PTY/yield/timeout/
   completion-collection semantics, isolated-vs-shared session routing, and
   `command_session_count` gating for isolated-workspace entry need a dedicated
   deep review against the docs contract (no Python ground truth exists for the
   native path).

3. `runtime.read_request_s` parity: confirm no production dashboard/alert reads
   the daemon `runtime.*` timings before deciding whether D3 should be raised.
