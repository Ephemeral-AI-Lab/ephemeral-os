# Verification — Daemon protocol & dispatch (sandbox)

Independent re-derivation of `daemon_protocol.md`. Python ground truth =
oldpy (`/tmp/oldpy/backend/src/sandbox/...`) + in-tree host
(`backend/src/sandbox/host/daemon_client.py`, byte-identical to oldpy on the
recovery path). Rust daemon lives under `sandbox/crates/` (the investigation's
"Rust mapping" header omits the `sandbox/` prefix but the file:line anchors are
correct). Every verdict below was re-derived by opening both sides; no anchor
taken on the investigation's word.

## Invariant verdict table

| # | Invariant | independent_status | severity | decisive bilateral anchor |
|---|-----------|--------------------|----------|---------------------------|
| 1 | Wire protocol version lockstep host↔daemon (compile-time) | confirmed_match | none | PY: never validates field; host injects `_eos_daemon_protocol_version` `daemon_client.py:38,77-85` (oldpy 48-49,190-195). RUST: `version.rs:10,13`; host derives `daemon_client.rs:37` from `eos_protocol`; real compile assert `runtime_artifact.rs:33` `const _: () = assert!(crate::daemon_client::DAEMON_PROTOCOL_VERSION == PROTOCOL_VERSION);`. Lockstep is single-sourcing the shared crate; daemon never reads the field (same as PY). |
| 2a | One compact JSON envelope/call, single `\n` frame | confirmed_match | none | PY: `+ b"\n"` server.py:90,133; `json.dumps(...,separators=(",",":"))`. RUST: `encode` = `serde_json::to_vec` + one `b'\n'` `envelope.rs:108-112`; `decode` rejects non-object + disambiguates Request/Error/Response `envelope.rs:121-137`; written back at `server.rs:243-245`. |
| 2b | Canonical response compare (drop timings/pid/uptime) | confirmed_match | none | RUST-only parity bar: `canonical.rs:14` DROP_KEYS=`["timings","daemon_pid","uptime_s"]`, sorts keys `:25-27`, quantizes f64 1e-9 `:42`. No PY counterpart (intentional). |
| 2c | CAS byte-identity (manifest_root_hash, layer_digest) | confirmed_match | none | manifest: PY `json.dumps({"layers":[...]},sort_keys=True,separators=(",",":"))` + sha256 `manifest.py:136-138`; RUST hand-builds `{"layers":[{"layer_id":..,"path":..}]}` w/ layer_id<path sort + ascii-escape incl. surrogate pairs `cas.rs:180-207`. digest: PY `kind\0path\0[payload]\0` `changes.py:146-158`, last-write-wins `sorted` `:161-166`; RUST identical framing `cas.rs:266-277`, BTreeMap last-write-wins `:255-262`. |
| 3 | Dispatcher routes ops; in-flight tracks calls (NO real dedupe) | confirmed_match | low | routing PY `dispatcher.py:147-149`, RUST `dispatcher.rs:203-225`. NO dedupe BOTH sides — overwrite by id: PY `in_flight.py:66` `self._by_invocation[invocation_id]=...`; RUST `invocation_registry.rs:126` `state.by_invocation.insert(...)`. "dedupe" is a fuzzy checklist term; registry only correlates by id. This no-dedupe is what amplifies D1. |
| 4 | Command-session lifecycle (PTY) preserved | confirmed_disparity (intentional) | none | PY `command_write_stdin`/`command_cancel` = `_command_session_not_found()` stubs `builtin_operations.py:160-165`; `command_collect_completed`→`[]`, `command_session_count`→0. Real contract is Rust-native `command.rs` (op_command_write_stdin:102). NOT deeply audited (no PY ground truth for native path) — matches investigation caveat. |
| 5 | Host recovery (spawn/connect/empty-response retry w/ backoff) | confirmed_disparity | high | State machine re-derived step-for-step PY `daemon_client.py:299-485` vs RUST `daemon_client.rs:229-381` — FAITHFUL except the fail-closed set (D1). Bootstrap fall-through (op set + control_plane down + WorkspaceBindingError + all-others-ok) matches PY:566-595 ↔ RUST:698-733. Readiness retry flag `True`/op-gated replay matches PY:355,413 ↔ RUST:295,352. |
| 6 | Auth + protocol field handled on both sides | confirmed_match | none | TCP-only pop+mismatch→unauthorized: PY `server.py:116` (`auth_token is not None and pop != token`), AF_UNIX `auth_token=None` default never auths `:69`; RUST `strip_tcp_auth` only when `is_tcp` `server.rs:250-251,348-365`, skips when token empty/None. Host adds auth only with token `daemon_client.rs:812-829`. Protocol field injected never validated (Inv 1). |

Extra invariants (independently confirmed):

