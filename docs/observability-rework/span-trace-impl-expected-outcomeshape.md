# Span Trace Expected Outcome Shape

Source: `docs/observability-rework/span-trace-impl.md`

This is a compact shape checklist for the span/trace instrumentation slice. It
does not restate the rollout order. It names the expected runtime wiring,
record labels, span/event parent shape, and checks after the slice lands.

Paths below use the current workspace layout:

```text
crates/sandbox-runtime/operation
crates/sandbox-runtime/namespace-execution
crates/sandbox-runtime/workspace
crates/sandbox-runtime/layerstack
```

## 1. High-level target

After this slice:

- `Request.request_id` is the trace id for daemon/runtime request handling.
- The daemon/runtime process shares one cloned `Observer`, so all daemon and
  in-process runtime spans use one `d-*` id sequence.
- `daemon.dispatch` is the trace root for normal runtime ops and records
  `attrs.op`.
- Fault `Response`s mark `daemon.dispatch` as `error`.
- Runtime crates may depend on the `sandbox-observability` leaf crate.
- `namespace.exec.shell` is an async span parked at launch and recorded at
  child-exit, before one-shot teardown/finalize.
- The minimum sync span set is `command.exec`, `workspace_session.create`,
  `namespace.exec.mount_overlay`, `workspace_session.capture_changes`,
  `layerstack.publish`, and `workspace_session.destroy`.
- Layer leases emit `lease.acquired` and `lease.released` events.
- One-shot finalize captures trace context once and always runs even when
  observability is disabled.

## 2. Dependency shape

Add this dependency:

```toml
sandbox-observability.workspace = true
```

Expected runtime crate manifests:

```text
crates/sandbox-runtime/operation/Cargo.toml
crates/sandbox-runtime/namespace-execution/Cargo.toml
crates/sandbox-runtime/workspace/Cargo.toml
crates/sandbox-runtime/layerstack/Cargo.toml
```

`sandbox-observability` stays a leaf. It must not gain dependencies on daemon,
runtime, manager, config, or protocol crates.

The old operation boundary test is repointed:

- Keep the assertion that runtime crates do not pull `rusqlite`.
- Remove the assertion that `operation` excludes `sandbox-observability`.
- Keep the observability crate dependency guard as the canonical leaf check.

## 3. Source touch shape

Expected main touch points:

```text
crates/sandbox-protocol/src/response.rs

crates/sandbox-daemon/src/server/dispatch.rs
crates/sandbox-daemon/src/observability/service.rs

crates/sandbox-runtime/operation/src/services.rs
crates/sandbox-runtime/operation/src/command/service/core.rs
crates/sandbox-runtime/operation/src/command/service/exec_command.rs
crates/sandbox-runtime/operation/src/workspace_session/service/impls/create_workspace_session.rs
crates/sandbox-runtime/operation/src/workspace_session/service/impls/capture_session_changes.rs
crates/sandbox-runtime/operation/src/workspace_session/service/impls/destroy_session.rs
crates/sandbox-runtime/operation/src/layerstack/service/impls/publish_changes.rs

crates/sandbox-runtime/namespace-execution/src/engine.rs
crates/sandbox-runtime/namespace-execution/src/shell.rs
crates/sandbox-runtime/namespace-execution/src/types.rs

crates/sandbox-runtime/workspace/src/namespace/mod.rs
crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs

crates/sandbox-runtime/layerstack/src/stack/mod.rs
crates/sandbox-runtime/layerstack/src/stack/lease/cleanup.rs
crates/sandbox-runtime/layerstack/src/stack/ops/publish.rs

crates/sandbox-observability/src/record.rs
crates/sandbox-observability/src/observer.rs
crates/sandbox-observability/src/lib.rs
```

The exact file split can be smaller if constructors stay readable, but the
runtime must have one shared observer path and one shared exec span registry.

Expected LOC below means implementation/change budget, not final file length.
Small drift is fine; a file landing at 2x the high end probably means a helper
or existing seam was missed.

