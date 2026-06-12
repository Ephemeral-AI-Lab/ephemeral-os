import { describe, expect, it } from "vitest";
import { z } from "zod";

import { defineTool } from "../../src/tool/define.js";
import { bindTool } from "../../src/tool/pipeline.js";
import type { ToolCallContext } from "../../src/tool/contract.js";
import { batchFixture, call, contentText, scopeFixture, RUN_ID } from "./support.js";

const echo = defineTool({
  name: "echo",
  description: "echo the input",
  input: z.object({ value: z.string() }),
  execute: (input) => Promise.resolve({ output: { echoed: input.value }, metadata: { len: input.value.length } }),
});

describe("bindTool pipeline", () => {
  it("parses, executes, and stamps a structured success result", async () => {
    const { scope } = scopeFixture();
    const bound = bindTool(echo, scope);
    const result = await bound.run(call("t1", "echo", { value: "hi" }), batchFixture());
    expect(result).toMatchObject({
      content: { echoed: "hi" },
      is_error: false,
      is_terminal: false,
      metadata: { len: 2 },
    });
    expect(result.tool_end_time).toBeGreaterThanOrEqual(result.tool_start_time);
  });

  it("hands execute the full call context and capabilities", async () => {
    const { scope } = scopeFixture();
    let seen: ToolCallContext | undefined;
    const probe = defineTool({
      name: "probe",
      description: "capture ctx",
      input: z.object({}),
      execute: (_input, ctx) => {
        seen = ctx;
        return Promise.resolve({ output: "ok" });
      },
    });
    const batch = batchFixture({
      llmMessages: [{ role: "user", content: [{ type: "text", text: "history" }] }],
    });
    await bindTool(probe, scope).run(call("t9", "probe"), batch);
    expect(seen?.runId).toBe(RUN_ID);
    expect(seen?.toolUseId).toBe("t9");
    expect(seen?.llmMessages, "the batch snapshot rides the context").toBe(batch.llmMessages);
    expect(seen?.backgroundTaskSupervisor).toBe(scope.backgroundTaskSupervisor);
    expect(seen?.notifier).toBe(scope.notifier);
    expect(seen?.signal).toBe(batch.signal);
  });

  it("maps an input shape mismatch to an in-run error result", async () => {
    const { scope } = scopeFixture();
    const result = await bindTool(echo, scope).run(
      call("t2", "echo", { value: 42 }),
      batchFixture(),
    );
    expect(result.is_error).toBe(true);
    expect(contentText(result.content)).toContain("invalid input for echo");
    expect(result.tool_start_time, "rejections stamp both clocks together").toBe(
      result.tool_end_time,
    );
  });

  it("never executes a call denied by preToolUse and surfaces the reason", async () => {
    let executed = 0;
    const guarded = defineTool({
      name: "guarded",
      description: "x",
      input: z.object({}),
      execute: () => {
        executed += 1;
        return Promise.resolve({ output: "ran" });
      },
    });
    const { scope } = scopeFixture([
      {
        event: "preToolUse",
        matcher: { toolName: "guarded" },
        run: () => ({ decision: "deny", reason: "blocked by policy" }),
      },
    ]);
    const result = await bindTool(guarded, scope).run(call("t3", "guarded"), batchFixture());
    expect(executed, "the call never executed").toBe(0);
    expect(result).toMatchObject({ is_error: true, content: "blocked by policy" });
  });

  it("replaces the executed result when postToolUse denies", async () => {
    const { scope } = scopeFixture([
      {
        event: "postToolUse",
        run: () => ({ decision: "deny", reason: "output rejected" }),
      },
    ]);
    const result = await bindTool(echo, scope).run(
      call("t4", "echo", { value: "hi" }),
      batchFixture(),
    );
    expect(result).toMatchObject({ is_error: true, content: "output rejected" });
  });

  it("maps a throwing execute to an error result and still runs post hooks", async () => {
    const seen: string[] = [];
    const thrower = defineTool({
      name: "thrower",
      description: "x",
      input: z.object({}),
      execute: () => Promise.reject(new Error("disk full")),
    });
    const { scope } = scopeFixture([
      {
        event: "postToolUse",
        run: (_call, result) => {
          seen.push("error" in result ? result.error : "unexpected");
          return { decision: "passthrough" };
        },
      },
    ]);
    const result = await bindTool(thrower, scope).run(call("t5", "thrower"), batchFixture());
    expect(result).toMatchObject({ is_error: true, content: "disk full" });
    expect(seen, "post hooks observed the failure").toEqual(["disk full"]);
  });

  it("settles as interrupted when the batch signal is already aborted", async () => {
    const { scope } = scopeFixture();
    const result = await bindTool(echo, scope).run(
      call("t6", "echo", { value: "hi" }),
      batchFixture({ signal: AbortSignal.abort() }),
    );
    expect(result).toMatchObject({ is_error: true, content: "interrupted" });
  });
});