| Invariant | independent_status | severity | bilateral anchor |
|-----------|--------------------|----------|------------------|
| MAX_REQUEST_BYTES=16 MiB, READ_TIMEOUT=30.0 | confirmed_match | none | PY `server.py:58,62`; RUST `version.rs:29,32`, `server.rs:47,50`; read cap `server.rs:442`. request_too_large envelope written back BOTH sides: PY `server.py:85-90`, RUST `server.rs:232-236`. |
| Thin-client codes 97/98 | confirmed_match | none | PY `_THIN_CLIENT_CONNECT_FAILED/IO_FAILED`; RUST `version.rs:23,26`. |
| Connect-retry delays (0.25,0.5,1.0,2.0) | confirmed_match | none | PY `daemon_client.py:47`; RUST `version.rs:35` + bit-exact test `version.rs:51-54`. |
| register_op collision semantics | confirmed_match | none | PY same-handler no-op / different raises `dispatcher.py:53-56`; RUST `fn_addr_eq` no-op / reject + `register_builtin` assert `dispatcher.rs:166-179`. |
| unknown_op / invalid_envelope wire kinds | confirmed_match | none | PY snake_case `dispatcher.py:149,169`; RUST `ErrorKind` `#[serde(rename_all="snake_case")]` `envelope.rs:63-77`, emitted `dispatcher.rs:194,220-224`. |
| internal_error error_id uuid on panic | confirmed_disparity | low | PY mints `uuid4().hex` into details `dispatcher.py:122-130`; RUST InternalError has NO error_id (only `{"op":op}`) `dispatcher.rs:231`, `server.rs:323-332`. |
| TTL 300s / reaper 30s / env overrides | confirmed_match | none | PY `in_flight.py:14-17`; RUST `invocation_registry.rs:28-37`. |
| Plugin isolation gate (forbidden_in_isolated_workspace) | confirmed_disparity | low | Block path preserved BOTH; unbootstrapped audit emit dropped in RUST (D4). PY `dispatcher.py:251-282`; RUST `plugin/mod.rs:325-337`. |

## Disparity adjudication

- **D1 (HIGH, host empty-response fail-closed set wrong) — CONFIRMED, decisively.**
  Re-derived all four facts:
  1. PY fail-closed set (in-tree AND oldpy, byte-identical) = `{api.edit_file,
     api.v1.edit_file, api.write_file, api.v1.write_file, api.v1.exec_command,
     api.v1.write_stdin, api.v1.command.write_stdin}` + `plugin.`
     (`daemon_client.py:614-622`).
  2. RUST set = `{api.edit_file, api.v1.edit_file, api.write_file,
     api.v1.write_file, api.v1.exec_command, api.v1.exec_stdin}` + `plugin.`
     (`daemon_client.rs:582-592`). Rust DROPPED both `write_stdin` ops, ADDED
     `exec_stdin`.
  3. Both dropped ops are REAL registered handlers in Rust
     (`dispatcher.rs:143-146` → `command::op_command_write_stdin`,
     real mutation `command.rs:102,1389`) and Python
     (`dispatcher.py:437-438`). `exec_stdin` is a PHANTOM: grep across
     `sandbox/**/*.rs` finds it ONLY in the fail-closed set + its test, never as
     a registered op anywhere. The added arm is a dead no-op.
  4. The pinning test `empty_response_gating_matches_python_set`
     (`daemon_client.rs:1211-1233`) asserts the phantom `api.v1.exec_stdin`
     fails closed and never tests the two real stdin ops — it passes by
     asserting the wrong thing.
  Harm mechanism re-confirmed: no-dedupe (Inv 3) means a wrongly-retryable
  empty-response replay of `write_stdin` after respawn double-applies the stdin
  write on the Rust native command session (the production path; the Python
  stub is a no-op, so harm is Rust-side, which STRENGTHENS D1). Severity HIGH
  upheld. Suggested fix (swap phantom for both real stdin ops + fix the test)
  is correct.

