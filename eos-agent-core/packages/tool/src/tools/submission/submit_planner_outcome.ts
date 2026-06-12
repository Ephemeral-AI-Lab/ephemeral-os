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
 * structure is checked here, and - when the run is pursuit-launched - the
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

/** Unique local ids and no in-payload cycles - correctable in-run. */
export function plannerStructureError(
  payload: PlannerOutcomePayload,
): string | undefined {
  const ids = new Set<string>();
  for (const item of payload.work_items) {
    if (ids.has(item.id)) return `duplicate work item id "${item.id}"`;
    ids.add(item.id);
  }
  const dependsOn = new Map(
    payload.work_items.map((item) => [
      item.id,
      item.depends_on.filter((dependency) => ids.has(dependency)),
    ]),
  );
  const done = new Set<string>();
  const visiting = new Set<string>();
  const hasCycle = (id: string): boolean => {
    if (done.has(id)) return false;
    if (visiting.has(id)) return true;
    visiting.add(id);
    for (const dependency of dependsOn.get(id) ?? []) {
      if (hasCycle(dependency)) return true;
    }
    visiting.delete(id);
    done.add(id);
    return false;
  };
  for (const item of payload.work_items) {
    if (hasCycle(item.id)) return "work item `depends_on` contains a dependency cycle";
  }
  return undefined;
}

function plannerContent(payload: PlannerOutcomePayload): JsonObject {
  const content: JsonObject = {
    summary: payload.summary,
    work_items: payload.work_items.map((item) => ({
      id: item.id,
      agent_name: item.agent_name,
      title: item.title,
      spec: item.spec,
      depends_on: [...item.depends_on],
    })),
  };
  if (payload.leg_goal !== undefined) {
    content.leg_goal = payload.leg_goal;
  }
  if (payload.next_leg_goal !== undefined) {
    content.next_leg_goal = payload.next_leg_goal;
  }
  return content;
}
