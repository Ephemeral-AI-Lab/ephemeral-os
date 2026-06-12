import { describe, expect, it } from "vitest";
import { z } from "zod";

import { createAgentOutcomeFn, unwrapAgentOutcomeFn } from "../../src/tool/outcome.js";
import { bindTerminalTool, type TerminalGate } from "../../src/tool/terminal.js";
import { batchFixture, call, contentText, scopeFixture } from "./support.js";

const SCHEMA = z.object({ summary: z.string().min(1) });

interface GateFixture extends TerminalGate {
  open: boolean;
  began: number;
  cancelled: number;
}

function gateFixture(): GateFixture {
  const gate: GateFixture = {
    open: true,
    began: 0,
    cancelled: 0,
    blockers: () => (gate.open ? [] : ["2 background task(s) still open", "1 undrained notification(s)"]),
    beginFinishing: () => {
      gate.began += 1;
    },
    cancelFinishing: () => {
      gate.cancelled += 1;
    },
  };
  return gate;
}

describe("bindTerminalTool", () => {
  it("accepts a valid submission, stamps is_terminal, and exposes the payload once", async () => {
    const { scope } = scopeFixture();
    const gate = gateFixture();
    const submissions: string[] = [];
    const binding = bindTerminalTool(
      unwrapAgentOutcomeFn(
        createAgentOutcomeFn({
          name: "submit_outcome",
          schema: SCHEMA,
          onSubmit: (payload, ctx) => {
            submissions.push(ctx.submissionId);
            return Promise.resolve({ accept: { summary: `${payload.summary}!` } });
          },
        }),
      ),
      scope,
      gate,
    );
    const result = await binding.bound.run(
      call("toolu_9", "submit_outcome", { summary: "done" }),
      batchFixture(),
    );
    expect(result).toMatchObject({ is_error: false, is_terminal: true });
    expect(submissions, "submissionId is the toolUseId").toEqual(["toolu_9"]);
    expect(gate.began, "the finishing latch closed before onSubmit").toBe(1);
    expect(binding.takeAccepted()).toEqual({ value: { summary: "done!" } });
    expect(binding.takeAccepted(), "read-once").toBeUndefined();
  });

  it("returns shape errors to the model without invoking onSubmit", async () => {
    const { scope } = scopeFixture();
    let invoked = 0;
    const binding = bindTerminalTool(
      unwrapAgentOutcomeFn(
        createAgentOutcomeFn({
          name: "submit_outcome",
          schema: SCHEMA,
          onSubmit: () => {
            invoked += 1;
            return Promise.resolve({ accept: { summary: "x" } });
          },
        }),
      ),
      scope,
      gateFixture(),
    );
    const result = await binding.bound.run(
      call("t1", "submit_outcome", { summary: 7 }),
      batchFixture(),
    );
    expect(result.is_error).toBe(true);
    expect(contentText(result.content)).toContain("invalid submission for submit_outcome");
    expect(invoked).toBe(0);
  });

  it("denies a submission while the gate is closed, enumerating the blockers", async () => {
    const { scope } = scopeFixture();
    const gate = gateFixture();
    gate.open = false;
    let invoked = 0;
    const binding = bindTerminalTool(
      unwrapAgentOutcomeFn(
        createAgentOutcomeFn({
          name: "submit_outcome",
          schema: SCHEMA,
          onSubmit: () => {
            invoked += 1;
            return Promise.resolve({ accept: { summary: "x" } });
          },
        }),
      ),
      scope,
      gate,
    );
    const result = await binding.bound.run(
      call("t1", "submit_outcome", { summary: "done" }),
      batchFixture(),
    );
    expect(result.is_error).toBe(true);
    expect(result.content).toBe(
      "submission denied: 2 background task(s) still open; 1 undrained notification(s)",
    );
    expect(invoked, "a closed gate costs the host nothing").toBe(0);
    expect(binding.takeAccepted()).toBeUndefined();
  });

  it("runs pre and post hooks before onSubmit so a hook deny costs nothing", async () => {
    const order: string[] = [];
    const { scope } = scopeFixture([
      {
        event: "preToolUse",
        matcher: { toolName: "submit_outcome" },
        run: () => {
          order.push("pre");
          return { decision: "passthrough" };
        },
      },
      {
        event: "postToolUse",
        matcher: { toolName: "submit_outcome" },
        run: () => {
          order.push("post");
          return { decision: "deny", reason: "vetting hook said no" };
        },
      },
    ]);
    let invoked = 0;
    const binding = bindTerminalTool(
      unwrapAgentOutcomeFn(
        createAgentOutcomeFn({
          name: "submit_outcome",
          schema: SCHEMA,
          onSubmit: () => {
            invoked += 1;
            return Promise.resolve({ accept: { summary: "x" } });
          },
        }),
      ),
      scope,
      gateFixture(),
    );
    const result = await binding.bound.run(
      call("t1", "submit_outcome", { summary: "done" }),
      batchFixture(),
    );
    expect(order).toEqual(["pre", "post"]);
    expect(result).toMatchObject({ is_error: true, content: "vetting hook said no" });
    expect(invoked, "onSubmit never ran").toBe(0);
  });

  it("returns a rejection to the live model and reopens the run", async () => {
    const { scope } = scopeFixture();
    const gate = gateFixture();
    const binding = bindTerminalTool(
      unwrapAgentOutcomeFn(
        createAgentOutcomeFn({
          name: "submit_outcome",
          schema: SCHEMA,
          onSubmit: () => Promise.resolve({ reject: "missing the error budget section" }),
        }),
      ),
      scope,
      gate,
    );
    const result = await binding.bound.run(
      call("t1", "submit_outcome", { summary: "done" }),
      batchFixture(),
    );
    expect(result).toMatchObject({
      is_error: true,
      is_terminal: false,
      content: "missing the error budget section",
    });
    expect(gate.cancelled, "the finishing latch reopened").toBe(1);
    expect(binding.takeAccepted()).toBeUndefined();
  });

  it("maps a throwing onSubmit to an in-run error and reopens the run", async () => {
    const { scope } = scopeFixture();
    const gate = gateFixture();
    const binding = bindTerminalTool(
      unwrapAgentOutcomeFn(
        createAgentOutcomeFn({
          name: "submit_outcome",
          schema: SCHEMA,
          onSubmit: () => Promise.reject(new Error("db locked")),
        }),
      ),
      scope,
      gate,
    );
    const result = await binding.bound.run(
      call("t1", "submit_outcome", { summary: "done" }),
      batchFixture(),
    );
    expect(result).toMatchObject({ is_error: true, is_terminal: false });
    expect(contentText(result.content)).toContain("db locked");
    expect(gate.cancelled).toBe(1);
  });
});

describe("createAgentOutcomeFn", () => {
  it("defaults onSubmit to the trivial validator and derives a description", async () => {
    const binding = unwrapAgentOutcomeFn(
      createAgentOutcomeFn({ name: "submit_outcome", schema: SCHEMA }),
    );
    expect(binding.description).toContain("Finish the run");
    await expect(
      binding.onSubmit({ summary: "ok" }, { runId: "r" as never, submissionId: "s" }),
    ).resolves.toEqual({ accept: { summary: "ok" } });
  });

  it("keeps a caller-supplied description and rejects empty names", () => {
    const binding = unwrapAgentOutcomeFn(
      createAgentOutcomeFn({
        name: "submit_outcome",
        description: "Submit the final research report.",
        schema: SCHEMA,
      }),
    );
    expect(binding.description).toBe("Submit the final research report.");
    expect(() => createAgentOutcomeFn({ name: " ", schema: SCHEMA })).toThrow(
      /non-empty name/,
    );
  });
});
