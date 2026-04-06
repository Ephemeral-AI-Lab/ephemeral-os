# opencode On-Flight Auto-Compaction

Study of how `anomalyco/opencode` (branch `dev`) compacts context within the agent loop.
All file references are under `packages/opencode/src/session/`.

## 1. Trigger / threshold — `overflow.ts`

```ts
const COMPACTION_BUFFER = 20_000
export function isOverflow({ cfg, tokens, model }) {
  if (cfg.compaction?.auto === false) return false
  const context = model.limit.context
  if (context === 0) return false
  const count = tokens.total
    || tokens.input + tokens.output + tokens.cache.read + tokens.cache.write
  const reserved = cfg.compaction?.reserved
    ?? Math.min(COMPACTION_BUFFER, ProviderTransform.maxOutputTokens(model))
  const usable = model.limit.input
    ? model.limit.input - reserved
    : context - ProviderTransform.maxOutputTokens(model)
  return count >= usable
}
```

- Threshold = `model.limit.input - reserved` (or `context - maxOutput` if no input limit).
- `reserved` defaults to ~20k (or `compaction.reserved` config).
- Token count comes from the provider's usage report on the prior assistant turn
  (input + output + cache read + cache write).
- Model limits come from the provider registry (`Provider.Model.limit.{context,input}`).

## 2. Mid-stream detection — `processor.ts`

After every stream event, the handler checks usage and flips a flag:

```ts
// processor.ts:305
if (!ctx.assistantMessage.summary
    && isOverflow({ cfg, tokens: usage.tokens, model: ctx.model })) {
  ctx.needsCompaction = true
}
```

The flag is also set when the provider raises `ContextOverflowError`:

```ts
// processor.ts:416
if (MessageV2.ContextOverflowError.isInstance(error)) {
  ctx.needsCompaction = true
  ...
}
```

The current stream is short-circuited and the step returns the sentinel `"compact"`:

```ts
// processor.ts:456-488
yield* stream.pipe(
  Stream.tap(handleEvent),
  Stream.takeUntil(() => ctx.needsCompaction),
  Stream.runDrain,
)
...
if (ctx.needsCompaction) return "compact"
```

In-flight tool calls that were running when the stream was aborted are marked
errored in `cleanup()` with `state.status = "error"` and message
`"Tool execution aborted"` (`processor.ts:399-411`).

## 3. Outer loop dispatch — `prompt.ts`

```ts
// prompt.ts:1542
if (result === "compact") {
  yield* compaction.create({
    sessionID,
    agent: lastUser.agent,
    model: lastUser.model,
    auto: true,
    overflow: !handle.message.finish, // true if mid-step abort
  })
}
```

`compaction.create` (`compaction.ts:349-372`) appends a synthetic user message
with a `"compaction"` task part to the session history. On the next iteration
of the outer prompt loop, that part is picked up as a pending task:

```ts
// prompt.ts:1400
if (task?.type === "compaction") {
  const result = yield* compaction.process({
    messages: msgs,
    parentID: lastUser.id,
    sessionID,
    auto: task.auto,
    overflow: task.overflow,
  })
  if (result === "stop") break
  continue
}
```

There is also a **proactive pre-step check** that fires before sending the next
request, using the just-finished message's tokens:

```ts
// prompt.ts:1412
if (lastFinished && lastFinished.summary !== true
    && (yield* compaction.isOverflow({ tokens: lastFinished.tokens, model }))) {
  yield* compaction.create({ sessionID, agent, model, auto: true })
  continue
}
```

## 4. The summarizer call — `compaction.ts:141-272`

- Uses a dedicated `"compaction"` agent (`agents.get("compaction")`).
- Model defaults to the user's current model unless the agent overrides it.
- Takes the **entire prior message history** via `structuredClone(messages)`, runs
  `MessageV2.toModelMessagesEffect(msgs, model, { stripMedia: true })` so images
  and binary attachments are stripped (otherwise the summarizer call itself
  could overflow).
- Appends one synthetic user turn with this default prompt (plugin-overridable
  via `experimental.session.compacting`):

```
Provide a detailed prompt for continuing our conversation above.
Focus on information that would be helpful for continuing the conversation...
Do not call any tools. Respond only with the summary text...

## Goal
## Instructions
## Discoveries
## Accomplished
## Relevant files / directories
```

- Sent with **`tools: {}`** and **`system: []`** — no tools, no system prompt.
- The output is stored as an assistant message tagged
  `summary: true, mode: "compaction", agent: "compaction"`.

