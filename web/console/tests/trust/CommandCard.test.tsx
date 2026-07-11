import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { LedgerEntry } from "@/pages/sandbox/terminal/ledger";

const mocks = vi.hoisted(() => ({
  rpc: vi.fn().mockResolvedValue({}),
}));

vi.mock("@/api/rpc", () => ({
  rpc: mocks.rpc,
  sandboxScope: (sandboxId: string) => ({ sandbox_id: sandboxId }),
}));

vi.mock("@/components/ErrorToast", () => ({
  useErrorToast: () => ({ showError: vi.fn() }),
}));

vi.mock("@/components/PortPreview", () => ({
  PortPreview: () => null,
}));

vi.mock("@/pages/sandbox/terminal/TranscriptViewer", () => ({
  TranscriptViewer: () => <div>transcript</div>,
}));

import { CommandCard } from "@/pages/sandbox/terminal/CommandCard";

const runningEntry: LedgerEntry = {
  localId: "local-1",
  commandSessionId: "command-1",
  cmd: "sleep 60",
  workspaceSessionId: "workspace-1",
  autoPublish: false,
  startedAt: Date.now(),
  status: "running",
  exitCode: null,
  endedAt: null,
  inlineOutput: null,
  publishRejected: false,
  publishRejectClass: null,
};

describe("CommandCard keyboard contract", () => {
  it("writes one control frame when Ctrl-C originates in the stdin field", async () => {
    render(
      <CommandCard
        sandboxId="sandbox-a"
        entry={runningEntry}
        expanded
        onToggle={() => {}}
        onUpdate={() => {}}
        previewScopes={[]}
      />,
    );

    fireEvent.keyDown(screen.getByPlaceholderText("type a line, Enter sends it"), {
      key: "c",
      ctrlKey: true,
    });

    await waitFor(() => {
      const stdinWrites = mocks.rpc.mock.calls.filter(
        ([operation]) => operation === "write_command_stdin",
      );
      expect(stdinWrites).toHaveLength(1);
    });
  });
});
