import { describe, expect, it } from "vitest";

import { ProviderError } from "@eos/llm-client";

import {
  startAgentRun,
  type ToolDefinition,
  type ToolOutput,
} from "../src/index.js";
import {
  MockLlmClient,
  USAGE,
  asCancelled,
  asCompleted,
  asFailed,
  assistantMessage,
  collectEvents,
  complete,
  deferred,
  expectProviderValid,
  failingTurn,
  gatedTurn,
  hangingTurn,
  must,
  registryOf,
  reasoningDelta,
  scriptedTurn,
  startMockRun,
  textBlock,
  textDelta,
  tick,
  toolResultBlock,
  toolUseBlock,
  toolUseDelta,
  userText,
} from "./support.js";

describe("agent loop", () => {
  it("completes after one text-only turn with the stop_reason surfaced (§14.1)", async () => {
    const reply = assistantMessage(textBlock("hello"));
    const { client, handle } = startMockRun([
      scriptedTurn([textDelta("hello"), complete(reply, "end_turn")]),
    ]);
    const outcome = asCompleted(await handle.outcome);
    expect(outcome.turns).toBe(1);
    expect(outcome.stop_reason).toBe("end_turn");
    expect(outcome.final_message).toEqual(reply);
    expect(outcome.usage).toEqual(USAGE);
    expect(outcome.llm).toEqual([userText("hi"), reply]);
    expect(outcome.displayed.map((entry) => entry.message)).toEqual(outcome.llm);
    expect(client.requests).toHaveLength(1);
    expectProviderValid(outcome.llm);
  });

  it("feeds tool results back into the next provider request (§14.2)", async () => {
    const tools = registryOf(["calc", () => Promise.resolve({ content: "42" })]);
    const call = assistantMessage(toolUseBlock("tu_1", "calc", { expr: "6*7" }));
    const { client, handle } = startMockRun(
      [
        scriptedTurn([complete(call, "tool_use")]),
        scriptedTurn([complete(assistantMessage(textBlock("the answer is 42")))]),
      ],
      { tools },
    );
    const outcome = asCompleted(await handle.outcome);
    expect(outcome.turns).toBe(2);
    const secondRequest = must(client.requests.at(1));
    expect(secondRequest.messages).toEqual([
      userText("hi"),
      call,
      { role: "user", content: [toolResultBlock("tu_1", "42")] },
    ]);
    expect(secondRequest.tools).toEqual([
      { name: "calc", description: "calc", input_schema: {} },
    ]);
    expectProviderValid(outcome.llm);
  });

  it("executes a parallel batch capped at 8 into one ordered result message (§14.3)", async () => {
    let inflight = 0;
    let maxInflight = 0;
    const tools = registryOf([
      "probe",
      (input) => {
        inflight += 1;
        maxInflight = Math.max(maxInflight, inflight);
        return tick().then((): ToolOutput => {
          inflight -= 1;
          return { content: JSON.stringify(input.i) };
        });
      },
    ]);
    const calls = Array.from({ length: 12 }, (_, i) =>
      toolUseBlock(`tu_${String(i)}`, "probe", { i }),
    );
    const { client, handle } = startMockRun(
      [
        scriptedTurn([complete(assistantMessage(...calls), "tool_use")]),
        scriptedTurn([complete(assistantMessage(textBlock("done")))]),
      ],
      { tools },
    );
    const outcome = asCompleted(await handle.outcome);
    expect(maxInflight).toBe(8);
    const resultMessage = must(must(client.requests.at(1)).messages.at(-1));
    expect(resultMessage.content).toEqual(
      Array.from({ length: 12 }, (_, i) =>
        toolResultBlock(`tu_${String(i)}`, String(i)),
      ),
    );
    expectProviderValid(outcome.llm);
  });

  it("maps a thrown tool error to is_error and keeps going (§14.4)", async () => {
    const tools = registryOf(
      ["boom", () => Promise.reject(new Error("kaboom"))],
      ["ok", () => Promise.resolve({ content: "fine" })],
    );
    const { client, handle } = startMockRun(
      [
        scriptedTurn([
          complete(
            assistantMessage(toolUseBlock("tu_a", "boom"), toolUseBlock("tu_b", "ok")),
            "tool_use",
          ),
        ]),
        scriptedTurn([complete(assistantMessage(textBlock("recovered")))]),
      ],
      { tools },
    );
    const outcome = asCompleted(await handle.outcome);
    expect(must(must(client.requests.at(1)).messages.at(-1)).content).toEqual([
      toolResultBlock("tu_a", "kaboom", true),
      toolResultBlock("tu_b", "fine"),
    ]);
    expectProviderValid(outcome.llm);
  });

  it("answers an unknown tool with a not-found error result and continues (§14.5)", async () => {
    const { client, handle } = startMockRun([
      scriptedTurn([
        complete(assistantMessage(toolUseBlock("tu_g", "ghost")), "tool_use"),
      ]),
      scriptedTurn([complete(assistantMessage(textBlock("moving on")))]),
    ]);
    const outcome = asCompleted(await handle.outcome);
    expect(must(must(client.requests.at(1)).messages.at(-1)).content).toEqual([
      toolResultBlock("tu_g", "tool not found: ghost", true),
    ]);
    expectProviderValid(outcome.llm);
  });

  it("salvages an interrupted stream to displayed only and cancels with the reason (§14.6)", async () => {
    const streamed = deferred();
    const { handle } = startMockRun([
      hangingTurn([textDelta("Hello, wo")], streamed),
    ]);
    const { events, done } = collectEvents(handle);
    await streamed.promise;
    handle.interrupt("user clicked stop");
    handle.interrupt("second call ignored");
    const outcome = asCancelled(await handle.outcome);
    expect(outcome.reason).toBe("user clicked stop");
    expect(outcome.turns).toBe(0);
    expect(outcome.usage).toEqual({ input_tokens: 0, output_tokens: 0 });
    const partial = must(outcome.displayed.at(-1));
    expect(partial.partial).toBe("interrupted");
    expect(partial.message).toEqual(assistantMessage(textBlock("Hello, wo")));
    expect(outcome.llm).toEqual([userText("hi")]);
    await done;
    expect(must(events.at(-1)).type).toBe("run_finished");
    expectProviderValid(outcome.llm);
  });

  it("closes a cancelled batch with settled plus synthetic results in both lists (§14.7)", async () => {
    const fastDone = deferred();
    const tools = registryOf(
      [
        "fast",
        () => {
          fastDone.resolve();
          return Promise.resolve({ content: "fast ok" });
        },
      ],
      ["slow", () => new Promise<ToolOutput>(() => undefined)],
    );
    const { handle } = startMockRun(
      [
        scriptedTurn([
          complete(
            assistantMessage(
              toolUseBlock("tu_fast", "fast"),
              toolUseBlock("tu_slow", "slow"),
            ),
            "tool_use",
          ),
        ]),
      ],
      { tools },
    );
    await fastDone.promise;
    await tick();
    handle.interrupt();
    const outcome = asCancelled(await handle.outcome);
    expect(outcome.reason).toBe("interrupted");
    const closing = {
      role: "user",
      content: [
        toolResultBlock("tu_fast", "fast ok"),
        toolResultBlock("tu_slow", "interrupted", true),
      ],
    };
    expect(must(outcome.llm.at(-1))).toEqual(closing);
    expect(must(outcome.displayed.at(-1)).message).toEqual(closing);
    expectProviderValid(outcome.llm);
  });

  it("delivers a steer queued mid-run with the next provider request (§14.8)", async () => {
    const batchStarted = deferred();
    const releaseBatch = deferred();
    const tools = registryOf([
      "wait",
      () => {
        batchStarted.resolve();
        return releaseBatch.promise.then((): ToolOutput => ({ content: "done" }));
      },
    ]);
    const { client, handle } = startMockRun(
      [
        scriptedTurn([
          complete(assistantMessage(toolUseBlock("tu_w", "wait")), "tool_use"),
        ]),
        scriptedTurn([complete(assistantMessage(textBlock("after")))]),
      ],
      { tools },
    );
    await batchStarted.promise;
    expect(handle.steer(userText("also check Y"))).toBe(true);
    releaseBatch.resolve();
    const outcome = asCompleted(await handle.outcome);
    expect(must(client.requests.at(1)).messages).toEqual([
      userText("hi"),
      assistantMessage(toolUseBlock("tu_w", "wait")),
      { role: "user", content: [toolResultBlock("tu_w", "done")] },
      userText("also check Y"),
    ]);
    expect(outcome.llm).toContainEqual(userText("also check Y"));
    expect(outcome.displayed.map((entry) => entry.message)).toContainEqual(
      userText("also check Y"),
    );
    expectProviderValid(outcome.llm);
  });

  it("extends the run when a steer lands during the final turn (§14.9)", async () => {
    const started = deferred();
    const release = deferred();
    const { client, handle } = startMockRun([
      gatedTurn(started, release.promise, [
        complete(assistantMessage(textBlock("first"))),
      ]),
      scriptedTurn([complete(assistantMessage(textBlock("post-steer")))]),
    ]);
    await started.promise;
    expect(handle.steer(userText("one more"))).toBe(true);
    release.resolve();
    const outcome = asCompleted(await handle.outcome);
    expect(outcome.turns).toBe(2);
    expect(outcome.final_message).toEqual(assistantMessage(textBlock("post-steer")));
    expect(must(client.requests.at(1)).messages).toEqual([
      userText("hi"),
      assistantMessage(textBlock("first")),
      userText("one more"),
    ]);
    expectProviderValid(outcome.llm);
  });

  it("rejects a steer once finishing has begun and validates the role (§14.10)", async () => {
    const reply = assistantMessage(textBlock("done"));
    const { handle } = startMockRun([scriptedTurn([complete(reply)])]);
    const outcome = asCompleted(await handle.outcome);
    expect(handle.steer(userText("too late"))).toBe(false);
    expect(outcome.llm).toEqual([userText("hi"), reply]);
    expect(outcome.displayed).toHaveLength(2);
    expect(() => handle.steer(assistantMessage(textBlock("wrong role")))).toThrow(
      TypeError,
    );
  });

  it("fails with max_turns and drops a steer queued after the budget is spent (§14.11)", async () => {
    const tools = registryOf(["echo", () => Promise.resolve({ content: "ok" })]);
    const started = deferred();
    const release = deferred();
    const { client, handle } = startMockRun(
      [
        scriptedTurn([
          complete(assistantMessage(toolUseBlock("tu_1", "echo")), "tool_use"),
        ]),
        gatedTurn(started, release.promise, [
          complete(assistantMessage(toolUseBlock("tu_2", "echo")), "tool_use"),
        ]),
      ],
      { tools, maxTurns: 2 },
    );
    await started.promise;
    expect(handle.steer(userText("late steer"))).toBe(true);
    release.resolve();
    const outcome = asFailed(await handle.outcome);
    expect(outcome.failure.kind).toBe("max_turns");
    expect(outcome.turns).toBe(2);
    expect(client.requests).toHaveLength(2);
    expect(outcome.llm).not.toContainEqual(userText("late steer"));
    expect(outcome.displayed.map((entry) => entry.message)).not.toContainEqual(
      userText("late steer"),
    );
    expect(handle.steer(userText("post-finish"))).toBe(false);
    expectProviderValid(outcome.llm);
  });

  it("fails with provider_error and salvages pre-error deltas (§14.12)", async () => {
    const { handle } = startMockRun([
      failingTurn(
        [textDelta("partial out")],
        new ProviderError("server", "upstream died", { status_code: 500 }),
      ),
    ]);
    const outcome = asFailed(await handle.outcome);
    expect(outcome.failure).toEqual({
      kind: "provider_error",
      message: "upstream died",
    });
    expect(outcome.turns).toBe(0);
    const partial = must(outcome.displayed.at(-1));
    expect(partial.partial).toBe("provider_error");
    expect(partial.message).toEqual(assistantMessage(textBlock("partial out")));
    expect(outcome.llm).toEqual([userText("hi")]);
    expectProviderValid(outcome.llm);
  });

  it("classifies an engine invariant violation as internal and still finishes (§14.13)", async () => {
    const { handle } = startMockRun([scriptedTurn([textDelta("oops")])]);
    const { events, done } = collectEvents(handle);
    const outcome = asFailed(await handle.outcome);
    expect(outcome.failure.kind).toBe("internal");
    expect(outcome.failure.message).toContain(
      "provider stream ended without assistant completion",
    );
    await done;
    expect(must(events.at(-1)).type).toBe("run_finished");
    expectProviderValid(outcome.llm);
  });

  it("completes with stop_reason max_tokens on truncation (§14.14)", async () => {
    const truncated = assistantMessage(textBlock("truncat"));
    const { handle } = startMockRun([
      scriptedTurn([complete(truncated, "max_tokens")]),
    ]);
    const outcome = asCompleted(await handle.outcome);
    expect(outcome.stop_reason).toBe("max_tokens");
    expect(outcome.final_message).toEqual(truncated);
  });

  it("treats an external signal abort exactly like interrupt() (§14.15)", async () => {
    const controller = new AbortController();
    const streamed = deferred();
    const { handle } = startMockRun(
      [hangingTurn([textDelta("Hi the")], streamed)],
      { signal: controller.signal },
    );
    await streamed.promise;
    controller.abort();
    const outcome = asCancelled(await handle.outcome);
    expect(outcome.reason).toBe("interrupted");
    const partial = must(outcome.displayed.at(-1));
    expect(partial.partial).toBe("interrupted");
    expect(outcome.llm).toEqual([userText("hi")]);
    expectProviderValid(outcome.llm);
  });

  it("keeps running to completion after the consumer breaks early (§14.16)", async () => {
    const reply = assistantMessage(textBlock("a"));
    const { handle } = startMockRun([
      scriptedTurn([textDelta("a"), complete(reply)]),
    ]);
    const iterator = handle.events[Symbol.asyncIterator]();
    const first = await iterator.next();
    expect(first.done).toBe(false);
    await iterator.return?.();
    const outcome = asCompleted(await handle.outcome);
    expect(outcome.final_message).toEqual(reply);
    expectProviderValid(outcome.llm);
  });

  it("emits the golden event sequence for a two-turn tool run (§14.18)", async () => {
    const tools = registryOf(["echo", () => Promise.resolve({ content: "echoed" })]);
    const { handle } = startMockRun(
      [
        scriptedTurn([
          textDelta("calling"),
          toolUseDelta("tu_1", "echo", { v: 1 }),
          complete(
            assistantMessage(textBlock("calling"), toolUseBlock("tu_1", "echo", { v: 1 })),
            "tool_use",
          ),
        ]),
        scriptedTurn([
          reasoningDelta("hmm"),
          textDelta("done"),
          complete(assistantMessage(textBlock("done")), "end_turn", {
            input_tokens: 7,
            output_tokens: 3,
          }),
        ]),
      ],
      { tools },
    );
    const { events, done } = collectEvents(handle);
    const outcome = asCompleted(await handle.outcome);
    await done;
    expect(events.map((event) => event.type)).toEqual([
      "turn_started",
      "assistant_text_delta",
      "tool_use_delta",
      "assistant_message_complete",
      "tool_execution_started",
      "tool_execution_completed",
      "turn_started",
      "reasoning_delta",
      "assistant_text_delta",
      "assistant_message_complete",
      "run_finished",
    ]);
    expect(must(events.at(0))).toEqual({ type: "turn_started", turn: 1 });
    expect(must(events.at(6))).toEqual({ type: "turn_started", turn: 2 });
    const last = must(events.at(-1));
    expect(last.type).toBe("run_finished");
    if (last.type === "run_finished") expect(last.outcome).toBe(outcome);
    expect(outcome.usage).toEqual({ input_tokens: 17, output_tokens: 8 });
    expect(outcome.turns).toBe(2);
    expectProviderValid(outcome.llm);
  });

  it("rejects empty initialMessages with a TypeError", () => {
    const client = new MockLlmClient([]);
    expect(() =>
      startAgentRun({
        llmClient: client,
        tools: new Map<string, ToolDefinition>(),
        model: "mock-model",
        initialMessages: [],
      }),
    ).toThrow(TypeError);
  });
});
