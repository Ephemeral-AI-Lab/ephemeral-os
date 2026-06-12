import type { AgentRunId, BackgroundTaskId } from "../contracts/index.js";
import type { Notifier } from "../notification/index.js";

/** How one background task ended; `outcome` is the task's one-line result. */
export interface BackgroundTaskOutcome {
  status: "success" | "failed" | "cancelled";
  outcome: string;
}

/** What a completion handler receives alongside the outcome. */
export interface BackgroundTaskCompletionContext {
  notifier: Notifier;
  runId: AgentRunId;
  taskId: BackgroundTaskId;
}

/**
 * The capability record a spawn site hands over. Every task declares how
 * its completion is handled — the type forces the choice; there is no
 * implicit default. Any task the model is expected to await must publish
 * in its `onCompletion` (the model has no other way to observe it);
 * `silent: true` is strictly for fire-and-forget work.
 */
export type BackgroundTask = {
  /** Provenance, e.g. "exec_command". */
  toolName: string;
  /** List row / human description. */
  title: string;
  /** Idempotent; no-op after completion. */
  cancel(): void | Promise<void>;
  done: Promise<BackgroundTaskOutcome>;
} & (
  | {
      onCompletion: (
        outcome: BackgroundTaskOutcome,
        ctx: BackgroundTaskCompletionContext,
      ) => void | Promise<void>;
    }
  | { silent: true }
);

/**
 * One `list()` row. No status field: a listed task is running (or briefly
 * settling while its completion handler runs); completed tasks are removed
 * — history lives in the event stream.
 */
export interface BackgroundTaskRow {
  taskId: BackgroundTaskId;
  toolName: string;
  title: string;
  /** Epoch ms registration time. */
  startedAt: number;
}

/** The run-scoped capability surface — exactly register / list / cancel. */
export interface BackgroundTaskSupervisor {
  register(task: BackgroundTask): { taskId: BackgroundTaskId };
  /** Registry contents: running plus settling tasks. */
  list(): BackgroundTaskRow[];
  /**
   * `true` iff it transitioned a running task to cancelling; `false` when
   * the task is not found (already completed and removed) or already
   * settling. Cancellation loses the race to completion by design.
   */
  cancel(taskId: BackgroundTaskId): Promise<boolean>;
}

/**
 * Task lifecycle facts for the run's event stream and records; the engine
 * stamps `seq`. Fields mirror the public camelCase task vocabulary.
 */
export type BackgroundTaskLifecycleEvent =
  | { type: "task_registered"; task: BackgroundTaskRow }
  | {
      type: "task_settled";
      taskId: BackgroundTaskId;
      toolName: string;
      title: string;
      outcome: BackgroundTaskOutcome;
      /** A throwing or timed-out `onCompletion`, recorded — never rethrown. */
      completionError?: string;
    };
