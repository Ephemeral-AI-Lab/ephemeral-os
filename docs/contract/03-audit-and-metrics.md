# Contract 03 — Audit Event Schema, Ring Buffer / Pull, `api.layer_metrics`, Isolated JSONL

**Status:** FROZEN contract extracted from the live Python sandbox runtime. A Rust
reimplementation (`eos-protocol` + `daemon` + `eos-isolated`) MUST reproduce
every shape here byte-for-byte (CAS / on-disk paths) or canonically-equal (wire
envelopes per plan AV-1).

> **Superseded 2026-06-14:** the daemon ring buffer (`api.audit.*`), the
> isolated-workspace JSONL sink, and `api.layer_metrics` are retired Python-era
> surfaces. The live Rust catalog now uses `sandbox.checkpoint.layer_metrics`.
> This document is kept as the frozen historical record.

**Plan items covered:** §1 acyclic-severing item 1 (move `daemon/audit_schema.py`
schema into `eos-protocol`); SF-3 (`api.layer_metrics` frozen in golden fixtures);
SF-6 (isolated-workspace JSONL schema + `EOS_ISOLATED_WORKSPACE_AUDIT_PATH`).

**Source-truth commit anchors (verified against checkout, not the plan's line
numbers):** every `path:line` below was opened and confirmed during extraction.

---

## 0. Two distinct audit channels (READ THIS FIRST)

There are **two independent audit sinks** in the Python runtime. Do not conflate them.

| Channel | Sink | Event-type vocabulary | Section payloads | Who reads it |
| --- | --- | --- | --- | --- |
| **A. Daemon ring buffer** | In-memory `AuditBuffer` singleton (`backend/src/eos-sandbox/daemon/audit_buffer.py`) | dotted families: `daemon.*`, `layer_stack.*`, `overlay_workspace.*`, `occ.*`, `isolated_workspace.*`, `os_resource.sampled`, `plugin.*`, `background_tool.*`, `tool_call.*` | the **typed** `*Section` dataclasses in `daemon/audit_schema.py` | host pulls via `api.audit.pull` / `api.audit.snapshot` |
| **B. Isolated-workspace JSONL** | Append-only file `_JsonlAuditSink` (`isolated_workspace/_control_plane/pipeline_registry.py:55-70`) at `EOS_ISOLATED_WORKSPACE_AUDIT_PATH` | five `sandbox_isolated_workspace_*` strings (`audit/events.py:59-67` `IsolatedWorkspaceAuditEvent`) | **ad-hoc dicts** built inline at each emit site (NOT the typed `IsolatedWorkspaceSection`) | live tests via `raw_exec`; daemon-side lifecycle mirror |

Both channels fire on isolated-workspace lifecycle events, but with **different
event-type strings and different payload shapes**:

- `pipeline._emit(...)` (`pipeline.py:481-485`) → channel **B** (JSONL), uses
  `IsolatedWorkspaceAuditEvent.<X>.value` (`sandbox_isolated_workspace_*`).
- `safe_emit(build_isolated_workspace_event(...))` /
  `_emit_isolated_workspace(...)` → channel **A** (ring), uses
  `isolated_workspace.entered/exited/sampled/evicted` + the typed
  `IsolatedWorkspaceSection`.

The Rust port must implement **both** independently.

---

## 1. Audit event schema — typed `*Section` dataclasses (channel A)

Source: `backend/src/eos-sandbox/daemon/audit_schema.py` (this whole module MOVES into
`eos-protocol` per plan §1 item 1; confirmed pure `dataclass`/`typing`, no runtime
deps except the lazy `safe_emit`/`safe_record_phase` bridges at the bottom which
stay daemon-side).

### 1.1 Serialization rule — `_drop_none` (`audit_schema.py:17-25`)

Every section serializes via `as_dict()` → `_drop_none(self, required=...)`:

1. `data = asdict(section)` (dataclass → dict, field declaration order).
2. Result keeps only keys whose value `is not None`.
3. Any key named in `required` is re-added **even if its value is None**, as long
   as the key exists in `data`.

Consequences a Rust port MUST match exactly (verified by running each section):

- A field set to `None` is **omitted** from the dict.
- A field with a non-None default IS emitted: `workspace_mode` (string default),
  `orphan_holder_count` / `orphan_cgroup_count` / `orphan_scratch_count` (default
  `0`, int). Confirmed `IsolatedWorkspaceSection().as_dict()` =
  `{"orphan_cgroup_count": 0, "orphan_holder_count": 0, "orphan_scratch_count": 0, "workspace_mode": "isolated"}`.
- `required` keys keep their declared key even when value is None — but in
  practice they are always set (positional/mandatory), so they appear with values.

### 1.2 Event envelope wrapper

