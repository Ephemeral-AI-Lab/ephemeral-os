# CLI Observability — Concrete Span/Trace Examples (per-operation)

Status: archived after implementation.

> **Historical rendered examples (operation-layout exempt, 2026-07-11):** CLI
> spelling, record ownership, and source paths below preserve the observability
> rework snapshot and are not current execution instructions.

`cli-observability.md` fixes the *rendered* trace/cgroup/events shapes; `README.md`
§4.1 gives one worked case (one-shot exec). This doc grounds the span/event/trace
model in **each** of the six instrumented operations — reading the **real handler**
under `crates/sandbox-runtime/operation/src` — and renders every one in the exact
waterfall style of `cli-observability.md` §4.2.

The examples below are the canonical post-fix shapes. Review findings have been folded
into the rendered records rather than carried as inline alternatives.

---

## 0. Conventions (record shape + render legend)

**Record envelope (new shape).** One JSON object per line. Envelope = `ts` +
`trace` (+ `kind`; for spans `span`/`parent`). **No** `sandbox`/`component`/`pid`.
`exit_code` rides in `attrs`. `ts` is completion time; `start = ts − dur_ms`.

**Span ids are illustrative.** The `d-*`/`np-*` ids below show the parent/child shape;
they are minted per process at span open (`crate-core-impl.md` §2.3), so the exact
sequence in a live trace depends on interleaving. The contract a reader checks is the tree
(which span parents which), never a literal slot number.

```json
{"ts":<unix_ms>,"kind":"span","trace":"<req>","span":"<proc>-<seq>","parent":"<proc>-<seq>|null","name":"<dotted>","dur_ms":<f64>,"status":"completed|error|cancelled|timed_out","attrs":{…}}
{"ts":<unix_ms>,"kind":"event","trace":"<req>","parent":"<proc>-<seq>","name":"<dotted>","attrs":{…}}
```

**Seam legend.**

| Mark | Mechanism | Recorded where / when |
|---|---|---|
| `span (sync)` | `obs.span(name)` → `SpanGuard` (`crate-core-impl.md` §3.4) | on drop, on the thread that owns the guard (dispatch for request spans; watcher for finalize spans under `with_context`) |
| `span (async)` | caller `SpanRegistry::launch(..., |child_ctx| …)` parks the span and exposes the child `TraceContext`; engine `on_terminal` → `record` | on the engine watcher thread, at child-exit, **before** finalize |
| `span (cross-proc)` | child `obs.with_context(ctx, ‖ obs.span(name))` (Phase B, `removal-and-phaseb-impl.md` §B.3) | on the forked namespace-process (`np` proc token) |
| `event` | `obs.event(name, attrs)` | immediately, on the thread that hit the seam (thread-local parent) |

Thread-local context does not cross threads by itself; watcher/cross-process work uses
the captured `TraceContext` plus `with_context`/`SpanRegistry::launch` to re-establish the
parent where the record is written.

**Render legend (matches `cli-observability.md` §4.2).** `+SS.mmm` = `(ts − dur_ms) −
trace_start`; tree by `parent`; siblings ordered by start; `[async]` = recorded on
another thread; `✓`/`✗` = `status`; `exit0` = `attrs.exit_code`. Events render as
`• name args` with no bar, no duration, no status.

> **Trace = `Request.request_id`** (`span-trace-impl.md` §2). Every operation below is
> a *separate* daemon request, so read/write/create/destroy each get their **own** trace
> id; they do **not** share the trace of the `exec_command` that created the session they
> touch. The lone exception is a write that *terminates* a one-shot command (§3B): the
> teardown effect lands on the **originating exec** trace, because the watcher context
> was captured at exec launch (`span-trace-impl.md` §7).

---

## 1. `exec_command` — Case A (one-shot) and persistent-session

### 1A. One-shot (`workspace_session_id` omitted) — grounded Case A