| File | Expected LOC | Shape |
|---|---:|---|
| `crates/sandbox-runtime/operation/Cargo.toml` | 1 | add obs dependency |
| `crates/sandbox-runtime/namespace-execution/Cargo.toml` | 1 | add obs dependency |
| `crates/sandbox-runtime/workspace/Cargo.toml` | 1 | add obs dependency |
| `crates/sandbox-runtime/layerstack/Cargo.toml` | 1 | add obs dependency |
| `crates/sandbox-protocol/src/response.rs` | 3-8 | `Response::is_fault()` |
| `crates/sandbox-daemon/src/server/dispatch.rs` | 25-45 | root span, context, status |
| `crates/sandbox-daemon/src/observability/service.rs` | 3-10 | observer accessor |
| `crates/sandbox-runtime/operation/src/services.rs` | 20-35 | observer threaded through builder |
| `crates/sandbox-runtime/operation/src/command/service/core.rs` | 45-70 | service fields, shared registry, constructors |
| `crates/sandbox-runtime/operation/src/command/service/exec_command.rs` | 35-60 | `command.exec`, async launch, finalize context |
| `crates/sandbox-runtime/operation/src/workspace_session/service/impls/create_workspace_session.rs` | 8-20 | create span |
| `crates/sandbox-runtime/operation/src/workspace_session/service/impls/capture_session_changes.rs` | 8-18 | capture span |
| `crates/sandbox-runtime/operation/src/workspace_session/service/impls/destroy_session.rs` | 8-18 | destroy span |
| `crates/sandbox-runtime/operation/src/layerstack/service/impls/publish_changes.rs` | 10-25 | publish operation span boundary |
| `crates/sandbox-runtime/namespace-execution/src/engine.rs` | 45-75 | `TerminalHook`, no `on_running`, watcher order |
| `crates/sandbox-runtime/namespace-execution/src/shell.rs` | 8-18 | terminal status mapping |
| `crates/sandbox-runtime/namespace-execution/src/types.rs` | 15-35 | delete old observer, id attrs impl |
| `crates/sandbox-runtime/workspace/src/namespace/mod.rs` | 5-15 | mount engine no-op hook |
| `crates/sandbox-runtime/workspace/src/namespace/setns_runner.rs` | 10-25 | sync mount span around `.wait()` |
| `crates/sandbox-runtime/layerstack/src/stack/mod.rs` | 10-20 | lease acquired event |
| `crates/sandbox-runtime/layerstack/src/stack/lease/cleanup.rs` | 10-20 | lease released event |
| `crates/sandbox-runtime/layerstack/src/stack/ops/publish.rs` | 25-45 | publish attrs/status |
| `crates/sandbox-observability/src/record.rs` | 5-15 | final name constants |
| `crates/sandbox-observability/src/observer.rs` | 20-40 | `SpanKeyAttrs`, terminal attrs |
| `crates/sandbox-observability/src/lib.rs` | 2-8 | export new leaf items |

## 4. Name vocabulary shape

`sandbox_observability::record::names` should include the final labels:

```rust
pub const DAEMON_DISPATCH: &str = "daemon.dispatch";
pub const COMMAND_EXEC: &str = "command.exec";
pub const WORKSPACE_SESSION_CREATE: &str = "workspace_session.create";
pub const WORKSPACE_SESSION_CAPTURE_CHANGES: &str = "workspace_session.capture_changes";
pub const WORKSPACE_SESSION_DESTROY: &str = "workspace_session.destroy";
pub const NAMESPACE_EXEC_SHELL: &str = "namespace.exec.shell";
pub const NAMESPACE_EXEC_MOUNT_OVERLAY: &str = "namespace.exec.mount_overlay";
pub const LAYERSTACK_PUBLISH: &str = "layerstack.publish";

pub const LEASE_ACQUIRED: &str = "lease.acquired";
pub const LEASE_RELEASED: &str = "lease.released";
pub const COMMAND_SIGNALED: &str = "command.signaled";
```

Remove or rename any stale shell label:

```rust
NAMESPACE_EXEC_RUN_SHELL = "namespace.exec.run_shell"
```

Do not add `workspace.create` back as a span.

## 5. Trace context shape

The dispatch blocking closure creates the request context:

```rust
TraceContext {
    trace: Arc::from(request.request_id.as_str()),
    parent: None,
}
```

Rules:

- Set the thread-local inside `tokio::task::spawn_blocking`, not in the async
  caller.
