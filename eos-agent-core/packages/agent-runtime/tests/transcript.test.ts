import { closeSync, openSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";

import { describe, expect, it } from "vitest";

import { agentRunIdFrom, toolUseIdFrom } from "@eos/contracts";
import type { AgentEvent, AgentRunOutcome } from "@eos/engine";
import type { UsageSnapshot } from "@eos/llm-client";

import {
  RunLog,
  readTranscriptFile,
  runTranscriptPath,
  type EventLine,
  type ResultLine,
  type RunLogMeta,
  type TranscriptLine,
} from "../src/transcript.js";
import {
  assistantMessage,
  must,
  readEventLines,
  readResultLines,
  readTranscriptLines,
  tempDir,
  textBlock,
  toolUseBlock,
} from "./support.js";

const BASE_USAGE: UsageSnapshot = { input_tokens: 1, output_tokens: 1 };

function meta(runId = "r-1", overrides: Partial<RunLogMeta> = {}): RunLogMeta {
  return {
    run_id: agentRunIdFrom(runId),
    agent_name: "worker",
    agent_kind: "worker",
    llm_client_id: "codex",
    model_id: "gpt-test",
    reasoning_effort: "low",
    max_turns: 8,
    ...overrides,
  };
}

function completedOutcome(
  usage: UsageSnapshot = BASE_USAGE,
  turns = 1,
): AgentRunOutcome {
  return {
    displayed: [],
    llm: [],
    usage,
    turns,
    status: "completed",
    final_message: assistantMessage(textBlock("done")),
    submission: { summary: "done" },
  };
}

function cancelledOutcome(reason: string): AgentRunOutcome {
  return {
    displayed: [],
    llm: [],
    usage: BASE_USAGE,
    turns: 1,
    status: "cancelled",
    reason,
  };
}

function runPath(dataDir: string, runId: string, file: string): string {
  return join(dataDir, "runs", runId, file);
}

function mergeSeqs(
  events: readonly EventLine[],
  transcript: readonly TranscriptLine[],
  result: readonly ResultLine[],
): number[] {
  return [...events, ...transcript, ...result]
    .map((line) => line.seq)
    .sort((a, b) => a - b);
}

describe("run log and transcript reader", () => {
  it("nests one transcript per run under <dataDir>/runs/<run_id>", () => {
    expect(runTranscriptPath("/data", "r-1")).toBe(
      join("/data", "runs", "r-1", "transcript.jsonl"),
    );
  });

  it("records lifecycle, transcript, and result lines with one shared seq", async () => {
    const dataDir = tempDir("eos-run-log-");
    const runId = "r-1";
    const usage: UsageSnapshot = {
      input_tokens: 2,
      output_tokens: 1,
      cache_read_input_tokens: 2,
    };
    const log = new RunLog(dataDir, meta(runId));
    const assistant = assistantMessage(
      textBlock("calling"),
      toolUseBlock("tu_1", "probe", { q: 1 }),
    );

    log.appendUser("initial", { role: "user", content: [textBlock("hi")] });
    const events: AgentEvent[] = [
      { type: "turn_started", turn: 1 },
      { type: "assistant_text_delta", text: "calling" },
      { type: "reasoning_delta", text: "reason" },
      {
        type: "tool_use_delta",
        tool_use_id: toolUseIdFrom("tu_1"),
        name: "probe",
        input: { q: 1 },
      },
      {
        type: "assistant_message_complete",
        message: assistant,
        usage,
        stop_reason: "tool_use",
      },
      {
        type: "tool_execution_started",
        tool_use_id: toolUseIdFrom("tu_1"),
        name: "probe",
        input: { q: 1 },
      },
      {
        type: "tool_execution_completed",
        tool_use_id: toolUseIdFrom("tu_1"),
        name: "probe",
        output: "ran",
        is_error: false,
        is_terminal: false,
        tool_start_time: 10,
        tool_end_time: 42,
        metadata: { hook_contexts: ["ctx"] },
      },
      { type: "run_finished", outcome: completedOutcome(usage) },
    ];
    for (const event of events) log.append(event);
    await log.flush();

    const transcript = readTranscriptLines(runTranscriptPath(dataDir, runId));
    const eventLines = readEventLines(runPath(dataDir, runId, "events.jsonl"));
    const result = readResultLines(runPath(dataDir, runId, "result.jsonl"));

    expect(readdirSync(dirname(log.transcriptPath)).sort()).toEqual([
      "events.jsonl",
      "result.jsonl",
      "transcript.jsonl",
    ]);
    expect(must(eventLines.at(0))).toMatchObject({
      seq: 0,
      type: "run_started",
      run_id: runId,
      agent_name: "worker",
      agent_kind: "worker",
      llm_client_id: "codex",
      model_id: "gpt-test",
      reasoning_effort: "low",
      max_turns: 8,
    });
    const eventTypes: string[] = eventLines.map((line) => line.type);
    expect(eventTypes).toEqual([
      "run_started",
      "turn_started",
      "turn_completed",
      "tool_started",
      "tool_completed",
      "run_finished",
    ]);
    expect(eventTypes, "delta events are never durable audit lines").not.toContain(
      "assistant_text_delta",
    );
    expect(
      eventLines.filter((line) => line.type === "turn_completed"),
    ).toEqual([
      expect.objectContaining({
        turn: 1,
        stop_reason: "tool_use",
        usage,
        cache_hit_rate: 0.5,
      }),
    ]);
    expect(eventLines).toContainEqual(
      expect.objectContaining({
        type: "tool_completed",
        tool_use_id: "tu_1",
        name: "probe",
        is_error: false,
        is_terminal: false,
        duration_ms: 32,
      }),
    );

    expect(transcript.map((line) => line.kind)).toEqual([
      "user",
      "assistant",
      "tool_result",
      "run_finished",
    ]);
    expect(
      transcript.map((line) => line.seq),
      "transcript seq is sparse because events and result share the counter",
    ).toEqual([1, 3, 7, 9]);
    expect(transcript[1]).toMatchObject({ kind: "assistant", message: assistant });
    expect(transcript[2]).toMatchObject({
      kind: "tool_result",
      result: {
        tool_use_id: "tu_1",
        content: "ran",
        is_error: false,
        metadata: { hook_contexts: ["ctx"] },
      },
    });
    expect(transcript[3]).toMatchObject({
      kind: "run_finished",
      outcome_status: "completed",
      submission: { summary: "done" },
    });

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      run_id: runId,
      agent_name: "worker",
      agent_kind: "worker",
      llm_client_id: "codex",
      model_id: "gpt-test",
      status: "completed",
      submission: { summary: "done" },
      turns: 1,
      usage,
      cache_hit_rate: 0.5,
    });
    expect(result[0].duration_ms).toBeGreaterThanOrEqual(0);
    expect(Date.parse(result[0].started_at)).not.toBeNaN();
    expect(Date.parse(result[0].finished_at)).not.toBeNaN();

    const seqs = mergeSeqs(eventLines, transcript, result);
    expect(seqs, "shared seq has no duplicates or gaps").toEqual(
      seqs.map((_, index) => index),
    );
  });

  it("pairs completed turns with per-turn usage and cache hit rate", async () => {
    const dataDir = tempDir("eos-run-log-");
    const runId = "r-2";
    const first: UsageSnapshot = { input_tokens: 5, output_tokens: 2 };
    const second: UsageSnapshot = {
      input_tokens: 2,
      output_tokens: 3,
      cache_read_input_tokens: 6,
      cache_creation_input_tokens: 2,
    };
    const log = new RunLog(dataDir, meta(runId));
    log.append({ type: "turn_started", turn: 1 });
    log.append({
      type: "assistant_message_complete",
      message: assistantMessage(textBlock("one")),
      usage: first,
      stop_reason: "end_turn",
    });
    log.append({ type: "turn_started", turn: 2 });
    log.append({
      type: "assistant_message_complete",
      message: assistantMessage(textBlock("two")),
      usage: second,
      stop_reason: "end_turn",
    });
    log.append({
      type: "run_finished",
      outcome: completedOutcome(
        {
          input_tokens: 7,
          output_tokens: 5,
          cache_read_input_tokens: 6,
          cache_creation_input_tokens: 2,
        },
        2,
      ),
    });
    await log.flush();

    const completed = readEventLines(runPath(dataDir, runId, "events.jsonl"))
      .filter((line) => line.type === "turn_completed");
    expect(completed).toEqual([
      expect.objectContaining({
        turn: 1,
        usage: first,
        cache_hit_rate: 0,
      }),
      expect.objectContaining({
        turn: 2,
        usage: second,
        cache_hit_rate: 0.6,
      }),
    ]);
  });

  it("leaves an unmatched turn_started line when a turn dies before completion", async () => {
    const dataDir = tempDir("eos-run-log-");
    const runId = "r-3";
    const log = new RunLog(dataDir, meta(runId));
    log.append({ type: "turn_started", turn: 1 });
    log.append({ type: "run_finished", outcome: cancelledOutcome("interrupted") });
    await log.flush();

    const events = readEventLines(runPath(dataDir, runId, "events.jsonl"));
    expect(events.map((line) => line.type)).toEqual([
      "run_started",
      "turn_started",
      "run_finished",
    ]);
    expect(events.some((line) => line.type === "turn_completed")).toBe(false);
    expect(readResultLines(runPath(dataDir, runId, "result.jsonl"))).toEqual([
      expect.objectContaining({
        status: "cancelled",
        interrupt_reason: "interrupted",
        usage: BASE_USAGE,
      }),
    ]);
  });

  it("returns increments whose concatenation is the whole transcript file", async () => {
    const dataDir = tempDir("eos-run-log-");
    const runId = "r-4";
    const log = new RunLog(dataDir, meta(runId));
    const path = runTranscriptPath(dataDir, runId);
    log.appendUser("initial", { role: "user", content: [textBlock("abcdef")] });
    log.append({ type: "run_finished", outcome: completedOutcome() });
    await log.flush();

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
    const dataDir = tempDir("eos-run-log-");
    const runId = "r-5";
    const log = new RunLog(dataDir, meta(runId));
    const path = runTranscriptPath(dataDir, runId);
    log.appendUser("initial", { role: "user", content: [textBlock("x")] });
    await log.flush();
    const size = (await readTranscriptFile(path, 0, 1_000_000)).next_offset;
    expect(await readTranscriptFile(path, size + 50, 10)).toEqual({
      data: "",
      next_offset: size,
      eof: true,
    });
  });

  it("flush surfaces a write failure instead of leaving it unhandled", async () => {
    const root = tempDir("eos-run-log-");
    const blockedPath = join(root, "not-a-dir");
    closeSync(openSync(blockedPath, "w"));
    const log = new RunLog(blockedPath, meta("r-6"));
    await expect(log.flush()).rejects.toThrow();
  });
});
