import { mintAgentRunId, type UserMessage } from "../contracts/index.js";
import { RunBackgroundTaskSupervisor } from "../background/index.js";
import {
  Conversation,
  RunHandle,
  runAgentLoop,
  type AgentRunHandle,
  type TerminationMode,
  type TurnFacts,
} from "../engine/index.js";
import { NotificationInbox, type Notifier } from "../notification/index.js";
import {
  HookEngine,
  buildToolExecutor,
  unwrapAgentOutcomeFn,
  type AgentOutcomeFn,
  type HookEntry,
  type TerminalGate,
  type ToolDefinition,
} from "../tool/index.js";

import {
  buildLlmClientRegistry,
  type LlmClientConfig,
  type LlmRef,
  type ResolvedLlmProfile,
} from "./llm-clients.js";
import { JsonlRunRecorder } from "./records.js";

/** Loop-turn budget when an agent spec does not pass `maxTurns`. */
const DEFAULT_MAX_TURNS = 32;
/** Bound on each `BackgroundTask.onCompletion` invocation. */
const DEFAULT_TASK_COMPLETION_TIMEOUT_MS = 30_000;

export interface AgentSdkConfig {
  /** Provider credentials/model profiles, as objects. */
  llmClients: LlmClientConfig;
  /** Global hook entries — callbacks only. */
  hooks?: HookEntry[];
  /** The SDK writes `<recordsDir>/<runId>/{events,messages}.jsonl` itself. */
  recordsDir?: string;
  /** Bounds each `BackgroundTask.onCompletion`; default 30_000. */
  taskCompletionTimeoutMs?: number;
}

/** The construction product: exactly one method. */
export interface AgentSdk {
  createAgent<T = string>(spec: AgentSpec<T>): Agent<T>;
}

/**
 * One agent template. `T` is the run's outcome payload type: the terminal
 * tool's accepted submission, or the final text (string) in text mode.
 */
export interface AgentSpec<T = string> {
  name: string;
  /** Resolves against `AgentSdkConfig.llmClients`. */
  llm: LlmRef;
  systemPrompt: string;
  /** ALL tools arrive here — the SDK ships none. */
  tools: ToolDefinition[];
  /** Absent → text termination mode (`T` stays string). */
  agentOutcomeFn?: AgentOutcomeFn<T>;
  /** Default 32. */
  maxTurns?: number;
  /** Per-agent extension of the globals. */
  hooks?: HookEntry[];
}

/** Reusable template: any number of calls, concurrent runs allowed. */
export interface Agent<T = string> {
  start(input: { messages: UserMessage[] }): AgentRunHandle<T>;
}

/**
 * Bind the process-level configuration: build every llm client eagerly
 * (config errors fail loudly here, before any agent exists) and capture
 * the global hooks and records root.
 */
export function createAgentSdk(config: AgentSdkConfig): AgentSdk {
  const llmClients = buildLlmClientRegistry(config.llmClients);
  const globalHooks = config.hooks ?? [];
  const recordsDir = config.recordsDir;
  const taskCompletionTimeoutMs =
    config.taskCompletionTimeoutMs ?? DEFAULT_TASK_COMPLETION_TIMEOUT_MS;

  return {
    createAgent<T = string>(spec: AgentSpec<T>): Agent<T> {
      // Agent-construction validation: dangling refs and name collisions
      // are template faults, surfaced before any run can start.
      const llm = llmClients.require(spec.llm);
      const outcome = spec.agentOutcomeFn
        ? unwrapAgentOutcomeFn(spec.agentOutcomeFn)
        : undefined;
      validateToolNames(spec.tools, outcome?.name);
      const hookEngine = new HookEngine([...globalHooks, ...(spec.hooks ?? [])]);
      return {
        start: (input) =>
          startRun<T>({
            spec,
            llm,
            hookEngine,
            recordsDir,
            taskCompletionTimeoutMs,
            messages: input.messages,
          }),
      };
    },
  };
}

interface StartRunArgs<T> {
  spec: AgentSpec<T>;
  llm: ResolvedLlmProfile;
  hookEngine: HookEngine;
  recordsDir: string | undefined;
  taskCompletionTimeoutMs: number;
  messages: UserMessage[];
}

/**
 * Per-run assembly, in dependency order: records sink, inbox, notifier
 * (latched after finish), task supervisor, handle (the seq-stamping
 * emitter), conversation, the terminal gate, the bound executor, and the
 * detached loop. The same supervisor and notifier instances land on the
 * handle and on every tool-call context.
 */
