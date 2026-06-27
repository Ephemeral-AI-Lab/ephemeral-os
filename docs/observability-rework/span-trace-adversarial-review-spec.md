# Span-Trace Observability — Adversarial Review Spec

Status: ready-to-run after the span-trace slice lands. Drives an evidence-gated
review of the implementation against `span-trace-impl.md` and its expected
outcome shape.

## 0. Why this exists

The span-trace slice wires the leaf `Observer` into daemon/runtime request flow,
parks async namespace shell spans, adds sync seam spans, and emits layerstack
lease events. A green build is not enough. This review asks whether the code
actually preserves behavior while adding correlation:

- **Completeness** — every required seam, attr, status, and event exists.
- **Correctness** — parent/trace/thread handoffs are true under real execution.
- **Behavior preservation** — observability cannot skip teardown or change errors.
- **Boundary discipline** — runtime may depend on the leaf, but the leaf stays leaf.
- **Smallness** — the implementation should stay near the expected LOC budget.

## 1. Hard rules

1. **Evidence or discard.** Every finding cites code as
   `crate/path/file.rs:line` plus a short quote, and cites the violated spec or
   checklist section.
2. **Review the real flow.** Trace `exec_command` end to end, including one-shot
   finalize and watcher-thread completion. Test names are not evidence.
3. **Adversarial stance.** Try to break parentage, status attribution, launch
   failure, disabled-observability behavior, and dependency boundaries.
4. **Severity.** Use `blocker` for behavior/spec violation, `major` for likely
   correctness or extensibility risk, `minor` for cleanup, `nit` for naming only.
5. **Actionable.** Each finding gives the smallest concrete edit.

## 2. Source of truth

- Primary spec: `docs/observability-rework/span-trace-impl.md`.
- Shape checklist: `docs/observability-rework/span-trace-impl-expected-outcomeshape.md`.
- Prerequisite shape: `docs/observability-rework/crate-core-impl-expected-outcomeshape.md`.
- Trace examples: `docs/observability-rework/cli-observability-examples.md`.
- Context only: `docs/observability-rework/README.md`.

## 3. Code under review

Primary files:

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

Also inspect relevant tests under:

```text
crates/sandbox-runtime/operation/tests/**
crates/sandbox-runtime/namespace-execution/tests/**
crates/sandbox-observability/tests/**
crates/sandbox-daemon/tests/**
```

## 4. Fixed intent

Do not relitigate:

- `trace = Request.request_id`.
- The dispatch root span is `daemon.dispatch`.
- `namespace.exec.shell` is async, recorded at child-exit before finalize.
- `namespace.exec.mount_overlay` is a sync span around the waited mount.
- One-shot finalize spans are siblings of the async shell under `command.exec`.
- `layerstack.publish` is a span, not an event.
- Lease facts are events: `lease.acquired`, `lease.released`.
- Emit is best-effort, config-gated, and never changes operation behavior.
- Runtime crates may depend on `sandbox-observability`; the leaf may not depend
  on runtime/daemon/manager/config/protocol.

## 5. Review lenses

Run these lenses independently, then dedupe confirmed findings.

| # | Lens | Charge |
|---|---|---|
| L1 | **Completeness** | Walk every requirement in `span-trace-impl.md` §2-§10 and checklist §1-§22. Verify each named span/event/attr/status and every expected test exists. |
| L2 | **Trace context / parentage** | Prove `TraceContext` is set inside `spawn_blocking`, not outside; verify `daemon.dispatch` is root, runtime spans inherit thread-local parent, and one-shot finalize restores the captured `command.exec` context exactly once. |
| L3 | **Async shell lifecycle** | Verify one shared `SpanRegistry<NamespaceExecutionId>` is used by launch and terminal hook; launch failure cancels without writing; `on_terminal` records before finalize; watcher errors map to the right `SpanStatus`; no second async map/adaptor exists. |
| L4 | **Sync seams / statuses** | Verify every fallible sync seam uses `obs.scope` or equivalent error status handling. Check `daemon.dispatch` flips to `error` for fault `Response`s and `command.exec` records `one_shot`. |
| L5 | **Layerstack events / publish span** | Verify lease events attach under the enclosing span via thread-local context; `layerstack.publish` is a span with `base`, `revision`, `layers_added`, `bytes`, `no_op`, and conflict `reason`. No raw paths or command lines in attrs. |
| L6 | **Behavior preservation** | Try to find any path where disabled/missing context skips `finalize_one_shot`, changes command errors, drops cleanup, or surfaces sink errors. Observability must be removable without semantic change. |
| L7 | **Boundary / dependency** | Verify runtime manifests add only the leaf dependency; no `rusqlite` enters runtime; `sandbox-observability` still forbids daemon/runtime/manager/config/protocol; old boundary test is repointed correctly. |
| L8 | **Smallness / LOC** | Compare touched files against checklist §3 LOC budgets. Flag files over 2x the high end unless the extra code is clearly deleting older code or covering real behavior. Bias to reusing existing helpers. |

## 6. Required probes

Each final report must explicitly answer:

- Does Case A one-shot `exec_command` produce this shape?

```text
daemon.dispatch
  command.exec
    workspace_session.create
      lease.acquired
      namespace.exec.mount_overlay
    namespace.exec.shell
    workspace_session.capture_changes
    layerstack.publish
    workspace_session.destroy
      lease.released
```

- Does persistent-session `exec_command` omit create/capture/publish/destroy and
  still record the async shell?
- Does standalone `create_workspace_session` nest lease acquire and mount under
  `workspace_session.create`?
- Does rejected `destroy_workspace_session` mark only `daemon.dispatch` as error
  when `destroy_session` is never reached?
- Does shell launch failure before watcher creation write no shell span?
- With observability disabled, does one-shot finalize still capture, publish, and
  destroy exactly as before?

## 7. Finding schema

```json
{
  "lens": "L3",
  "title": "one line",
  "severity": "blocker|major|minor|nit",
  "evidence": [{ "file": "crates/.../x.rs", "lines": "120-134", "quote": "..." }],
  "spec_ref": "span-trace-impl.md §4 / checklist §11",
  "claim": "what is wrong or missing",
  "why_it_matters": "behavior, trace shape, or boundary impact",
  "recommended_change": "smallest concrete edit"
}
```

## 8. Final output

Start with one line:

```text
verdict: ship-as-is | ship-with-changes | needs-rework — biggest reason
```

Then list confirmed findings ordered by severity. Each finding must include:

- **Suggested fix:** smallest concrete edit.
- **Evidence:** file/line quote plus spec ref.
- **Reason:** why the implementation violates the expected shape or risks behavior.

End with:

- **Trace-shape verdict:** whether Case A and persistent-session shapes match.
- **Behavior verdict:** whether observability can be disabled without semantic change.
- **Boundary verdict:** whether dependency rules still hold.
- **LOC verdict:** files over budget and what to cut.

No item without confirmed code evidence survives.
