import type { ToolExecutor } from "@eos/engine";

import type { ToolDefinition } from "./contract.js";
import { toolBatchExecutor } from "./executor.js";
import type { HookAdvisoryRequirement } from "./hooks/protocol.js";
import { HookEngine } from "./hooks/runner.js";
import { bindTool, type HookPayloadFacts } from "./pipeline.js";
import type { AgentRunState } from "./run-state.js";

/**
 * Registration never sees a port: the composition root calls the family
 * factories (each injected with exactly its own services), selects the
 * profile's definitions, and hands this function finished definitions only.
 */
export interface BuildToolExecutorInput {
  runState: AgentRunState;
  /** Already profile-selected; the profile is the ONLY selection source. */
  definitions: ToolDefinition[];
  /** Operator hooks; absent means no hooks, not built-in ones. */
  hookEngine?: HookEngine;
  /** Per-call hook payload snapshots supplied by the runtime. */
  hookPayloadFacts?: () => HookPayloadFacts;
}

/**
 * Bind exactly the supplied definitions through the pipeline and return the
 * engine's `ToolExecutor` over a deterministically sorted registry
 * (prompt-cache stability). Hook `additionalContext` rides each result's
 * `metadata.hook_contexts`; the engine loop is its only publisher, so no
 * inbox is wired here.
 */
export function buildToolExecutor(input: BuildToolExecutorInput): ToolExecutor {
  const hooks = input.hookEngine ?? new HookEngine([]);
  const advisoryByTool = advisoryRequirements(input.definitions);
  const tools = input.definitions
    .slice()
    .sort((a, b) => (a.name < b.name ? -1 : 1))
    .map((definition) =>
      bindTool(definition, {
        hooks,
        advisoryRequirement: advisoryByTool.get(definition.name) ?? { required: false },
        hookPayloadFacts: input.hookPayloadFacts,
      }),
    );
  return toolBatchExecutor({ runState: input.runState, tools });
}

function advisoryRequirements(
  definitions: readonly ToolDefinition[],
): ReadonlyMap<string, HookAdvisoryRequirement> {
  const requirements = new Map<string, HookAdvisoryRequirement>();
  for (const definition of definitions) {
    if (!definition.isAdvisoryRequired) {
      requirements.set(definition.name, { required: false });
      continue;
    }
    const requirement: HookAdvisoryRequirement = { required: true };
    if (definition.advisorPrompt !== undefined) {
      requirement.advisor_prompt = definition.advisorPrompt;
    }
    requirements.set(definition.name, requirement);
  }
  return requirements;
}