Handler chain: `cli_definition/command_operations.rs:dispatch_exec_command` →
`exec_command.rs:18` → `resolve_exec_workspace` (`:93`, no id → `create_one_shot_workspace_session`,
`core.rs:126`) → `workspace_session/.../create_workspace_session.rs:9` → workspace-crate
`create_workspace.rs:7` (`acquire_snapshot_with_lease` + `manager.open` →
`initialize_handle` → `mount_overlay`, **`.wait()`ed**) → engine `run_shell_interactive`
(`exec_command.rs:59`, async shell) → `wait_for_command_yield` (`:90`, default 1000 ms) →
yield-return. On child-exit the `finalize_closure` (`exec_command.rs:175`) runs
`finalize_one_shot` (`:189`): capture session changes (refreshes the session base),
publish (result discarded), then destroy on the **watcher** thread.

**Seams that fire**

| Record | Kind | Site | Parent | Thread / when |
|---|---|---|---|---|
| `daemon.dispatch` `d-0` | span (sync) | `dispatch.rs` closure (`span-trace-impl.md` §2) | — | dispatch thread, on return (~1.05s) |
| `command.exec` `d-1` | span (sync) | `exec_command.rs:18` | `d-0` | dispatch thread, at yield (~1.05s) |
| `workspace_session.create` `d-2` | span (sync) | `create_workspace_session.rs:9` | `d-1` | dispatch thread |
| `lease.acquired` | event | `stack/mod.rs:acquire_snapshot` (`:78-81`) | `d-2` | dispatch thread |
| `namespace.exec.mount_overlay` `d-4` | span (sync) | `setns_runner.rs:37` (status from `.wait()` `Result`) | `d-2` | dispatch thread (sync mount guard) |
| `namespace.exec.run_shell` `d-5` | span (async) | command engine `on_terminal` | `d-1` | command-engine watcher, at child-exit (~4.27s) |
| `namespace.runner.spawn_child` `np-0` | span (cross-proc) | `shell_exec.rs:40-63` (Phase B) | `d-5` | forked namespace-process |
| `workspace_session.capture_changes` `d-6` | span (sync) | `capture_session_changes.rs:7` | `d-1` | watcher thread (finalize closure, under `with_context`) |
| `layerstack.publish` `d-7` | span (sync) | `publish_changes.rs:7` → `publish_layer_unlocked` | `d-1` | watcher thread |
| `workspace_session.destroy` `d-8` | span (sync) | `destroy_session.rs:7` | `d-1` | watcher thread |
| `lease.released` | event | `cleanup.rs:release_lease_locked` (`:16`) | `d-8` | watcher thread |

`workspace.create` remains dropped as near-coextensive with `workspace_session.create`;
`exec.terminal` remains folded into `namespace.exec.run_shell`'s own `status`/`exit_code`.
The one-shot tail now publishes: `layerstack.publish` carries `r5→r6`, while
`lease.released` reports the original released lease revision `r5`.

**Raw `observability.ndjson` (append order ≈ `ts` order)**

