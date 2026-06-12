import {
  mintBackgroundTaskId,
  type AgentRunId,
  type BackgroundTaskId,
} from "../contracts/index.js";
import type { Notifier } from "../notification/index.js";

import type {
  BackgroundTask,
  BackgroundTaskCompletionContext,
  BackgroundTaskLifecycleEvent,
  BackgroundTaskOutcome,
  BackgroundTaskRow,
  BackgroundTaskSupervisor,
} from "./background-task.js";

export interface RunBackgroundTaskSupervisorDeps {
  runId: AgentRunId;
  /** Handed to completion handlers; the supervisor itself never publishes. */
  notifier: Notifier;
  /** Bounds each `onCompletion` invocation. */
  completionTimeoutMs: number;
  /** `task_registered` / `task_settled` sink (the run's event emitter). */
  emit: (event: BackgroundTaskLifecycleEvent) => void;
}

interface TaskEntry {
  task: BackgroundTask;
  row: BackgroundTaskRow;
  /** Set when completion handling starts; a settling task cannot be cancelled. */
  settling: boolean;
}

/**
 * The per-run task registry — the registry IS the open set the park/exit
 * gates read. On `done` resolving, the supervisor does exactly one thing:
 * invoke `onCompletion` once, awaited and bounded (or remove immediately
 * for `silent` tasks); a task is removed the moment its completion
 * handling finishes, and every removal wakes a parked loop. The gate
 * methods (`isEmpty`/`count`/`changeCount`/`waitForChange`/`disposeAll`)
 * are engine-facing internals, deliberately absent from the public
 * `BackgroundTaskSupervisor` interface.
 */
export class RunBackgroundTaskSupervisor implements BackgroundTaskSupervisor {
  readonly #deps: RunBackgroundTaskSupervisorDeps;
  readonly #entries = new Map<BackgroundTaskId, TaskEntry>();
  readonly #wakers = new Set<() => void>();
  #changes = 0;
  #disposedReason: string | undefined;

  constructor(deps: RunBackgroundTaskSupervisorDeps) {
    this.#deps = deps;
  }

  /**
   * Adopt a task. The supervisor owns rejection mapping: a `done` that
   * rejects settles as `failed`, so spawn sites hand over raw promise
   * chains. After run-end disposal the supervisor is latched: a late
   * registration (an abandoned `execute()` continuation finishing after
   * the run) is immediately cancelled and leaves no trace.
   */
  register(task: BackgroundTask): { taskId: BackgroundTaskId } {
    const taskId = mintBackgroundTaskId();
    if (this.#disposedReason !== undefined) {
      task.done.catch(() => undefined);
      void Promise.resolve()
        .then(() => task.cancel())
        .catch(() => undefined);
      return { taskId };
    }
    const entry: TaskEntry = {
      task,
      row: {
        taskId,
        toolName: task.toolName,
        title: task.title,
        startedAt: Date.now(),
      },
      settling: false,
    };
    this.#entries.set(taskId, entry);
    this.#deps.emit({ type: "task_registered", task: entry.row });
    task.done.then(
      (outcome) => {
        this.#settle(taskId, outcome);
      },
      (error: unknown) => {
        this.#settle(taskId, { status: "failed", outcome: errorMessage(error) });
      },
    );
    return { taskId };
  }

  list(): BackgroundTaskRow[] {
    return [...this.#entries.values()].map((entry) => ({ ...entry.row }));
  }

  async cancel(taskId: BackgroundTaskId): Promise<boolean> {
    const entry = this.#entries.get(taskId);
    if (entry === undefined || entry.settling) return false;
    entry.settling = true;
    await this.#teardown(entry);
    await this.#complete(entry, { status: "cancelled", outcome: "cancelled" });
    return true;
  }

  // --- engine-facing internals (not on the public interface) -------------

  /** The gates' `task registry empty` conjunct. */
  isEmpty(): boolean {
    return this.#entries.size === 0;
  }

  /** Registry size (running + settling); turn-boundary fact. */
  count(): number {
    return this.#entries.size;
  }

  /** Monotonic registry-change counter; capture before evaluating a gate. */
  changeCount(): number {
    return this.#changes;
  }

  /**
   * Level-triggered against `since`: resolves immediately when the
   * registry changed after the capture, on the next change, or on abort —
   * the park's "any task removal wakes the loop" source.
   */
  waitForChange(since: number, signal: AbortSignal): Promise<void> {
    if (this.#changes !== since || signal.aborted) return Promise.resolve();
    return new Promise((resolve) => {
      const wake = (): void => {
        this.#wakers.delete(wake);
        signal.removeEventListener("abort", wake);
        resolve();
      };
      this.#wakers.add(wake);
      signal.addEventListener("abort", wake);
    });
  }

  /**
   * Run-end disposal: cancel still-running tasks, run their completion
   * handlers with `status: "cancelled"` (publishes after run end are
   * no-ops; silent tasks are simply removed), and leave the registry
   * empty. No task survives its run.
   */
  async disposeAll(reason: string): Promise<void> {
    this.#disposedReason ??= reason;
    const open = [...this.#entries.values()].filter(
      (entry) => !entry.settling,
    );
    for (const entry of open) entry.settling = true;
    await Promise.all(
      open.map(async (entry) => {
        await this.#teardown(entry);
        await this.#complete(entry, { status: "cancelled", outcome: reason });
      }),
    );
  }

  // --- one completion path ------------------------------------------------

  /** A natural settle against a non-running task is dropped (cancel race). */
  #settle(taskId: BackgroundTaskId, outcome: BackgroundTaskOutcome): void {
    const entry = this.#entries.get(taskId);
    if (entry === undefined || entry.settling) return;
    entry.settling = true;
    void this.#complete(entry, outcome);
  }

  /** Teardown failures never undo a recorded cancellation. */
  async #teardown(entry: TaskEntry): Promise<void> {
    try {
      await entry.task.cancel();
    } catch {
      // cancel() is best-effort by contract.
    }
  }

  /**
   * The single completion path for natural, cancelled, and disposed tasks:
   * invoke the handler once (awaited, bounded) or skip it for `silent`,
   * then remove, emit `task_settled`, and wake the loop. A throwing or
   * timed-out handler is recorded on the event and never wedges the run.
   */
  async #complete(
    entry: TaskEntry,
    outcome: BackgroundTaskOutcome,
  ): Promise<void> {
    let completionError: string | undefined;
    if (!("silent" in entry.task)) {
      const handler = entry.task.onCompletion;
      const ctx: BackgroundTaskCompletionContext = {
        notifier: this.#deps.notifier,
        runId: this.#deps.runId,
        taskId: entry.row.taskId,
      };
      try {
        await withTimeout(
          Promise.resolve().then(() => handler(outcome, ctx)),
          this.#deps.completionTimeoutMs,
        );
      } catch (error) {
        completionError = errorMessage(error);
      }
    }
    this.#entries.delete(entry.row.taskId);
    this.#changes += 1;
    this.#deps.emit({
      type: "task_settled",
      taskId: entry.row.taskId,
      toolName: entry.row.toolName,
      title: entry.row.title,
      outcome,
      ...(completionError !== undefined && { completionError }),
    });
    for (const wake of [...this.#wakers]) wake();
  }
}

async function withTimeout(work: Promise<void>, ms: number): Promise<void> {
  let timer: NodeJS.Timeout | undefined;
  try {
    await Promise.race([
      work,
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => {
          reject(new Error(`onCompletion exceeded ${String(ms)}ms`));
        }, ms);
      }),
    ]);
  } finally {
    clearTimeout(timer);
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
