import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

import type { JsonObject } from "@eos/contracts";
import type {
  LlmClient,
  LlmRequest,
  LlmStreamEvent,
  LlmStreamOptions,
  UsageSnapshot,
} from "@eos/llm-client";
import { defineTool, type ToolDefinition } from "@eos/tool";
import { z } from "zod";
import { describe, expect, it } from "vitest";

import type {
  LlmClientBinding,
  LlmClientRegistry,
} from "../src/llm-client-registry.js";
import { createAgentRuntime, type AgentRuntime } from "../src/runtime.js";
import {
  asString,
  must,
  readEventLines,
  readResultLines,
  tempDir,
  userMessage,
  writeProfile,
  type ProfileSpec,
} from "../tests/support.js";
import {
  CODEX_CLIENT_ID,
  loadConfiguredCodexRuntime,
} from "./support/codex-runtime.js";
import { finishedRun } from "./support/fixtures.js";

const PROBE_STEPS = 5;
const TOOL_DELAY_MS = Number(process.env.EOS_CACHE_BENCH_TOOL_DELAY_MS ?? "750");
const FINISH_TOOL = "finish_cache_benchmark";
const EXPECTED_SUMMARY = "cache bench done";

const codex = loadConfiguredCodexRuntime();

if (!codex.available) {
  console.warn(`agent-runtime cache benchmark skipped: ${codex.reason}`);
}

function codexBinding(): LlmClientBinding {
  if (!codex.available) {
    throw new Error("unreachable: the suite is skipped without credentials");
  }
  return codex.binding;
}

class VolatilePrefixClient implements LlmClient {
  #calls = 0;

  constructor(private readonly inner: LlmClient) {}

  streamMessage(
    request: LlmRequest,
    options?: LlmStreamOptions,
  ): AsyncIterable<LlmStreamEvent> {
    this.#calls += 1;
    return this.inner.streamMessage(
      {
        ...request,
        system_prompt: [
          `volatile-cache-bust-call: ${String(this.#calls)}`,
          `volatile-cache-bust-time: ${new Date().toISOString()}`,
          request.system_prompt ?? "",
        ].join("\n"),
      },
      options,
    );
  }
}

interface BenchmarkRuntime {
  runtime: AgentRuntime;
  dataDir: string;
}

interface BenchmarkMetrics {
  label: string;
  runId: string;
  turns: number;
  turnRates: number[];
  aggregateRate: number;
  usage: UsageSnapshot;
}

function singleClientRegistry(
  binding: LlmClientBinding,
  client: LlmClient,
): LlmClientRegistry {
  return {
    require(llmClientId) {
      if (llmClientId !== binding.id) {
        throw new Error(`unknown llm client id "${llmClientId}"`);
      }
      return { ...binding, client };
    },
  };
}

function benchmarkRuntime(
  label: string,
  binding: LlmClientBinding,
  client: LlmClient,
): BenchmarkRuntime {
  const root = tempDir(`eos-cache-bench-${label}-`);
  const profilesDir = join(root, "profiles");
  mkdirSync(profilesDir, { recursive: true });
  writeProfile(profilesDir, benchmarkProfile(label));
  const hooksPath = join(root, "hooks.json");
  writeFileSync(hooksPath, "[]\n");
  const dataDir = join(root, "data");
  const runtime = createAgentRuntime({
    agentProfilesDir: profilesDir,
    llmClients: singleClientRegistry(binding, client),
    baseTools: [cacheProbeTool(), finishBenchmarkTool()],
    hookConfigPath: hooksPath,
    dataDir,
  });
  return { runtime, dataDir };
}

function benchmarkProfile(name: string): ProfileSpec {
  return {
    name,
    kind: "main",
    llmClientId: CODEX_CLIENT_ID,
    allowed: ["cache_probe"],
    terminal: FINISH_TOOL,
    maxTurns: PROBE_STEPS + 3,
    body: benchmarkSystemPrompt(),
  };
}

function benchmarkSystemPrompt(): string {
  return [
    "You are the EOS cache benchmark agent.",
    "Follow the current user message and the latest tool result exactly.",
    "Make exactly one tool call per assistant turn. Write no prose.",
    'First call cache_probe with {"step":1}.',
    `If cache_probe returns continue:true, call cache_probe with the returned next_step on the next turn.`,
    `If cache_probe returns continue:false, call ${FINISH_TOOL} with summary exactly "${EXPECTED_SUMMARY}" on the next turn.`,
    "Stable cache anchor follows. It is intentionally long and immutable; never quote it.",
    stableAnchor(),
  ].join("\n\n");
}

function stableAnchor(): string {
  return Array.from({ length: 96 }, (_, index) => {
    const n = String(index + 1).padStart(2, "0");
    return `Cache anchor ${n}: preserve byte-stable runtime instructions, tool schemas, audit semantics, transcript ordering, usage accounting, and benchmark labels for EOS agent-core cache measurement.`;
  }).join("\n");
}

