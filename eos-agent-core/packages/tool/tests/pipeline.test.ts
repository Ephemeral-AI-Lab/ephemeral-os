import { describe, expect, it } from "vitest";

import { scriptedRunState, scriptedTool } from "@eos/testkit";
import { z } from "zod";

import { defineTool } from "../src/define.js";
import type { HookConfigEntry } from "../src/hooks/protocol.js";
import { live, preHook, runPipeline, sleep } from "./support.js";

describe("tool pipeline", () => {
  it("returns interrupted without executing when the signal is already aborted (§15.10)", async () => {
    let executed = false;
    const tool = scriptedTool({
      name: "probe",
      execute: () => {
        executed = true;
        return Promise.resolve({ content: "ran" });
      },
    });
    const controller = new AbortController();
    controller.abort();
    const result = await runPipeline(tool, { signal: controller.signal });
    expect(result).toMatchObject({ content: "interrupted", is_error: true });
    expect(executed).toBe(false);
    expect(result.tool_end_time, "rejection stamps one instant").toBe(
      result.tool_start_time,
    );
  });

  it("denies a stale isolated-mode call before parse and hooks (§15.10, §15.14)", async () => {
    let executed = false;
    let hookRan = false;
    const tool = defineTool({
      name: "write",
      description: "write",
      input: z.object({ path: z.string() }),
      execute: () => {
        executed = true;
        return Promise.resolve({ content: "wrote" });
      },
    });
    const result = await runPipeline(tool, {
      // Invalid input on purpose: the guard must win, proving it runs first.
      input: { wrong: true },
      runState: scriptedRunState("worker", { isIsolated: true }),
      entries: [
        preHook(() => {
          hookRan = true;
          return {};
        }),
      ],
    });
    expect(result.is_error).toBe(true);
    expect(result.content).toContain("not available while the workspace is isolated");
    expect(executed, "execute never ran").toBe(false);
    expect(hookRan, "hooks never ran").toBe(false);
  });

  it("rejects invalid input with a zod summary before hooks run (§15.10)", async () => {
    let hookRan = false;
    const tool = defineTool({
      name: "calc",
      description: "calc",
      input: z.object({ n: z.number() }),
      execute: () => Promise.resolve({ content: "ok" }),
    });
    const result = await runPipeline(tool, {
      input: { n: "not a number" },
      entries: [
        preHook(() => {
          hookRan = true;
          return {};
        }),
      ],
    });
    expect(result.is_error).toBe(true);
    expect(result.content).toContain("invalid input for calc");
    expect(result.content).toContain("n:");
    expect(hookRan).toBe(false);
  });

  it("runs pre-hooks, execute, then post-hooks, timing execute only (§15.10)", async () => {
    const order: string[] = [];
    const tool = scriptedTool({
      name: "probe",
      execute: () => {
        order.push("execute");
        return Promise.resolve({ content: "ran" });
      },
    });
    const entries: HookConfigEntry[] = [
      {
        event: "PreToolUse",
        hooks: [
          {
            type: "callback",
            run: async () => {
              order.push("pre");
              await sleep(120);
              return {};
            },
          },
        ],
      },
      {
        event: "PostToolUse",
        hooks: [
          {
            type: "callback",
            run: async () => {
              order.push("post");
              await sleep(120);
              return {};
            },
          },
        ],
      },
    ];
    const before = Date.now();
    const result = await runPipeline(tool, { entries });
    const elapsed = Date.now() - before;
    expect(order).toEqual(["pre", "execute", "post"]);
    expect(result.is_error).toBe(false);
    const toolTime = result.tool_end_time - result.tool_start_time;
    expect(elapsed, "the wall clock includes both hook sleeps").toBeGreaterThanOrEqual(200);
    expect(toolTime, "the stamped clock brackets execute only").toBeLessThan(100);
  });

  it("denies from a pre-hook without executing and surfaces the reason (§15.11)", async () => {
    let executed = false;
    let failureHookRan = false;
    const tool = scriptedTool({
      name: "guarded",
      execute: () => {
        executed = true;
        return Promise.resolve({ content: "ran" });
      },
    });
    const result = await runPipeline(tool, {
      entries: [
        preHook(() => ({ decision: "deny", reason: "not today" })),
        {
          event: "PostToolUseFailure",
          hooks: [
            {
              type: "callback",
              run: () => {
                failureHookRan = true;
                return Promise.resolve({});
              },
            },
          ],
        },
      ],
    });
    expect(result).toMatchObject({ content: "not today", is_error: true });
    expect(executed).toBe(false);
    expect(failureHookRan, "a deny is not an execute failure").toBe(false);
  });

  it("passes runtime background-session snapshots to pre-hooks", async () => {
    let executed = false;
    let seen: string[] = [];
    const tool = scriptedTool({
      name: "submit_main_outcome",
      isTerminal: true,
      execute: () => {
        executed = true;
        return Promise.resolve({ content: { summary: "done" } });
      },
    });
    const result = await runPipeline(tool, {
      input: { summary: "done" },
      hookPayloadFacts: () => ({
        background_sessions: [
          {
            type: "subagent",
            id: "run_child",
            status: "running",
            started_at: "2026-06-11T00:00:00.000Z",
          },
        ],
      }),
      entries: [
        {
          event: "PreToolUse",
          matcher: "submit_main_outcome",
          hooks: [
            {
              type: "callback",
              run: (payload) => {
                seen = (payload.background_sessions ?? []).map(
                  (session) => `${session.type}:${session.id} (${session.status})`,
                );
                return Promise.resolve({
                  decision: "deny",
                  reason: `cannot submit while ${String(seen.length)} background session(s) are open: ${seen.join(", ")}`,
                });
              },
            },
          ],
        },
      ],
    });
    expect(result.is_error).toBe(true);
    expect(result.content).toContain("subagent:run_child (running)");
    expect(seen).toEqual(["subagent:run_child (running)"]);
    expect(executed).toBe(false);
  });

  it("applies a single updatedInput re-validated through the same schema (§15.12)", async () => {
    const received: number[] = [];
    const tool = defineTool({
      name: "calc",
      description: "calc",
      input: z.object({ n: z.number() }),
      execute: (input) => {
        received.push(input.n);
        return Promise.resolve({ content: "ok" });
      },
    });
    const result = await runPipeline(tool, {
      input: { n: 1 },
      entries: [preHook(() => ({ updatedInput: { n: 42 } }))],
    });
    expect(result.is_error).toBe(false);
    expect(received).toEqual([42]);
  });

  it("rejects an updatedInput the schema refuses (§15.12)", async () => {
    let executed = false;
    const tool = defineTool({
      name: "calc",
      description: "calc",
      input: z.object({ n: z.number() }),
      execute: () => {
        executed = true;
        return Promise.resolve({ content: "ok" });
      },
    });
    const result = await runPipeline(tool, {
      input: { n: 1 },
      entries: [preHook(() => ({ updatedInput: { n: "boom" } }))],
    });
    expect(result.is_error).toBe(true);
    expect(result.content).toContain("hook updatedInput rejected by calc schema");
    expect(executed).toBe(false);
  });

  it("denies when two hooks supply conflicting updates (§15.12)", async () => {
    let executed = false;
    const tool = defineTool({
      name: "calc",
      description: "calc",
      input: z.object({ n: z.number() }),
      execute: () => {
        executed = true;
        return Promise.resolve({ content: "ok" });
      },
    });
    const result = await runPipeline(tool, {
      input: { n: 1 },
      entries: [
        preHook(() => ({ updatedInput: { n: 2 } })),
        preHook(() => ({ updatedInput: { n: 3 } })),
      ],
    });
    expect(result.is_error).toBe(true);
    expect(result.content).toContain("conflicting updatedInput");
    expect(executed).toBe(false);
  });

  it("routes a thrown execute to PostToolUseFailure with the error text (§15.10)", async () => {
    const seen: (string | undefined)[] = [];
    let postRan = false;
    const tool = scriptedTool({
      name: "boom",
      isTerminal: true,
      execute: () => Promise.reject(new Error("kaboom")),
    });
    const result = await runPipeline(tool, {
      entries: [
        {
          event: "PostToolUseFailure",
          hooks: [
            {
              type: "callback",
              run: (payload) => {
                seen.push(payload.error);
                return Promise.resolve({});
              },
            },
          ],
        },
        {
          event: "PostToolUse",
          hooks: [
            {
              type: "callback",
              run: () => {
                postRan = true;
                return Promise.resolve({});
              },
            },
          ],
        },
      ],
    });
    expect(result).toMatchObject({ content: "kaboom", is_error: true });
    expect(result.is_terminal, "a failed terminal call never terminates").toBe(false);
    expect(seen).toEqual(["kaboom"]);
    expect(postRan, "PostToolUse is the resolve path only").toBe(false);
  });

  it("treats a resolved error outcome as the resolve path for hooks", async () => {
    const responses: (string | undefined)[] = [];
    const tool = scriptedTool({
      name: "soft-fail",
      execute: () => Promise.resolve({ content: "file missing", isError: true }),
    });
    const result = await runPipeline(tool, {
      entries: [
        {
          event: "PostToolUse",
          hooks: [
            {
              type: "callback",
              run: (payload) => {
                responses.push(payload.tool_response);
                return Promise.resolve({});
              },
            },
          ],
        },
      ],
    });
    expect(result).toMatchObject({ content: "file missing", is_error: true });
    expect(responses).toEqual(["file missing"]);
  });

  it("accumulates additionalContext under metadata.hook_contexts in stage order (04.5 §11)", async () => {
    const tool = scriptedTool({
      name: "probe",
      execute: () => Promise.resolve({ content: "ran", metadata: { cost: 1 } }),
    });
    const result = await runPipeline(tool, {
      entries: [
        preHook(() => ({ additionalContext: "lint config changed recently" })),
        {
          event: "PostToolUse",
          hooks: [
            {
              type: "callback",
              run: () => Promise.resolve({ additionalContext: "remember to rerun CI" }),
            },
          ],
        },
      ],
    });
    expect(result.is_error).toBe(false);
    expect(result.metadata, "tool metadata survives beside the hook transport").toMatchObject({
      cost: 1,
    });
    expect(result.metadata?.hook_contexts).toEqual([
      "lint config changed recently",
      "remember to rerun CI",
    ]);
  });

  it("carries hook_contexts on a deny so the engine can still publish them (04.5 §11)", async () => {
    const tool = scriptedTool({
      name: "guarded",
      execute: () => Promise.resolve({ content: "ran" }),
    });
    const result = await runPipeline(tool, {
      entries: [
        preHook(() => ({
          decision: "deny",
          reason: "not today",
          additionalContext: "the repo is frozen for release",
        })),
      ],
    });
    expect(result.is_error).toBe(true);
    expect(result.metadata?.hook_contexts).toEqual(["the repo is frozen for release"]);
  });

  it("accumulates hook warnings under metadata.hook_warnings (§15.13)", async () => {
    const tool = scriptedTool({
      name: "probe",
      execute: () => Promise.resolve({ content: "ran", metadata: { cost: 1 } }),
    });
    const result = await runPipeline(tool, {
      entries: [
        {
          event: "PreToolUse",
          hooks: [
            {
              type: "callback",
              run: () => Promise.reject(new Error("hook crashed")),
            },
          ],
        },
      ],
    });
    expect(result.is_error, "a crashed hook is non-blocking").toBe(false);
    expect(result.metadata).toMatchObject({ cost: 1 });
    expect(result.metadata?.hook_warnings).toEqual([
      "callback hook failed: hook crashed",
    ]);
  });

  it("freezes the per-call meta and stamps is_terminal on clean terminal results", async () => {
    let frozen = false;
    let isolatedSeen: boolean | undefined;
    const tool = scriptedTool({
      name: "submit",
      isTerminal: true,
      execute: (_input, ctx) => {
        frozen = Object.isFrozen(ctx.meta) && Object.isFrozen(ctx.meta.run);
        isolatedSeen = ctx.meta.run.workspace.is_isolated;
        return Promise.resolve({ content: { summary: "done" } });
      },
    });
    const result = await runPipeline(tool, { signal: live() });
    expect(result.is_terminal).toBe(true);
    expect(frozen, "meta and its run snapshot are frozen").toBe(true);
    expect(isolatedSeen).toBe(false);
  });
});
