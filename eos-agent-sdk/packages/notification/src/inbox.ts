import type { JsonObject, UserMessage } from "@eos/contracts";

interface InboxEntry {
  message: string;
  key?: string;
}

/**
 * The system-side twin of the steer queue: a plain mailbox of host-published
 * strings, drained by the loop one priority below steers. Exhaustiveness
 * property hosts may rely on: every entry is a host publish — the SDK
 * injects nothing.
 */
export class NotificationInbox {
  #entries: InboxEntry[] = [];
  #wakers = new Set<() => void>();

  /**
   * Queue a message. A pending entry with the same `key` is replaced in
   * place (original queue position, latest message).
   */
  publish(message: string, opts?: { key?: string }): void {
    const entry: InboxEntry = { message, key: opts?.key };
    const pending =
      entry.key === undefined
        ? -1
        : this.#entries.findIndex((candidate) => candidate.key === entry.key);
    if (pending >= 0) this.#entries[pending] = entry;
    else this.#entries.push(entry);
    this.#wake();
  }

  /** Remove and return all pending messages, in queue order. */
  drain(): string[] {
    if (this.#entries.length === 0) return [];
    const drained = this.#entries;
    this.#entries = [];
    return drained.map((entry) => entry.message);
  }

  /** The exit/park gates' `inbox drained` conjunct. */
  isEmpty(): boolean {
    return this.#entries.length === 0;
  }

  /** Pending-entry count; the submission gate enumerates it in denials. */
  count(): number {
    return this.#entries.length;
  }

  /**
   * Level-triggered wait backing the loop's park: resolves immediately
   * if entries are pending, on the next publish, or on abort.
   */
  waitForNext(signal: AbortSignal): Promise<void> {
    if (this.#entries.length > 0 || signal.aborted) return Promise.resolve();
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

  #wake(): void {
    for (const wake of [...this.#wakers]) wake();
  }
}

/**
 * The one rendering helper: wrap a JSON payload as a user message holding
 * `<system_notification>{json}</system_notification>`. Rendering happens at
 * the loop's drain; the inbox stores plain strings.
 *
 * Every `<` in the serialized payload is escaped to its unicode JSON
 * escape sequence (still valid JSON), so untrusted text (command output,
 * task summaries) can never spoof the tag boundary.
 */
export function systemNotificationMessage(payload: JsonObject): UserMessage {
  const json = JSON.stringify(payload).replaceAll("<", "\\u003c");
  return {
    role: "user",
    content: [
      {
        type: "text",
        text: `<system_notification>${json}</system_notification>`,
      },
    ],
  };
}
