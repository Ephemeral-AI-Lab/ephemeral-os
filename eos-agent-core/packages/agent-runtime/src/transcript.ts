import { mkdirSync } from "node:fs";
import { appendFile, readFile } from "node:fs/promises";
import { dirname, join } from "node:path";

import type { JsonValue, Message, ToolCallResult } from "@eos/contracts";
import type { AgentEvent } from "@eos/engine";

/** Where one run's transcript lives under the runtime's data dir. */
export function runTranscriptPath(dataDir: string, runId: string): string {
  return join(dataDir, "runs", runId, "transcript.jsonl");
}

/** One conversation-shaping entry, before the writer stamps `seq`/`ts`. */
type TranscriptEntry =
  | { kind: "user"; origin: "initial" | "steer"; message: Message }
  | { kind: "assistant"; message: Message }
  | { kind: "tool_result"; result: ToolCallResult }
  | { kind: "notification"; text: string }
  | {
      kind: "run_finished";
      outcome_status: string;
      interrupt_reason?: string;
      submission?: JsonValue;
    };

/** One JSONL line; snake_case: serialized. */
export type TranscriptLine = { seq: number; ts: string } & TranscriptEntry;

/**
 * Per-run JSONL writer: one ordered append queue, fed by the runtime's
 * event subscriber (decision 4 - not the engine, not tools). `steer` and
 * `notification` lines have no live source until the broadcaster phase
 * grows the event stream; the union already carries them for recorders.
 * A failed write latches and resurfaces on every later `flush()`.
 */
export class TranscriptWriter {
  readonly #path: string;
  #seq = 0;
  #queue: Promise<void> = Promise.resolve();
  #directoryReady = false;
  #failure: Error | undefined;

  constructor(path: string) {
    this.#path = path;
  }

  /** Append the line for one engine event; non-shaping events are skipped. */
  append(event: AgentEvent): void {
    const entry = eventEntry(event);
    if (entry) this.#enqueue(entry);
  }

  /** Initial and (future) steered user input; the runtime is the source. */
  appendUser(origin: "initial" | "steer", message: Message): void {
    this.#enqueue({ kind: "user", origin, message });
  }

  /** Resolves once every line appended so far is on disk. */
  flush(): Promise<void> {
    return this.#queue.then(() => {
      if (this.#failure) throw this.#failure;
    });
  }

  #enqueue(entry: TranscriptEntry): void {
    const line: TranscriptLine = {
      seq: this.#seq,
      ts: new Date().toISOString(),
      ...entry,
    };
    this.#seq += 1;
    this.#queue = this.#queue.then(
      () => this.#write(line),
      // Keep the chain settled so one failure never leaves an unhandled
      // rejection; flush() rethrows the recorded failure instead.
    ).catch((error: unknown) => {
      this.#failure ??= error instanceof Error ? error : new Error(String(error));
    });
  }

  async #write(line: TranscriptLine): Promise<void> {
    if (!this.#directoryReady) {
      mkdirSync(dirname(this.#path), { recursive: true });
      this.#directoryReady = true;
    }
    await appendFile(this.#path, `${JSON.stringify(line)}\n`, "utf8");
  }
}

/** The event-to-line mapping; deltas and dispatch markers shape nothing. */
function eventEntry(event: AgentEvent): TranscriptEntry | undefined {
  switch (event.type) {
    case "assistant_message_complete":
      return { kind: "assistant", message: event.message };
    case "tool_execution_completed":
      return {
        kind: "tool_result",
        result: {
          tool_use_id: event.tool_use_id,
          content: event.output,
          is_error: event.is_error,
          is_terminal: event.is_terminal,
          tool_start_time: event.tool_start_time,
          tool_end_time: event.tool_end_time,
          ...(event.metadata !== undefined && { metadata: event.metadata }),
        },
      };
    case "run_finished": {
      const { outcome } = event;
      return {
        kind: "run_finished",
        outcome_status: outcome.status,
        ...(outcome.status === "cancelled" && { interrupt_reason: outcome.reason }),
        ...(outcome.status === "completed" &&
          outcome.submission !== undefined && { submission: outcome.submission }),
      };
    }
    default:
      return undefined;
  }
}

/** One byte-offset read of a transcript file. */
export interface TranscriptRead {
  data: string;
  /** Resume offset (bytes); clamped to the file size. */
  next_offset: number;
  eof: boolean;
}

/**
 * Byte-offset reader with a per-call cap. Offsets are byte positions by
 * contract: callers resume from `next_offset`, so a chunk boundary may
 * split a line (or a multibyte character) and concatenation restores it.
 */
export async function readTranscriptFile(
  path: string,
  offset: number,
  maxBytes: number,
): Promise<TranscriptRead> {
  const buffer = await readFile(path);
  const start = Math.min(Math.max(offset, 0), buffer.length);
  const end = Math.min(buffer.length, start + Math.max(maxBytes, 0));
  const slice = buffer.subarray(start, end);
  return {
    data: slice.toString("utf8"),
    next_offset: end,
    eof: end >= buffer.length,
  };
}
