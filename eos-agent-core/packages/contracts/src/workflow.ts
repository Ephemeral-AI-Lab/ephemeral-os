import { z } from "zod";

import { MessageSchema } from "./messages.js";

// --- entity ids (brand + mint/from, the ids.ts pattern) ----------------------

/** Identifier for one delegated workflow. Minted by `delegate`. */
export const WorkflowIdSchema = z.string().min(1).brand<"WorkflowId">();
export type WorkflowId = z.infer<typeof WorkflowIdSchema>;

export function mintWorkflowId(): WorkflowId {
  return WorkflowIdSchema.parse(crypto.randomUUID());
}

export function workflowIdFrom(raw: string): WorkflowId {
  return WorkflowIdSchema.parse(raw);
}

/** Identifier for one iteration of a workflow. */
export const IterationIdSchema = z.string().min(1).brand<"IterationId">();
export type IterationId = z.infer<typeof IterationIdSchema>;

export function mintIterationId(): IterationId {
  return IterationIdSchema.parse(crypto.randomUUID());
}

export function iterationIdFrom(raw: string): IterationId {
  return IterationIdSchema.parse(raw);
}

/** Identifier for one attempt inside an iteration. */
export const AttemptIdSchema = z.string().min(1).brand<"AttemptId">();
export type AttemptId = z.infer<typeof AttemptIdSchema>;

export function mintAttemptId(): AttemptId {
  return AttemptIdSchema.parse(crypto.randomUUID());
}

export function attemptIdFrom(raw: string): AttemptId {
  return AttemptIdSchema.parse(raw);
}

/** Identifier for one attempt's plan (the planning-act record). */
export const PlanIdSchema = z.string().min(1).brand<"PlanId">();
export type PlanId = z.infer<typeof PlanIdSchema>;

export function mintPlanId(): PlanId {
  return PlanIdSchema.parse(crypto.randomUUID());
}

export function planIdFrom(raw: string): PlanId {
  return PlanIdSchema.parse(raw);
}

/** Identifier for one work item minted at planner materialization. */
export const WorkItemIdSchema = z.string().min(1).brand<"WorkItemId">();
export type WorkItemId = z.infer<typeof WorkItemIdSchema>;

export function mintWorkItemId(): WorkItemId {
  return WorkItemIdSchema.parse(crypto.randomUUID());
}

export function workItemIdFrom(raw: string): WorkItemId {
  return WorkItemIdSchema.parse(raw);
}

// --- status -------------------------------------------------------------------

/** One status enum for all five workflow entities. */
export const WorkflowEntityRunStatusSchema = z.enum([
  "NotStarted",
  "Running",
  "Success",
  "Failed",
  "Cancelled",
]);
export type WorkflowEntityRunStatus = z.infer<typeof WorkflowEntityRunStatusSchema>;

/** Terminal statuses; mutating a terminal entity is always a no-op. */
export type WorkflowTerminalStatus = Extract<
  WorkflowEntityRunStatus,
  "Success" | "Failed" | "Cancelled"
>;

export function isWorkflowEntityTerminal(
  status: WorkflowEntityRunStatus,
): status is WorkflowTerminalStatus {
  return status === "Success" || status === "Failed" || status === "Cancelled";
}

// --- submission payloads --------------------------------------------------------

export const PlannerWorkItemSpecSchema = z.object({
  /** Planner-local id; the service mints global WorkItemIds and rewrites `needs`. */
  id: z.string().min(1),
  /** Worker profile to launch. */
  agent_name: z.string().min(1),
  description: z.string().min(1),
  work_item_spec: z.string().min(1),
  needs: z.array(z.string()).default([]),
});
export type PlannerWorkItemSpec = z.infer<typeof PlannerWorkItemSpecSchema>;

/**
 * The planner's terminal payload. `iteration_focus` and `deferred_goal`
 * declare and reset as one atomic pair: omitting both keeps the standing
 * declaration, and a `deferred_goal` never validates without the focus
 * that produced it. Materialization requires the iteration's first
 * declaration to be present.
 */
export const PlannerOutcomePayloadSchema = z
  .object({
    summary: z.string().min(1),
    iteration_focus: z.string().min(1).optional(),
    deferred_goal: z.string().min(1).optional(),
    work_items: z.array(PlannerWorkItemSpecSchema).min(1),
  })
  .superRefine((payload, ctx) => {
    if (payload.deferred_goal !== undefined && payload.iteration_focus === undefined) {
      ctx.addIssue({
        code: "custom",
        path: ["deferred_goal"],
        message: "deferred_goal requires iteration_focus",
      });
    }
  });
export type PlannerOutcomePayload = z.infer<typeof PlannerOutcomePayloadSchema>;

/** The worker's terminal payload; `is_pass` decides success/failure. */
export const WorkerOutcomePayloadSchema = z.object({
  summary: z.string().min(1),
  is_pass: z.boolean(),
  outcome: z.string().min(1),
});
export type WorkerOutcomePayload = z.infer<typeof WorkerOutcomePayloadSchema>;

// --- context read DTOs ----------------------------------------------------------

/**
 * One paged file read over the context path universe. Content is the field
 * text verbatim; the owning entity's status rides here, never the content.
 * Paging is an overwrite read over the latest DB-derived render.
 */
export interface ContextPage {
  path: string;
  status: WorkflowEntityRunStatus;
  total_bytes: number;
  offset: number;
  content: string;
  next_offset?: number;
}

/**
 * Deduplicated entity-local search hits; `field` is the filename of the
 * matched file (one fact, one path).
 */
