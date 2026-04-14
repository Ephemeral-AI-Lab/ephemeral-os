# Query Engine

The core loop that streams LLM responses, executes tools mid-stream, manages background tasks, and compacts conversation history for API submissions.

## Overall Architecture

The query engine consists of three layers: the `EphemeralAgent` runtime wrapper, the `QueryContext` configuration container, and the `_run_query_loop` state machine that drives message cycles. Each cycle streams from the LLM, executes tools (either immediately or deferred to background), collects results, and feeds them back into the next turn until the agent has no more tool calls.

```mermaid
graph TB
    Agent["EphemeralAgent.run(prompt)"]
    Agent -->|appends user message| DisplayMessages["display_messages<br/>(mutable list)"]
    Agent -->|passes to| RunQuery["run_query()"]
    
    RunQuery -->|creates stamped event iterator| QueryLoop["_run_query_loop()"]
    QueryLoop -->|infinite while True| LoopBody["Main Loop Iteration"]
    
    LoopBody -->|compacts history| Compact["compact_for_api()"]
    Compact -->|builds api_messages| APIRequest["ApiMessageRequest"]
    APIRequest -->|streams from| LLM["api_client.stream_message()"]
    
    LLM -->|yields events| EventHandler["Event Handler<br/>(switch on event type)"]
    EventHandler -->|TextDelta| Text["yield AssistantTextDelta"]
    EventHandler -->|ToolUseDelta| ToolAdd["executor.add_tool()"]
    EventHandler -->|MessageComplete| Final["capture final_message<br/>+ usage"]
    
    ToolAdd -->|concurrent execution| Executor["StreamingToolExecutor"]
    Executor -->|tracks| TrackedTool["TrackedTool objects"]
    TrackedTool -->|execution result| ToolComplete["yield ToolExecutionCompleted"]
    
    Final -->|no tool_uses?| NoTools{"Has pending<br/>background?"}
    NoTools -->|yes| WaitBG["wait_any(timeout)"]
    NoTools -->|no| Return["return (exit loop)"]
    
    Final -->|has tool_uses| Dispatch["Dispatch Tool Results"]
    Dispatch -->|validates batch| BatchCheck["validate_tool_batch()"]
    Dispatch -->|budget exceeded?| BudgetStop["yield error<br/>return"]
    
    Dispatch -->|foreground tools| FGExec["execute sequentially<br/>or in parallel"]
    FGExec -->|single| SingleSeq["await execute_tool_call()"]
    FGExec -->|multiple| ParallelExec["asyncio.gather()"]
    
    Dispatch -->|background tools| BGLaunch["launch_background_tool()"]
    BGLaunch -->|creates task| BGMgr["BackgroundTaskManager"]
    BGMgr -->|tracks| BGTask["TrackedBackgroundTask"]
    
    SingleSeq -->|append result| DisplayMessages
    ParallelExec -->|append results| DisplayMessages
    BGLaunch -->|append result| DisplayMessages
    
    DisplayMessages -->|loop continues| Compact
    
    style Agent fill:#e1f5ff
    style QueryLoop fill:#fff3e0
    style LLM fill:#f3e5f5
    style Executor fill:#e8f5e9
    style BGMgr fill:#fce4ec
```

## Message and Tool Result Flow

Each turn produces a sequence of `StreamEvent` objects flowing from the LLM stream through execution, collection, and finally into `display_messages` for the next cycle. Tools yield intermediate progress, completion, or cancellation events that structure the conversation state.

```mermaid
sequenceDiagram
    participant Loop as Query Loop
    participant LLM as API Stream
    participant Executor as StreamingToolExecutor
    participant ToolExec as Tool Execution
    participant Msg as display_messages
    
    Loop->>LLM: stream_message(ApiMessageRequest)
    LLM-->>Loop: ApiTextDeltaEvent / ApiThinkingDeltaEvent
    Loop->>Executor: add_tool(ApiToolUseDeltaEvent)
    Executor->>ToolExec: _start_tool() [async]
    ToolExec-->>Loop: ToolExecutionProgress (optional)
    Loop->>Executor: get_progress()
    Executor-->>Loop: [ToolExecutionProgress, ...]
    
    LLM-->>Loop: ApiMessageCompleteEvent
    Loop->>Loop: await executor.get_remaining()
    Executor-->>Loop: [ToolExecutionCompleted | ToolExecutionCancelled, ...]
    
    Loop->>Msg: append ConversationMessage(role=assistant)
    Loop->>Msg: append ConversationMessage(role=user, content=[ToolResultBlock, ...])
    
    activate Msg
    Msg->>Msg: grows with tool results
    deactivate Msg
    
    Loop->>LLM: compact_for_api(display_messages)
    LLM-->>Loop: [api_messages...]
    Note over Loop: Next iteration uses compacted history
```

## Tool Execution Dispatch

When the LLM sends tool calls, the loop must decide: execute immediately in the foreground, defer to background, or reject due to batch validation or budget. The `StreamingToolExecutor` handles mid-stream tool starts; deferred tools bypass it and go through the background path.

