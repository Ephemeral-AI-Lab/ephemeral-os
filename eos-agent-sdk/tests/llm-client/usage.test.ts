import { describe, expect, it } from "vitest";

import { cacheHitRate, type UsageSnapshot } from "../../src/llm-client/index.js";

describe("usage snapshots", () => {
  it.each([
    {
      usage: { input_tokens: 10, output_tokens: 2 },
      expected: 0,
      label: "no cache fields",
    },
    {
      usage: {
        input_tokens: 0,
        output_tokens: 2,
        cache_read_input_tokens: 30,
      },
      expected: 1,
      label: "read-only prompt",
    },
    {
      usage: {
        input_tokens: 20,
        output_tokens: 2,
        cache_read_input_tokens: 30,
        cache_creation_input_tokens: 10,
      },
      expected: 0.5,
      label: "input, read, and creation mix",
    },
    {
      usage: { input_tokens: 0, output_tokens: 0 },
      expected: 0,
      label: "zero prompt denominator",
    },
  ] satisfies readonly {
    usage: UsageSnapshot;
    expected: number;
    label: string;
  }[])("computes cache hit rate for $label", ({ usage, expected }) => {
    expect(cacheHitRate(usage)).toBe(expected);
  });
});