- Use `Request.request_id`; do not use layerstack `owner_request_id`.
- Runtime sync spans inherit parentage from the thread-local.
- Async shell launch captures the current context with `Observer::context()`.
- One-shot finalize restores the captured context with `Observer::with_context`.

## 6. Dispatch root shape

Add this protocol helper:

```rust
impl Response {
    pub fn is_fault(&self) -> bool {
        self.value.get("error").is_some()
    }
}
```

Expected normal dispatch body shape:

```rust
let operations = Arc::clone(&self.operations);
let observer = self.observer();
let task = tokio::task::spawn_blocking(move || {
    let ctx = TraceContext {
        trace: Arc::from(request.request_id.as_str()),
        parent: None,
    };
    observer.with_context(ctx, || {
        let dispatch = observer.span(names::DAEMON_DISPATCH);
        dispatch.attr("op", request.op.clone());
        let response = sandbox_runtime::dispatch_operation(&operations, &request);
        if response.is_fault() {
            dispatch.status(SpanStatus::Error);
        }
        response
    })
    .into_json_value()
});
```

Private observability and daemon-ready ops do not need this root unless they are
intentionally made part of the runtime trace model.

## 7. Observer ownership shape

`DaemonObservability` owns the process `Observer` and exposes a clone:

```rust
impl DaemonObservability {
    pub(crate) fn observer(&self) -> Observer {
        self.observer.clone()
    }
}
```

The runtime operations builder receives that same clone:

```rust
SandboxRuntimeOperations::from_config(config, observer)
```

Shape rules:

- There is no per-component observer.
- Tests that construct services directly may use a disabled/no-op observer, but
  production must pass the daemon observer into every emitting service.
- Two independent enabled observers in the daemon/runtime process are wrong:
  they would collide on span ids and lose parent links.

## 8. Runtime service shape

Expected fields by service:

```rust
pub struct CommandOperationService {
    obs: Observer,
    exec_spans: Arc<SpanRegistry<NamespaceExecutionId>>,
    engine: Arc<NamespaceExecutionEngine<CommandExecValue>>,
    // existing fields...
}

pub struct WorkspaceSessionService {
    obs: Observer,
    // existing fields...
}

pub struct LayerStackService {
    obs: Observer,
    // existing fields...
}
```

Constructor rule:

- Build one `Arc<SpanRegistry<NamespaceExecutionId>>`.
- Pass that same registry to the command service and the namespace engine.
- Do not create one registry for launch and another for terminal recording.

Expected command constructor shape:

```rust
let exec_spans = Arc::new(SpanRegistry::new(obs.clone()));
let engine = Arc::new(NamespaceExecutionEngine::new(
    exec_spans.clone(),
    MAX_ACTIVE_COMMANDS,
    COMMAND_ENGINE_SETUP_TIMEOUT_S,
));
Self::with_engine(workspace, layerstack, config, engine, exec_spans, obs)
```

## 9. Namespace engine hook shape

Before:

```rust
observer: Arc<dyn ExecutionObserver>
```

After:

```rust
terminal_hook: Arc<dyn TerminalHook<NamespaceExecutionId>>
```

Delete from `namespace-execution/src/types.rs`:

```rust
ExecutionObserver
NoopObserver
```

Use from the observability leaf:

```rust
NoopHook
TerminalHook
SpanStatus
```

Remove all `on_running` calls. The live running state remains in
`ExecutionRegistry`; the observability hook records only the terminal edge.

## 10. Terminal status shape

Add a local mapping:

```rust
impl NamespaceExecutionTerminalStatus {
    pub fn to_span_status(self) -> SpanStatus {
        match self {
            Self::Ok => SpanStatus::Completed,
            Self::Error => SpanStatus::Error,
            Self::TimedOut => SpanStatus::TimedOut,
            Self::Cancelled => SpanStatus::Cancelled,
        }
    }
}
```

The engine watcher calls `terminal_hook.on_terminal(...)` immediately after
`child.wait_completion()` outcome is known and before finalize/teardown runs.

Expected watcher order:

```text
wait child
record async shell span through TerminalHook
run finalize/teardown
complete ExecutionRegistry live state
resolve promise
```

Finalize failures affect the live execution status, not the already-recorded
`namespace.exec.shell` span.

## 11. Async shell span shape

