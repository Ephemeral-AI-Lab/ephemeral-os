import { useState } from "react";
import { Link } from "react-router";
import { Plus, Trash2 } from "lucide-react";
import { RpcError, rpcUnchecked, sandboxScope } from "@/api/rpc";
import type { WorkspaceSessionCreated } from "@/api/types";
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

interface DestroyRefusal {
  sessionId: string;
  commandSessionIds: string[];
}

/**
 * Workspace sessions under an **all** entry. Selection has one meaning:
 * it filters the ledger to that session's commands and pre-fills the
 * composer target; **all** leaves the ledger unfiltered. Destroy surfaces
 * the API's refusal when the command ledger is non-empty, listing the
 * blocking command sessions as `#cmd-` jump links.
 */
export function SessionSidebar({
  sandboxId,
  workspaces,
  selected,
  onSelect,
  onChanged,
}: {
  sandboxId: string;
  workspaces: WorkspaceSnapshot[];
  selected: string | null;
  onSelect: (sessionId: string | null) => void;
  onChanged: () => void;
}) {
  const [profile, setProfile] = useState<"shared" | "isolated">("shared");
  const [creating, setCreating] = useState(false);
  const [graceSeconds, setGraceSeconds] = useState("");
  const [refusal, setRefusal] = useState<DestroyRefusal | null>(null);
  const { showError } = useErrorToast();

  const create = async () => {
    setCreating(true);
    try {
      await rpcUnchecked<WorkspaceSessionCreated>(
        "create_workspace_session",
        sandboxScope(sandboxId),
        { network_profile: profile },
      );
      onChanged();
    } catch (error) {
      showError(error);
    } finally {
      setCreating(false);
    }
  };

  const destroy = async (sessionId: string) => {
    const grace = graceSeconds.trim();
    const args: Record<string, unknown> = { workspace_session_id: sessionId };
    if (grace !== "" && Number.isFinite(Number(grace))) {
      args["grace_s"] = Number(grace);
    }
    try {
      await rpcUnchecked("destroy_workspace_session", sandboxScope(sandboxId), args);
      setRefusal(null);
      if (selected === sessionId) onSelect(null);
      onChanged();
    } catch (error) {
      if (error instanceof RpcError && !error.transport) {
        const ids = (error.details as { active_command_session_ids?: string[] })
          ?.active_command_session_ids;
        if (Array.isArray(ids)) {
          setRefusal({ sessionId, commandSessionIds: ids });
          return;
        }
      }
      showError(error);
    }
  };

  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-surface">
      <div className="border-b border-line px-3 py-2 text-xs font-semibold uppercase tracking-wide text-ink-mid">
        Sessions
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        <SessionRow
          label="all"
          hint="unfiltered ledger"
          active={selected === null}
          onClick={() => onSelect(null)}
        />
        {workspaces.map((workspace) => (
          <div key={workspace.workspace_id}>
            <SessionRow
              label={workspace.workspace_id}
              hint={`${workspace.network_profile} · ${workspace.layers.layer_count} layers`}
              active={selected === workspace.workspace_id}
              onClick={() => onSelect(workspace.workspace_id)}
              onDestroy={() => void destroy(workspace.workspace_id)}
            />
            {refusal?.sessionId === workspace.workspace_id ? (
              <div className="mx-1 mb-2 rounded border border-warn/50 bg-warn-soft p-2 text-[11px] leading-4 text-ink">
                Refused: the session&apos;s command ledger is non-empty.
                Blocking commands:
                <ul className="mt-1">
                  {refusal.commandSessionIds.map((commandId) => (
                    <li key={commandId}>
                      <Link
                        to={`#cmd-${encodeURIComponent(commandId)}`}
                        className="font-mono text-accent hover:underline"
                      >
                        {commandId}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ))}
        {workspaces.length === 0 ? (
          <p className="px-2 py-3 text-[11px] text-ink-faint">
            No live sessions. Create one below, or run a command with
            auto-publish.
          </p>
        ) : null}
      </div>
      <div className="border-t border-line p-2">
        <div className="flex items-center gap-1">
          <Select
            value={profile}
            onValueChange={(value) => setProfile(value as "shared" | "isolated")}
          >
            <SelectTrigger className="h-7 flex-1 text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="shared">shared</SelectItem>
              <SelectItem value="isolated">isolated</SelectItem>
            </SelectContent>
          </Select>
          <Button
            size="sm"
            variant="primary"
            onClick={() => void create()}
            disabled={creating}
            title="create workspace session"
          >
            <Plus size={12} />
            session
          </Button>
        </div>
        <div className="mt-1.5 flex items-center gap-1">
          <label className="text-[11px] text-ink-faint" htmlFor="grace-seconds">
            destroy grace (s)
          </label>
          <Input
            id="grace-seconds"
            value={graceSeconds}
            onChange={(event) => setGraceSeconds(event.target.value)}
            placeholder="0"
            className="h-6 w-16 px-1.5 text-xs"
            inputMode="decimal"
          />
        </div>
      </div>
    </aside>
  );
}

function SessionRow({
  label,
  hint,
  active,
  onClick,
  onDestroy,
}: {
  label: string;
  hint: string;
  active: boolean;
  onClick: () => void;
  onDestroy?: () => void;
}) {
  return (
    <div
      className={`group mb-0.5 flex items-center gap-1 rounded px-2 py-1.5 ${
        active ? "bg-accent-soft" : "hover:bg-surface-hover"
      }`}
    >
      <button type="button" onClick={onClick} className="min-w-0 flex-1 text-left">
        <span
          className={`block truncate font-mono text-xs ${active ? "font-medium text-accent" : "text-ink"}`}
        >
          {label}
        </span>
        <span className="block truncate text-[10px] text-ink-faint">{hint}</span>
      </button>
      {onDestroy ? (
        <button
          type="button"
          onClick={onDestroy}
          className="hidden shrink-0 rounded p-1 text-ink-faint hover:bg-danger-soft hover:text-danger group-hover:block"
          title={`destroy ${label}`}
        >
          <Trash2 size={11} />
        </button>
      ) : null}
    </div>
  );
}
