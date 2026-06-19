# `tool_call` Sandbox Protocol Findings

Date: 2026-06-18
Status: Side note for later cleanup

## Summary

`tool_call` is currently embedded in the sandbox/namespace runner request path,
but the name is agent-layer vocabulary. Inside the sandbox boundary, the
agent-facing tool invocation should be wrapped into a sandbox protocol form with
sandbox-native names. Once daemon dispatch and compatibility surfaces no longer
depend on the legacy field name, remove `tool_call` from the internal
namespace-runner request shape.

## Findings

- The namespace-runner protocol defines the agent-shaped payload as
  `ToolCall`, and `RunRequest` exposes it as `tool_call`:
  [`crates/daemon/linux-namespace-subprocess/src/protocol/mod.rs`](../../../crates/daemon/linux-namespace-subprocess/src/protocol/mod.rs#L75-L88).

- The current low-level command launch helper builds `RunRequest.tool_call`
  for command-service launches:
  [`crates/daemon/command/src/launch.rs`](../../../crates/daemon/command/src/launch.rs#L33-L65).

- The workspace namespace setns helper also constructs `ToolCall` directly for
  sandbox namespace requests:
  [`crates/daemon/workspace/src/namespace/setns_runner.rs`](../../../crates/daemon/workspace/src/namespace/setns_runner.rs#L133-L156).

- The legacy `operation::command` path still has a local `tool_call` builder and
  passes legacy command policy such as `remountable` inside the args object:
  [`crates/daemon/operation/src/command/prepare.rs`](../../../crates/daemon/operation/src/command/prepare.rs#L94-L109).

- Runner code consumes `request.tool_call` as the dispatch and argument source:
  [`fresh_ns.rs`](../../../crates/daemon/linux-namespace-subprocess/src/runner/fresh_ns.rs#L171-L177),
  [`fresh_ns/command.rs`](../../../crates/daemon/linux-namespace-subprocess/src/runner/fresh_ns/command.rs#L19-L27),
  and [`setns.rs`](../../../crates/daemon/linux-namespace-subprocess/src/runner/setns.rs#L477-L483).

- `RunRequest` is a daemon-to-namespace-runner wire DTO, not only an in-process
  helper type. The workspace setns path serializes it to the ns-runner child's
  stdin, and the command path writes the same request shape as a runner artifact:
  [`protocol/mod.rs`](../../../crates/daemon/linux-namespace-subprocess/src/protocol/mod.rs#L1-L1),
  [`setns_runner.rs`](../../../crates/daemon/workspace/src/namespace/setns_runner.rs#L206-L229),
  and
  [`pty.rs`](../../../crates/daemon/command/src/pty.rs#L300-L344).

- The request is not command-only. `RunnerVerb` also carries `plugin_service`,
  `plugin_setup`, and unknown verb passthrough, and workspace namespace helpers
  can construct arbitrary setns verbs such as overlay mount/remount operations:
  [`protocol/mod.rs`](../../../crates/daemon/linux-namespace-subprocess/src/protocol/mod.rs#L32-L45),
  [`fresh_ns.rs`](../../../crates/daemon/linux-namespace-subprocess/src/runner/fresh_ns.rs#L83-L89),
  and
  [`setns_runner.rs`](../../../crates/daemon/workspace/src/namespace/setns_runner.rs#L133-L156).

- Tests currently assert the serialized legacy field name:
  [`crates/daemon/command/src/launch.rs`](../../../crates/daemon/command/src/launch.rs#L103-L107)
  and
  [`crates/daemon/operation_service/tests/command_exec.rs`](../../../crates/daemon/operation_service/tests/command_exec.rs#L304-L310).

## Cleanup Direction

- Keep agent-level request concepts at the daemon/client boundary.
- Convert the agent tool invocation into a sandbox protocol request before it
  crosses into namespace runner code.
- Rename or replace `ToolCall` / `tool_call` in
  `linux-namespace-subprocess::protocol` with sandbox-native request vocabulary.
- Treat this as a wire-compatibility migration: support old and new serialized
  request forms, or version the runner request, until every daemon producer and
  ns-runner consumer has moved together.
- Preserve `RunnerVerb` compatibility for command, plugin, setup, setns overlay,
  remount/probe, and unknown passthrough forms during the rename.
- Preserve compatibility until daemon dispatch migration and legacy
  `operation::command` cleanup no longer require the old serialized field name.