Each `build_*_event(event_type, section)` returns:

```json
{"type": "<event_type>", "payload": {"<section_key>": <section.as_dict()>}}
```

Section keys (the single sub-key under `payload`), per `build_*` helper:

| Builder (`audit_schema.py`) | `payload` sub-key | Section class |
| --- | --- | --- |
| `build_daemon_event` (:280) | `daemon` | `DaemonSection` |
| `build_layer_stack_event` (:66) | `layer_stack` | `LayerStackSection` |
| `build_overlay_workspace_event` (:97) | `overlay_workspace` | `OverlayWorkspaceSection` |
| `build_isolated_workspace_event` (:133) | `isolated_workspace` | `IsolatedWorkspaceSection` |
| `build_occ_event` (:167) | `occ` | `OccSection` |
| `build_plugin_event` (:196) | `plugin` | `PluginSection` |
| `build_background_tool_event` (:223) | `background_tool` | `BackgroundToolSection` |
| `build_tool_call_event` (:253) | `tool_call` | `ToolCallSection` |
| `build_os_resource_event` (:287) | `os_resource` | `OsResourceSection` (fixed type `"os_resource.sampled"`) |

Verified envelope examples:
```
build_daemon_event("daemon.started", DaemonSection(boot_epoch_id=123, pid=42))
  -> {"type": "daemon.started", "payload": {"daemon": {"boot_epoch_id": 123, "pid": 42}}}
build_os_resource_event(OsResourceSection(sampled_at_monotonic_s=3.25, rss_bytes=1000))
  -> {"type": "os_resource.sampled", "payload": {"os_resource": {"rss_bytes": 1000, "sampled_at_monotonic_s": 3.25}}}
```

### 1.3 Section field tables (exact field name → Python type → default)

All `| None` fields default to `None` and are dropped when None. Types: `int`,
`float`, `str`, `bool`, `dict[str, float]`. JSON mapping: int→number(integer),
float→number, str→string, bool→bool, dict→object.

**`DaemonSection`** (`:28-39`, key `daemon`) — all `int|float|None`, no required:
| field | type | default |
|---|---|---|
| boot_epoch_id | int \| None | None |
| pid | int \| None | None |
| pressure | float \| None | None |
| retained_events | int \| None | None |
| retained_bytes | int \| None | None |

**`LayerStackSection`** (`:42-63`, key `layer_stack`) — all `|None`, no required:
| field | type |
|---|---|
| operation_id | str \| None |
| operation_step | int \| None |
| lease_id | str \| None |
| owner_request_id | str \| None |
| manifest_version | int \| None |
| manifest_root_hash | str \| None |
| layer_count | int \| None |
| lease_wait_ms | float \| None |
| lock_wait_ms | float \| None |
| lease_hold_ms | float \| None |
| prepare_snapshot_ms | float \| None |
| squash_trigger_reason | str \| None |
| squash_input_layers | int \| None |
| squash_result_layers | int \| None |
| squash_failure_kind | str \| None |

**`OverlayWorkspaceSection`** (`:75-94`, key `overlay_workspace`):
| field | type | default |
|---|---|---|
| operation_id | str \| None | None |
| workspace_mode | str | `"ephemeral"` (always emitted) |
| workspace_handle_id | str \| None | None |
| lease_id | str \| None | None |
| manifest_root_hash | str \| None | None |
| mount_ms | float \| None | None |
| cleanup_ms | float \| None | None |
| scratch_removed | bool \| None | None |
| cleanup_failure_kind | str \| None | None |
| committed_layer_id | str \| None | None |
| publish_layer_ms | float \| None | None |
| changed_path_count | int \| None | None |
| upperdir_bytes | int \| None | None |

**`IsolatedWorkspaceSection`** (`:106-130`, key `isolated_workspace`):
| field | type | default |
|---|---|---|
| operation_id | str \| None | None |
| workspace_mode | str | `"isolated"` (always emitted) |
| workspace_handle_id | str \| None | None |
| caller_id | str \| None | None |
| holder_pid | int \| None | None |
| holder_pid_alive | bool \| None | None |
| cgroup_id | str \| None | None |
| cgroup_removed | bool \| None | None |
| scratch_removed | bool \| None | None |
| upperdir_bytes | int \| None | None |
| upperdir_cap_bytes | int \| None | None |
| memory_current_bytes | int \| None | None |
| memory_peak_bytes | int \| None | None |
| cpu_usage_usec_delta | int \| None | None |
| orphan_holder_count | int | `0` (always emitted) |
| orphan_cgroup_count | int | `0` (always emitted) |
| orphan_scratch_count | int | `0` (always emitted) |
| sampled_at_monotonic_s | float \| None | None |