```json
{"ts":1719500000009,"kind":"event","trace":"req-7f3","parent":"d-2","name":"lease.acquired","attrs":{"revision":"r5"}}
{"ts":1719500000040,"kind":"span","trace":"req-7f3","span":"d-4","parent":"d-2","name":"namespace.exec.mount_overlay","dur_ms":27.0,"status":"completed"}
{"ts":1719500000042,"kind":"span","trace":"req-7f3","span":"d-2","parent":"d-1","name":"workspace_session.create","dur_ms":39.0,"status":"completed"}
{"ts":1719500000061,"kind":"span","trace":"req-7f3","span":"np-0","parent":"d-5","name":"namespace.runner.spawn_child","dur_ms":6.0,"status":"completed","attrs":{}}
{"ts":1719500001050,"kind":"span","trace":"req-7f3","span":"d-1","parent":"d-0","name":"command.exec","dur_ms":1048.0,"status":"completed","attrs":{"one_shot":true}}
{"ts":1719500001051,"kind":"span","trace":"req-7f3","span":"d-0","name":"daemon.dispatch","dur_ms":1051.0,"status":"completed","attrs":{"op":"exec_command"}}
{"ts":1719500004273,"kind":"span","trace":"req-7f3","span":"d-5","parent":"d-1","name":"namespace.exec.run_shell","dur_ms":4231.0,"status":"completed","attrs":{"exit_code":0}}
{"ts":1719500004286,"kind":"span","trace":"req-7f3","span":"d-6","parent":"d-1","name":"workspace_session.capture_changes","dur_ms":11.0,"status":"completed","attrs":{"one_shot":true}}
{"ts":1719500004299,"kind":"span","trace":"req-7f3","span":"d-7","parent":"d-1","name":"layerstack.publish","dur_ms":12.0,"status":"completed","attrs":{"base":"r5","revision":"r6","layers_added":1,"bytes":40960,"no_op":false}}
{"ts":1719500004320,"kind":"event","trace":"req-7f3","parent":"d-8","name":"lease.released","attrs":{"revision":"r5"}}
{"ts":1719500004325,"kind":"span","trace":"req-7f3","span":"d-8","parent":"d-1","name":"workspace_session.destroy","dur_ms":25.0,"status":"completed","attrs":{"one_shot":true}}
```

Consistency: every `parent` resolves to a `span` id under the one trace `req-7f3`;
`d-1`/`d-0` complete at ~1.05s while `d-5` is still running (no record yet). `d-5`
carries the child-exit instant (4.273s, stamped **before** finalize); the finalize tail
(`d-6`/`d-7`/`d-8` + `lease.released`) is written just after, on the watcher thread,
under `d-1`.
`np-0` (`ts` 4ms earlier than the shell launch records on disk only because it appears
in append order before `d-1`/`d-0`) starts at `61 − 6 = 55 ms`. `workspace.create` is
dropped (C1); `np-0.parent` resolves because it is stamped with the shell span's *minted*
id (here `d-5`), not a literal slot number (§0).

**Rendered — `sandbox-cli observability trace --sandbox-id eos-abc --trace-id req-7f3`**

```
trace req-7f3   sandbox eos-abc   wall 4.33s   (call returned at 1.05s)

  +00.000  daemon.dispatch op=exec_command                 1051ms  ✓
  +00.002   └ command.exec one_shot                        1048ms  ✓
  +00.003      ├ workspace_session.create                    39ms  ✓
  +00.009      │   • lease.acquired r5
  +00.013      │   └ namespace.exec.mount_overlay            27ms  ✓
  +00.042      ├ namespace.exec.run_shell       [async]    4231ms  ✓ exit0   ← outlives call
  +00.055      │   └ namespace.runner.spawn_child            6ms  ✓   [Phase B: cross-process]
  +04.275      ├ workspace_session.capture_changes           11ms  ✓
  +04.287      ├ layerstack.publish r5→r6 +1 layer 40KB      12ms  ✓
  +04.300      └ workspace_session.destroy one_shot          25ms  ✓
  +04.320         • lease.released r5
```

`mount_overlay` is a **sync span** nested directly under `workspace_session.create`; it is
`.wait()`ed synchronously on the dispatch thread, carries no `[async]` mark, and does not
outlive the call. Only the shell line carries that meaning.

### 1B. Persistent session (`workspace_session_id` supplied)

No create, no mount, no one-shot teardown — `resolve_exec_workspace` resolves the
existing session (`exec_command.rs:97`, `resolve_workspace_session`), and the
`finalize_closure` is a no-op (`self.one_shot.then(...)` is `None`,
`exec_command.rs:172`). The session **outlives** the command, so the trace has **no**
destroy/lease tail.