```mermaid
graph TD
    ToolUse["Tool Use Block arrives<br/>in LLM stream"]
    
    ToolUse -->|executor.add_tool| Check1{"should_defer<br/>returns true?"}
    Check1 -->|yes| Defer["Mark in _deferred set<br/>return None<br/>(do not execute)"]
    Check1 -->|no| CheckInput{"Input complete<br/>and valid?"}
    
    CheckInput -->|no| Queue["Track as queued<br/>(wait for more deltas)"]
    CheckInput -->|yes| Start["_start_tool() creates<br/>asyncio.Task"]
    
    Start -->|concurrent-safe| RunAsync["await _execute_tool()"]
    Start -->|not safe| Queue2["Track as executing<br/>(sequential)"]
    
    RunAsync -->|run_tool_safely| ToolCall["tool_def.run(input)"]
    ToolCall -->|success| Result["ToolResult<br/>(output, is_error=False)"]
    ToolCall -->|error| Error["ToolResult<br/>(error msg, is_error=True)"]
    
    Result -->|after stream ends| GetRemaining["executor.get_remaining()"]
    Error -->|after stream ends| GetRemaining
    
    GetRemaining -->|iterate _tools| Yield["yield ToolExecutionCompleted<br/>or ToolExecutionCancelled"]
    
    Defer -->|after stream ends| BGPath["Background Dispatch Path"]
    BGPath -->|launch_background_tool| BGCheck{"background_preflight<br/>check"}
    BGCheck -->|preflight fails| BGReject["Yield error result<br/>do not launch"]
    BGCheck -->|preflight passes| BGLaunch["manager.launch()<br/>creates TrackedBackgroundTask"]
    BGLaunch -->|asyncio task runs| BGRun["tool runs async<br/>in parallel"]
    
    Yield -->|append to tool_results| ToolResults["accumulate ToolResultBlock"]
    BGReject -->|append to tool_results| ToolResults
    BGLaunch -->|yield BackgroundTaskStarted| ToAgent["agent sees task started"]
    
    style ToolUse fill:#fff9c4
    style Defer fill:#ffccbc
    style Start fill:#c8e6c9
    style BGPath fill:#f8bbd0
```

## Background Task Lifecycle

Background tools launch async tasks tracked by `BackgroundTaskManager`. Tasks can run concurrently with the next LLM turn. The loop polls via `collect_completed()` at the start of each iteration to deliver finished tasks back to the agent.

```mermaid
stateDiagram-v2
    [*] --> RUNNING: manager.launch()
    
    RUNNING --> COMPLETED: task completes successfully
    RUNNING --> FAILED: task raises exception
    RUNNING --> CANCELLED: cancel() called or task.cancel()
    
    COMPLETED --> DELIVERED: collect_completed() returns task
    FAILED --> DELIVERED: collect_completed() returns task
    CANCELLED --> DELIVERED: collect_completed() returns task
    
    DELIVERED --> [*]
    
    note right of RUNNING
        asyncio_task is active
        progress_lines append on demand
        can be queried via check_background_progress
    end note
    
    note right of COMPLETED
        Terminal state
        result captured in task.result
        Waiting for engine to deliver
    end note
    
    note right of DELIVERED
        collect_completed() marks this
        agent sees BackgroundTaskCompleted event
        Removed from polling in next iteration
    end note
```

## Loop Termination and Exit Conditions

The query loop exits when: (1) the LLM sends no tool calls and no background tasks are pending, (2) the tool call budget is exhausted, or (3) a fatal error occurs. Background tasks waiting to complete can extend the loop past a no-tool turn.

```mermaid
graph TD
    FinalMsg{"final_message<br/>captured from<br/>stream"}
    
    FinalMsg -->|has tool_uses| HasTools["Process tool results<br/>and append to display_messages"]
    FinalMsg -->|no tool_uses| NoTools["Check background<br/>task status"]
    
    HasTools -->|tool_call_limit exceeded| BudgetExit["yield budget error<br/>return (EXIT)"]
    HasTools -->|limit OK| Loop["loop continues<br/>append results<br/>next iteration"]
    
    NoTools -->|background_manager is None<br/>or no pending| Exit1["return (EXIT)<br/>no more work"]
    NoTools -->|has pending background| WaitBG["await wait_any()<br/>timeout=30s"]
    
    WaitBG -->|task completed| Deliver["deliver_completed_background_task()<br/>append to display_messages<br/>yield event"]
    WaitBG -->|timeout| Reminder["append_and_emit_reminder()<br/>agent sees progress update"]
    
    Deliver -->|loop continues| Loop
    Reminder -->|loop continues| Loop
    
    BudgetExit -->|cancel all pending| CancelBG["background_manager.cancel_all()"]
    CancelBG --> EndNode(["END"])
    Exit1 --> EndNode
    
    style FinalMsg fill:#fff3e0
    style BudgetExit fill:#ffcccc
    style WaitBG fill:#ffe0b2
    style Loop fill:#c8e6c9
```

## Integration Points: Hooks, External Triggers, and Snapshots