**`OccSection`** (`:142-164`, key `occ`) — all `|None`, no required:
| field | type |
|---|---|
| operation_id | str \| None |
| operation_step | int \| None |
| changeset_id | str \| None |
| changed_path_count | int \| None |
| transaction_lock_wait_ms | float \| None |
| prepare_ms | float \| None |
| apply_ms | float \| None |
| commit_ms | float \| None |
| committed_layer_id | str \| None |
| publish_layer_ms | float \| None |
| committed_layer_bytes | int \| None |
| conflict_kind | str \| None |
| conflict_path | str \| None |
| conflict_reason | str \| None |
| base_manifest_version | int \| None |
| current_manifest_version | int \| None |

**`PluginSection`** (`:174-193`, key `plugin`) — required=`("plugin_id","plugin_kind")`:
| field | type | default |
|---|---|---|
| plugin_id | str | **required (no default)** |
| plugin_kind | str | **required (no default)** |
| plugin_version | str \| None | None |
| plugin_tool_name | str \| None | None |
| request_bytes | int \| None | None |
| response_bytes | int \| None | None |
| duration_ms | float \| None | None |
| status | str \| None | None |
| error_kind | str \| None | None |
| message_hash | str \| None | None |
| workspace_handle_id | str \| None | None |
| caller_id | str \| None | None |
| peak_resident_bytes | int \| None | None |

**`BackgroundToolSection`** (`:203-220`, key `background_tool`) —
required=`("background_work_id",)`:
| field | type | default |
|---|---|---|
| background_work_id | str | **required (no default)** |
| work_kind | str \| None | None |
| tool_name | str \| None | None |
| caller_id | str \| None | None |
| uptime_ms | float \| None | None |
| status | str \| None | None |
| exit_code | int \| None | None |
| duration_ms | float \| None | None |
| error_kind | str \| None | None |
| cancel_reason | str \| None | None |
| delivery_latency_ms | float \| None | None |

**`ToolCallSection`** (`:232-250`, key `tool_call`) —
required=`("tool_use_id","tool_name")`:
| field | type | default |
|---|---|---|
| tool_use_id | str | **required (no default)** |
| tool_name | str | **required (no default)** |
| caller_id | str \| None | None |
| workspace_mode | str \| None | None |
| workspace_handle_id | str \| None | None |
| phase | str \| None | None |
| duration_ms | float \| None | None |
| total_ms | float \| None | None |
| exit_status | str \| None | None |
| bytes_in | int \| None | None |
| bytes_out | int \| None | None |
| phase_totals_rollup | dict[str, float] \| None | None |

**`OsResourceSection`** (`:262-277`, key `os_resource`) —
`sampled_at_monotonic_s` is a mandatory positional (no default), rest `|None`:
| field | type | default |
|---|---|---|
| sampled_at_monotonic_s | float | **required (no default)** |
| rss_bytes | int \| None | None |
| cpu_user_s | float \| None | None |
| cpu_system_s | float \| None | None |
| cpu_throttled_us | int \| None | None |
| io_read_bytes | int \| None | None |
| io_write_bytes | int \| None | None |
| io_read_ops | int \| None | None |
| io_write_ops | int \| None | None |

### 1.4 Event-type string constants & families (channel A vocabulary)

There are TWO sources for event-type strings, and they disagree in style — the
**canonical, in-use** strings are the dotted families documented in
`audit_buffer.py:19-43` (and emitted via the `build_*_event(event_type, ...)`
calls at the actual emit sites). The `audit/events.py` module defines a SEPARATE,
older constant set used by `audit/translation.py`/`audit/bus.py` — see §1.5.

**Canonical dotted event families** (single source of truth comment,
`audit_buffer.py:19-43`), with lane assignment:

```
daemon.{started, stopped, audit_buffer_pressure}        [critical]
daemon.restart_observed                                 [critical]
isolated_workspace.{entered, exited, evicted,
    orphan_check_completed, orphan_reaped}              [critical]
isolated_workspace.sampled                              [sample]
overlay_workspace.{mounted, published, cleaned,
    cleanup_failed}                                     [critical]
layer_stack.{squash_triggered, squash_completed,
    squash_failed}                                      [critical]
layer_stack.{lease_requested, lease_acquired,
    lease_released, lock_acquired,
    snapshot_prepared}                                  [normal]
occ.conflict_rejected                                   [critical]
occ.{changeset_prepared, transaction_lock_acquired,
    apply_committed, publish_layer}                     [normal]
os_resource.sampled                                     [sample]
plugin.{tool_invoked, tool_completed, error}            [normal]
plugin.peak_resident_sampled                            [sample]
background_tool.{started, completed, failed,
    cancelled, delivered}                               [normal]
background_tool.heartbeat                               [sample]
tool_call.{started, finished}                           [normal]
tool_call.phase                                         [sample]
```

