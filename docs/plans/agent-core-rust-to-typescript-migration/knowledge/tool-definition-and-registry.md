# Claude Code Tool Definition and Registry

Status: Observed
Date: 2026-06-10
Source path: `/Users/yifanxu/machine_learning/LoVC/c c/src`
Migration context: `eos-agent-core/` TypeScript migration reference

## The Tool Contract

Every tool is one object satisfying `Tool<Input, Output, Progress>`
(`Tool.ts:362`), generic over a Zod input schema, an arbitrary output type,
and a progress-payload union. There is no class hierarchy — tools are plain
object literals built by `buildTool()` and matched by name at dispatch time
(`findToolByName`, `Tool.ts:358`, which also checks `aliases` for renamed
tools).

The contract splits into four concern groups:

| Group | Members | Consumed by |
| --- | --- | --- |
| Execution | `call`, `inputSchema`, `outputSchema?`, `validateInput?`, `mapToolResultToToolResultBlockParam` | `toolExecution.ts` pipeline |
| Permission | `checkPermissions`, `preparePermissionMatcher?`, `isDestructive?`, `getPath?`, `toAutoClassifierInput` | permission system + hooks `if` matching |
| Runtime metadata | `isEnabled`, `isConcurrencySafe`, `isReadOnly`, `interruptBehavior?`, `maxResultSizeChars`, `shouldDefer?`, `alwaysLoad?`, `strict?`, `searchHint?`, `aliases?`, `isMcp?`, `mcpInfo?`, `inputsEquivalent?`, `backfillObservableInput?` | orchestrator, scheduler, ToolSearch, prompt assembly |
| Prompt + UI | `prompt`, `description`, `userFacingName`, `renderToolUseMessage`, `renderToolResultMessage?`, `renderToolUseProgressMessage?`, `renderGroupedToolUse?`, `extractSearchText?`, ~10 more optional render hooks | system-prompt builder and React/Ink UI only |

`call()` is the single execution entry point (`Tool.ts:379`):

```ts
call(args, context: ToolUseContext, canUseTool, parentMessage,
     onProgress?) : Promise<ToolResult<Output>>
```

`ToolResult<T>` (`Tool.ts:321`) carries `data` (typed output), optional
`newMessages` (extra conversation messages the tool injects), an optional
`contextModifier` (a `ToolUseContext → ToolUseContext` function, only honored
for non-concurrency-safe tools), and optional `mcpMeta` passthrough.

## Runtime Metadata Semantics

These flags are what the scheduler and interrupt machinery actually branch
on; they are the part worth replicating precisely.

| Flag | Default (`buildTool`) | Effect |
| --- | --- | --- |
| `isConcurrencySafe(input)` | `false` | Batch partitioning: safe tools run in parallel, unsafe tools run alone (`toolOrchestration.ts:91`). Input-dependent — Bash returns true only for read-only commands. |
| `isReadOnly(input)` | `false` | Permission fast-paths and UI condensation. |
| `isDestructive(input)` | `false` | Set only for irreversible ops (delete/overwrite/send). |
| `interruptBehavior()` | `'block'` when absent | On a steering interrupt (`abort reason 'interrupt'`), `'cancel'` tools are killed with a synthetic rejection; `'block'` tools run to completion (`StreamingToolExecutor.ts:223`). |
| `isEnabled()` | `true` | Filters the registry per environment. |
| `maxResultSizeChars` | required | Result larger than this is persisted to disk and replaced by a `<persisted-output>` preview (`utils/toolResultStorage.ts:208`). `Infinity` = never persist (Read — persisting would create a circular Read→file→Read loop). |
| `shouldDefer` / `alwaysLoad` | – | ToolSearch deferral: deferred tools are sent with `defer_loading: true` and must be loaded via ToolSearch before calls validate (`Tool.ts:442`). |
| `strict` | – | Opts into API strict schema adherence. |
| `aliases` | – | Backwards-compatible lookup after renames (old transcripts keep working; alias fallback at `toolExecution.ts:351`). |
| `backfillObservableInput(input)` | – | Mutates a *clone* of tool_use input before observers (SDK stream, transcript, hooks, canUseTool) see it; the original API-bound input is never mutated to preserve prompt-cache bytes (`Tool.ts:474`). |
| `inputsEquivalent(a, b)` | – | Dedup for permission decisions across retries. |

## buildTool Defaults

`buildTool()` (`Tool.ts:783`) spreads `TOOL_DEFAULTS` under the definition so
all 60+ tools share one fail-closed default set (`Tool.ts:757`):

- `isEnabled → true`, `isConcurrencySafe → false`, `isReadOnly → false`,
  `isDestructive → false` (assume writes, assume unsafe);
- `checkPermissions → allow` (defers to the general permission system in
  `permissions.ts`; tool-specific logic only when overridden);
- `toAutoClassifierInput → ''` (skip the auto-mode security classifier;
  security-relevant tools must override);
- `userFacingName → name`.

