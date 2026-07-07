import { useSearchParams } from "react-router";
import { GitBranch } from "lucide-react";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { FileTree } from "@/pages/sandbox/files/FileTree";
import { FileView } from "@/pages/sandbox/files/FileView";
import { Tooltip } from "@/components/ui/tooltip";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/cn";

const PUBLISHED = "__published__";

/**
 * Files tab: FileTree + SessionScopePicker driving both tree and viewer.
 * The scope picker mirrors the API's dual mode — the latest published
 * snapshot (no session id) or a live session's mounted workspace. Blame only
 * exists in published scope.
 */
export function FilesTab() {
  const { sandboxId, snapshot } = useSandbox();
  const [searchParams, setSearchParams] = useSearchParams();
  const path = searchParams.get("path") ?? "";
  const session = searchParams.get("session");
  const blameOn = searchParams.get("blame") === "1";

  const workspaces = snapshot?.sandboxes[0]?.workspaces ?? [];

  const apply = (next: { path?: string | null; session?: string | null; blame?: boolean }) => {
    const params = new URLSearchParams(searchParams);
    if (next.path !== undefined) {
      if (next.path) params.set("path", next.path);
      else params.delete("path");
    }
    if (next.session !== undefined) {
      if (next.session) params.set("session", next.session);
      else params.delete("session");
    }
    if (next.blame !== undefined) {
      if (next.blame) params.set("blame", "1");
      else params.delete("blame");
    }
    setSearchParams(params, { replace: true });
  };

  return (
    <div className="flex h-full min-h-0">
      <aside className="flex w-64 shrink-0 flex-col border-r border-line bg-surface">
        <div className="border-b border-line p-2">
          <label className="mb-1 block text-[11px] text-ink-faint">scope</label>
          <Select
            value={session ?? PUBLISHED}
            onValueChange={(value) =>
              apply({ session: value === PUBLISHED ? null : value, blame: false })
            }
          >
            <SelectTrigger className="w-full">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={PUBLISHED}>published snapshot</SelectItem>
              {workspaces.map((workspace) => (
                <SelectItem key={workspace.workspace_id} value={workspace.workspace_id}>
                  live · {workspace.workspace_id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <FileTree
          sandboxId={sandboxId}
          session={session}
          selectedPath={path}
          onSelect={(selected) => apply({ path: selected })}
        />
      </aside>

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-line bg-surface px-3 py-1.5">
          <span className="text-xs text-ink-faint">
            {session ? (
              <>
                live session <span className="font-mono text-ink-mid">{session}</span>
              </>
            ) : (
              "latest published snapshot"
            )}
          </span>
          <span className="ml-auto">
            <Tooltip
              content={
                session
                  ? "Blame reads the published auditability log and takes no session id — switch to the published snapshot to use it."
                  : "Color each line by its owner from the publish auditability log."
              }
            >
              <button
                type="button"
                disabled={session !== null}
                onClick={() => apply({ blame: !blameOn })}
                className={cn(
                  "flex items-center gap-1 rounded border px-2 py-0.5 text-[11px]",
                  session !== null
                    ? "cursor-not-allowed border-line text-ink-faint"
                    : blameOn
                      ? "border-accent bg-accent-soft text-accent"
                      : "border-line text-ink-mid hover:bg-surface-hover",
                )}
              >
                <GitBranch size={11} />
                blame
              </button>
            </Tooltip>
          </span>
        </div>
        {path === "" ? (
          <div className="mx-auto mt-16 max-w-md rounded-lg border border-line bg-surface p-8 text-center">
            <div className="text-sm font-semibold">Pick a file</div>
            <p className="mt-2 text-xs text-ink-mid">
              Browse the {session ? "live session workspace" : "published snapshot"}{" "}
              on the left. Blame is available in published scope.
            </p>
          </div>
        ) : (
          <FileView
            sandboxId={sandboxId}
            path={path}
            session={session}
            blameOn={blameOn && session === null}
          />
        )}
      </div>
    </div>
  );
}
