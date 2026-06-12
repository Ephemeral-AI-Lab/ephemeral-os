import { mkdirSync } from "node:fs";
import { appendFile } from "node:fs/promises";
import { join } from "node:path";

import type { AgentEvent, ConversationRecord } from "../engine/index.js";

/** The lifecycle kinds `events.jsonl` keeps; stream deltas are not recorded. */
const RECORDED_EVENT_TYPES = new Set<AgentEvent["type"]>([
  "run_started",
  "turn_started",
  "tool_execution_started",
  "tool_execution_completed",
  "task_registered",
  "task_settled",
  "run_finished",
]);

/**
 * The per-run records writer: `<recordsDir>/<runId>/events.jsonl` (every
 * lifecycle event, engine `seq` preserved) and `messages.jsonl` (the
 * conversation artifact). Wired at construction, so records are lossless
 * from the first line regardless of when (or whether) anyone consumes
 * `events()`. Append-only and line-buffered: one ordered write queue, no
 * fsync guarantee — a crash can truncate at most the final line, and one
 * writer owns a run directory. A failed write latches and resurfaces on
 * `flush()`; it never disturbs the run.
 */
export class JsonlRunRecorder {
  readonly #runDir: string;
  readonly #eventsPath: string;
  readonly #messagesPath: string;
  #messageSeq = 0;
  #queue: Promise<void> = Promise.resolve();
  #directoryReady = false;
  #failure: Error | undefined;

  constructor(recordsDir: string, runId: string) {
    this.#runDir = join(recordsDir, runId);
    this.#eventsPath = join(this.#runDir, "events.jsonl");
    this.#messagesPath = join(this.#runDir, "messages.jsonl");
  }

  /** Every stamped event flows through; non-lifecycle members are skipped. */
  event(event: AgentEvent): void {
    if (!RECORDED_EVENT_TYPES.has(event.type)) return;
    this.#enqueue(this.#eventsPath, event);
  }

  /** One line per conversation message, in append order. */
  message(record: ConversationRecord): void {
    const line = { seq: this.#messageSeq, ...record };
    this.#messageSeq += 1;
    this.#enqueue(this.#messagesPath, line);
  }

  /** Resolves once every line enqueued so far is on disk; test seam. */
  flush(): Promise<void> {
    return this.#queue.then(() => {
      if (this.#failure) throw this.#failure;
    });
  }

  #enqueue(path: string, line: object): void {
    const stamped = { ts: new Date().toISOString(), ...line };
    this.#queue = this.#queue
      .then(() => this.#write(path, stamped))
      .catch((error: unknown) => {
        this.#failure ??= error instanceof Error ? error : new Error(String(error));
      });
  }

  async #write(path: string, line: object): Promise<void> {
    if (!this.#directoryReady) {
      mkdirSync(this.#runDir, { recursive: true });
      this.#directoryReady = true;
    }
    await appendFile(path, `${JSON.stringify(line)}\n`, "utf8");
  }
}
