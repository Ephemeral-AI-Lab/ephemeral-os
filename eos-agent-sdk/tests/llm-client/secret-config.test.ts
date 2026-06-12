import { inspect } from "node:util";

import { describe, expect, it } from "vitest";

import { RetryConfigSchema, StreamGuardConfigSchema } from "../../src/llm-client/config.js";
import { SecretString } from "../../src/llm-client/secret.js";

describe("secret string", () => {
  const secret = new SecretString("sk-super-secret");

  it("redacts string conversion, json, and inspect", () => {
    expect(String(secret), "String conversion").toBe("[redacted]");
    expect(JSON.stringify({ api_key: secret })).toBe(
      '{"api_key":"[redacted]"}',
    );
    expect(inspect(secret), "util.inspect").toBe("[redacted]");
    expect(inspect({ nested: secret })).not.toContain("sk-super-secret");
  });

  it("exposes the raw value only explicitly", () => {
    expect(secret.expose()).toBe("sk-super-secret");
  });
});

describe("retry config", () => {
  it("applies the documented defaults", () => {
    expect(RetryConfigSchema.parse({})).toEqual({
      max_retries: 3,
      base_delay_s: 1,
      max_delay_s: 30,
      status_codes: [429, 500, 502, 503, 529],
    });
  });

  it("rejects negative delays", () => {
    expect(
      RetryConfigSchema.safeParse({ base_delay_s: -1 }).success,
      "negative base_delay_s",
    ).toBe(false);
    expect(
      RetryConfigSchema.safeParse({ max_delay_s: -0.5 }).success,
      "negative max_delay_s",
    ).toBe(false);
  });
});

describe("stream guard config", () => {
  it("defaults the idle timeout to 90s and rejects negatives", () => {
    expect(StreamGuardConfigSchema.parse({})).toEqual({ idle_timeout_s: 90 });
    expect(
      StreamGuardConfigSchema.safeParse({ idle_timeout_s: -1 }).success,
    ).toBe(false);
  });
});