function cacheProbeTool(): ToolDefinition {
  return defineTool({
    name: "cache_probe",
    description:
      "Return the next cache benchmark instruction. Input: { step: number }.",
    input: z.object({ step: z.number().int().min(1).max(PROBE_STEPS) }),
    execute: async (input) => {
      await new Promise((resolve) => setTimeout(resolve, TOOL_DELAY_MS));
      let content: JsonObject;
      if (input.step < PROBE_STEPS) {
        const nextStep = input.step + 1;
        content = {
          continue: true,
          next_step: nextStep,
          instruction: `Call cache_probe with {"step":${String(nextStep)}}.`,
        };
      } else {
        content = {
          continue: false,
          instruction: `Call ${FINISH_TOOL} with summary "${EXPECTED_SUMMARY}".`,
        };
      }
      return { content };
    },
  });
}

function finishBenchmarkTool(): ToolDefinition {
  return defineTool({
    name: FINISH_TOOL,
    description:
      "Finish the cache benchmark. Terminal: a successful call ends the run.",
    input: z.object({ summary: z.string().min(1) }),
    isTerminal: true,
    execute: (input) => Promise.resolve({ content: { summary: input.summary } }),
  });
}

async function runBenchmarkCase(
  label: string,
  binding: LlmClientBinding,
  client: LlmClient,
): Promise<BenchmarkMetrics> {
  const { runtime } = benchmarkRuntime(label, binding, client);
  const run = runtime.startRun({
    agentName: label,
    initialMessages: [
      userMessage(
        [
          "Start the cache benchmark now.",
          'Call cache_probe with {"step":1}; continue until it tells you to finish.',
        ].join(" "),
      ),
    ],
  });
  const outcome = await run.handle.outcome;
  expect(outcome.status, `${label} completed`).toBe("completed");
  if (outcome.status !== "completed") {
    throw new Error(`${label} failed with status ${outcome.status}`);
  }
  expect(asString(summaryOf(outcome.submission))).toBe(EXPECTED_SUMMARY);
  await finishedRun(runtime, label);

  const runDir = dirname(run.transcriptPath);
  const events = readEventLines(join(runDir, "events.jsonl"));
  const results = readResultLines(join(runDir, "result.jsonl"));
  const result = must(results.at(0));
  const completedTurns = events.filter((line) => line.type === "turn_completed");
  expect(completedTurns.length, `${label} turn count`).toBeGreaterThanOrEqual(
    PROBE_STEPS,
  );
  expect(result.run_id).toBe(run.runId);
  expect(result.status).toBe("completed");
  expect(result.cache_hit_rate).toBeCloseTo(cacheHitRate(result.usage), 10);

  const metrics: BenchmarkMetrics = {
    label,
    runId: run.runId,
    turns: completedTurns.length,
    turnRates: completedTurns.map((line) => line.cache_hit_rate),
    aggregateRate: result.cache_hit_rate,
    usage: result.usage,
  };
  logMetrics(metrics);
  return metrics;
}

function summaryOf(submission: unknown): unknown {
  if (
    typeof submission !== "object" ||
    submission === null ||
    !("summary" in submission)
  ) {
    throw new Error("expected a benchmark submission object with summary");
  }
  return submission.summary;
}

function cacheHitRate(usage: UsageSnapshot): number {
  const read = usage.cache_read_input_tokens ?? 0;
  const denominator =
    usage.input_tokens + read + (usage.cache_creation_input_tokens ?? 0);
  return denominator > 0 ? read / denominator : 0;
}

function logMetrics(metrics: BenchmarkMetrics): void {
  console.log(
    [
      `[cache-bench] ${metrics.label}`,
      `run=${metrics.runId}`,
      `turns=${String(metrics.turns)}`,
      `aggregate=${formatRate(metrics.aggregateRate)}`,
      `turns=[${metrics.turnRates.map(formatRate).join(", ")}]`,
      `input=${String(metrics.usage.input_tokens)}`,
      `cache_read=${String(metrics.usage.cache_read_input_tokens ?? 0)}`,
      `cache_creation=${String(metrics.usage.cache_creation_input_tokens ?? 0)}`,
      `output=${String(metrics.usage.output_tokens)}`,
    ].join(" "),
  );
}

function formatRate(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

describe.skipIf(!codex.available)("cache hit benchmark over live codex (e2e)", () => {
  it(
    "measures stable-prefix cache hits against a cache-busted baseline",
    { timeout: 300_000 },
    async () => {
      const binding = codexBinding();
      const stable = await runBenchmarkCase("cache_stable", binding, binding.client);
      const volatile = await runBenchmarkCase(
        "cache_volatile",
        binding,
        new VolatilePrefixClient(binding.client),
      );

      expect(stable.aggregateRate, "stable aggregate cache rate is bounded").toBeGreaterThanOrEqual(0);
      expect(stable.aggregateRate, "stable aggregate cache rate is bounded").toBeLessThanOrEqual(1);
      expect(volatile.aggregateRate, "volatile aggregate cache rate is bounded").toBeGreaterThanOrEqual(0);
      expect(volatile.aggregateRate, "volatile aggregate cache rate is bounded").toBeLessThanOrEqual(1);
      console.log(
        `[cache-bench] stable_minus_volatile=${formatRate(
          stable.aggregateRate - volatile.aggregateRate,
        )}`,
      );
    },
  );
});
