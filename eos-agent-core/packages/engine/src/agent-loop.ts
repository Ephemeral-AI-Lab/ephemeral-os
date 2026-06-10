import { toolUses } from "@eos/contracts";
import { ProviderError, type UsageSnapshot } from "@eos/llm-client";

import type { Conversation } from "./conversation.js";
import type { AgentRunStatus, RunHandle } from "./run-handle.js";
import { runToolBatch } from "./tool-runner.js";
import type { ToolRegistry } from "./tools.js";
import { addUsage, runAssistantTurn, type TurnConfig } from "./turn.js";

/** Everything one run's loop needs; assembled by `startAgentRun`. */
export interface AgentLoopContext {
  handle: RunHandle;
  conversation: Conversation;
  tools: ToolRegistry;
  turnConfig: TurnConfig;
  maxTurns: number;
}

/**
 * The loop spine — control flow only. Streaming, transcript writes, and
 * tool dispatch live in `turn.ts`, `conversation.ts`, and `tool-runner.ts`.
 * Never throws: every exit classifies into exactly one `finish` call, and
 * completion commits in the same synchronous block as its decision so a
 * late steer can never be accepted-but-dropped.
 */
export async function runAgentLoop(ctx: AgentLoopContext): Promise<void> {
  const { handle, conversation } = ctx;
  let turns = 0;
  let usage: UsageSnapshot = { input_tokens: 0, output_tokens: 0 };
  const finish = (status: AgentRunStatus): void => {
    handle.finish({
      displayed: [...conversation.displayedMessages()],
      llm: [...conversation.llmMessages()],
      usage,
      turns,
      ...status,
    });
  };
  try {
    for (;;) {
      if (handle.signal.aborted) {
        finish({ status: "cancelled", reason: handle.cancelReason });
        return;
      }
      if (turns >= ctx.maxTurns) {
        finish({
          status: "failed",
          failure: {
            kind: "max_turns",
            message: `run spent its ${ctx.maxTurns}-turn budget without completing`,
          },
        });
        return;
      }
      for (const steered of handle.drainSteers()) conversation.appendUser(steered);
      handle.emit({ type: "turn_started", turn: turns + 1 });
      const turn = await runAssistantTurn(
        ctx.turnConfig,
        conversation,
        handle.signal,
        handle.emit,
      );
      conversation.appendAssistant(turn.message);
      turns += 1;
      usage = addUsage(usage, turn.usage);
      const calls = toolUses(turn.message);
      if (calls.length === 0) {
        if (handle.hasPendingSteers()) continue;
        finish({
          status: "completed",
          final_message: turn.message,
          stop_reason: turn.stop_reason,
        });
        return;
      }
      const results = await runToolBatch(calls, ctx.tools, handle.signal, handle.emit);
      conversation.appendToolResults(results);
      if (handle.signal.aborted) {
        finish({ status: "cancelled", reason: handle.cancelReason });
        return;
      }
    }
  } catch (error) {
    finish(classifyLoopError(error, handle));
  } finally {
    if (!handle.finished) {
      finish({
        status: "failed",
        failure: { kind: "internal", message: "agent loop exited without finishing" },
      });
    }
  }
}

function classifyLoopError(error: unknown, handle: RunHandle): AgentRunStatus {
  if (handle.signal.aborted) {
    return { status: "cancelled", reason: handle.cancelReason };
  }
  if (error instanceof ProviderError) {
    return {
      status: "failed",
      failure: { kind: "provider_error", message: error.message },
    };
  }
  return {
    status: "failed",
    failure: {
      kind: "internal",
      message: error instanceof Error ? error.message : String(error),
    },
  };
}
