import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { toolUseIdFrom } from "@eos/contracts";
import type { AgentEvent, AgentRunOutcome } from "@eos/engine";

import {
  TranscriptWriter,
  readTranscriptFile,
  runTranscriptPath,
} from "../src/transcript.js";
import {
  assistantMessage,
  must,
  readTranscriptLines,
  tempDir,
  textBlock,
  toolUseBlock,
} from "./support.js";

const OUTCOME_BASE = {
  displayed: [],
  llm: [],
  usage: { input_tokens: 1, output_tokens: 1 },
  turns: 1,
};

function completedOutcome(): AgentRunOutcome {
  return {
    ...OUTCOME_BASE,
    status: "completed",
    final_message: assistantMessage(textBlock("done")),
    submission: { summary: "done" },
  };
}

function cancelledOutcome(reason: string): AgentRunOutcome {
  return { ...OUTCOME_BASE, status: "cancelled", reason };
}

describe("transcript writer and reader", () => {
  it("nests one transcript per run under <dataDir>/runs/<run_id>", () => {
    expect(runTranscriptPath("/data", "r-1")).toBe(
      join("/data", "runs", "r-1", "transcript.jsonl"),
    );
  });

  it("records every conversation-shaping event in order, skipping deltas (§13.9)", async () => {
    const path = runTranscriptPath(tempDir("eos-transcript-"), "r-1");
    const writer = new TranscriptWriter(path);
    const assistant = assistantMessage(
      textBlock("calling"),
      toolUseBlock("tu_1", "probe", { q: 1 }),
    );
    writer.appendUser("initial", { role: "user", content: [textBlock("hi")] });
    const events: AgentEvent[] = [
      { type: "turn_started", turn: 1 },
      { type: "assistant_text_delta", text: "calling" },
      {
        type: "tool_execution_started",
        tool_use_id: toolUseIdFrom("tu_1"),
        name: "probe",
        input: { q: 1 },
      },
      { type: "assistant_message_complete", message: assistant, usage: { input_tokens: 1, output_tokens: 1 }, stop_reason: "tool_use" },
      {
        type: "tool_execution_completed",
        tool_use_id: toolUseIdFrom("tu_1"),
        name: "probe",
        output: "ran",
        is_error: false,
        is_terminal: false,
        tool_start_time: 1,
        tool_end_time: 2,
        metadata: { hook_contexts: ["ctx"] },
      },
      { type: "run_finished", outcome: completedOutcome() },
    ];
    for (const event of events) writer.append(event);
    await writer.flush();

    const lines = readTranscriptLines(path);
    expect(lines.map((line) => line.kind)).toEqual([
      "user",
      "assistant",
      "tool_result",
      "run_finished",
    ]);
    expect(lines.map((line) => line.seq), "seq is dense and ordered").toEqual([
      0, 1, 2, 3,
    ]);
    expect(lines.every((line) => typeof line.ts === "string")).toBe(true);
    expect(lines[0]).toMatchObject({ kind: "user", origin: "initial" });
    expect(lines[1]).toMatchObject({ kind: "assistant", message: assistant });
    expect(lines[2]).toMatchObject({
      kind: "tool_result",
      result: {
        tool_use_id: "tu_1",
        content: "ran",
        is_error: false,
        metadata: { hook_contexts: ["ctx"] },
      },
    });
    expect(lines[3]).toMatchObject({
      kind: "run_finished",
      outcome_status: "completed",
      submission: { summary: "done" },
    });
  });

  it("records the interrupt reason on a cancelled run_finished line (§8)", async () => {
    const path = runTranscriptPath(tempDir("eos-transcript-"), "r-2");
    const writer = new TranscriptWriter(path);
    writer.append({ type: "run_finished", outcome: cancelledOutcome("caller_disposed") });
    await writer.flush();
    expect(must(readTranscriptLines(path).at(0))).toMatchObject({
      kind: "run_finished",
      outcome_status: "cancelled",
      interrupt_reason: "caller_disposed",
    });
  });

  it("returns increments whose concatenation is the whole file (§13.9)", async () => {
    const path = runTranscriptPath(tempDir("eos-transcript-"), "r-3");
    const writer = new TranscriptWriter(path);
    writer.appendUser("initial", { role: "user", content: [textBlock("abcdef")] });
    writer.append({ type: "run_finished", outcome: completedOutcome() });
    await writer.flush();

    const whole = await readTranscriptFile(path, 0, 1_000_000);
    expect(whole.eof).toBe(true);

    let offset = 0;
    let assembled = "";
    let rounds = 0;
    for (;;) {
      const read = await readTranscriptFile(path, offset, 7);
      expect(read.next_offset, `round ${String(rounds)} advances`).toBeGreaterThanOrEqual(
        offset,
      );
      assembled += read.data;
      offset = read.next_offset;
      rounds += 1;
      if (read.eof) break;
    }
    expect(assembled).toBe(whole.data);
    expect(offset).toBe(whole.next_offset);
  });

  it("clamps reads past the end to an empty eof chunk", async () => {
    const path = runTranscriptPath(tempDir("eos-transcript-"), "r-4");
    const writer = new TranscriptWriter(path);
    writer.appendUser("initial", { role: "user", content: [textBlock("x")] });
    await writer.flush();
    const size = (await readTranscriptFile(path, 0, 1_000_000)).next_offset;
    expect(await readTranscriptFile(path, size + 50, 10)).toEqual({
      data: "",
      next_offset: size,
      eof: true,
    });
  });

  it("flush surfaces a write failure instead of leaving it unhandled", async () => {
    // A directory path cannot be appended to as a file.
    const dir = tempDir("eos-transcript-");
    const writer = new TranscriptWriter(dir);
    writer.appendUser("initial", { role: "user", content: [textBlock("x")] });
    await expect(writer.flush()).rejects.toThrow();
  });
});
