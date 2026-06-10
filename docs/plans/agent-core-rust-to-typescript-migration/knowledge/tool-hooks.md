# Claude Code Tool Hooks (PreToolUse / PostToolUse)

Status: Observed
Date: 2026-06-10
Source path: `/Users/yifanxu/machine_learning/LoVC/c c/src`
Migration context: `eos-agent-core/` TypeScript migration reference

Companion docs: `tool-execution-pipeline.md` (where hooks sit in the
pipeline), `tool-definition-and-registry.md` (the per-tool
`preparePermissionMatcher` contract).

## Event Surface and Definition Shapes

The full hook-event union lives in `entrypoints/sdk/coreTypes.ts:25-53`
(~28 events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
`UserPromptSubmit`, `Stop`, `StopFailure`, `SubagentStart/Stop`,
`SessionStart/End`, `PreCompact`, `PostCompact`, `PermissionRequest`,
`PermissionDenied`, `Notification`, task/teammate/worktree/config events).
This doc covers only the tool-execution trio.

A hook config entry is `{ matcher?: string, hooks: HookCommand[] }`
(`schemas/hooks.ts:194`), where `matcher` matches tool names and
`HookCommand` is a discriminated union (`schemas/hooks.ts:15-189`):

| Type | Runs | Notable fields |
| --- | --- | --- |
| `command` | shell process, JSON payload on stdin | `command`, `shell?: bash\|powershell`, `timeout?` (s), `if?`, `once?`, `async?`, `asyncRewake?`, `statusMessage?` |
| `prompt` | one-shot small-model LLM eval, `$ARGUMENTS` = input JSON | `prompt`, `model?`, `timeout?`, `if?` |
| `agent` | agentic verifier (default Haiku, default 60 s) | `prompt`, `model?`, `timeout?` |
| `http` | POST of payload to URL | `url`, `headers?` with `$VAR` interpolation gated by `allowedEnvVars` |
| `callback` | in-process function (SDK/internal) | `callback(input, toolUseID, abortSignal, …)` (`types/hooks.ts:210`) |

## Runner Mechanics

`utils/hooks.ts` is the generic executor:

- Payload: hook input JSON written to stdin with trailing newline
  (`hooks.ts:1006,1210`); shape per event, e.g. PreToolUse =
  `{hook_event_name, tool_name, tool_input, tool_use_id, …base}`
  (`hooks.ts:3418`).
- Spawn: `spawn(cmd, {shell: true})` (Git-Bash resolution on Windows,
  `CLAUDE_CODE_SHELL_PREFIX` wrapping, `${CLAUDE_PLUGIN_ROOT}`-style
  substitutions) (`hooks.ts:818-984`).
- Timeout: default `TOOL_HOOK_EXECUTION_TIMEOUT_MS = 600_000` per hook
  (`hooks.ts:166`), overridable per hook in seconds; enforced by a combined
  parent-signal + timeout AbortSignal (`hooks.ts:2149`).
- Parallelism: all matching hooks for one event run via
  `Promise.all` (`hooks.ts:2744`); reported durations are wall-clock.
- Exit codes: `0` success; `2` = blocking error (the hook's stderr becomes
  model-visible feedback); other nonzero = non-blocking, surfaced to the
  user only (`hooks.ts:2617-2670`).
- stdout: parsed as JSON against the `hookJSONOutputSchema`
  (`types/hooks.ts:49-176`) — `decision`, `continue`, `stopReason`,
  `suppressOutput`, `systemMessage`, and `hookSpecificOutput`
  (`permissionDecision: allow|deny|ask`, `permissionDecisionReason`,
  `updatedInput`, `additionalContext`, `updatedMCPToolOutput`). Plain text
  is treated as message output. Schema mismatch → non-blocking error with a
  Zod hint (`hooks.ts:2504`).
- Decision precedence across parallel hooks of one event:
  `deny > ask > allow > passthrough` (`hooks.ts:2820-2847`) — all hooks
  still run after a deny; the strictest wins.

### `if` conditions

Command hooks may carry `if: "Bash(git *)"` in permission-rule syntax.
`prepareIfConditionMatcher` (`hooks.ts:1390`) parses the tool input once
via `tool.inputSchema.safeParse` + `tool.preparePermissionMatcher(input)`,
then evaluates each hook's pattern as a closure: tool-name mismatch →
no match; bare tool name → match-all; rule content → tool-specific matcher
(Bash prefix patterns etc.). Non-matching hooks are skipped without
spawning (`hooks.ts:1841`). Tools without `preparePermissionMatcher` only
support name-level matching.

### Async variants

- `async: true` — a hook may also self-declare async by printing
  `{"async": true}` as its first stdout line; it is backgrounded and
  registered in a global `AsyncHookRegistry` (`utils/hooks/
  AsyncHookRegistry.ts:253`), polled later (`checkForAsyncHookResponses`),
  default async wait 15 s.
- `asyncRewake: true` — backgrounded, and on exit code 2 enqueues a
  task-notification that wakes the model (`hooks.ts:205-245`). The abort
  handler no-ops on reason `'interrupt'` (steering) but a hard Esc cancel
  kills it.