- **D2 (LOW, cancel cleanup-wait missing + inverted cleanup_done) — CONFIRMED.**
  PY `cancel` cancels, then `await asyncio.wait_for(asyncio.shield(task),
  timeout=_CANCEL_CLEANUP_WAIT_S=5.0)`, returns `cleanup_done = task.done()`
  (True once settled), `already_done = not cancelled`
  (`builtin_operations.py:47,182-200`). RUST `op_cancel` returns immediately
  `cleanup_done: !cancelled`, `already_done: !cancelled`, no wait
  (`dispatcher.rs:296-305`) — so on a successful cancel `cleanup_done=false`
  (inverted vs PY's eventual true) and the 5 s synchronous drain is gone.
  Severity LOW validated by `grep cleanup_done|already_done` across
  backend/agent-core/sandbox → ONLY the producer at `dispatcher.rs:303-304`,
  zero consumers.

- **D3 (LOW, runtime.* timings stubbed to 0.0) — CONFIRMED.**
  PY computes real `runtime.boot_to_dispatch_s` / `runtime.dispatch_s`
  (`dispatcher.py:198-212`, `max(0.0, ...)`). RUST `attach_runtime_timings`
  (`dispatcher.rs:3067`) inserts `0.0` placeholders for all three keys
  (boot_to_dispatch_s, dispatch_s, read_request_s). `canonical.rs:14` drops the
  whole `timings` subtree before any parity compare → invisible to the bar.
  Telemetry-only, LOW. Upheld.

- **D4 (LOW, plugin_check_unbootstrapped audit emit dropped) — CONFIRMED (with
  a sharpening).** PY `_plugin_block_decision` emits
  `workspace_lifecycle.plugin_check_unbootstrapped` whenever no isolated
  pipeline is bootstrapped (`dispatcher.py:259-282`), and the gate is invoked
  for EVERY plugin op (even with no agent_id, to preserve that emit —
  `dispatcher.py:116`). RUST `ensure_plugin_family_allowed` only Errs on
  `agent_has_active_handle`; the no-handle/unbootstrapped branch returns Ok
  silently with no audit (`plugin/mod.rs:325-337`). So the emit is dropped on
  every plugin op in the common (no-handle) case, not just rarely. Functional
  gating intact. LOW upheld.

## New findings

- **TTL reaper boundary operator differs (LOW, NEW-ish — investigation's "Extra
  findings" #1, independently re-confirmed).** PY `reap_stale` uses `>=`
  (`in_flight.py:123` `now - entry.last_seen >= self._ttl_seconds`); RUST
  `ttl_sweep` uses strict `>` (`invocation_registry.rs:227`
  `now - entry.last_seen > self.ttl_s`). An entry idle EXACTLY ttl seconds
  reaps in PY, not in RUST. Boundary-only, LOW. Confirmed.

- **count_by_agent extra `!ttl_reaped` guard (note, not a bug — confirmed).** RUST
  adds `!entry.ttl_reaped` (`invocation_registry.rs:203`) plus
  `!entry.abort.is_finished()`; PY filters `background && agent && not
  task.done()` only (`in_flight.py:98-102`). A ttl_reaped task is also aborted
  (finished), so behaviorally aligned. Confirmed harmless.

- **Read-timeout response path differs (LOW, NEW).** On a per-request read
  timeout, PY returns NO response (closes; server.py docstring + the
  `request_too_large`-only writeback at server.py:85-93). RUST maps timeout to
  `DaemonError::Io` and writes back a generic error envelope
  (`server.rs:237-241,453-458`). Degraded-path-only divergence; the host
  treats a closed/empty TCP read as empty_response either way, so recovery
  semantics are unaffected. Not flagged by the investigation; LOW.

- **Plugin gate TOCTOU / no per-agent slot (Open Q1, re-confirmed as open).** PY
  runs the gate under `acquire_dispatch_slot(agent_id)` so `exit_pending`
  cannot flip mid-call (`dispatcher.py:94-114`); RUST does a bare
  `agent_has_active_handle` per call site with no per-agent serialization
  (`plugin/mod.rs:121,207,253,1074,1591` → `:331`). Whether `crate::isolated`'s
  own locking closes the race was NOT traced (no isolated-workspace concurrency
  source read here). Remains an open flag for the isolated-workspace review, not
  asserted as a bug.

- **Envelope serialization ASCII note (LOW, re-confirmed).** RUST `encode`
  emits raw UTF-8 (`serde_json::to_vec`) whereas PY `json.dumps` defaults to
  `ensure_ascii=True`. Benign at runtime (both ends Rust; CAS hashes correctly
  hand-roll ascii escaping in `cas.rs`, NOT via the envelope path); only the
  envelope.rs doc-comment "byte-stable" overclaims for non-ASCII. No
  byte-comparison consumer of the request envelope exists. LOW/note.

## Overall verdict

The investigation is accurate and well-anchored. Every "match" verdict
re-derived to a confirmed_match; the four disparities (D1-D4) all reproduce at
the exact file:line claimed, with the same severities. NO investigator_missed
(no "match" that is actually broken) and NO false-alarm disparities (no flagged
item that is actually implemented across the eos-protocol boundary). D1 is the
load-bearing finding — HIGH, bilateral, and the harm is concentrated on the
Rust native command-session path (production), which the investigation's "harm
mechanism" framing slightly understates by not noting the Python `write_stdin`
is itself a no-op stub. The phantom `exec_stdin` arm and the mis-asserting test
are exactly as described. The only material additions over the investigation are
sharpenings (D4 fires on every no-handle plugin op; D1 harm is Rust-path-only)
and one genuinely new LOW note (read-timeout response path differs). No verdict
overturned.
