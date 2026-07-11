import "@mantine/core/styles.css";
import { Button } from "@mantine/core";
import { describe, expect, it } from "vitest";

describe("Mantine import smoke", () => {
  it("loads the core stylesheet and component exports", () => {
    expect(Button).toBeTruthy();
  });
});