`event_type` is a **free-form string argument** to `build_*_event` — the buffer
does not validate it against this list. The list is the documented vocabulary the
Rust emit sites must produce; lane is chosen by each `safe_emit(..., lane=...)`
call site, not derived from the type. Confirmed concrete emit:
`daemon.audit_buffer_pressure` (`audit_buffer.py:345`, lane `critical`),
`isolated_workspace.entered/exited/sampled/evicted` (pipeline + lifecycle).

### 1.5 `audit/events.py` — the SEPARATE constant set (do not confuse with §1.4)

`backend/src/eos-sandbox/audit/events.py` is a different module (the host-side
`audit/` translation bus, plan's `audit/` 650-LOC line item). Its constants use
**different strings** than the ring's dotted families. Enumerate for completeness;
the Rust ring-buffer port follows §1.4, not this set, but a port of `audit/`
translation must reproduce these:

- `OPERATION_*`: `sandbox.operation.{started,completed,failed,conflicted}`
- `OCC_*`: `sandbox.occ.{prepared,committed,conflicted}`
- `OVERLAY_EXECUTED`: `sandbox.overlay.executed`
- `LAYER_STACK_*`: `sandbox.layer_stack.{lease_acquired,layer_published,auto_squashed}`
- `RESOURCE_SNAPSHOT`: `sandbox.resource.snapshot`
- `WORKSPACE_LIFECYCLE_*`: `workspace_lifecycle_{started,completed,failed,batch_rejected}`
  (note: NO `sandbox.` prefix)
- `IsolatedWorkspaceAuditEvent` enum (`:59-67`) — used by channel **B** JSONL:
  - `ENTER = "sandbox_isolated_workspace_enter"`
  - `EXIT = "sandbox_isolated_workspace_exit"`
  - `TOOL_CALL = "sandbox_isolated_workspace_tool_call"`
  - `EVICTED = "sandbox_isolated_workspace_evicted"`
  - `GC_ORPHAN = "sandbox_isolated_workspace_gc_orphan"`
- `EVENT_FAMILIES` dict + `ALL_EVENT_TYPES` tuple + `TIMING_SIGNAL_EVENTS` dict
  group these (`:71-95`).

---

## 2. Ring buffer + pull contract (channel A)

Source: `backend/src/eos-sandbox/daemon/audit_buffer.py`. The daemon **never writes
audit to disk**; consumers pull from this in-memory ring.

### 2.1 Constants (frozen)

- `SCHEMA_VERSION = "sandbox.daemon.audit.pull.v1"` (`:57`). Appears in every
  `pull`/`snapshot` response under key `"schema"`. Lane changes = v2 break.
- Lanes (`:59-62`): `Lane = Literal["critical","normal","sample"]`.
  - `_LANES = ("critical","normal","sample")` — iteration/storage order.
  - `_EVICTION_ORDER = ("sample","normal","critical")` — eviction tries sample
    first, critical last (critical survives sample-lane pressure).
- Defaults (`:64-65`): `_DEFAULT_MAX_EVENTS = 50_000`,
  `_DEFAULT_MAX_BYTES = 8 * 1024 * 1024 = 8_388_608`.
- Pressure threshold default `0.8` (`_PressureTracker.threshold`, `:91`; and
  `AuditBuffer.__init__(pressure_threshold=0.8)`, `:134`).
- `__init__` raises `ValueError("max_events and max_bytes must be positive")` if
  either ≤ 0 (`:136-137`).
- `boot_epoch_id`: caller-supplied `int`, else `time.monotonic_ns()` (`:140-142`).

### 2.2 Capacity & drop (eviction) semantics

- Each appended event is stored in BOTH its lane deque AND a global `_all` deque,
  in append order (`:187-188`).
- Per-lane `_LaneCounters` track `events`, `bytes`, `dropped` (`:83-87`).
- **Cap enforcement** (`_enforce_caps_locked`, `:237-243`) runs after every append:
  while `sum(events) > max_events` OR `sum(bytes) > max_bytes`, evict one. Note
  the comparison is strictly `>` — the buffer may hold exactly `max_events`/
  `max_bytes`; it evicts only when exceeding.
- **Eviction** (`_evict_one_locked`, `:245-262`): walk `_EVICTION_ORDER`
  (`sample`→`normal`→`critical`); pop the OLDEST (`popleft`) event from the first
  non-empty lane; remove it from `_all`; decrement that lane's `events`/`bytes`;
  increment that lane's `dropped` and the global `_dropped_total`. Update
  `_lost_before_seq = max(_lost_before_seq, victim.seq + 1)`.
- `encoded_bytes` per event = `len(json.dumps(payload, default=str).encode("utf-8"))`
  (`_encoded_size`, `:76-80`); on `TypeError`/`ValueError` falls back to
  `len(repr(payload).encode("utf-8"))`. **NOTE for Rust:** this is computed over
  the payload AFTER `seq` and `lane` keys are injected (see §2.3), and uses
  Python `json.dumps` default formatting (`", "` / `": "` separators) for the
  size only — the size is approximate-but-deterministic-per-input; it is NOT a
  canonical-form size. `retained_bytes` therefore depends on this exact encoding.
  A Rust port must match the Python `json.dumps(..., default=str)` byte length to
  keep `retained_bytes` / `pressure` identical. (See risks.)

### 2.3 `append(event, lane="normal")` (`:170-201`)

1. Raise `ValueError(f"unknown lane: {lane!r}")` if lane not in `_LANES`.
2. Compute `encoded = _encoded_size(event)` (BEFORE seq/lane injection — confirmed
   `:173`, encoded is computed on the raw `event` arg).
3. Under lock: `seq = self._next_seq; self._next_seq += 1`.
4. `payload = dict(event); payload["seq"] = seq; payload["lane"] = lane`. So the
   stored/returned event object is the original event with two extra keys appended
   **at the end**: `seq` (int) then `lane` (str).
5. Append to lane deque + `_all`, bump counters, `_enforce_caps_locked()`.
6. Compute pressure; if `_PressureTracker.cross_rising(pressure)` (edge-triggered
   rising cross of `0.8`), capture a snapshot dict and, OUTSIDE the lock, invoke
   each registered pressure-cross callback (`:195-200`, swallow callback errors).
7. Return `seq` (int).

**Pressure** (`_pressure_locked`, `:264-270`):
`max(retained_bytes / max_bytes, retained_events / max_events)` (float).

**Pressure-cross emitter** (`_wire_pressure_emitter`, `:340-355`): on rising 0.8
cross, appends a `daemon.audit_buffer_pressure` event (built via
`build_daemon_event` with `DaemonSection(pressure, retained_events,
retained_bytes)`) to the SAME buffer at lane `critical`. Registered on the
singleton at creation (`get_audit_buffer`, `:321-328`).

### 2.4 `pull(after_seq=-1, limit=1000)` (`:203-226`)

- `if limit <= 0: limit = 1`.
- Iterate `_all` in order; skip events with `ev.seq <= after_seq`; append
  `dict(ev.payload)` to `out` until `len(out) >= limit`.
- `next_cursor = out[-1]["seq"] if out else after_seq` — the cursor is the seq of
  the LAST returned event, or the input `after_seq` unchanged when nothing matched.
- Response shape (top-level keys; dispatcher then adds `"success": true`):

```json
{
  "schema": "sandbox.daemon.audit.pull.v1",
  "cursor": {"after_seq": <next_cursor:int>, "lost_before_seq": <int>},
  "buffer": { ...§2.6 buffer block... },
  "snapshot": {"daemon": {"boot_epoch_id": <int>, "next_seq": <int>}},
  "events": [ <event payloads, each with seq+lane>, ... ]
}
```

Pull does NOT advance any internal cursor — it is stateless w.r.t. the buffer; the
consumer drives the cursor by passing `after_seq=<cursor.after_seq>` on the next call.
Evicted events are simply absent from `_all`; the gap is signalled by
`lost_before_seq` (consumer detects loss when `lost_before_seq > last_seen_seq+1`).

### 2.5 `snapshot()` (`:228-235`)

Same as pull minus `cursor` and `events`:

```json
{
  "schema": "sandbox.daemon.audit.pull.v1",
  "buffer": { ...§2.6... },
  "snapshot": {"daemon": {"boot_epoch_id": <int>, "next_seq": <int>}}
}
```

### 2.6 `buffer` block (`_buffer_block`, `:294-305`)

| key | type | source |
|---|---|---|
| retained_events | int | sum of lane event counts |
| retained_bytes | int | sum of lane byte counts |
| max_events | int | configured cap |
| max_bytes | int | configured cap |
| pressure | float | `max(bytes/max_bytes, events/max_events)` |
| dropped_event_count | int | `_dropped_total` |
| dropped_event_count_by_lane | object{critical,normal,sample → int} | per-lane `dropped` |
| lost_before_seq | int | first seq still possibly retrievable floor |

`snapshot` block (`_snapshot_block`, `:307-314`): `{"daemon": {"boot_epoch_id":
int, "next_seq": int}}`.

### 2.7 Dispatcher routes (`backend/src/eos-sandbox/daemon/rpc/dispatcher.py`)

Registered in `_register_builtin_operations` (`:413-445`):

- `api.audit.pull` → `_audit_pull_handler` (`:385-392`):
  `after_seq = int(args.get("after_seq", -1))`, `limit = int(args.get("limit",
  1000))`, calls `get_audit_buffer().pull(...)`, sets `result["success"] = True`.
- `api.audit.snapshot` → `_audit_snapshot_handler` (`:395-400`): calls
  `snapshot()`, sets `result["success"] = True`.
- `api.audit.reset_floor` → `_audit_reset_floor_handler` (`:403-410`):
  - **GATE:** if env `EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET` (stripped, lowercased)
    `!= "true"` → return error envelope (see §2.8).
  - Else returns `{"success": True, "warnings": [], "timings": {}}` (a no-op
    success — it does NOT mutate the buffer floor in the current code).

The singleton (`get_audit_buffer`, `:321-328`) is process-wide, created on first
access, with the pressure emitter wired. At dispatcher registration the daemon
also appends a boot event (`dispatcher.py:447-449`, `build_daemon_event(...)` —
truncated in this extraction but begins the boot sequence).

### 2.8 Error envelope (`_error_envelope`, `dispatcher.py:215-229`)

Shared shape for failures (used by `reset_floor` forbidden, `unknown_op`, etc.):

```json
{
  "success": false,
  "warnings": [],
  "timings": {},
  "error": {"kind": "<str>", "message": "<str>", "details": <object or {}>}
}
```

For `reset_floor` forbidden: `kind="forbidden"`, message=
`"api.audit.reset_floor requires EOS_DAEMON_AUDIT_ALLOW_FLOOR_RESET=true"`,
details=`{"op": "api.audit.reset_floor"}`.

---

## 3. `api.layer_metrics` envelope (SF-3)

Source: `backend/src/eos-sandbox/daemon/builtin_operations.py:131-166` (function
`layer_metrics`, async). Route: `dispatcher.py:432`
`"api.layer_metrics": builtin_operations.layer_metrics`. Frozen for golden
fixtures per plan AV-1 / SF-3 — the observability surface for the manifest-depth
invariant.

Input: `args` must carry `layer_stack_root` (via `require_layer_stack_root(args)`).

Return dict — **exact field names, order as written, types** (`:149-166`):

| field | type | source expression |
|---|---|---|
| success | bool (always `true`) | literal |
| manifest_version | int | `manifest.version` |
| manifest_depth | int | `manifest.depth` |
| active_leases | int | `manager.active_lease_count()` |
| leased_layers | int | `len(leased_layer_ids)` |
| layer_dirs | int | `len(layer_dirs)` (count of entries in `storage_root/layers`) |
| referenced_layers | int | `len(referenced_layer_ids)` (active ∪ leased) |
| orphan_layer_count | int | `len(orphan_layer_ids)` (on-disk − referenced) |
| missing_layer_count | int | `len(missing_layer_ids)` (referenced − on-disk) |
| orphan_layer_ids | array[str] | `sorted(orphan)[:20]` (capped at 20) |
| missing_layer_ids | array[str] | `sorted(missing)[:20]` (capped at 20) |
| staging_dirs | int | `len(staging_dirs)` (entries in `storage_root/staging`) |
| storage_bytes | int | sum of `lstat().st_size` over every file/symlink under `storage_root` (rglob) |
| workspace_bound | bool | `binding is not None` |
| workspace_root | str | `binding.workspace_root` if bound else `""` |
| base_root_hash | str | `binding.base_root_hash` if bound else `""` |

Plan SF-3's "storage bytes / active leases / manifest depth" map to
`storage_bytes` / `active_leases` / `manifest_depth` respectively. The envelope is
**richer** than the plan's three-field summary — all 16 fields above are part of
the frozen contract. `manifest_depth` (`manifest.depth`) is the observability
surface for the ~16-layer mount(8) depth ceiling invariant.

**Computation detail a Rust port must reproduce:**
- `on_disk_layer_ids` = names of subdirs in `<storage_root>/layers` that are dirs.
- `active_layer_ids` = `{layer.layer_id for layer in manifest.layers}`.
- `leased_layer_ids` = `{layer.layer_id for layer in manager.leased_layers()}`.
- `referenced = active ∪ leased`; `orphan = sorted(on_disk − referenced)`;
  `missing = sorted(referenced − on_disk)`.
- `storage_bytes` walks `storage_root.rglob("*")` counting `is_file()` or
  `is_symlink()` entries by `lstat().st_size` (symlink size = link length, NOT
  target size — `lstat`, not `stat`).
- `orphan_layer_ids`/`missing_layer_ids` are SORTED then sliced `[:20]`.

---

## 4. Isolated-workspace JSONL schema + `EOS_ISOLATED_WORKSPACE_AUDIT_PATH` (SF-6, channel B)

### 4.1 Path resolution & env var

Source: `backend/src/eos-sandbox/isolated_workspace/_control_plane/pipeline_registry.py`.

- `DEFAULT_AUDIT_JSONL_PATH = "/tmp/sandbox_isolated_workspace_events.jsonl"` (`:32`).
- Sink path (`ensure_pipeline`, `:103-106`):
  `os.environ.get("EOS_ISOLATED_WORKSPACE_AUDIT_PATH", "").strip() or
  DEFAULT_AUDIT_JSONL_PATH`. So: env var, stripped; if empty/whitespace → the
  `/tmp/...` default.
- The sink is `_JsonlAuditSink(path)` (`:55-70`), bound once at pipeline
  construction (first `api.isolated_workspace.enter`).

### 4.2 JSONL line format

`_JsonlAuditSink.emit(event_type, payload)` (`:69-70`) calls
`append_jsonl_event(self._path, {"type": event_type, "payload": dict(payload)})`.

`append_jsonl_event` (`backend/src/audit/jsonl.py:25-40`):
- No-op if path is falsy (`if not path: return`).
- `Path(path).parent.mkdir(parents=True, exist_ok=True)`.
- `payload = {"ts": time.time(), **dict(event)}` — so each line's TOP-LEVEL key
  order is **`ts`, `type`, `payload`** (`ts` inserted first).
- `data = json.dumps(payload, default=_json_default, ensure_ascii=False) + "\n"`.
- Written via raw `os.open(path, O_WRONLY|O_CREAT|O_APPEND, 0o644)` + `os.write`
  (append-only, one line per event).

So each JSONL line is exactly:
```json
{"ts": <float epoch seconds>, "type": "sandbox_isolated_workspace_<X>", "payload": {<ad-hoc dict>}}
```
- `ts` = `time.time()` (Unix epoch float, NON-monotonic, runtime-assigned).
- `ensure_ascii=False` → UTF-8 literal, not `\uXXXX`.
- `_json_default` (`jsonl.py:13-22`): tries `value.model_dump(mode="json")`, then
  `Path → str`, then `str(value)`.

### 4.3 The five event types and their EXACT payload fields (channel B)

These payloads are ad-hoc dicts built at each emit site, NOT the typed
`IsolatedWorkspaceSection`. Verified at the cited lines.

**ENTER** = `sandbox_isolated_workspace_enter`
(`_control_plane/workspace_handle_lifecycle.py:105-119`):
| key | type | source |
|---|---|---|
| workspace_handle_id | str | handle id |
| caller_id | str | caller id |
| manifest_version | int | `handle.manifest_version` |
| manifest_root_hash | str | `handle.manifest_root_hash` |
| ns_ip | str \| null | `str(handle.veth.ns_ip)` if veth else `None` |
| rfc1918_egress_mode | str | `self._config.rfc1918_egress` |
| lowerdir_layer_count | int | `len(layer_paths)` |
| tree-copy | bool | literal `False` (NOTE the hyphen in the key name) |
| total_ms | float | `timer.total_ms()` |
| phases_ms | object{str→float} | `timer.phases_ms` |

**EXIT** = `sandbox_isolated_workspace_exit`
(`workspace_handle_lifecycle.py:258-267`):
| key | type | source |
|---|---|---|
| workspace_handle_id | str | handle id |
| reason | str | literal `"explicit"` |
| lifetime_s | float | `clock - handle.created_at` |
| upperdir_bytes_discarded | int | `_directory_file_bytes(handle.upperdir)` |
| total_ms | float | `timer.total_ms()` |
| phases_ms | object{str→float} | `timer.phases_ms` |

**TOOL_CALL** = `sandbox_isolated_workspace_tool_call`
(`pipeline.py:398-407`):
| key | type | source |
|---|---|---|
| workspace_handle_id | str | handle id |
| argv0 | str | `argv[0] if argv else ""` |
| exit_code | int | subprocess exit code |
| duration_s | float | `clock - start` |
| total_ms | float | `timer.total_ms()` |
| phases_ms | object{str→float} | `timer.phases_ms` (e.g. `{"exec": ...}`) |

**EVICTED** = `sandbox_isolated_workspace_evicted` (`pipeline.py:307-320`,
fired by `ttl_sweep`):
| key | type | source |
|---|---|---|
| workspace_handle_id | str | handle id |
| reason | str | literal `"ttl"` |
| lifetime_s | float | `stats.get("lifetime_s", 0.0)` |
| upperdir_bytes_discarded | int | `stats.get("evicted_upperdir_bytes", 0)` |
| total_ms | float | `stats.get("total_ms", 0.0)` |
| phases_ms | object{str→float} | `stats.get("phases_ms", {})` |

**GC_ORPHAN** = `sandbox_isolated_workspace_gc_orphan`
(`_control_plane/orphan_reaper.py`, multiple sites :128/:151/:179/:203/:227/:297):
| key | type | source |
|---|---|---|
| kind | str | one of `"veth"`, `"scratch"`, `"cgroup"`, `"holder"` (per reap site) |
| identifier | str | the reaped resource name/id |
| total_ms | float | `<share_ms> + reap_ms` |
| phases_ms | object{str→float} | `{"discover": <share_ms>, "reap": <reap_ms>}` |

(Confirmed `veth` site :130-134 and `scratch` site :153-157; the remaining sites
use the same four-key shape with their own `kind`/`identifier`.)

### 4.4 Relationship to channel A

The same lifecycle moments ALSO emit channel-A ring events with DIFFERENT type
strings + the typed `IsolatedWorkspaceSection`:
- ENTER → also `isolated_workspace.entered` (lifecycle `:120-`).
- EXIT → also `isolated_workspace.exited` (lifecycle `:270-`).
- EVICTED → also `isolated_workspace.evicted` (pipeline `:321-333`, lane critical).
- sample tick → only `isolated_workspace.sampled` (pipeline `:350-364`, lane sample;
  NO channel-B counterpart).

A Rust port must emit both; the JSONL (B) and the ring (A) are not derivable from
each other (different field sets).

---

## 5. Golden fixtures written (under `eos-protocol/fixtures/`)

All captured from live Python (`cd backend && uv run python -c ...`). Timing/
runtime-variable fields are noted; a fixture comparison must allowlist them.

| fixture | what it pins | runtime-variable fields |
|---|---|---|
| `audit_pull_empty.json` | empty `api.audit.pull` (default caps 50000/8388608) | `snapshot.daemon.boot_epoch_id` |
| `audit_pull_two_events.json` | two-event pull (caps 10/1024 for a small fixture), cursor advance | `boot_epoch_id`; `buffer.retained_bytes`+`pressure` depend on the `json.dumps` byte length of the events |
| `audit_snapshot_empty.json` | empty `api.audit.snapshot` | `boot_epoch_id` |
| `audit_reset_floor_forbidden.json` | `api.audit.reset_floor` with env gate OFF | none |
| `audit_reset_floor_allowed.json` | `api.audit.reset_floor` with env gate ON | none |
| `layer_metrics.json` | `api.layer_metrics` envelope (all 16 fields) — synthetic values | all values are state-dependent; field NAMES + TYPES are the contract |
| `isolated_workspace_audit.jsonl` | one line per channel-B event type (5 lines), key order `ts,type,payload` | `ts` (epoch float); all numeric values synthetic |

`boot_epoch_id` in the audit fixtures was pinned to `999` via the
`AuditBuffer(boot_epoch_id=...)` test hook; in production it is `monotonic_ns()`
at first buffer access — exclude from byte compare, assert it is an int.

---

## 6. Verified anchor corrections vs the plan

- Plan task pointed at "`backend/src/eos-sandbox/audit/` — the ring buffer + pull
  contract". **CORRECTION:** the ring buffer + pull contract actually lives in
  `backend/src/eos-sandbox/daemon/audit_buffer.py`, NOT `backend/src/eos-sandbox/audit/`.
  The `audit/` package (`events.py`, `translation.py`, `bus.py`, `lifecycle.py`,
  `timing.py`, `conflict_markers.py`) is the host-side translation bus + the
  event-type constants (§1.5) + the shared `audit/jsonl.py` helper used by the
  isolated JSONL sink. Both are documented above.
- Plan SF-3 / AV-1 cite `daemon/builtin_operations.py:131,152` and route
  `rpc/dispatcher.py:432`. **CONFIRMED EXACT:** `layer_metrics` def starts at
  `:131`; `manifest_depth` line is `:152`; route is `dispatcher.py:432`.
- Plan SF-6 cites `pipeline_registry.py:104` for `EOS_ISOLATED_WORKSPACE_AUDIT_PATH`.
  **CONFIRMED EXACT:** the `os.environ.get("EOS_ISOLATED_WORKSPACE_AUDIT_PATH",
  "")` read is at `pipeline_registry.py:104`.
- Plan §1 item 1 calls `daemon/audit_schema.py` "pure dataclass/typing".
  **CONFIRMED** — only stdlib `dataclasses`/`typing`/`enum`(none here) imports;
  the two `safe_emit`/`safe_record_phase` bottom helpers do LAZY imports of
  `audit_buffer`/`engine.tool_call.phase_buffer` (not module-load deps). Those two
  bridge functions are daemon-side glue, NOT part of the movable schema.
```
