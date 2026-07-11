import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { screen } from "@testing-library/react";
import { notifications } from "@mantine/notifications";
import { afterEach, describe, expect, it } from "vitest";
import { renderWithAppProviders } from "../utils/renderWithAppProviders";

afterEach(() => notifications.clean());

describe("P02 application provider foundation", () => {
  it("has one production Mantine provider and one Notifications host", async () => {
    const root = resolve(process.cwd(), "src");
    const [providers, main, globals] = await Promise.all([
      readFile(resolve(root, "AppProviders.tsx"), "utf8"),
      readFile(resolve(root, "main.tsx"), "utf8"),
      readFile(resolve(root, "index.css"), "utf8"),
    ]);

    expect(providers.match(/<MantineProvider\b/g)).toHaveLength(1);
    expect(providers.match(/<Notifications\b/g)).toHaveLength(1);
    expect(providers).toContain("<QueryClientProvider");
    expect(main.indexOf('@mantine/core/styles.css')).toBeLessThan(
      main.indexOf('@mantine/notifications/styles.css'),
    );
    expect(main.indexOf('@mantine/notifications/styles.css')).toBeLessThan(main.indexOf('"./index.css"'));
    expect(globals).toContain("#root");
    expect(globals).toContain("min-height: 100%");
  });

  it("renders a shared provider, router, and notification host", async () => {
    renderWithAppProviders(<button type="button">Provider child</button>);

    expect(screen.getByRole("button", { name: "Provider child" })).toBeTruthy();
    expect(document.documentElement.getAttribute("data-mantine-color-scheme")).toBe("light");

    notifications.show({
      autoClose: false,
      id: "p02-unit-notification",
      message: "The host renders notification content.",
      title: "P02 notification",
    });

    expect(await screen.findByText("P02 notification")).toBeTruthy();
    expect(screen.getByText("The host renders notification content.")).toBeTruthy();
  });
});
