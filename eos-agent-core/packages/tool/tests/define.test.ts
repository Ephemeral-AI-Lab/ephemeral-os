import { describe, expect, it } from "vitest";

import { z } from "zod";

import { defineTool } from "../src/define.js";

describe("defineTool", () => {
  it("applies fail-closed defaults: non-terminal, banned in isolated mode", () => {
    const tool = defineTool({
      name: "probe",
      description: "a probe",
      input: z.object({}),
      execute: () => Promise.resolve({ content: "ok" }),
    });
    expect(tool.isTerminal).toBe(false);
    expect(tool.isBatchExecutionForbidden).toBe(false);
    expect(tool.availableInIsolatedWorkspace).toBe(false);
  });

  it.each([
    { isTerminal: undefined, flag: undefined, expected: false, rule: "plain tools batch freely" },
    { isTerminal: true, flag: undefined, expected: true, rule: "terminal implies batch-forbidden" },
    { isTerminal: true, flag: false, expected: false, rule: "explicit relax wins over terminal" },
    { isTerminal: undefined, flag: true, expected: true, rule: "non-terminal tools can opt in" },
  ])(
    "resolves isBatchExecutionForbidden to $expected when isTerminal=$isTerminal and flag=$flag ($rule)",
    ({ isTerminal, flag, expected }) => {
      const tool = defineTool({
        name: "probe",
        description: "a probe",
        input: z.object({}),
        isTerminal,
        isBatchExecutionForbidden: flag,
        execute: () => Promise.resolve({ content: "ok" }),
      });
      expect(tool.isBatchExecutionForbidden).toBe(expected);
    },
  );

  it("derives the wire spec from the zod input schema", () => {
    const tool = defineTool({
      name: "read",
      description: "read a file",
      input: z.object({ path: z.string(), limit: z.number().optional() }),
      execute: () => Promise.resolve({ content: "ok" }),
    });
    expect(tool.spec.name).toBe("read");
    expect(tool.spec.description).toBe("read a file");
    expect(tool.spec.input_schema).toMatchObject({
      type: "object",
      properties: {
        path: { type: "string" },
        limit: { type: "number" },
      },
      required: ["path"],
    });
  });

  it("rejects an empty tool name", () => {
    expect(() =>
      defineTool({
        name: "",
        description: "nameless",
        input: z.object({}),
        execute: () => Promise.resolve({ content: "ok" }),
      }),
    ).toThrow();
  });
});
