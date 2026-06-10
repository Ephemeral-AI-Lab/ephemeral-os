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
    expect(tool.terminal).toBe(false);
    expect(tool.availableInIsolatedWorkspace).toBe(false);
  });

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
