import { readdir, readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fireEvent, screen } from "@testing-library/react";
import { notifications } from "@mantine/notifications";
import { afterEach, describe, expect, it } from "vitest";
import { RpcError } from "@/api/rpc";
import { toErrorNotification, useErrorToast } from "@/components/ErrorToast";
import { renderWithAppProviders } from "../utils/renderWithAppProviders";

async function sourceFiles(directory: string): Promise<string[]> {
  const entries = await readdir(directory, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) return sourceFiles(path);
      return /\.(?:ts|tsx)$/.test(entry.name) ? [path] : [];
    }),
  );
  return files.flat();
}

function ErrorNotificationProbe() {
  const { showError } = useErrorToast();
  return (
    <button
      type="button"
      onClick={() =>
        showError(
          new RpcError({
            kind: "policy_denied",
            message: "Publishing is not allowed for this workspace.",
            details: { policy: "workspace_read_only" },
            transport: false,
          }),
        )
      }
    >
      Show normalized error
    </button>
  );
}

afterEach(() => notifications.clean());

describe("P03 shared primitive migration", () => {
  it("removes legacy UI wrapper and Radix imports from source", async () => {
    const source = resolve(process.cwd(), "src");
    const files = await sourceFiles(source);
    const contents = await Promise.all(files.map((file) => readFile(file, "utf8")));
    const allSource = contents.join("\n");

    expect(allSource).not.toMatch(/components\/ui\/(button|input|dialog|select|popover|tooltip)/);
    expect(allSource).not.toContain("@radix-ui/react");
  });

  it("keeps RPC error normalization while Mantine owns notification rendering", () => {
    expect(
      toErrorNotification(
        new RpcError({
          kind: "policy_denied",
          message: "Publishing is not allowed for this workspace.",
          details: { policy: "workspace_read_only" },
          transport: false,
        }),
      ),
    ).toEqual({
      kind: "policy_denied",
      message: "Publishing is not allowed for this workspace.",
      details: '{"policy":"workspace_read_only"}',
    });

    expect(
      toErrorNotification(
        new RpcError({
          kind: "network_error",
          message: "console unreachable",
          transport: true,
        }),
      ),
    ).toEqual({
      kind: "network_error (transport)",
      message: "console unreachable",
      details: null,
    });
  });

  it("renders a dismissible normalized error through the shared notification host", async () => {
    renderWithAppProviders(<ErrorNotificationProbe />);
    fireEvent.click(screen.getByRole("button", { name: "Show normalized error" }));

    expect(await screen.findByText("policy_denied")).toBeTruthy();
    expect(screen.getByText("Publishing is not allowed for this workspace.")).toBeTruthy();
    expect(screen.getByText('{"policy":"workspace_read_only"}')).toBeTruthy();
  });
});
