import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  fileRead: vi.fn(),
  fileReadToEnd: vi.fn(),
  fileWrite: vi.fn(),
  editorText: "server version one",
}));

vi.mock("@/api/files", () => ({
  fileBlame: vi.fn(),
  fileRead: mocks.fileRead,
  fileReadToEnd: mocks.fileReadToEnd,
  fileWrite: mocks.fileWrite,
}));

vi.mock("@/api/rpc", () => ({
  RpcError: class RpcError extends Error {},
}));

vi.mock("@/components/ErrorToast", () => ({
  useErrorToast: () => ({ showError: vi.fn() }),
}));

vi.mock("@/pages/sandbox/files/blame", () => ({
  blameGutter: vi.fn(),
  ownerInfo: vi.fn(),
  ownersOf: vi.fn(),
}));

vi.mock("@codemirror/state", () => ({
  EditorState: {
    create: () => ({ doc: { toString: () => mocks.editorText } }),
    readOnly: { of: vi.fn() },
  },
}));

vi.mock("@codemirror/view", () => ({
  EditorView: class EditorView {
    static lineWrapping = {};
    static theme = vi.fn(() => ({}));
    static editable = { of: vi.fn() };
    state: { doc: { toString: () => string } };

    constructor({ state }: { state: { doc: { toString: () => string } } }) {
      this.state = state;
    }

    destroy() {}
  },
  lineNumbers: vi.fn(() => ({})),
  keymap: { of: vi.fn(() => ({})) },
}));

vi.mock("@codemirror/commands", () => ({
  defaultKeymap: [],
  history: vi.fn(() => ({})),
  historyKeymap: [],
}));

import { FileView } from "@/pages/sandbox/files/FileView";

const whole = (content: string) => ({
  content,
  totalLines: 1,
  totalBytes: content.length,
});

describe("FileView conflict contract", () => {
  beforeEach(() => {
    mocks.editorText = "server version one";
    mocks.fileRead.mockResolvedValue({
      content: "server version one",
      start_line: 1,
      num_lines: 1,
      total_lines: 1,
      total_bytes: 18,
      next_offset: null,
      truncated: false,
    });
    mocks.fileReadToEnd
      .mockResolvedValueOnce(whole("server version one"))
      .mockResolvedValueOnce(whole("server version two"));
  });

  it("keeps the local draft visible when a concurrent change blocks saving", async () => {
    render(
      <MemoryRouter>
        <FileView
          sandboxId="sandbox-a"
          path="notes.txt"
          session={null}
          blameOn={false}
        />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
    mocks.editorText = "operator local draft";
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    expect(await screen.findByText(/Local draft preserved/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /keep editing local draft/i })).toBeTruthy();
  });
});
