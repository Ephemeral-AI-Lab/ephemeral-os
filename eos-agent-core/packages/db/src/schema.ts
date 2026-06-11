import type {
  AgentRunId,
  AttemptId,
  IterationId,
  PlanId,
  WorkItemId,
  WorkflowEntityRunStatus,
  WorkflowId,
} from "@eos/contracts";
import type { Generated, Selectable } from "kysely";

/**
 * Row shapes only - workflow semantics (derived goals, focus views,
 * archives) live in `@eos/workflow`. Timestamps are ISO-8601 strings.
 */
export interface WorkflowsTable {
  id: WorkflowId;
  parent_run_id: AgentRunId;
  goal: string;
  status: WorkflowEntityRunStatus;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

export interface IterationsTable {
  id: IterationId;
  workflow_id: WorkflowId;
  sequence: number;
  origin: "initial" | "deferred_goal";
  max_attempts: number;
  status: WorkflowEntityRunStatus;
  created_at: string;
  updated_at: string;
}

export interface AttemptsTable {
  id: AttemptId;
  workflow_id: WorkflowId;
  iteration_id: IterationId;
  sequence: number;
  status: WorkflowEntityRunStatus;
  fail_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface PlansTable {
  id: PlanId;
  workflow_id: WorkflowId;
  iteration_id: IterationId;
  attempt_id: AttemptId;
  agent_run_id: AgentRunId | null;
  status: WorkflowEntityRunStatus;
  /** Null = kept the standing declaration; the pair records atomically. */
  declared_focus: string | null;
  declared_deferred_goal: string | null;
  planner_summary: string | null;
  created_at: string;
  updated_at: string;
}

export interface WorkItemsTable {
  id: WorkItemId;
  workflow_id: WorkflowId;
  iteration_id: IterationId;
  attempt_id: AttemptId;
  plan_id: PlanId;
  agent_name: string;
  agent_run_id: AgentRunId | null;
  status: WorkflowEntityRunStatus;
  description: string;
  work_item_spec: string;
  /** JSON-encoded WorkItemId array; encoding owned by `@eos/workflow`. */
  needs: string;
  worker_summary: string | null;
  worker_outcome: string | null;
  created_at: string;
  updated_at: string;
}

export interface LaunchQueueTable {
  id: Generated<number>;
  workflow_id: WorkflowId;
  kind: "plan" | "work_item";
  entity_id: string;
  state: "queued" | "claimed";
  /** Stamped at claim; the post-commit launcher's staleness guard. */
  launch_token: string | null;
  created_at: string;
}

export interface WorkflowDatabase {
  workflows: WorkflowsTable;
  iterations: IterationsTable;
  attempts: AttemptsTable;
  plans: PlansTable;
  work_items: WorkItemsTable;
  launch_queue: LaunchQueueTable;
}

export type WorkflowRow = Selectable<WorkflowsTable>;
export type IterationRow = Selectable<IterationsTable>;
export type AttemptRow = Selectable<AttemptsTable>;
export type PlanRow = Selectable<PlansTable>;
export type WorkItemRow = Selectable<WorkItemsTable>;
export type LaunchQueueRow = Selectable<LaunchQueueTable>;
