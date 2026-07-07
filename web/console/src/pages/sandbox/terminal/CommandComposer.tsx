import { useEffect, useRef, useState } from "react";
import { Play } from "lucide-react";
import { rpc, sandboxScope } from "@/api/rpc";
import type { CommandOutput } from "@/api/types";
import type { WorkspaceSnapshot } from "@/api/observability";
import { useErrorToast } from "@/components/ErrorToast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const AUTO_PUBLISH = "__auto_publish__";

/**
 * The prompt line. Fires exec_command with `yield_time_ms: 0` — an
 * agent-tool affordance a polling browser never needs, so it is pinned and
 * hidden. `timeout_ms` is semantic and stays user-visible, but optional:
 * an empty timeout omits the argument entirely (never 0). The "implicit"
 * target creates a one-shot session that captures and publishes on
 * completion, labelled auto-publish.
 */
export function CommandComposer({
  sandboxId,
  workspaces,
  targetSession,
  onLaunched,
}: {
  sandboxId: string;
  workspaces: WorkspaceSnapshot[];
  targetSession: string | null;
  onLaunched: (cmd: string, workspaceSessionId: string | null, output: CommandOutput) => void;
}) {
  const [cmd, setCmd] = useState("");
  const [target, setTarget] = useState<string>(targetSession ?? AUTO_PUBLISH);
  const [timeoutSeconds, setTimeoutSeconds] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const { showError } = useErrorToast();

  useEffect(() => {
    setTarget(targetSession ?? AUTO_PUBLISH);
  }, [targetSession]);

  const submit = async () => {
    const text = cmd.trim();
    if (text === "" || busy) return;
    const workspaceSessionId = target === AUTO_PUBLISH ? null : target;
    const args: Record<string, unknown> = { cmd: text, yield_time_ms: 0 };
    if (workspaceSessionId) args["workspace_session_id"] = workspaceSessionId;
    const timeout = timeoutSeconds.trim();
    if (timeout !== "" && Number.isFinite(Number(timeout)) && Number(timeout) > 0) {
      args["timeout_ms"] = Math.round(Number(timeout) * 1000);
    }
    setBusy(true);
    try {
      const output = await rpc<CommandOutput>(
        "exec_command",
        sandboxScope(sandboxId),
        args,
      );
      setCmd("");
      onLaunched(text, workspaceSessionId, output);
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  return (
    <form
      className="flex min-w-0 items-center gap-2 border-t border-line bg-surface px-3 py-2"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <span className="shrink-0 font-mono text-sm text-ink-faint">$</span>
      <Input
        ref={inputRef}
        value={cmd}
        onChange={(event) => setCmd(event.target.value)}
        placeholder="run a command…"
        className="min-w-0 flex-1 font-mono"
        autoFocus
      />
      <label className="shrink-0 text-[11px] text-ink-faint" htmlFor="composer-target">
        in
      </label>
      <Select value={target} onValueChange={setTarget}>
        <SelectTrigger id="composer-target" className="w-48 shrink-0">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={AUTO_PUBLISH}>auto-publish</SelectItem>
          {workspaces.map((workspace) => (
            <SelectItem key={workspace.workspace_id} value={workspace.workspace_id}>
              {workspace.workspace_id} ({workspace.network_profile})
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <label className="shrink-0 text-[11px] text-ink-faint" htmlFor="composer-timeout">
        timeout (s)
      </label>
      <Input
        id="composer-timeout"
        value={timeoutSeconds}
        onChange={(event) => setTimeoutSeconds(event.target.value)}
        placeholder="none"
        className="w-14 shrink-0 font-mono"
        inputMode="numeric"
      />
      <Button
        type="submit"
        variant="primary"
        className="shrink-0"
        disabled={busy || cmd.trim() === ""}
      >
        <Play size={12} />
        run
      </Button>
    </form>
  );
}