The query loop integrates with external systems via `QueryContext` fields: `hook_executor` for post-run submissions, `on_turn` callbacks for live progress, and `api_messages_snapshot` for compaction state inspection.

```mermaid
graph LR
    QueryLoop["Query Loop<br/>_run_query_loop()"]
    
    QueryLoop -->|on_turn callback| OnTurnCB["on_turn(display_messages)"]
    OnTurnCB -->|user-provided| External1["Live progress tracking<br/>e.g. UI streaming"]
    
    QueryLoop -->|hook_executor| HookExec["hook_executor<br/>(from QueryContext)"]
    HookExec -->|used by posthook layer| PostRun["Post-run submission phase<br/>(outside query loop)"]
    PostRun -->|submits results| External2["External tool backends<br/>e.g. file operations"]
    
    QueryLoop -->|snapshots| APISnapshot["context.api_messages_snapshot<br/>(before each LLM call)"]
    APISnapshot -->|compact_for_api| Compaction["SessionState tracking<br/>(compaction module)"]
    Compaction -->|auditing| External3["Message compaction history<br/>token usage tracking"]
    
    QueryLoop -->|agent metadata| AgentStamp["agent_name, work_id<br/>stamped on events"]
    AgentStamp -->|multiplexing| External4["Session relay / agent pools<br/>multi-agent coordination"]
    
    style QueryLoop fill:#fff3e0
    style External1 fill:#c5cae9
    style External2 fill:#d1c4e9
    style External3 fill:#b2dfdb
    style External4 fill:#f1c6e8
```

## Conversation State and Streaming

The `display_messages` list is the source of truth for conversation history. Each `ConversationMessage` contains a role (assistant or user) and content blocks: text, tool uses (from LLM), or tool results (execution feedback). Streaming compaction happens before each API call to manage token budgets.

```mermaid
graph TB
    subgraph History["display_messages (Append-Only)"]
        M1["ConversationMessage<br/>role: user<br/>content: [TextBlock]"]
        M2["ConversationMessage<br/>role: assistant<br/>content: [TextBlock,<br/>ToolUseBlock, ...]"]
        M3["ConversationMessage<br/>role: user<br/>content: [ToolResultBlock,<br/>ToolResultBlock]"]
        M4["ConversationMessage<br/>role: assistant<br/>..."]
        
        M1 --> M2
        M2 --> M3
        M3 --> M4
    end
    
    History -->|passed to| Compact["compact_for_api()<br/>(compaction module)"]
    Compact -->|applies SessionState<br/>summarization rules| APIMessages["api_messages<br/>(compacted copy)"]
    
    APIMessages -->|sent to| LLM["LLM API"]
    LLM -->|returns| NewMessage["final_message"]
    
    NewMessage -->|appended| M4
    
    style M1 fill:#e1f5ff
    style M2 fill:#f3e5f5
    style M3 fill:#fff3e0
    style M4 fill:#f3e5f5
    style Compact fill:#e8f5e9
    style APIMessages fill:#fce4ec
```

## Agent Runtime Wrapper

The `EphemeralAgent` is spawned per request by `spawn_agent()`, wrapping the query loop with agent-specific config: model, toolkits, system prompt, and budget. It owns the mutable `display_messages` list and exposes a read-only `display_messages` property to callers.

```mermaid
graph TD
    Spawn["spawn_agent(config, messages,<br/>agent_def, session_state, sandbox_id)"]
    
    Spawn -->|resolve identity| Identity["_resolve_agent_identity()"]
    Identity -->|db lookup| Model["resolved_model<br/>api_client"]
    
    Spawn -->|build registry| Registry["_build_agent_tool_registry()"]
    Registry -->|load toolkits| Toolkits["skill_registry<br/>daytona_toolkit<br/>background_toolkit"]
    
    Spawn -->|build prompt| Prompt["_build_agent_system_prompt()"]
    Prompt -->|finalize with awareness| Awareness["finalize_tool_registry_and_prompt()"]
    Awareness -->|inject capability text| SystemPrompt["system_prompt"]
    
    Spawn -->|create context| QC["QueryContext<br/>(api_client, tool_registry,<br/>system_prompt, model, ...)"]
    
    QC -->|wrap| Agent["EphemeralAgent<br/>(agent_name, query_context,<br/>_display_messages=[...])"]
    
    Agent -->|expose property| DisplayProp["display_messages<br/>(read-only view)"]
    Agent -->|async method| Run["run(prompt)<br/>-> AsyncIterator[StreamEvent]"]
    
    Run -->|calls| RunQuery["run_query(context,<br/>display_messages)"]
    RunQuery -->|stamped events| EventIter["stamped_event_iterator"]
    EventIter -->|yields| ToCallers["caller receives events<br/>(with agent_name, work_id)"]
    
    style Spawn fill:#c8e6c9
    style Identity fill:#fff9c4
    style Registry fill:#ffe0b2
    style Prompt fill:#f8bbd0
    style Agent fill:#e1f5ff
    style Run fill:#f3e5f5
```
