import {
  PlannerOutcomePayloadSchema,
  type JsonObject,
  type PlannerOutcomePayload,
  type SubmissionBinding,
} from "@eos/contracts";

import type { ToolDefinition } from "../../contract.js";
import { defineTool } from "../../define.js";
import { ADVISOR_PROMPT } from "../advisory_prompts/submit_planner_outcome_prompt.js";
import { DESCRIPTION } from "../description_prompts/submit_planner_outcome_prompt.js";

export type PlannerSubmissionBinding = Extract<SubmissionBinding, { kind: "planner" }>;

/**
 * The planner's terminal tool with its per-kind payload schema. Validation
 * is in-run, end to end (§2.15): shape rides the pipeline's Zod parse,
 * structure is checked here, and - when the run is workflow-launched - the
 * §2.19 binding validates materialization rules and mutates in one DB
 * transaction, returning an error result the agent corrects before
 * terminating. Unbound runs keep the service-free behavior: the payload
 * rides the run outcome as `submission`.
 */
export function submitPlannerOutcomeTool(
  binding?: PlannerSubmissionBinding,
): ToolDefinition {
  return defineTool({
    name: "submit_planner_outcome",
    description: DESCRIPTION,
    input: PlannerOutcomePayloadSchema,
    isTerminal: true,
    isAdvisoryRequired: true,
    advisorPrompt: ADVISOR_PROMPT,
    execute: async (input) => {
      const structureError = plannerStructureError(input);
      if (structureError !== undefined) {
        return { content: structureError, isError: true };
      }
      if (!binding) return { content: plannerContent(input) };
      const result = await binding.submit(input);
      return result.ok
        ? { content: { summary: input.summary } }
        : { content: result.error, isError: true };
    },
  });
}

/** Unique local ids, declared `needs`, no cycles - correctable in-run. */
export function plannerStructureError(
  payload: PlannerOutcomePayload,
): string | undefined {
  const ids = new Set<string>();
  for (const item of payload.work_items) {
    if (ids.has(item.id)) return `duplicate work item id "${item.id}"`;
    ids.add(item.id);
  }
  for (const item of payload.work_items) {
    for (const need of item.needs) {
      if (!ids.has(need)) {
        return `work item "${item.id}" needs undeclared id "${need}"`;
      }
    }
  }
  const needsOf = new Map(payload.work_items.map((item) => [item.id, item.needs]));
  const done = new Set<string>();
  const visiting = new Set<string>();
  const hasCycle = (id: string): boolean => {
    if (done.has(id)) return false;
    if (visiting.has(id)) return true;
    visiting.add(id);
    for (const need of needsOf.get(id) ?? []) {
      if (hasCycle(need)) return true;
    }
    visiting.delete(id);
    done.add(id);
    return false;
  };
  for (const item of payload.work_items) {
    if (hasCycle(item.id)) return "work item `needs` contain a dependency cycle";
  }
  return undefined;
}

function plannerContent(payload: PlannerOutcomePayload): JsonObject {
  const content: JsonObject = {
    summary: payload.summary,
    work_items: payload.work_items.map((item) => ({
      id: item.id,
      agent_name: item.agent_name,
      description: item.description,
      work_item_spec: item.work_item_spec,
      needs: [...item.needs],
    })),
  };
  if (payload.iteration_focus !== undefined) {
    content.iteration_focus = payload.iteration_focus;
  }
  if (payload.deferred_goal !== undefined) {
    content.deferred_goal = payload.deferred_goal;
  }
  return content;
}
