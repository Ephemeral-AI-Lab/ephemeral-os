# 01 — Wire Protocol (AF_UNIX + 127.0.0.1 TCP, newline-delimited JSON envelopes)

FROZEN contract extracted from the live Python sandbox runtime on **2026-05-31**.
A Rust reimplementation must reproduce this byte-for-byte at the canonicalized-equal
bar (plan §2 / AV-1). Every claim below is cited as `path:line` against the actual
checkout (repo root `/Users/yifanxu/machine_learning/LoVC/EphemeralOS`, sources under
`backend/src/sandbox`).

> **Superseded 2026-06-14:** this document is a frozen historical Python-runtime
> record. The live Rust catalog now uses `sandbox.*` operation names from
> `crates/protocol/src/catalog.rs` and `crates/operation/ops.json`; `api.*`
> names below are retained only as archived migration context.

> Plan-anchor corrections (read these first — the task's cited anchors were partly wrong):
> - **The request framing, `MAX_REQUEST_BYTES`, read timeout, and auth check are NOT in
>   `daemon/rpc/dispatcher.py`.** They live in **`daemon/rpc/server.py`**. `dispatcher.py`
>   is the op-routing + error-envelope module; `server.py` is the AF_UNIX/TCP socket server.
> - **There is no `ping` op anywhere in the sandbox source.** `grep -rni ping src/sandbox`
>   returns zero op registrations. The plan's "`ready`/`ping`" (plan line 208) and the task's
>   "ping op" have no Python counterpart. The actual liveness op is **`api.v1.heartbeat`**
>   (`builtin_operations.py:113`); the actual readiness op is **`api.runtime.ready`**.
> - **Plan §11.1 does not exist as a heading.** The wire-protocol content sits in plan §11
>   (line 363) and §2 (lines 112–118). Documented from those.
> - The protocol-version field is **inside `args`**, not a top-level envelope sibling
>   (see §1.3). The auth field is **TCP-only, conditional** (see §5).

---

## 0. Transports and exact byte framing

Two transports carry the identical newline-delimited JSON envelope. The daemon
binds both via one `serve()` (`daemon/rpc/server.py:167-209`).

| Transport | Bind | Auth | Source |
|-----------|------|------|--------|
| **AF_UNIX** (local fallback) | `asyncio.start_unix_server(..., path=<socket>, limit=MAX_REQUEST_BYTES)` | none (`auth_token=None`) | `server.py:183-187` |
| **127.0.0.1 TCP** (Docker host-forwarded) | `asyncio.start_server(..., host=tcp_host, port=tcp_port, limit=MAX_REQUEST_BYTES)`, only when `tcp_host and tcp_port` | `auth_token` enforced if non-None | `server.py:192-202` |

**Socket path:** `/eos/runtime/daemon/runtime.sock`. PID file:
`/eos/runtime/daemon/runtime.pid`.
Socket parent dir is chmod `0o700`; socket inode forced to `0o600` after bind
(`server.py:151,181,190`).

**Wire framing — one envelope per connection, request then response:**

- **Request:** UTF-8 JSON object, **terminated by a single `\n`**. The daemon reads
  exactly one line via `reader.readline()` (`server.py:74-75`). Anything after the first
  `\n` on that connection is never read.
- **Response:** the daemon serializes with
  `json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"`
  (`server.py:133`) — **compact separators (no spaces), single trailing `\n`** — then
  closes the connection.
- The host reads the response until EOF (the daemon half-closes after writing).
  TCP host reader: `daemon_client.py:521-527` (reads 65536-byte chunks until empty).
  Thin client (AF_UNIX): `daemon/scripts/thin_client.py:39-53` (same loop).

**Host request serialization is byte-identical to the daemon's response style:**
`json.dumps({"op":..,"invocation_id":..,"args":..}, separators=(",", ":"))` then
`+ b"\n"` (`daemon_client.py:114-117`, `:517`; thin client appends `\n` at
`thin_client.py:33`). **A Rust implementation MUST use compact separators
`(",", ":")` and exactly one trailing `\n` on both request and response.**

---

## 1. Request envelope schema (host → daemon)

### 1.1 Top-level shape

```
{"op": <string>, "invocation_id": <string>, "args": <object>}
```

Built at `daemon_client.py:114-117`:

```python
envelope_json = json.dumps(
    {"op": op, "invocation_id": invocation_id, "args": clean_args},
    separators=(",", ":"),
)
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `op` | string | **yes** | Non-empty op name; e.g. `api.v1.read_file`. Validated `dispatcher.py:165-175` (rejects non-string/empty → `invalid_envelope` error). This is the routing key into `OP_TABLE` (`dispatcher.py:39,147-149`). |
| `invocation_id` | string | **yes** (host always sets it) | uuid4 hex. If absent/blank on arrival the daemon synthesizes one and logs a warning (`dispatcher.py:176-179`). Cancel op uses a fresh id (`daemon_client.py:109-110`); all others reuse `args.invocation_id` or a new uuid4 (`:111-113`). |
| `args` | object (JSON dict) | optional in protocol; in practice always present | Defaults to `{}` if missing/`None` (`dispatcher.py:180-182`). If present and not a dict → `invalid_envelope` error (`:183-193`). |

### 1.2 invocation_id duplication (load-bearing)

The host writes `invocation_id` **both** at top level **and inside `args`**
(`daemon_client.py:112-113`: `clean_args["invocation_id"] = invocation_id`). The
daemon also `setdefault`s it into args (`dispatcher.py:194`). A faithful request
fixture shows it in both places. (Exception: `api.v1.cancel` — top-level id is a
fresh uuid4; the cancel target id is passed inside `args.invocation_id` by the
caller, `builtin_operations.py:95`.)

### 1.3 `args` standard members

`call_daemon_api` always injects `layer_stack_root` into args
(`daemon_client.py:170-173`); default value `DEFAULT_LAYER_STACK_ROOT =
"/eos/layer-stack"` (`paths.py:15`).

The **protocol-version field lives INSIDE `args`, not at the top level.** Path:
`DaemonSandboxTransport.call` → `call_daemon_api(sandbox_id, op,
with_daemon_protocol_version(payload), ...)` (`api/transport.py:59-64`). The third
positional arg of `call_daemon_api` is `args` (`daemon_client.py:161-168`), and
`with_daemon_protocol_version` prepends the version field to that dict
(`daemon_client.py:186-191`):

```python
def with_daemon_protocol_version(payload):
    return {DAEMON_PROTOCOL_FIELD: DAEMON_PROTOCOL_VERSION, **dict(payload)}
```

So a fully-built read_file request envelope is:

```json
{"op":"api.v1.read_file","invocation_id":"...","args":{"layer_stack_root":"/eos/layer-stack","_eos_daemon_protocol_version":1,"path":"...","caller_id":"...","invocation_id":"..."}}
```

| Constant | Value | Source |
|----------|-------|--------|
| `DAEMON_PROTOCOL_VERSION` | `1` (int) | `daemon_client.py:46` |
| `DAEMON_PROTOCOL_FIELD` | `"_eos_daemon_protocol_version"` | `daemon_client.py:47` |
| `DAEMON_AUTH_FIELD` | `"_eos_daemon_auth_token"` | `daemon_client.py:48`; mirrored `server.py:52` |

**Note:** the daemon server **never reads or validates** `_eos_daemon_protocol_version`
(`grep` across `daemon/` for the field name returns nothing in the read/dispatch
path). It is carried by the host but currently inert on the daemon side — a versioning
hook for future coordinated bumps. Rust must still emit it (inside `args`) to match
Python-emitted fixtures at the canonicalized-equal bar.

`None`-valued args are stripped before serialization (`_without_none`,
`daemon_client.py:108,669-670`).

### 1.4 read_file request (concrete)

Op `api.v1.read_file` (alias of verb `read_file`, `builtin_operations.py:61`).
Relevant args: `path` (string, required — `require_single_file_path` enforced in
`dispatch.py:239-240`), `caller_id` (string, optional), plus the standard members above.
Fixture: `read_file_request.json`.

---

## 2. Response envelope schema (daemon → host)

There is **no single fixed response struct** — handlers return JSON-safe dicts that
are passed through `_to_response_dict` (`dispatcher.py:150-159,244-248`). Two
near-universal conventions hold:

1. **`success`** (bool) — every handler result and every error envelope carries it.
   `success: false` + a non-empty string `status` field marks a *handler-level policy
   result* (not a transport error); the host treats those as normal responses
   (`daemon_client.py:152-158`, `_is_handler_level_error_result`).
2. **`timings`** (object of `metric → float`) — the daemon attaches runtime timings to
   every dict response (see §2.2).

### 2.1 read_file response (concrete)

From `_read_file_from_layer_stack` (`workspace_tool/dispatch.py:302-318`):

```json
{"success":true,"workspace":"ephemeral","content":"<file text>","exists":<bool>,"encoding":"utf-8","timings":{...}}
```

| Field | Type | Notes |
|-------|------|-------|
| `success` | bool (`true`) | |
| `workspace` | string (`"ephemeral"`) | |
| `content` | string | empty string `""` when `exists` is false (`dispatch.py:311`) |
| `exists` | bool | |
| `encoding` | string (`"utf-8"`) | literal |
| `timings` | object | **26 keys** — see below; structure is part of the contract |

**`timings` for read_file = 26 keys**, in this insertion order:

1. **21 `resource.*` keys** from `_layer_stack_file_resource_timings`
   (`dispatch.py:436-465`, verified key count = 21). All are floats. Only the first
   three vary with workload; the other **18 are hardcoded `0.0` literals** in the source
   (deterministic — they are part of the response *structure*, not measured timings):
   - `resource.command_exec.changed_path_count` (= changed file count; `0.0` for read)
   - `resource.layer_stack.manifest_depth` (= number of manifest layers)
   - `resource.layer_stack.manifest_path_count` (= same layer count)
   - `resource.command_exec.run_dir_tree_{exists,bytes,file_count,dir_count,entry_count,truncated}` — all `0.0`
   - `resource.command_exec.workspace_tree_{exists,bytes,file_count,dir_count,entry_count,truncated}` — all `0.0`
   - `resource.command_exec.upperdir_tree_{exists,bytes,file_count,dir_count,entry_count,truncated}` — all `0.0`
2. `api.read.layer_stack_read_s`, `api.read.total_s` (`dispatch.py:315-316`) — measured.
3. `runtime.boot_to_dispatch_s`, `runtime.dispatch_s` (`dispatcher.py:211-212`),
   `runtime.read_request_s` (`server.py:130`) — measured, added by the server wrap.

The same 21-key `_layer_stack_file_resource_timings` block also feeds write/edit
responses; only `changed_path_count` differs there.

Fixture: `read_file_response.json` (all 26 keys present; measured ones = placeholder `0.0`).

### 2.2 Non-deterministic fields (canonical-form normalization — REQUIRED)

**Recommended canonical form: drop the entire `timings` object before comparison**,
plus drop `daemon_pid` and `uptime_s`. Rationale: nearly every value inside `timings`
is a measured float and cannot be byte-reproduced. The only deterministic entries are
the 18 hardcoded-`0.0` `resource.*_tree_*`/`changed_path_count` keys and the structural
`manifest_depth`/`manifest_path_count` (which a Rust runtime computes from its own
manifest) — none of these establish a separate invariant the parity bar needs to
check at the wire level, so dropping the whole object is the clean rule. The
non-reproducible members specifically are:

- `timings.runtime.boot_to_dispatch_s`, `timings.runtime.dispatch_s`
  (`dispatcher.py:211-212`) — added by the dispatcher to every dict response.
- `timings.runtime.read_request_s` (`server.py:130-132`) — added by the **server**
  (not the dispatcher) to every dispatched dict response, including error envelopes.
- all per-op `*.total_s` / measured `*_s` timings (e.g. `api.read.total_s`,
  `runtime.ready.*_s`).
- `daemon_pid`, `uptime_s` in `api.runtime.ready` (`builtin_operations.py:183-184`).

A naive full-response fixture is otherwise un-matchable. The fixtures here set all
such measured fields to deterministic placeholders (`0.0`, pid `1234`) and document
them as canonical-normalize targets; the deterministic `resource.*` keys carry their
real literal values.

---

## 3. Error envelope schema

Built by `_error_envelope` (`dispatcher.py:215-229`):

```json
{"success":false,"warnings":[],"timings":{},"error":{"kind":<string>,"message":<string>,"details":<object>}}
```

| Field | Type | Value |
|-------|------|-------|
| `success` | bool | always `false` |
| `warnings` | array | always `[]` |
| `timings` | object | `{}` at the `_error_envelope` builder; see wire note below |
| `error.kind` | string | error class — see table below |
| `error.message` | string | human message |
| `error.details` | object | per-error detail dict (`{}` if none) |

**Known error `kind` values and their raise conditions:**

| `kind` | Raised when | Source |
|--------|-------------|--------|
| `invalid_envelope` | `op` missing/non-string/empty; or `args` present but not a dict; or (TCP) envelope not a JSON object | `dispatcher.py:166-193`; `server.py:111-115` |
| `bad_json` | request line is not valid UTF-8 JSON | `server.py:104-109` |
| `request_too_large` | `readline()` raises `LimitOverrunError`/`ValueError` (line exceeds `MAX_REQUEST_BYTES`). Daemon writes this envelope then closes | `server.py:77-94` |
| `unauthorized` | TCP only: `auth_token` configured and `envelope[_eos_daemon_auth_token] != auth_token` (popped before compare) | `server.py:116-120` |
| `unknown_op` | `op` not in `OP_TABLE` | `dispatcher.py:147-149` |
| `internal_error` | any handler raises; envelope carries `details.error_id` (uuid4 hex) | `dispatcher.py:121-131` |
| `forbidden` / `forbidden_in_isolated_workspace` / `lifecycle_in_progress` | handler/gate policy refusals | `dispatcher.py:251-273`; `dispatch.py:262-281`; `builtin_operations.py:404-409` |

**Host-side error decoding** (`daemon_client.py:134-149`): a response with a non-null
`error` that is NOT a handler-level result raises `_DaemonDispatchError(kind, message,
details)`. Non-dict `error` → `kind="RuntimeError"`.

**Error `timings` on the wire (load-bearing):** `_error_envelope` always builds
`"timings":{}`, but the **server** wraps every *dispatched* response — including
`unknown_op` and `internal_error` — and inserts `timings["runtime.read_request_s"]`
(`server.py:125-132`). So on the wire those error envelopes have a non-empty `timings`.
Only `request_too_large` (and the read-timeout silent-close) bypass the server wrap and
go out with `"timings":{}` exactly (`server.py:83-94`). The canonical form's
"drop `runtime.read_request_s`" rule (§2.2) reconciles the two: after dropping it, both
error classes canonicalize to `"timings":{}`.

Fixtures: `error_unknown_op.json` (pre-server-wrap shape, `timings:{}`),
`error_request_too_large.json` (wire-exact, `timings:{}`).

---

## 4. Readiness op — `api.runtime.ready`

**Request** (host builds it during respawn recovery, `daemon_client.py:322-329`):

```json
{"op":"api.runtime.ready","invocation_id":"<uuid4hex>","args":{"layer_stack_root":"<root>"}}
```

`layer_stack_root` is **required** for readiness; absence raises
`MissingLayerStackRoot` host-side before sending (`daemon_client.py:314-320`).
Registered op → `builtin_operations.runtime_ready` (`dispatcher.py:435`).

**Response** (`builtin_operations.runtime_ready`, `:169-189`):

```json
{"success":true,"ready":<bool>,"probes":[...],"daemon_pid":<int>,"uptime_s":<float>,"timings":{...}}
```

| Field | Type | Notes |
|-------|------|-------|
| `success` | bool (`true`) | |
| `ready` | bool | `all(probe.status == "ok")` over the 3 probes (`:181`) |
| `probes` | array of 3 objects | order fixed: `control_plane`, `data_plane`, `mutation_gate` (`:174-178`) |
| `daemon_pid` | int | `os.getpid()` — **non-deterministic** |
| `uptime_s` | float | **non-deterministic** |
| `timings` | object | `runtime.ready.<name>_s` per probe + `runtime.ready.total_s` — **non-deterministic** |

**Each probe object** (`_run_probe`, `:235-256`):
`{"name":<string>,"status":"ok"|"down","details":<object>}`.

- `control_plane.details` on ok: `workspace_root`, `manifest_version`,
  `manifest_depth`, `base_root_hash` (`:201-206`). On failure
  (`require_workspace_binding` raises) → `status:"down"`,
  `details:{error_type, error}` (`:245-250`).
- `data_plane.details`: `handlers_services_ready:true`, `shell_services_ready:true`,
  `workspace_mount_mode` ∈ {`"private_namespace"`, `"unavailable"`} per
  `detect_private_mount_namespace()` (`:209-220`). The field name is
  `workspace_mount_mode` (this is the `mount_mode` the task referenced at `:215`).
- `mutation_gate.details`: `backend_ready:true`, `backend_fields` = field names of
  `OccRuntimeServices` (`["layer_stack","occ_service","occ_client","gitignore","layer_stack_manager"]`),
  `occ_client_class` = class name of `services.occ_client` (`"OccClient"`) (`:223-232`).

**Bootstrap special case** (`daemon_client.py:538-567`): for original ops
`api.ensure_workspace_base` / `api.build_workspace_base`, the host accepts
`ready:false` IF `control_plane` is down with `error_type=="WorkspaceBindingError"`
AND every other probe is `ok` — it logs a warning and proceeds (`:371-385`).

Fixture: `readiness_response.json`.

---

## 5. Auth field (TCP-only, conditional)

- **AF_UNIX never carries auth.** `_handle_connection` is called with no `auth_token`
  for the unix server (`server.py:184`), so the auth branch is skipped.
- **TCP** is gated only when the daemon was started with a non-None `auth_token`
  (`server.py:192-202`). The check: `envelope.pop(DAEMON_AUTH_FIELD, None) !=
  auth_token` → `unauthorized` error (`server.py:116-120`). The field is **popped
  (removed) before dispatch**, so handlers never see it.
- **Host side** adds the field only on the TCP path and only when
  `endpoint.auth_token` is non-empty (`_authenticated_envelope_json`,
  `daemon_client.py:673-683`):

  ```python
  envelope[DAEMON_AUTH_FIELD] = endpoint.auth_token  # then re-dumps compact
  ```

  It is injected at the **top level** of the envelope (sibling of `op`), distinct from
  the protocol-version field which lives inside `args`.

---

## 6. `api.layer_metrics` (observability surface, plan AV-1 SF-3)

Op `api.layer_metrics` → `builtin_operations.layer_metrics`
(`dispatcher.py:432`, confirmed). Response fields (`builtin_operations.py:149-166`):

```
success(bool), manifest_version(int :151), manifest_depth(int :152),
active_leases(int), leased_layers(int), layer_dirs(int), referenced_layers(int),
orphan_layer_count(int), missing_layer_count(int),
orphan_layer_ids(array≤20), missing_layer_ids(array≤20), staging_dirs(int),
storage_bytes(int), workspace_bound(bool), workspace_root(string), base_root_hash(string)
```

`manifest_depth` (`:152`) is the manifest-depth invariant observability the plan's SF-3
calls out. `storage_bytes` is summed via `rglob` over `manager.storage_root`
(`:145-148`). Requires `layer_stack_root` in args.

---

## 7. State machine — 97/98 outcomes + connect-retry + TCP-cache invalidation

### 7.1 The two thin-client exit codes

Defined twice, consistently:

| Code | Name | Host const | Guest const |
|------|------|------------|-------------|
| **97** | CONNECT_FAILED | `_THIN_CLIENT_CONNECT_FAILED` (`daemon_client.py:37`) | `CONNECT_FAILED` (`thin_client.py:9`) |
| **98** | IO_FAILED | `_THIN_CLIENT_IO_FAILED` (`daemon_client.py:38`) | `IO_FAILED` (`thin_client.py:10`) |

These are surfaced as `RawExecResult.exit_code` (TCP path constructs them directly;
thin-client path returns them as a real process exit code).
`RawExecResult` = `{success: bool, exit_code: int, stdout: str, stderr: str}`
(`shared/models.py:149-154`).

### 7.2 Exact conditions for 97 (CONNECT_FAILED)

**TCP path** (`_call_tcp_daemon`, `daemon_client.py:482-489`): raised when
`_call_tcp_daemon_inner` raises `_TcpConnectFailed`, which wraps an `OSError` from
`asyncio.open_connection(host, port)` (`:512-515`). `stderr =
"EOS_DAEMON_CONNECT_FAILED:<OSError subclass name>"`, `exit_code=97`, `stdout=""`,
`success=False`.

**AF_UNIX thin client** (`thin_client.py:24-30`): raised when `client.connect(socket_path)`
raises `ConnectionRefusedError` / `FileNotFoundError` / `OSError`. Writes
`EOS_DAEMON_CONNECT_FAILED:<class>` to stderr, returns 97.

### 7.3 Exact conditions for 98 (IO_FAILED)

**TCP path** (`daemon_client.py:475-505`):
- **Empty response:** connect+read succeeded but `stdout.strip()` is empty →
  `exit_code=98`, `stderr="EOS_DAEMON_IO_FAILED:empty_response"`
  (`_EMPTY_RESPONSE_MESSAGE`, `:39,475-481`).
- **Stream failure:** `_TcpIoFailed` (wraps `OSError` during write/read after connect,
  `:528-529`) → `stderr="EOS_DAEMON_IO_FAILED:<OSError subclass>"` (`:490-497`).
- **Timeout:** `asyncio.TimeoutError` from the outer `wait_for` (timeout = `timeout`
  arg or `60`, `:466-474`) → `stderr="EOS_DAEMON_IO_FAILED:asyncio.TimeoutError"`
  (`:498-504`).

**AF_UNIX thin client** (`thin_client.py:32-51`): `OSError` on `sendall`/`shutdown` →
98; `socket.timeout` or `OSError` during `recv` loop → 98. Thin-client socket timeout
= env `EPHEMERALOS_RUNTIME_CLIENT_TIMEOUT` (default `600`s) (`thin_client.py:20`).

### 7.4 Send dispatch + TCP-endpoint-cache invalidation

`_send_daemon_envelope` (`daemon_client.py:436-457`):

1. If a `tcp_endpoint` is set, call `_call_tcp_daemon` first.
2. If the TCP result's `exit_code != 97` → return it (success or 98 or any non-97).
3. **If TCP returned 97 (CONNECT_FAILED): drop the cached endpoint** via
   `invalidate_daemon_tcp_endpoint(sandbox_id)` (`:451`, the `:445-449` region the task
   referenced) — the cached host port may be stale after a container restart/remap.
4. **Fall back to the AF_UNIX thin client** via `exec_fn(... _daemon_thin_client_command
   ...)` (`:452-457`).

**Endpoint cache** (`daemon_client.py:218-252`):
`_tcp_endpoint_cache: dict[sandbox_id → _DaemonTcpEndpoint|None]`, populated lazily
under a per-sandbox `asyncio.Lock`. `invalidate_daemon_tcp_endpoint` pops the entry so
the next call re-resolves via the docker adapter's `get_daemon_tcp_endpoint`
(`:237-252`). Endpoint = `{host, port, internal_port, auth_token}`
(`_DaemonTcpEndpoint`, `:51-56`; normalized `:255-278`, default host `127.0.0.1`).

### 7.5 Connect-retry loop (post-respawn)

`_call_thin_client_with_connect_retry` (`daemon_client.py:397-433`):

```
_CONNECT_RETRY_DELAYS_S = (0.25, 0.5, 1.0, 2.0)   # daemon_client.py:45
```

For each delay: send the envelope; if `exit_code != 97` return immediately; else
`asyncio.sleep(delay)` and retry. After all delays, one final send (no further sleep).
Worst-case added latency ≈ 3.5s before declaring failure. Absorbs the daemon's transient
accept-queue refusal right after spawn / under parallel load.

### 7.6 Full respawn-recovery state machine

`_dispatch_with_daemon_spawn_recovery` (`daemon_client.py:281-394`):

1. Send the original envelope once (`_send_daemon_envelope`).
2. **If result is NOT 97 AND not (empty-response-98 AND op is retryable)** → return it
   (`:300-303`). Retryable = NOT a mutation op: excludes `api.edit_file`,
   `api.v1.edit_file`, `api.write_file`, `api.v1.write_file`, `api.v1.shell`, and any
   `plugin.*` op (`_can_retry_empty_response`, `:577-592`). Mutation ops **fail closed**
   on empty response to avoid replaying a write after a daemon respawn.
3. Otherwise **respawn the daemon** via `exec_fn(... _daemon_spawn_command ...)`,
   timeout `_DAEMON_SPAWN_TIMEOUT = 20`s (`:40,305-309`). If spawn exits non-zero,
   return the spawn result (caller surfaces the exec failure).
4. Require `layer_stack_root` in args else `MissingLayerStackRoot` (`:314-320`).
5. Send `api.runtime.ready{layer_stack_root}` via the connect-retry loop, timeout 30s
   (`:322-337`). Non-zero exit → `RuntimeReadinessFailed`. Bad JSON →
   `BadRuntimeReadinessResponse`. Error envelope → `RuntimeReadinessFailed` (or its
   `kind`). `ready != true` → `RuntimeNotReady` (unless the bootstrap special case in
   §4 applies) (`:338-385`).
6. On ready, replay the original envelope via the connect-retry loop (`:387-394`).

### 7.7 Thin-client launch command

`_daemon_thin_client_command` (`daemon_client.py:595-603`): builds an `sh -c`
invocation of an inline launcher that picks the first working
`python3.13|python3.12|python3.11|python3.10|python3` (`_PYTHON_CANDIDATES`, `:36`)
satisfying `sys.version_info >= (3,10)`, then `exec`s
`<bundle>/eos-sandbox/daemon/scripts/thin_client.py <socket> <envelope_json>`
(`DAEMON_THIN_CLIENT_PATH`, `paths.py:18`). Launcher exits 127 if no Python ≥3.10
(`:638-651`).

---

## 8. Server limits (server.py — NOT dispatcher.py)

| Constant | Value | Source |
|----------|-------|--------|
| `MAX_REQUEST_BYTES` | `16 * 1024 * 1024` = **16777216** bytes (16 MiB) | `server.py:58` |
| `REQUEST_READ_TIMEOUT_S` | `30.0` seconds | `server.py:62` |
| `DAEMON_AUTH_FIELD` (server mirror) | `"_eos_daemon_auth_token"` | `server.py:52` |

- `MAX_REQUEST_BYTES` is passed as the `limit=` to both `start_unix_server` and
  `start_server` (`server.py:186,201`), bounding the readline buffer. Overrun →
  `request_too_large` error envelope, written then connection closed (`:77-94`).
- `REQUEST_READ_TIMEOUT_S` wraps `reader.readline()` (`:74-75`). On
  `asyncio.TimeoutError` the daemon writes **nothing** and closes the connection
  (`:95-98`) — the host then sees an empty/closed stream (→ host TCP path maps empty
  response to 98).

---

## 9. Op table (registered ops — for completeness)

From `_register_builtin_operations` (`dispatcher.py:413-457`). No `ping`.

```
api.isolated_workspace.{enter,exit,status,list_open,test_reset}
api.{read_file,write_file,edit_file,glob,grep}  + api.v1.{read_file,write_file,edit_file,glob,grep,shell}
api.ensure_workspace_base, api.build_workspace_base
api.acquire_snapshot, api.commit_to_workspace, api.release_lease
api.layer_stack.fence_stale_staging
api.layer_metrics
api.plugin.{ensure,status}
api.runtime.ready
api.v1.{cancel,heartbeat,inflight_count,pty_session_count}
api.workspace_binding
api.audit.{pull,snapshot,reset_floor}
```

Verb→op aliases (`builtin_operations.py:57-64`): `read_file`/`glob`/`grep`/`write_file`/
`edit_file` each register both `api.<verb>` and `api.v1.<verb>`; `shell` is `api.v1.shell`
only.

The plan's "ping" maps best to **`api.v1.heartbeat`** (`builtin_operations.py:113-117`):
request args `{invocation_ids: [string]}`, response `{success:true, touched:int}`.
Fixtures: `heartbeat_request.json`, `heartbeat_response.json`.

---

## 10. OCC service keying (plan AV-1 / occ_runtime_services)

`get_occ_runtime_services(layer_stack_root)` keys a per-`layer_stack_root` cache
(`occ_runtime_services.py:48-96`). Cache key = `str(Path(root).resolve(strict=False))`
(`_runtime_service_cache_key`, `:122-126`). Cache is an `OrderedDict` (LRU) bounded at
**`_OCC_RUNTIME_SERVICES_CACHE_MAX = 256`** (`:43`), guarded by
**`threading.RLock`** (`_RUNTIME_SERVICE_CACHE_LOCK`, `:45`). LRU eviction closes the
evicted bundle's `occ_service` (`:129-145`). `OccRuntimeServices` fields (structural
contract, `:28-41`): `layer_stack, occ_service, occ_client, gitignore,
layer_stack_manager`. This per-root keying is what the readiness probes and
`layer_metrics` exercise.

---

## 11. Fixtures emitted

Written to `eos-sandbox/crates/eos-protocol/fixtures/envelopes/`. The import succeeds under
`uv run python` (`sandbox.daemon.rpc.dispatcher` imports cleanly and runs
`_register_builtin_operations()` at module load). Constants, `with_daemon_protocol_version`,
and `_error_envelope` were called from the real modules; serialization uses the daemon's
exact `json.dumps(obj, separators=(",", ":")) + "\n"`.

| File | Built from | Real code used |
|------|-----------|----------------|
| `read_file_request.json` | §1 | `DAEMON_PROTOCOL_VERSION/FIELD`, `with_daemon_protocol_version`, `DEFAULT_LAYER_STACK_ROOT` |
| `read_file_response.json` | §2.1 shape | shape from `dispatch.py:302-318`; all 26 timing keys, 21-key `resource.*` block cross-checked against `_layer_stack_file_resource_timings` source |
| `error_unknown_op.json` | §3 | `dispatcher._error_envelope("unknown_op", ...)` |
| `error_request_too_large.json` | §3/§8 | `dispatcher._error_envelope("request_too_large", ...)` |
| `readiness_response.json` | §4 shape | shape from `builtin_operations.py:169-232` (placeholder pid/uptime/timings) |
| `heartbeat_request.json` | §9 | request shape; standard `args` members (`layer_stack_root` + `_eos_daemon_protocol_version`) injected per §1.3 |
| `heartbeat_response.json` | §9 | shape from `builtin_operations.py:117` |

**Exact emit command:** `cd backend && uv run python -c '<script>'` — the script imports
`DAEMON_PROTOCOL_VERSION, DAEMON_PROTOCOL_FIELD, DAEMON_AUTH_FIELD,
with_daemon_protocol_version` from `sandbox.host.daemon_client`, `DEFAULT_LAYER_STACK_ROOT`
from `sandbox.daemon.paths`, `_error_envelope` from `sandbox.daemon.rpc.dispatcher`; builds
each dict per the sections above; writes
`json.dumps(obj, separators=(",", ":")) + "\n"` to each file.

**Fixture caveats (canonical-form / parity bar):** `*_response` and `readiness_response`
fixtures contain placeholder values for non-deterministic fields (timings `0.0`,
`daemon_pid 1234`, `uptime_s 0.0`, `base_root_hash` all-zeros, `manifest_version/depth 1`).
A live daemon emits real values there. The canonical form (§2.2) must normalize these
before the AV-1 canonicalized-equal comparison. The *request*, *error*, and *readiness
response* fixtures are exact and deterministic.