`ToolDef` (`Tool.ts:721`) is the authoring type: the same shape with the
defaultable keys optional; `BuiltTool<D>` reconstructs exact literal types so
call sites keep narrow types without `satisfies Tool` boilerplate.

## Registry and Pool Assembly

```
getAllBaseTools()  tools.ts:193   exhaustive, env/feature-gated list
   │  (order is cache-load-bearing: must stay in sync with the server-side
   │   system-prompt cache config — comment at tools.ts:191)
   ▼
getTools(permissionContext)  tools.ts:271
   ├─ CLAUDE_CODE_SIMPLE mode → only Bash/Read/Edit
   ├─ remove special tools (ListMcpResources, ReadMcpResource, SyntheticOutput)
   ├─ filterToolsByDenyRules: blanket deny rules strip tools BEFORE the
   │   model ever sees them, using the same matcher as runtime checks, so
   │   `mcp__server` rules strip a whole server (tools.ts:262)
   ├─ REPL mode: hide primitive tools wrapped by the REPL VM
   └─ isEnabled() filter
   ▼
assembleToolPool(permissionContext, mcpTools)  tools.ts:345
   ├─ deny-filter MCP tools
   ├─ sort each partition by name, built-ins as contiguous prefix
   │   (prompt-cache stability: a flat sort would interleave MCP tools into
   │    built-ins and invalidate downstream cache keys — tools.ts:354)
   └─ uniqBy name, built-ins win conflicts
```

Conditional registration uses build-time `feature()` gates plus lazy
`require()` so unused tools are dead-code-eliminated and circular imports
broken (`tools.ts:14-156`). Mid-session MCP connects are handled by
`ToolUseContext.options.refreshTools`, polled between turns
(`query.ts:1660`).

## ToolUseContext: the Ambient Runtime

`ToolUseContext` (`Tool.ts:158`) is the dependency-injection record threaded
through every call. Key fields for a runtime port:

- `options`: tools list, mainLoopModel, mcpClients, agentDefinitions,
  `isNonInteractiveSession`, `refreshTools`;
- `abortController`: the per-turn (or per-tool, under the streaming
  executor) cancellation root;
- `getAppState`/`setAppState` plus `setAppStateForTasks` — the latter always
  reaches the root store so deeply nested subagents can register background
  tasks that outlive their own turn (`Tool.ts:184`);
- `readFileState`: LRU file-freshness cache backing Edit's
  read-before-write rule;
- `setInProgressToolUseIDs`, `setHasInterruptibleToolInProgress`: UI/loop
  signals;
- `agentId`/`agentType`: set only for subagents — used by hooks and to drop
  heavyweight `toolUseResult` payloads from subagent transcripts;
- `toolDecisions`: per-toolUseID map recording accept/reject + source for
  telemetry, deleted after the tool finishes (`toolExecution.ts:1741`);
- `messages`: the current conversation view (reassigned each iteration).

The context is immutable-by-convention: tools return `contextModifier`
functions instead of mutating, and the orchestrator decides when to fold
them in.

## EOS Migration Takeaways

- Model a tool as one flat record: Zod input schema + async `call` +
  metadata predicates. Skip the ~15 UI render hooks; keep `userFacingName`
  and an activity string.
- Make the metadata predicates input-dependent functions, not static booleans
  — `isConcurrencySafe(input)` / `isReadOnly(input)` for Bash-like tools is
  what makes safe batching possible at all.
- Centralize fail-closed defaults in one `buildTool`-style constructor so a
  forgotten override degrades to "serial, unsafe, ask" rather than
  "parallel, silent, allow".
- Keep registry assembly deterministic and sorted with built-ins as a stable
  prefix if prompt caching is in play; deny-filter before prompt assembly,
  not just at call time.
- Thread one context record through all calls; expose state via
  getter/updater pairs, and give detached work an always-root state writer
  (`setAppStateForTasks` pattern).
- `maxResultSizeChars` + disk persistence belongs on the tool contract; the
  one tool that must never persist (Read) expresses that as `Infinity`, not
  a special case in the storage layer.

## Source Anchors

- Tool type: `/Users/yifanxu/machine_learning/LoVC/c c/src/Tool.ts:362`
- ToolResult: `/Users/yifanxu/machine_learning/LoVC/c c/src/Tool.ts:321`
- ToolUseContext: `/Users/yifanxu/machine_learning/LoVC/c c/src/Tool.ts:158`
- buildTool + defaults: `/Users/yifanxu/machine_learning/LoVC/c c/src/Tool.ts:757`
- interruptBehavior contract: `/Users/yifanxu/machine_learning/LoVC/c c/src/Tool.ts:407`
- backfillObservableInput contract: `/Users/yifanxu/machine_learning/LoVC/c c/src/Tool.ts:474`
- Registry: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools.ts:193`
- Deny filtering: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools.ts:262`
- Pool assembly + cache-stable sort: `/Users/yifanxu/machine_learning/LoVC/c c/src/tools.ts:345`
- Result persistence threshold: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/toolResultStorage.ts:208`
