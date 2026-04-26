# Query Engine

The query engine streams one model response for one ephemeral agent run. A run
has exactly one provider request shaped as:

1. system prompt
2. user prompt
3. assistant response

Tool calls are executed as side effects of the assistant response and surfaced as
stream events. Tool results are not appended as synthetic `user` messages and
are not fed back into a second model request in the same run.

## Overall Architecture

The engine consists of three layers:

- `EphemeralAgent`: owns the request-local message list and appends the single
  user prompt before calling `run_query`.
- `QueryContext`: carries model, tools, execution metadata, terminal tools, and
  exit state for the run.
- `_run_query_loop`: builds one `ApiMessageRequest`, consumes one provider
  stream, executes any requested tools, records prompt-report events, and exits.

```
EphemeralAgent.run(prompt)
        |
        v
messages = [ConversationMessage(role="user")]
        |
        v
run_query(context, messages)
        |
        v
build_query_run_request()
        |
        v
api_client.stream_message(ApiMessageRequest)
        |
        +--> ThinkingDelta / AssistantTextDelta stream events
        |
        +--> ApiToolUseDeltaEvent -> StreamingToolExecutor
        |
        +--> ApiMessageCompleteEvent
        |
        v
append assistant message
        |
        v
dispatch remaining tool calls
        |
        v
append system notifications to messages and emit SystemNotification events
        |
        v
set QueryExitReason and return
```

## Message Shape

The durable message list for a normal run starts with:

- the user-authored prompt
- the assistant message returned by the provider

When tools or hooks emit system notifications, the query loop appends a
synthetic `user` message containing `SystemNotificationBlock` content at the
safe post-dispatch flush point. Provider serialization wraps those blocks in
`<system-notification>` tags so a later run that restores the transcript can
distinguish engine-generated context from user-authored text.

Runtime context such as cwd, date, environment, issue context, and PR comment
context is appended to the system prompt. It is not injected as an additional
`user` message.

Tool results are recorded in prompt reports via `record_tool_results`, but they
are not added to `messages`. This preserves the fixed provider-request shape and
prevents accidental continuation prompts.

## Tool Execution

Tools may still execute mid-stream when the provider emits `tool_use` deltas.
The execution path is:

1. mode and budget gates reject invalid tool calls before execution
2. `StreamingToolExecutor` starts non-deferred tools as soon as their input is
   available
3. deferred background tools are launched through `BackgroundTaskManager`
4. after the provider stream completes, remaining tool tasks are drained
5. `ToolExecutionCompleted`, `ToolExecutionCancelled`, and `SystemNotification`
   events are emitted to the caller

Terminal tools are batch-exclusive. A successful terminal tool sets
`QueryContext.terminal_result` and exits with `QueryExitReason.TOOL_STOP`.

Non-terminal tool calls do not create a second model request. If a run executes
only non-terminal tools and no terminal tool succeeds, the run exits without a
terminal result. Team runtime callers treat that as an agent that did not submit
through its required terminal path.

## Background Tasks

Background-capable tools can launch work asynchronously during the single
assistant response. The engine emits `BackgroundTaskStarted` immediately.

Because there is no follow-up provider request in the same run, background task
completion is not auto-delivered back to the model. Pending background tasks are
cancelled when the run exits unless they have already completed and surfaced
through runtime events or explicit background control tools in the same assistant
response.

## Exit Conditions

The query loop exits after one assistant response. The exit reason is:

- `TEXT_RESPONSE`: the assistant returned text or non-terminal tool calls only
- `TOOL_STOP`: a terminal tool succeeded
- `RESOURCE_LIMIT`: the tool budget was exhausted during tool dispatch

There is no terminal nudge retry and no no-tool continuation loop. Recovery and
replanning must be represented as a fresh agent run with a fresh prompt.
