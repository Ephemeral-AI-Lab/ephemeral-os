import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { JsonlRunRecorder } from "../../src/runtime/records.js";

function recorderFixture(): { recorder: JsonlRunRecorder; dir: string } {
  const dir = mkdtempSync(join(tmpdir(), "eos-recorder-"));
  return { recorder: new JsonlRunRecorder(dir, "run-1"), dir };
}

function lines(dir: string, file: string): { seq: number; type?: string; kind?: string }[] {
  return readFileSync(join(dir, "run-1", file), "utf8")
    .trim()
    .split("\n")
    .map((line) => JSON.parse(line) as { seq: number; type?: string; kind?: string });
}

describe("JsonlRunRecorder", () => {
  it("appends lifecycle events with their engine seq and skips stream members", async () => {
    const { recorder, dir } = recorderFixture();
    recorder.event({ seq: 0, type: "run_started", run_id: "run-1" as never, agent_name: "a" });
    recorder.event({ seq: 1, type: "turn_started", turn: 1 });
    recorder.event({ seq: 2, type: "assistant_text_delta", text: "skip me" });
    recorder.event({
      seq: 3,
      type: "assistant_message_complete",
      message: { role: "assistant", content: [] },
      usage: { input_tokens: 1, output_tokens: 1 },
    });
    recorder.event({
      seq: 4,
      type: "run_finished",
      outcome: {
        status: "completed",
        outcome: "done",
        usage: { input_tokens: 1, output_tokens: 1 },
        turns: 1,
      },
    });
    await recorder.flush();
    const events = lines(dir, "events.jsonl");
    expect(events.map((line) => line.type)).toEqual([
      "run_started",
      "turn_started",
      "run_finished",
    ]);
    expect(events.map((line) => line.seq), "engine seq preserved, holes allowed").toEqual([
      0, 1, 4,
    ]);
  });

  it("numbers conversation lines independently", async () => {
    const { recorder, dir } = recorderFixture();
    recorder.message({
      kind: "user",
      origin: "initial",
      message: { role: "user", content: [] },
    });
    recorder.message({
      kind: "assistant",
      message: { role: "assistant", content: [] },
    });
    await recorder.flush();
    const messages = lines(dir, "messages.jsonl");
    expect(messages.map((line) => [line.seq, line.kind])).toEqual([
      [0, "user"],
      [1, "assistant"],
    ]);
  });

  it("latches a write failure and resurfaces it on flush", async () => {
    const recorder = new JsonlRunRecorder("/dev/null/not-a-dir", "run-1");
    recorder.event({ seq: 0, type: "turn_started", turn: 1 });
    await expect(recorder.flush()).rejects.toThrow();
  });
});
