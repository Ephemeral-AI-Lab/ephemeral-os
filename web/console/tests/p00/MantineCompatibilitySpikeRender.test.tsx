import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
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
let originalPath2D: typeof Path2D | undefined;

beforeEach(() => {
  originalGetContext = HTMLCanvasElement.prototype.getContext;
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: vi.fn(() => canvasContext),
  });
  originalPath2D = globalThis.Path2D;
  Object.defineProperty(globalThis, "Path2D", {
    configurable: true,
    value: class {
      constructor() {
        return new Proxy(this, {
          get(target, property) {
            return property in target ? target[property as keyof typeof target] : () => undefined;
          },
        });
      }
    },
  });
});

afterEach(() => {
  Object.defineProperty(HTMLCanvasElement.prototype, "getContext", {
    configurable: true,
    value: originalGetContext,
  });
  Object.defineProperty(globalThis, "Path2D", {
    configurable: true,
    value: originalPath2D,
  });
});

describe("Mantine compatibility fixture render", () => {
  it("mounts the portal, notification, and form probes", () => {
    render(
      <MantineCompatibilitySpike
        withUplot={false}
        withVirtualCombobox={false}
        withTree={false}
        withCodeMirror={false}
      />,
    );
    expect(screen.getByRole("button", { name: "Open modal" })).toBeTruthy();
  });

  it("mounts the virtual combobox probe", () => {
    render(
      <MantineCompatibilitySpike
        withUplot={false}
        withTree={false}
        withCodeMirror={false}
      />,
    );
    expect(screen.getByRole("textbox", { name: "Virtual option" })).toBeTruthy();
  });

  it("mounts the Mantine tree probe and expands it with keyboard input", async () => {
    const user = userEvent.setup();
    render(
      <MantineCompatibilitySpike
        withUplot={false}
        withVirtualCombobox={false}
        withCodeMirror={false}
      />,
    );
    const workspace = screen.getByRole("treeitem", { name: "workspace" });
    workspace.focus();
    await user.keyboard(" ");
    expect(screen.getByText("src")).toBeTruthy();
  });

  it("mounts the CodeMirror probe", () => {
    render(
      <MantineCompatibilitySpike
        withUplot={false}
        withVirtualCombobox={false}
        withTree={false}
      />,
    );
    expect(screen.getByTestId("codemirror-probe").querySelector(".cm-editor")).toBeTruthy();
  });

  it("mounts the uPlot probe with the fixture canvas context", () => {
    render(
      <MantineCompatibilitySpike
        withVirtualCombobox={false}
        withTree={false}
        withCodeMirror={false}
      />,
    );
    expect(screen.getByTestId("uplot-probe").querySelector(".uplot")).toBeTruthy();
  });
});
