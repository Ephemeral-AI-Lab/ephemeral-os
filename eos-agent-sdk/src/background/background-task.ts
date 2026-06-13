import type { AgentRunId, BackgroundTaskId } from "../contracts/index.js";
import type { Notifier } from "../notification/index.js";

/** How one background task ended; `outcome` is the task's one-line result. */
export interface BackgroundTaskOutcome {
  status: "success" | "failed" | "cancelled";
  outcome: string;
}

/**
 * Host-chosen addressing handle for a background task — the key `list`/`cancel`
 * use to name it. The supervisor treats it as opaque (no per-`type` behavior)
 * and only enforces uniqueness among active tasks.
 */
export interface BackgroundTaskTag {
  type: string;
  id: string;
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
  /** Host-chosen addressing handle; unique among active tasks. */
  tag: BackgroundTaskTag;
  /** List row / human description. */
  title: string;
  /** Idempotent; no-op after completion. */
  cancel(reason?: string): void | Promise<void>;
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
  tag: BackgroundTaskTag;
  title: string;
  /** Epoch ms registration time. */
  startedAt: number;
}

/** The run-scoped capability surface — exactly register / list / cancel. */
export interface BackgroundTaskSupervisor {
  /** Rejects (throws) a task whose `tag` matches one already active. */
  register(task: BackgroundTask): { taskId: BackgroundTaskId };
  /** Registry contents: running plus settling tasks. */
  list(): BackgroundTaskRow[];
  /**
   * Resolves the active task by `tag` and passes `reason` to its `cancel`
   * callback. `true` iff it transitioned a running task to cancelling;
   * `false` when the tag names no active task (already completed and
   * removed) or that task is already settling. Cancellation loses the race
   * to completion by design.
   */
  cancel(tag: BackgroundTaskTag, reason?: string): Promise<boolean>;
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
      tag: BackgroundTaskTag;
      title: string;
      outcome: BackgroundTaskOutcome;
      /** A throwing or timed-out `onCompletion`, recorded — never rethrown. */
      completionError?: string;
    };