**Seams:** `d-0 daemon.dispatch`; `d-1 command.exec` (attrs `workspace_session`,
`one_shot=false`); `d-2 namespace.exec.run_shell` (async, parent `d-1`);
`np-0 namespace.runner.spawn_child` (cross-proc, parent `d-2`, Phase B).

**Raw (after the shell completes; Phase B carries `np-0` into the trace)**

```json
{"ts":1719500100039,"kind":"span","trace":"req-9a1","span":"np-0","parent":"d-2","name":"namespace.runner.spawn_child","dur_ms":6.0,"status":"completed","attrs":{}}
{"ts":1719500101021,"kind":"span","trace":"req-9a1","span":"d-1","parent":"d-0","name":"command.exec","dur_ms":1020.0,"status":"completed","attrs":{"workspace_session":"ws-7","one_shot":false}}
{"ts":1719500101021,"kind":"span","trace":"req-9a1","span":"d-0","name":"daemon.dispatch","dur_ms":1021.0,"status":"completed","attrs":{"op":"exec_command"}}
{"ts":1719500107320,"kind":"span","trace":"req-9a1","span":"d-2","parent":"d-1","name":"namespace.exec.run_shell","dur_ms":7300.0,"status":"completed","attrs":{"exit_code":0}}
```

**Rendered (completed)**

```
trace req-9a1   sandbox eos-abc   wall 7.32s   (call returned at 1.02s)

  +00.000  daemon.dispatch op=exec_command                 1021ms  ✓
  +00.001   └ command.exec ws-7                            1020ms  ✓
  +00.020      └ namespace.exec.run_shell  ns-42  [async]  7300ms  ✓ exit0   ← outlives call
  +00.033          └ namespace.runner.spawn_child            6ms  ✓   [Phase B: cross-process]
```

In **Phase A** (`np-0` not yet threaded across the fork) the `namespace.runner.spawn_child`
row is absent here — it lands under its own trace until `removal-and-phaseb-impl.md`
Part B carries `(trace, parent)` over the fork.

**Rendered while still running (`--trace-id last`)** — the shell has **no record yet**; the
open span merges from the live registry (`cli-observability.md` §4.2):

```
trace req-9a1   sandbox eos-abc   wall — (in flight)   1 span open

  +00.000  daemon.dispatch op=exec_command                 1021ms  ✓
  +00.001   └ command.exec ws-7                            1020ms  ✓
  +00.020      └ namespace.exec.run_shell  ns-42  [async]  running  (live, from registry)
```

`np-0`'s `parent = d-5`/`d-2` is constructible because the async shell span id is minted
at launch. `SpanRegistry::launch` calls `open`, parks the span, and passes the returned
child `TraceContext { trace, parent: <new span id> }` to the launch body; Phase B threads
that context into the fork request.

---

## 2. `read_command_lines` — a fast synchronous buffered read

Handler: `read_command_lines.rs:12` → `engine().with_value(&id, read_command_window)`
(`:20`) — a pure in-memory transcript-window read (no async, no child, no I/O),
sub-millisecond. It is **not** in `span-trace-impl.md` §5's sync-seam table, so it gets
**no span of its own**.

**Seams that fire**

`read_command_lines` runs in its **own** trace (`req-rd1`), disconnected from the
command's trace (`req-9a1`). It is not in the sync-seam table, so there is no read span;
only the dispatch root records the request. These single-node read/write poll-loop traces
are deliberately low-value — retained only so every dispatched op has a uniform root span
(the settled floor); their volume is bounded by config-gating (all-or-nothing) plus file
rotation, and a high-frequency poll loop can evict richer exec traces under that cap
(`span-trace-impl.md` §2 m13).

```json
{"ts":1719500050000,"kind":"span","trace":"req-rd1","span":"d-0","name":"daemon.dispatch","dur_ms":0.4,"status":"completed","attrs":{"op":"read_command_lines"}}
```

```
trace req-rd1   sandbox eos-abc   wall 0.4ms

  +00.000  daemon.dispatch op=read_command_lines              0ms  ✓
```

