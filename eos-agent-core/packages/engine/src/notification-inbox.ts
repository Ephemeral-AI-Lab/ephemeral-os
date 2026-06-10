import type { JsonObject, Message } from "@eos/contracts";

interface InboxEntry {
  message: Message;
  key?: string;
  tag?: unknown;
}

/**
 * The system-side twin of the steer queue: a plain mailbox of
 * already-rendered `Message`s, drained by the loop one priority below
 * steers. Deliberately generic - any holder of the reference can publish
 * (the supervisor and hook context today; trigger rules and agent-to-agent
 * messages later, with no inbox change).
 */
export class NotificationInbox {
  #entries: InboxEntry[] = [];
  #drainedCallbacks: ((tags: unknown[]) => void)[] = [];
  #wakers = new Set<() => void>();

  /**
   * Queue a rendered message. A pending entry with the same `key` is
   * replaced in place; `tag` is handed back opaquely on drain.
   */
  publish(message: Message, opts?: { key?: string; tag?: unknown }): void {
    const entry: InboxEntry = { message, key: opts?.key, tag: opts?.tag };
    const pending =
      entry.key === undefined
        ? -1
        : this.#entries.findIndex((candidate) => candidate.key === entry.key);
    if (pending >= 0) this.#entries[pending] = entry;
    else this.#entries.push(entry);
    this.#wake();
  }

  /**
   * Remove all pending entries and fire `onDrained(tags)` in the same
   * synchronous block, so no interleaved publish or second drain can
   * double-deliver or skip an entry.
   */
  drain(): Message[] {
    if (this.#entries.length === 0) return [];
    const drained = this.#entries;
    this.#entries = [];
    const tags = drained
      .filter((entry) => entry.tag !== undefined)
      .map((entry) => entry.tag);
    for (const callback of this.#drainedCallbacks) callback(tags);
    return drained.map((entry) => entry.message);
  }

  /** Delivery bookkeeping for publishers (the supervisor self-subscribes). */
  onDrained(callback: (tags: unknown[]) => void): void {
    this.#drainedCallbacks.push(callback);
  }

  /**
   * Level-triggered wait backing the loop's auto-wait: resolves immediately
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
 * publish; the inbox stores plain messages, so new publishers never require
 * inbox or engine changes.
 */
export function systemNotificationMessage(payload: JsonObject): Message {
  return {
    role: "user",
    content: [
      {
        type: "text",
        text: `<system_notification>${JSON.stringify(payload)}</system_notification>`,
      },
    ],
  };
}
