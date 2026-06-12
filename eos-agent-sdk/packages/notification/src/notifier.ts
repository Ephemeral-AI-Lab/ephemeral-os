/**
 * The run-scoped publish capability (one per run, same instance on the
 * handle and on every tool-call context). Only the host publishes — from
 * tools, `BackgroundTask.onCompletion`, `turnBoundary` hooks, or the
 * handle; the SDK itself never does.
 */
export interface Notifier {
  /**
   * Drains at the next turn boundary. An undrained message with the same
   * `key` is replaced (coalesce); the key has no other meaning.
   */
  publish(message: string, opts?: { key?: string }): void;
}
