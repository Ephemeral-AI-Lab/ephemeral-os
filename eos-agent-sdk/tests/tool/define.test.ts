import { describe, expect, it } from "vitest";
import { z } from "zod";

import { defineTool } from "../../src/tool/define.js";

describe("defineTool", () => {
  it("returns a frozen definition with the trimmed name", () => {
    const tool = defineTool({
      name: "  read_file  ",
      description: "read a file",
      input: z.object({ path: z.string() }),
      execute: () => Promise.resolve({ output: "ok" }),
    });
    expect(tool.name).toBe("read_file");
    expect(Object.isFrozen(tool)).toBe(true);
  });

  it("rejects an empty name", () => {
    expect(() =>
      defineTool({
        name: "   ",
        description: "x",
        input: z.object({}),
        execute: () => Promise.resolve({ output: "ok" }),
      }),
    ).toThrow(/non-empty name/);
  });
});