### Overflow replay path

If `overflow: true` (the provider hard-rejected the request, commonly because
of oversized media), `processCompaction` rewinds to the **user message before**
`parentID` and replays it *after* the summary, rewriting media parts to
`"[Attached mime: file]"`:

```ts
// compaction.ts:161-177
if (input.overflow) {
  // walk back to find the user message before parentID
  // messages = input.messages.slice(0, i); replay = that user msg
}
// compaction.ts:286-313
// re-emits the replay user message with media parts stripped
```

Otherwise it injects a synthetic user nudge so the next iteration can continue:

```
Continue if you have next steps, or stop and ask for clarification...
```

## 5. What is preserved vs dropped

- **Session DB:** everything is kept — nothing is destroyed on disk.
- **Next request context:** built only from messages **after** the
  `summary: true` marker. Both `prune` (`compaction.ts:108-127`) and
  `processCompaction` (`159-177`) walk until they hit a `summary`/`compaction`
  marker and stop there.
- **Protected from pruning:** the last two user turns are never pruned
  (`if (turns < 2) continue`, `compaction.ts:110-111`).
- **Dropped from context:** all earlier turns and tool outputs prior to the
  summary message, media attachments stripped, in-flight tool calls marked
  errored.

## 6. Lightweight tool-output prune — `compaction.ts:35-139`

A separate, cheaper mechanism independent of full summarization. It runs
**in the background after every successful step** (`prompt.ts:1563`):

```ts
yield* compaction.prune({ sessionID }).pipe(
  Effect.ignore,
  Effect.forkIn(scope),
)
```

Behavior:

- Walks parts backwards, keeping the most recent ~`PRUNE_PROTECT = 40_000`
  tokens of tool outputs.
- Skills-tool outputs (`PRUNE_PROTECTED_TOOLS = ["skill"]`) are never pruned.
- Older completed tool outputs get `state.time.compacted = Date.now()` stamped
  — the content is erased but message structure stays intact.
- Only fires if the amount pruned ≥ `PRUNE_MINIMUM = 20_000` tokens.
- Stops at any `summary` message.
- Disabled via `cfg.compaction.prune === false`.

## 7. Sync vs async

| Mechanism | Timing | Blocks loop? |
|---|---|---|
| Full summarization (`compaction.process`) | Between loop iterations | **Yes — synchronous** |
| Tool-output prune (`compaction.prune`) | After each successful step | **No — forked in scope** |
| Mid-stream detection | During stream events | Aborts current step only |

The agent loop never stops mid tool call: overflow flips a flag, the current
stream drains/aborts, the step returns `"compact"`, the outer loop enqueues a
compaction task, processes it synchronously on the next tick, then resumes
normal stepping with the summary as the new context root.

## 8. Key files and line references

- `packages/opencode/src/session/overflow.ts` — threshold formula
- `packages/opencode/src/session/processor.ts:305,416,456-488` — mid-stream trigger
- `packages/opencode/src/session/compaction.ts`
  - `35-139` — background `prune`
  - `141-347` — `processCompaction` summarizer
  - `189-217` — default summarizer prompt
  - `349-372` — `create` (enqueue task message)
- `packages/opencode/src/session/prompt.ts`
  - `1400` — task dispatch
  - `1412` — proactive pre-step check
  - `1542` — handling `"compact"` step result
  - `1563` — background prune fork

## 9. Design takeaways for EphemeralOS

1. **Two-tier strategy.** A cheap background prune that erases old tool outputs
   handles the common case; a full LLM-summarization only runs when token
   budget is actually exceeded.
2. **Sentinel return from the step function.** The inner stream handler doesn't
   perform compaction itself — it sets a flag, aborts the stream, and returns
   a sentinel so the outer loop owns the decision. This keeps the step function
   side-effect-free and makes compaction a first-class loop transition.
3. **Enqueue via synthetic user message.** Compaction is represented as a task
   part on a user message, so it flows through the same dispatch as normal
   tasks — no separate state machine.
4. **Strip media before summarizing.** The summarizer call itself could
   overflow; stripping media and using no tools / no system prompt bounds it.
5. **Protect recent turns and skill outputs.** Last 2 user turns and any
   skill-tool output are never pruned — preserves the task frame.
6. **Overflow replay.** If the provider rejected the request outright, the
   last user turn is re-injected *after* the summary so no user intent is lost.
7. **Synchronous at loop boundary, async everywhere else.** Full summarization
   blocks between iterations (correctness); prune runs forked (latency).
