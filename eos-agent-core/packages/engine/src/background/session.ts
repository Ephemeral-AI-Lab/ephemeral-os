/**
 * Background-session data contracts. The supervisor is generic: `type` is
 * an open string here; the narrow session-kind union is a tool-side
 * refinement that arrives with the spawning tool families.
 */
export interface SessionRef {
  type: string;
  id: string;
}

/**
 * `running -> completed | failed | cancelled -> delivered -> evicted`.
 * Terminal-status and delivered are separate facts; eviction requires both,
 * so the model can never miss a completion.
 */
export type SessionStatus =
  | "running"
  | "completed"
  | "failed"
  | "cancelled"
  | "delivered";

export interface SessionOutcome {
  status: "completed" | "failed" | "cancelled";
  /** One line; detail stays behind the session kind's read tool. */
  summary: string;
}

/**
 * The capability record a spawn site hands over - no driver classes. Each
 * capability closes over exactly the right port (`exec_command` over
 * `killCommand`, `run_subagent` over the child run), so the supervisor
 * never resolves kind -> behavior.
 */
export interface SessionHandle {
  /** Push settlement; a handle settles exactly once. */
  settled: Promise<SessionOutcome>;
  /** Teardown; must still work after the run's signal has aborted. */
  cancel(reason: string): Promise<void>;
  /** Optional one-line descriptor for `list_background_sessions`. */
  describe?(): string;
}

/** One row of `list()`: running and settled-but-undelivered sessions. */
export interface SessionRow {
  type: string;
  id: string;
  status: Exclude<SessionStatus, "delivered">;
  /** ISO-8601 registration time. */
  started_at: string;
  /** Present once the session settled. */
  summary?: string;
  /** From the handle's `describe()`, when provided. */
  description?: string;
}
