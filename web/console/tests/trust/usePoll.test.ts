import { describe, expect, it } from "vitest";
import {
  FAST_POLL_MS,
  pollInterval,
  SLOW_POLL_MS,
  type PollTracking,
} from "@/poll/usePoll";

describe("pollInterval", () => {
  it("keeps the 400ms fast and 2s slow cadences while data changes", () => {
    const fast: PollTracking = { at: 0, fingerprint: "" };
    const slow: PollTracking = { at: 0, fingerprint: "" };

    expect(pollInterval({ state: "running" }, "fast", fast, 100)).toBe(FAST_POLL_MS);
    expect(pollInterval({ state: "ready" }, "slow", slow, 100)).toBe(SLOW_POLL_MS);
  });

  it("pauses in a hidden tab and decays idle polling without exceeding 8s", () => {
    const fast: PollTracking = { at: 100, fingerprint: '{"state":"running"}' };
    const slow: PollTracking = { at: 100, fingerprint: '{"state":"ready"}' };

    expect(pollInterval({ state: "running" }, "fast", fast, 200, true)).toBe(false);
    expect(pollInterval({ state: "running" }, "fast", fast, 15_101)).toBe(1_600);
    expect(pollInterval({ state: "ready" }, "slow", slow, 15_101)).toBe(8_000);
  });
});