`SpanRegistry<NamespaceExecutionId>` is the terminal hook.

Expected leaf API support:

```rust
pub trait SpanKeyAttrs {
    fn write_attrs(&self, attrs: &mut Attrs);
}

impl<K: Eq + Hash + SpanKeyAttrs> TerminalHook<K> for SpanRegistry<K> {
    fn on_terminal(&self, id: &K, status: SpanStatus, exit_code: Option<i64>) {
        let mut attrs = Attrs::new();
        attrs.insert("async".into(), true.into());
        if let Some(code) = exit_code {
            attrs.insert("exit_code".into(), code.into());
        }
        id.write_attrs(&mut attrs);
        self.record(id, status, attrs);
    }
}
```

Expected namespace id attrs:

```rust
impl SpanKeyAttrs for NamespaceExecutionId {
    fn write_attrs(&self, attrs: &mut Attrs) {
        attrs.insert("exec_id".into(), self.0.clone().into());
    }
}
```

Expected launch site shape:

```rust
self.exec_spans.launch(
    id.clone(),
    self.obs.context(),
    names::NAMESPACE_EXEC_SHELL,
    |_child_ctx| {
        self.engine().run_shell_interactive(
            exec_command,
            target,
            id.clone(),
            on_complete,
            cgroup_procs_path,
        )
    },
)
```

Launch failure before a watcher exists cancels the parked span internally and
writes no span. A process shutdown sweep may write remaining parked spans as
`cancelled`.

## 12. Sync span seam shape

Minimum sync span contract:

| Span name | Site | Parent | Required attrs/status |
|---|---|---|---|
| `daemon.dispatch` | daemon dispatch blocking closure | none | `op`; fault `Response` -> `error` |
| `command.exec` | `CommandOperationService::exec_command` | `daemon.dispatch` | `one_shot`; fallible scope |
| `workspace_session.create` | `create_workspace_session` | caller span | fallible scope |
| `namespace.exec.mount_overlay` | `workspace/src/namespace/setns_runner.rs` around `.wait()` | `workspace_session.create` | status from wait result |
| `workspace_session.capture_changes` | `capture_session_changes` | `command.exec` in one-shot tail | fallible scope |
| `layerstack.publish` | publish boundary | `command.exec` in one-shot tail | publish attrs; conflict -> `error` |
| `workspace_session.destroy` | `destroy_session` | caller span | fallible scope |

Fallible seams use:

```rust
obs.scope(names::WORKSPACE_SESSION_DESTROY, |span| {
    // body returning Result<T, E>
})
```

Plain `obs.span(...)` is acceptable only for infallible scopes or when explicit
status is set before drop.

## 13. Layerstack event shape

Expected events:

| Event name | Site | Attrs |
|---|---|---|
| `lease.acquired` | after `leases.acquire` in `stack/mod.rs::acquire_snapshot` | `revision` |
| `lease.released` | after `leases.release` in `stack/lease/cleanup.rs::release_lease_locked` | `revision` |

Emit as plain thread-local events:

```rust
obs.event(names::LEASE_ACQUIRED, json!({ "revision": revision }));
obs.event(names::LEASE_RELEASED, json!({ "revision": revision }));
```

No captured context is passed into layerstack event functions. The caller sets
thread-local context at dispatch or at the top of one-shot finalize.

## 14. Publish span shape

`layerstack.publish` is a span, not an event.

Expected attrs:

```text
base
revision
layers_added
bytes
no_op
reason
```

Rules:

- `base` is the expected base revision.
- `revision` is the published manifest revision when available.
- `layers_added` and `bytes` are computed at the operation boundary or returned
  by `PublishChangesResult`.
- `no_op` records no-change publishes.
- Manifest conflict sets `status = error` and `attrs.reason = "manifest_conflict"`.
- Do not record source paths.

## 15. One-shot finalize shape

Before, finalize was gated only by `one_shot`:

```rust
if let Some(handler) = one_shot_handler {
    finalize_one_shot(workspace, layerstack, handler);
}
```

After, it still gates only by `one_shot`, but restores captured context:

```rust
let obs = self.obs.clone();
let ctx = obs.context();
let one_shot_handler = self.one_shot.then(|| self.handler.clone());
move |_result| {
    if let Some(handler) = one_shot_handler {
        obs.with_context(ctx, || {
            finalize_one_shot(workspace, layerstack, handler);
        });
    }
}
```