---

## 3. `write_command_stdin` — stdin write (may trigger terminal completion)

Handler: `write_command_stdin.rs:6`. Resolves the live target (`exec.output_len`,
`:15`), writes stdin (`:49`) — or `exec.cancel()` (`:43`) when `is_kill_input` (`:74`,
Ctrl-C `\u{3}` / Ctrl-D `\u{4}`) — then `wait_for_command_yield` (`:64`, up to
`yield_time_ms`, default 1000; forced to 1000 on kill, `:63`). Like read, it is **not**
in the §5 table, so only `daemon.dispatch` fires in the write's **own** trace.

**3A. Plain write (command keeps running)**

```json
{"ts":1719500060312,"kind":"span","trace":"req-wr1","span":"d-0","name":"daemon.dispatch","dur_ms":312.0,"status":"completed","attrs":{"op":"write_command_stdin"}}
```

```
trace req-wr1   sandbox eos-abc   wall 0.31s

  +00.000  daemon.dispatch op=write_command_stdin            312ms  ✓
```

**3B. Kill input (Ctrl-D) terminates a one-shot command.** The write returns a trivial
trace; the **effect** — the shell async span completing and the one-shot teardown — lands
on the **originating** exec trace (`req-7f3`), because the watcher thread's context and
the finalize closure were captured at *exec* launch (`span-trace-impl.md` §7), not at
this write.

```json
{"ts":1719500061002,"kind":"span","trace":"req-wr2","span":"d-0","name":"daemon.dispatch","dur_ms":1002.0,"status":"completed","attrs":{"op":"write_command_stdin"}}
```

```
trace req-wr2   sandbox eos-abc   wall 1.00s

  +00.000  daemon.dispatch op=write_command_stdin           1002ms  ✓
```

…while, under `req-7f3`, `namespace.exec.run_shell` (`d-5`) closes as `cancelled` and the
`workspace_session.capture_changes` (`d-6`), `layerstack.publish` (`d-7`),
`workspace_session.destroy` (`d-8`), and `lease.released` tail append — possibly long
after `exec_command` returned. The dispatch duration is the yield-wait poll window, not
stdin write cost. If a write-intent event is added later, its past-tense label is
`command.signaled`.

---

## 4. `create_workspace_session` (standalone) — mount_overlay (`.wait()`ed) + lease event

Handler: `cli_definition/workspace_session_operations.rs:dispatch_create_workspace_session`
(`:101`) → `create_workspace_session.rs:9` → workspace-crate `create_workspace.rs:7`
(`acquire_snapshot_with_lease` lease, then `manager.open` → `initialize_handle` →
`mount_overlay`, **`.wait()`ed**) → `prepare_workspace_cgroup` (`:15`) + sessions insert
(`:19`).

**Seams that fire**

| Record | Kind | Site | Parent | Thread / when |
|---|---|---|---|---|
| `daemon.dispatch` `d-0` | span (sync) | `dispatch.rs` closure | — | dispatch thread |
| `workspace_session.create` `d-1` | span (sync) | `create_workspace_session.rs:9` | `d-0` | dispatch thread |
| `lease.acquired` | event | `stack/mod.rs:acquire_snapshot` (`:78-81`) | `d-1` | dispatch thread |
| `namespace.exec.mount_overlay` `d-3` | span (sync) | `setns_runner.rs:37` (status from `.wait()` `Result`) | `d-1` | dispatch thread (sync mount guard) |

**Raw**

```json
{"ts":1719500070009,"kind":"event","trace":"req-c1","parent":"d-1","name":"lease.acquired","attrs":{"revision":"r5"}}
{"ts":1719500070040,"kind":"span","trace":"req-c1","span":"d-3","parent":"d-1","name":"namespace.exec.mount_overlay","dur_ms":27.0,"status":"completed"}
{"ts":1719500070042,"kind":"span","trace":"req-c1","span":"d-1","parent":"d-0","name":"workspace_session.create","dur_ms":41.0,"status":"completed"}
{"ts":1719500070043,"kind":"span","trace":"req-c1","span":"d-0","name":"daemon.dispatch","dur_ms":43.0,"status":"completed","attrs":{"op":"create_workspace_session"}}
```

