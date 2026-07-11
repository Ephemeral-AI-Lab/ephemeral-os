import { describe, expect, it } from "vitest";
import { MantineCompatibilitySpike } from "./MantineCompatibilitySpike";

describe("Mantine compatibility spike module", () => {
  it("loads its fixture module", () => {
    expect(MantineCompatibilitySpike).toBeTruthy();
  });
});