`Observer::with_context` accepts `Option<TraceContext>`. `None` means teardown
still runs and emit calls no-op; it must never skip `finalize_one_shot`.

## 16. Case A trace shape

An `exec_command` with no existing session should produce this parent shape:

```text
daemon.dispatch op=exec_command
  command.exec one_shot=true
    workspace_session.create
      lease.acquired
      namespace.exec.mount_overlay
    namespace.exec.shell async=true exec_id=... exit_code=...
    workspace_session.capture_changes
    layerstack.publish
    workspace_session.destroy
      lease.released
```

Record timing rules:

- `namespace.exec.shell` completes at child-exit.
- The shell span is written before capture/publish/destroy finalize spans.
- The shell span and finalize spans are siblings under `command.exec`.
- `lease.acquired` is under `workspace_session.create`.
- `lease.released` is under `workspace_session.destroy`.

Span ids such as `d-0` and `d-1` are illustrative. Parent/child shape is the
contract.

## 17. Persistent-session trace shape

An `exec_command` with an existing session should produce:

```text
daemon.dispatch op=exec_command
  command.exec one_shot=false
    namespace.exec.shell async=true exec_id=... exit_code=...
```

It should not include:

```text
workspace_session.create
workspace_session.capture_changes
layerstack.publish
workspace_session.destroy
lease.acquired
lease.released
```

The existing caller-owned session lives beyond the command.

## 18. Standalone session trace shapes

`create_workspace_session` should produce:

```text
daemon.dispatch op=create_workspace_session
  workspace_session.create
    lease.acquired
    namespace.exec.mount_overlay
```

`destroy_workspace_session` success should produce:

```text
daemon.dispatch op=destroy_workspace_session
  workspace_session.destroy
    lease.released
```

Admission rejection before `destroy_session` should produce only
`daemon.dispatch` with `status = error`.

## 19. Raw query shape

Lease stream:

```text
raw --kind event --name lease.acquired
raw --kind event --name lease.released
```

Publish audit:

```text
raw --kind span --name layerstack.publish
```

Live in-flight command:

- No completed `namespace.exec.shell` record is written yet.
- Snapshot/live view reads the active command from the runtime registry.
- The log is not used to reconstruct running state.

## 20. Deletion checklist

Delete or retire:

- `ExecutionObserver` trait.
- `NoopObserver` type.
- `NamespaceExecutionEngine::observer` field name.
- Engine `on_running` calls.
- Any bespoke namespace-exec observer adapter.
- Any second async span map beside `SpanRegistry`.
- The old boundary assertion that `operation` must not depend on
  `sandbox-observability`.
- Stale `namespace.exec.run_shell` label.

Do not delete:

- `ExecutionRegistry`; it remains the live-state source.
- `NoopHook`; it remains the generic no-op terminal hook.
- Mount-overlay engine support; only its observability modeling changes to a
  sync guard around `.wait()`.

## 21. Minimal final check

Expected gates after the slice:

```text
rg -n "ExecutionObserver|NoopObserver|on_running|namespace.exec.run_shell" crates
rg -n "rusqlite" crates/sandbox-runtime
cargo fmt
cargo build
cargo test
cargo clippy --all-targets
```

The first command should return no live source usage. The second should return
no runtime dependency or code usage.

## 22. Required behavior tests

Expected focused tests:

- Case A integration: one-shot `exec_command` writes the shape in section 16.
- Sync failure: a fallible `obs.scope` seam returning `Err` writes
  `status:"error"`.
- Fault response: invalid or rejected request marks `daemon.dispatch` as
  `error`.
- Launch failure: shell launch failure before watcher creation writes no
  `namespace.exec.shell` span.
- Live in-flight: persistent command has no shell span record until terminal,
  while snapshot shows active execution.
- Events view: `lease.acquired` and `lease.released` are returned by raw event
  filtering.
- Publish audit: `layerstack.publish` is returned by raw span filtering.
- Disabled observability: the same command behavior occurs and no span/event
  records are written.
- Sink error: emit failure does not change operation result.
- Boundary: runtime may depend on `sandbox-observability`; runtime must not
  depend on `rusqlite`.