Consistency: `trace_start = 1719500070000` (dispatch start). The mount (`d-3`, start
`+13`, ends `ts 70040`) sits **inside** `workspace_session.create` (`d-1`, start `+01`,
ends `ts 70042`), which sits inside `daemon.dispatch` — every parent's `dur_ms` brackets
its children's spans. (`workspace.create` is dropped; the `d-*` ids are illustrative, §0.)

**Rendered**

```
trace req-c1   sandbox eos-abc   wall 43ms

  +00.000  daemon.dispatch op=create_workspace_session        43ms  ✓
  +00.001   └ workspace_session.create                        41ms  ✓
  +00.009      • lease.acquired r5
  +00.013      └ namespace.exec.mount_overlay                 27ms  ✓
```

`mount_overlay` is `.wait()`ed synchronously on the dispatch thread, so it carries no
`[async]` mark, lands before the sync parents close, and does not outlive the call.

---

## 5. `destroy_workspace_session` (standalone) — admission gate, lease tail (no publish)

Handler: `dispatch_destroy_workspace_session` (`workspace_session_operations.rs:116`) →
`destroy_workspace_session_with_admission` (`command/service/core.rs:90`: lock lifecycle
→ **active-command admission check** `:96-105` → `resolve_session` → `destroy_session`).
The `workspace_session.destroy` span sits at `destroy_session.rs:7` — **inside** the
destroy, **after** admission passes. `destroy_workspace` (workspace-crate
`destroy_workspace.rs`) **evicts** the upperdir (`manager.close`) and **releases** the
lease (`release_lease`); it does **not** publish.

**5A. Success**

| Record | Kind | Site | Parent | Thread |
|---|---|---|---|---|
| `daemon.dispatch` `d-0` | span (sync) | `dispatch.rs` closure | — | dispatch thread |
| `workspace_session.destroy` `d-1` | span (sync) | `destroy_session.rs:7` | `d-0` | dispatch thread |
| `lease.released` | event | `cleanup.rs:release_lease_locked` (`:16`) | `d-1` | dispatch thread |

```json
{"ts":1719500080090,"kind":"event","trace":"req-d1","parent":"d-1","name":"lease.released","attrs":{"revision":"r6"}}
{"ts":1719500080095,"kind":"span","trace":"req-d1","span":"d-1","parent":"d-0","name":"workspace_session.destroy","dur_ms":24.0,"status":"completed"}
{"ts":1719500080096,"kind":"span","trace":"req-d1","span":"d-0","name":"daemon.dispatch","dur_ms":26.0,"status":"completed","attrs":{"op":"destroy_workspace_session"}}
```

```
trace req-d1   sandbox eos-abc   wall 26ms

  +00.000  daemon.dispatch op=destroy_workspace_session       26ms  ✓
  +00.001   └ workspace_session.destroy                       24ms  ✓
  +00.020      • lease.released r6
```

**5B. Admission-reject (active commands exist).** `destroy_session` is **never reached**
(`core.rs:101-105` returns `ActiveCommands` before `resolve_session`), so there is **no**
`workspace_session.destroy` span — only the root. The dispatch closure returns
`active_command_rejection` → `Response::fault_with_details` (`workspace_session_operations.rs:179`).
The dispatch root records `status:"error"` because the returned `Response` is a fault:

```json
{"ts":1719500081001,"kind":"span","trace":"req-d2","span":"d-0","name":"daemon.dispatch","dur_ms":1.0,"status":"error","attrs":{"op":"destroy_workspace_session"}}
```

