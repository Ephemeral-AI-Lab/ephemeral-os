import { render, screen } from "@testing-library/react";
import { MantineProvider, Text } from "@mantine/core";
import { describe, expect, it } from "vitest";

describe("Mantine render smoke", () => {
  it("mounts the provider in jsdom", () => {
    render(
      <MantineProvider>
        <Text>provider mounted</Text>
      </MantineProvider>,
    );
    expect(screen.getByText("provider mounted")).toBeTruthy();
  });
});