function startRun<T>(args: StartRunArgs<T>): AgentRunHandle<T> {
  if (args.messages.length === 0) {
    throw new TypeError("start() requires at least one user message");
  }
  const runId = mintAgentRunId();
  const recorder = args.recordsDir
    ? new JsonlRunRecorder(args.recordsDir, runId)
    : undefined;
  const inbox = new NotificationInbox();

  // The handle, supervisor, and notifier form a construction cycle; the
  // box defers the handle reference (tasks register from tool calls,
  // strictly after the handle exists, and the notifier latches on finish:
  // publishes after run end are no-ops).
  const handleRef: { current?: RunHandle<T> } = {};
  const notifier: Notifier = {
    publish(message, opts) {
      if (handleRef.current?.finished === true) return;
      inbox.publish(message, opts);
    },
  };

  const supervisor = new RunBackgroundTaskSupervisor({
    runId,
    notifier,
    completionTimeoutMs: args.taskCompletionTimeoutMs,
    emit: (event) => handleRef.current?.emit(event),
  });

  const handle = new RunHandle<T>({
    runId,
    backgroundTaskSupervisor: supervisor,
    notifier,
    ...(recorder && { tap: (event) => { recorder.event(event); } }),
  });
  handleRef.current = handle;

  const conversation = new Conversation(
    args.messages,
    recorder ? (entry) => { recorder.message(entry); } : undefined,
  );

  // The submission gate, closed over the run's live state. `submitting`
  // also denies a second terminal call after one was accepted in-batch.
  let submitting = false;
  const gate: TerminalGate = {
    blockers: () => {
      const blockers: string[] = [];
      const tasks = supervisor.count();
      if (tasks > 0) blockers.push(`${String(tasks)} background task(s) still open`);
      const notes = inbox.count();
      if (notes > 0) blockers.push(`${String(notes)} undrained notification(s)`);
      if (handle.hasPendingSteers()) blockers.push("a queued user message is pending");
      if (submitting) blockers.push("a submission was already accepted");
      return blockers;
    },
    beginFinishing: () => {
      submitting = true;
      handle.beginFinishing();
    },
    cancelFinishing: () => {
      submitting = false;
      handle.cancelFinishing();
    },
  };

  const built = buildToolExecutor<T>({
    scope: {
      runId,
      backgroundTaskSupervisor: supervisor,
      notifier,
      hooks: args.hookEngine,
    },
    tools: args.spec.tools,
    ...(args.spec.agentOutcomeFn && {
      outcome: { fn: args.spec.agentOutcomeFn, gate },
    }),
  });
  const mode: TerminationMode<T> = built.takeAccepted
    ? { kind: "terminal", takeAccepted: built.takeAccepted }
    : { kind: "text" };

  const boundaryCtx = { notifier, runId };
  const onTurnBoundary = (facts: TurnFacts): Promise<void> =>
    args.hookEngine.turnBoundary(facts, boundaryCtx);

  handle.emit({ type: "run_started", run_id: runId, agent_name: args.spec.name });
  void runAgentLoop<T>({
    handle,
    conversation,
    tools: built.executor,
    turnConfig: {
      client: args.llm.client,
      model: args.llm.model,
      systemPrompt: args.spec.systemPrompt,
      maxTokens: args.llm.maxTokens,
      reasoningEffort: args.llm.reasoningEffort,
      toolSpecs: () => built.executor.specs(),
    },
    maxTurns: args.spec.maxTurns ?? DEFAULT_MAX_TURNS,
    inbox,
    tasks: supervisor,
    onTurnBoundary,
    mode,
  });
  return handle;
}

/** Collisions silently shadow at dispatch, so they are construction faults. */
function validateToolNames(
  tools: readonly ToolDefinition[],
  terminalName: string | undefined,
): void {
  const seen = new Set<string>();
  const duplicated = new Set<string>();
  for (const tool of tools) {
    if (seen.has(tool.name)) duplicated.add(tool.name);
    seen.add(tool.name);
  }
  if (terminalName !== undefined && seen.has(terminalName)) {
    duplicated.add(terminalName);
  }
  if (duplicated.size > 0) {
    throw new Error(
      `duplicate tool name(s): ${[...duplicated].sort().join(", ")}`,
    );
  }
}