```
trace req-d2   sandbox eos-abc   wall 1ms

  +00.000  daemon.dispatch op=destroy_workspace_session        1ms  ✗   (rejected: active commands)
```

On admission reject, `destroy_session` is not reached, so there is no
`workspace_session.destroy` span. The rejection reason stays in the response body; the
trace only needs the red dispatch root.

---

## 6. `publish_changes` — publish to the layerstack

Handler: `layerstack/service/impls/publish_changes.rs:7` (`LayerStackService::publish_changes`)
→ `LayerStack::publish_validated_changes` → `publish_layer_unlocked` (write layer dir,
`fsync_tree_files`, `fsync_dir`, `rename`, `write_manifest`, `write_layer_bytes` — real
durational I/O). `span-trace-impl.md` §6 (M9) now wires a sync `layerstack.publish`
**span** over `publish_layer_unlocked` (`publish.rs`), `status=error` + `attrs.reason`
on the `ManifestConflict` path (`publish.rs:89-97`, mapped at `publish_changes.rs:50`)
— folding what used to be an event pair into one span. The production one-shot finalize
path calls it after capture and before destroy (`exec_command.rs:finalize_one_shot`).

Publishing is durational I/O (multiple fsyncs + rename + manifest write), so it is a
span, not an event. `SpanStatus` encodes success/rejection; conflict details stay in
`attrs.reason`.

```json
{"ts":1719500090014,"kind":"span","trace":"req-p1","span":"d-2","parent":"d-1","name":"layerstack.publish","dur_ms":12.0,"status":"completed","attrs":{"base":"r5","revision":"r6","layers_added":1,"bytes":40960,"no_op":false}}
{"ts":1719500090015,"kind":"span","trace":"req-p1","span":"d-1","parent":"d-0","name":"command.exec","dur_ms":14.0,"status":"completed","attrs":{"one_shot":true}}
{"ts":1719500090016,"kind":"span","trace":"req-p1","span":"d-0","name":"daemon.dispatch","dur_ms":16.0,"status":"completed","attrs":{"op":"exec_command"}}
```
On conflict:
```json
{"ts":1719500090004,"kind":"span","trace":"req-p2","span":"d-2","parent":"d-1","name":"layerstack.publish","dur_ms":3.0,"status":"error","attrs":{"base":"r5","reason":"manifest_conflict"}}
{"ts":1719500090005,"kind":"span","trace":"req-p2","span":"d-1","parent":"d-0","name":"command.exec","dur_ms":5.0,"status":"completed","attrs":{"one_shot":true}}
{"ts":1719500090006,"kind":"span","trace":"req-p2","span":"d-0","name":"daemon.dispatch","dur_ms":6.0,"status":"completed","attrs":{"op":"exec_command"}}
```

**Rendered (span-shape, nested under its caller)**

```
trace req-p1   sandbox eos-abc   wall 16ms

  +00.000  daemon.dispatch op=<caller>                        16ms  ✓
  +00.001   └ <caller span d-1>                               14ms  ✓
  +00.002      └ layerstack.publish r5→r6 +1 layer 40KB       12ms  ✓
```

---

## 7. Cross-op summary

| Op | Own span(s) | Async | Events | Trace shape |
|---|---|---|---|---|
| exec_command (one-shot) | dispatch, exec, ws_session.create, mount, capture, publish, destroy | shell | lease.acquired, lease.released | rich; async finalize tail |
| exec_command (persistent) | dispatch, exec | shell | — | rich; no teardown |
| read_command_lines | dispatch only | — | — | single node |
| write_command_stdin | dispatch only | — | — | single node; terminal effect lands on originating exec trace |
| create_workspace_session | dispatch, ws_session.create, mount | — | lease.acquired | nested sync create |
| destroy_workspace_session | dispatch, ws_session.destroy on success | — | lease.released on success | reject is red dispatch root only |
| publish_changes | layerstack.publish | — | — | sync publish span under its caller |