## PreToolUse: Permission Integration

Invoked from `toolExecution.ts:800` after Zod parse + `validateInput`,
before any permission decision. `runPreToolUseHooks`
(`toolHooks.ts:435-650`) yields typed events the pipeline folds in:
`message`, `hookPermissionResult`, `hookUpdatedInput` (input replacement
without a decision), `preventContinuation`, `stopReason`,
`additionalContext`, `stop` (result already pushed, never execute).

The hook decision then goes through `resolveHookPermissionDecision`
(`toolHooks.ts:332-433`), which is the precedence kernel:

```
hook says            resulting behavior
─────────────────    ──────────────────────────────────────────────
allow                skip the interactive prompt, BUT:
                      • settings deny rule still overrides → deny   :386
                      • settings ask rule still prompts            :392
                      • requiresUserInteraction() tools still
                        prompt unless hook gave updatedInput       :353
deny                 immediate block with hook's reason; no
                     fallback to rules or dialog                   :408
ask / no decision    normal canUseTool flow; hook's reason and
                     updatedInput pre-fill the dialog              :413
```

So: a hook can auto-approve (subject to deny/ask rules), hard-deny, modify
input (both as passthrough `updatedInput` and inside an allow decision),
inject context messages, or stop the turn — and a hook `allow` can never
override a configured deny rule.

## PostToolUse and PostToolUseFailure

`runPostToolUseHooks` (`toolHooks.ts:39-191`) runs only after a
*successful* `tool.call()`, synchronously in the pipeline (the next API
call waits). Payload includes `tool_response`. Capabilities:

- inject `additionalContext` (appended as attachment messages the model
  sees alongside the tool_result);
- raise a blocking error (`hook_blocking_error` attachment — feedback the
  model must address);
- `preventContinuation` → the query loop ends the turn with
  `{reason: 'hook_stopped'}`;
- for **MCP tools only**: replace the tool output via
  `updatedMCPToolOutput` — which is why the pipeline emits MCP results
  *after* hooks but non-MCP results *before* (`toolExecution.ts:1477,1540`);
- cannot modify input (already executed).

`runPostToolUseFailureHooks` runs only in the catch path
(`toolExecution.ts:1700`), receives the error text plus an `isInterrupt`
flag (AbortError), and can only add context.

Hook progress is surfaced as `hook_progress` progress messages (filtered
out of tool-progress rendering, `Tool.ts:312`), and slow hook batches emit
timing summaries (>500 ms display threshold, ≥2 s debug log,
`toolExecution.ts:134-137`).

## Cancellation

Hooks inherit the tool's AbortSignal combined with their own timeout. On
abort the child process is killed and the runner returns
`{stderr: 'Hook cancelled', aborted: true}` (`hooks.ts:1300`). An aborted
PreToolUse batch falls through to the pipeline's abort checks; running
hooks are not detachable (except `asyncRewake`, which survives steering
interrupts by design).

## EOS Migration Takeaways

- Type hook results as a discriminated event stream, not a single return
  value — the pipeline folds 7 event kinds from one PreToolUse pass, and
  that shape keeps permission/context/stop concerns separable.
- Keep the precedence kernel explicit and centralized: per-event
  `deny > ask > allow > passthrough` across parallel hooks, and
  hook-allow < configured deny rule. This is the security-relevant logic;
  everything else is plumbing.
- Distinguish three output channels per hook: model-visible feedback
  (exit 2 / blocking error), user-visible notice (other nonzero), and
  structured decisions (stdout JSON). Don't collapse them.
- Run an event's hooks in parallel with per-hook timeout signals derived
  from the turn signal; report wall-clock, not summed, durations.
- Input mutation needs two distinct paths: passthrough `updatedInput`
  (before the permission decision) and decision-attached `updatedInput`
  (replaces input for `call()`); both must be observable to later stages.
- The MCP output-replacement capability forces result-emission ordering to
  depend on hook capability — decide early whether EOS allows hooks to
  rewrite outputs, because it shapes the executor.

## Source Anchors

- Event union: `/Users/yifanxu/machine_learning/LoVC/c c/src/entrypoints/sdk/coreTypes.ts:25`
- Hook command schemas: `/Users/yifanxu/machine_learning/LoVC/c c/src/schemas/hooks.ts:15-213`
- Hook output schema: `/Users/yifanxu/machine_learning/LoVC/c c/src/types/hooks.ts:49`
- Runner (spawn/timeout/parse/exit codes): `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/hooks.ts:747-1335,2500-2750`
- Decision precedence: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/hooks.ts:2820`
- `if` matcher: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/hooks.ts:1390`
- resolveHookPermissionDecision: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolHooks.ts:332`
- runPre/PostToolUseHooks: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolHooks.ts:435,39`
- Pipeline call sites: `/Users/yifanxu/machine_learning/LoVC/c c/src/services/tools/toolExecution.ts:800,1483,1700`
- Async registry: `/Users/yifanxu/machine_learning/LoVC/c c/src/utils/hooks/AsyncHookRegistry.ts:113-268`
