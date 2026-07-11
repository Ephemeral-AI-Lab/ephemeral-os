import { useState } from "react";
import { CornerDownLeft } from "lucide-react";
import { Button } from "@mantine/core";
import { rpc, sandboxScope } from "@/api/rpc";
import { useErrorToast } from "@/components/ErrorToast";

export const CTRL_C = "\u0003";
export const CTRL_D = "\u0004";

/**
 * The terminal's input line while a command runs. Enter sends the line via
 * write_command_stdin (yield pinned to 0) followed by an immediate
 * transcript nudge so the program's reaction renders without waiting for
 * the next poll tick. Ctrl-C / Ctrl-D stop at the input after writing so
 * the enclosing terminal-frame shortcut cannot send a duplicate control
 * frame. The explicit buttons stay for discoverability.
 */
export function StdinBar({
  sandboxId,
  commandSessionId,
  nudge,
}: {
  sandboxId: string;
  commandSessionId: string;
  nudge: () => void;
}) {
  const [text, setText] = useState("");
  const { showError } = useErrorToast();

  const write = async (stdin: string) => {
    try {
      await rpc("write_command_stdin", sandboxScope(sandboxId), {
        command_session_id: commandSessionId,
        stdin,
        yield_time_ms: 0,
      });
    } catch (error) {
      showError(error);
    } finally {
      nudge();
    }
  };

  const submitLine = () => {
    const line = text;
    setText("");
    void write(`${line}\n`);
  };

  return (
    <div className="flex items-center gap-1 border-t border-line bg-surface px-2 py-1">
      <span className="font-mono text-xs text-ink-faint">stdin</span>
      <input
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            submitLine();
          } else if (event.key === "c" && event.ctrlKey) {
            event.preventDefault();
            event.stopPropagation();
            void write("\u0003");
          } else if (event.key === "d" && event.ctrlKey) {
            event.preventDefault();
            event.stopPropagation();
            void write("\u0004");
          }
        }}
        placeholder="type a line, Enter sends it"
        className="h-6 min-w-0 flex-1 border-none bg-transparent px-1 font-mono text-xs text-ink outline-none placeholder:text-ink-faint"
      />
      <Button size="compact-xs" variant="subtle" onClick={submitLine} title="send line">
        <CornerDownLeft size={12} />
      </Button>
      <Button
        size="compact-xs"
        variant="subtle"
        onClick={() => void write("\u0003")}
        title="send Ctrl-C (SIGINT)"
        className="font-mono"
      >
        ^C
      </Button>
      <Button
        size="compact-xs"
        variant="subtle"
        onClick={() => void write("\u0004")}
        title="send Ctrl-D (EOF)"
        className="font-mono"
      >
        ^D
      </Button>
    </div>
  );
}
