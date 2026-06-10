import {
  JsonObjectSchema,
  agentRunIdFrom,
  sandboxIdFrom,
  type AgentKind,
  type JsonObject,
} from "@eos/contracts";
import {
  defineTool,
  type AgentRunState,
  type ToolCallContext,
  type ToolDefinition,
  type ToolOutcome,
} from "@eos/tool";

/** A scripted definition: permissive JSON-object input, flags opt-in. */
export function scriptedTool(options: {
  name: string;
  execute: (input: JsonObject, ctx: ToolCallContext) => Promise<ToolOutcome>;
  description?: string;
  isTerminal?: boolean;
  isBatchExecutionForbidden?: boolean;
  availableInIsolatedWorkspace?: boolean;
}): ToolDefinition {
  return defineTool({
    name: options.name,
    description: options.description ?? options.name,
    input: JsonObjectSchema,
    isTerminal: options.isTerminal,
    isBatchExecutionForbidden: options.isBatchExecutionForbidden,
    availableInIsolatedWorkspace: options.availableInIsolatedWorkspace,
    execute: options.execute,
  });
}

/** An `AgentRunState` with placeholder facts and a live workspace cell. */
export function scriptedRunState(
  kind: AgentKind = "main",
  overrides: { isIsolated?: boolean; transcriptPath?: string } = {},
): AgentRunState {
  return {
    run_id: agentRunIdFrom("run-fixture"),
    kind,
    sandbox_id: sandboxIdFrom("sb-fixture"),
    transcript_path: overrides.transcriptPath ?? "/dev/null",
    workspace: { isIsolated: overrides.isIsolated ?? false },
  };
}

/**
 * Structurally a `@eos/engine` `SessionOutcome`/`SessionHandle` pair
 * (testkit deliberately depends only on contracts + tool, so the shapes
 * are declared here and checked structurally at the registration site).
 */
export interface ScriptedSessionOutcome {
  status: "completed" | "failed" | "cancelled";
  summary: string;
}

export interface ScriptedSessionHandle {
  handle: {
    settled: Promise<ScriptedSessionOutcome>;
    cancel(reason: string): Promise<void>;
    describe?(): string;
  };
  /** Resolve the natural settlement. */
  settle(outcome: ScriptedSessionOutcome): void;
  /** Reject `settled` (supervisor maps it to a failed session). */
  fail(error: Error): void;
  /** Reasons passed to `cancel`, in call order. */
  cancelled: string[];
}

/** A push-settled capability handle for supervisor and family suites. */
export function scriptedSessionHandle(describe?: string): ScriptedSessionHandle {
  let settle!: (outcome: ScriptedSessionOutcome) => void;
  let fail!: (error: Error) => void;
  const settled = new Promise<ScriptedSessionOutcome>((resolve, reject) => {
    settle = resolve;
    fail = reject;
  });
  const cancelled: string[] = [];
  return {
    handle: {
      settled,
      cancel: (reason) => {
        cancelled.push(reason);
        return Promise.resolve();
      },
      ...(describe !== undefined && { describe: () => describe }),
    },
    settle,
    fail,
    cancelled,
  };
}
