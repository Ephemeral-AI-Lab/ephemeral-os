import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { notifications } from "@mantine/notifications";
import { MantineCompatibilitySpike } from "./MantineCompatibilitySpike";

const canvasContext = new Proxy(
  {
    measureText: () => ({ width: 8 }),
    createLinearGradient: () => ({ addColorStop() {} }),
  },
  {
    get(target, property) {
      return property in target ? target[property as keyof typeof target] : () => undefined;
    },
    set() {
      return true;
    },
  },
);

let originalGetContext: typeof HTMLCanvasElement.prototype.getContext;
let originalMatchMedia: typeof window.matchMedia;

beforeEach(() => {
  originalGetContext = HTMLCanvasElement.prototype.getContext;
  originalMatchMedia = window.matchMedia;
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: vi.fn(() => canvasContext),
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: query === "(prefers-reduced-motion: reduce)",
      media: query,
      onchange: null,
      addEventListener() {},
      removeEventListener() {},
      addListener() {},
      removeListener() {},
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => {
  notifications.clean();
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: originalGetContext,
  });
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: originalMatchMedia,
  });
});

describe("P00 Mantine compatibility fixture", () => {
  it("integrates portals, keyboard focus restoration, form validation, and notifications", async () => {
    const user = userEvent.setup();
    render(<MantineCompatibilitySpike withUplot={false} />);

    const modalTrigger = screen.getByRole("button", { name: "Open modal" });
    await user.click(modalTrigger);
    expect(await screen.findByRole("dialog", { name: "P00 portal modal" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Close modal" }));
    await waitFor(() => expect(document.activeElement).toBe(modalTrigger));

    await user.click(screen.getByRole("button", { name: "Validate form" }));
    expect(await screen.findByText("A label is required")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Show notification" }));
    expect(await screen.findByText("P00 notification")).toBeTruthy();

    screen.getByRole("button", { name: "Tooltip trigger" }).focus();
    expect(await screen.findByRole("tooltip")).toBeTruthy();
  });

  it("honors reduced motion and keeps heavy integration primitives mountable", async () => {
    const user = userEvent.setup();
    render(<MantineCompatibilitySpike withUplot={false} />);

    expect(screen.getByTestId("reduced-motion").textContent).toBe("true");
    const workspace = screen.getByRole("treeitem", { name: "workspace" });
    workspace.focus();
    await user.keyboard(" ");
    expect(screen.getByText("src")).toBeTruthy();
    expect(screen.getByTestId("codemirror-probe").querySelector(".cm-editor")).toBeTruthy();

    const combobox = screen.getByRole("textbox", { name: "Virtual option" });
    await user.click(combobox);
    await waitFor(() =>
      expect(screen.getByTestId("virtual-options").querySelectorAll('[role="option"]').length).toBeGreaterThan(0),
    );
  });
});