export interface ContextSearch {
  files: readonly { path: string; status: WorkflowEntityRunStatus }[];
  matches: readonly {
    path: string;
    status: WorkflowEntityRunStatus;
    field: string;
    snippet: string;
  }[];
  /** Explicit truncation notice; caps are never silent. */
  truncated?: string;
}

// --- context-script IO (snake_case: crosses the process boundary) ---------------

export const WorkflowContextWorkItemSchema = z.strictObject({
  id: z.string(),
  agent_name: z.string(),
  description: z.string(),
  spec: z.string(),
  needs: z.array(z.string()),
  status: WorkflowEntityRunStatusSchema,
  summary: z.string().nullable(),
  outcome: z.string().nullable(),
  agent_run_id: z.string().nullable(),
  context_path: z.string(),
});
export type WorkflowContextWorkItem = z.infer<typeof WorkflowContextWorkItemSchema>;

export const WorkflowContextPlanSchema = z.strictObject({
  id: z.string(),
  status: WorkflowEntityRunStatusSchema,
  /** Null means the plan kept the standing declaration. */
  declared_focus: z.string().nullable(),
  declared_deferred_goal: z.string().nullable(),
  summary: z.string().nullable(),
  agent_run_id: z.string().nullable(),
  context_path: z.string(),
});
export type WorkflowContextPlan = z.infer<typeof WorkflowContextPlanSchema>;

export const WorkflowContextAttemptSchema = z.strictObject({
  id: z.string(),
  sequence: z.number().int(),
  status: WorkflowEntityRunStatusSchema,
  fail_reason: z.string().nullable(),
  is_consistent_with_iteration_focus: z.boolean(),
  /** Live or archived folder path; a refocus is the one event that moves it. */
  context_path: z.string(),
  plan: WorkflowContextPlanSchema,
  work_items: z.array(WorkflowContextWorkItemSchema),
});
export type WorkflowContextAttempt = z.infer<typeof WorkflowContextAttemptSchema>;

export const WorkflowContextIterationSchema = z.strictObject({
  id: z.string(),
  sequence: z.number().int(),
  origin: z.enum(["initial", "deferred_goal"]),
  status: WorkflowEntityRunStatusSchema,
  /** Null means no declaration yet. */
  focus: z.string().nullable(),
  deferred_goal: z.string().nullable(),
  max_attempts: z.number().int(),
  context_path: z.string(),
  attempts: z.array(WorkflowContextAttemptSchema),
});
export type WorkflowContextIteration = z.infer<typeof WorkflowContextIterationSchema>;

/**
 * The full typed snapshot a context script receives: ALL facts, including
 * the ones the default policy hides. Hiding is policy; the composer decides.
 */
export const WorkflowContextSnapshotSchema = z.strictObject({
  workflow: z.strictObject({
    id: z.string(),
    original_goal: z.string(),
    current_goal: z.string(),
    status: WorkflowEntityRunStatusSchema,
    context_path: z.string(),
    iterations: z.array(WorkflowContextIterationSchema),
  }),
});
export type WorkflowContextSnapshot = z.infer<typeof WorkflowContextSnapshotSchema>;

export const PlannerContextInputSchema = z.strictObject({
  kind: z.literal("planner"),
  workflow_context: WorkflowContextSnapshotSchema,
  current: z.strictObject({
    workflow_id: z.string(),
    iteration_id: z.string(),
    attempt_id: z.string(),
    plan_id: z.string(),
  }),
});
export type PlannerContextInput = z.infer<typeof PlannerContextInputSchema>;

export const WorkerContextInputSchema = z.strictObject({
  kind: z.literal("worker"),
  workflow_context: WorkflowContextSnapshotSchema,
  current: z.strictObject({
    workflow_id: z.string(),
    iteration_id: z.string(),
    attempt_id: z.string(),
    work_item_id: z.string(),
  }),
});
export type WorkerContextInput = z.infer<typeof WorkerContextInputSchema>;

/** A launch message must be a real user `Message`, content blocks included. */
export const InitialUserMessageSchema = MessageSchema.extend({
  role: z.literal("user"),
});
export type InitialUserMessage = z.infer<typeof InitialUserMessageSchema>;

/**
 * What a context script must print: the launch's complete ordered initial
 * messages. Replace, never merge - the runtime appends nothing around them.
 */
export const ContextScriptOutputSchema = z.object({
  initial_messages: z.array(InitialUserMessageSchema).min(1),
});
export type ContextScriptOutput = z.infer<typeof ContextScriptOutputSchema>;

// --- service-facing contracts (shared by @eos/workflow and @eos/tool) -----------

/** The workflow's terminal state, mapped onto the background session outcome. */
export interface WorkflowTerminal {
  status: WorkflowTerminalStatus;
  /** One line, from the closing entity's recorded summaries. */
  summary: string;
}

export interface DelegateWorkflowInput {
  goal: string;
  max_attempts?: number;
}

/** The caller-side handle `delegate` returns; `cancel` resolves after the cascade. */
export interface DelegatedWorkflow {
  workflowId: WorkflowId;
  terminal: Promise<WorkflowTerminal>;
  cancel(reason: string): Promise<void>;
  /** Goal one-liner for session listings. */
  describe(): string;
}

export type SubmissionResult = { ok: true } | { ok: false; error: string };

/**
 * The entity-bound submission seam (§2.19): built per claimed entity by the
 * workflow launcher, threaded through the launch port, and wired by the
 * runtime into the child run's terminal submission tool. `submit` validates
 * materialization rules and runs one DB transaction (mutate + claim).
 */
export type SubmissionBinding =
  | {
      kind: "planner";
      submit(payload: PlannerOutcomePayload): Promise<SubmissionResult>;
    }
  | {
      kind: "worker";
      submit(payload: WorkerOutcomePayload): Promise<SubmissionResult>;
    };
