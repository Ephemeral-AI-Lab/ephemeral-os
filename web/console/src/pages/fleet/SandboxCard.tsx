import { Link, useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Trash2 } from "lucide-react";
import { rpcStream, systemScope } from "@/api/rpc";
import type { SandboxRecord } from "@/api/types";
import { inFlightCount, type SandboxSnapshot } from "@/api/observability";
import { recordSample } from "@/lib/sparkHistory";
import { ConfirmDestroyDialog } from "@/components/ConfirmDestroyDialog";
import { HealthDot } from "@/components/HealthDot";
import { ResourceSparkline } from "@/components/ResourceSparkline";
import { SquashDialog } from "@/components/SquashDialog";
import { StateBadge } from "@/components/StateBadge";
import { StreamLogPane } from "@/components/StreamLogPane";
import { Button } from "@/components/ui/button";
import { DialogTrigger } from "@/components/ui/dialog";
import { useErrorToast } from "@/components/ErrorToast";

export function SandboxCard({
  record,
  snapshot,
  createLogs,
}: {
  record: SandboxRecord;
  snapshot: SandboxSnapshot | undefined;
  createLogs: string[] | undefined;
}) {
  const navigate = useNavigate();
  const spark = recordSample(record.id, snapshot?.resources.latest ?? null);
  const sessions = snapshot?.workspaces.length ?? 0;
  const commands = snapshot ? inFlightCount(snapshot) : 0;
  const layers = snapshot?.stack.layer_count;

  return (
    <article className="group flex h-full min-h-56 flex-col overflow-hidden rounded-xl border border-line bg-surface shadow-sm transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-md motion-reduce:transform-none motion-reduce:transition-none">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <Link
          to={`/sandboxes/${encodeURIComponent(record.id)}`}
          className="min-w-0 flex-1 truncate font-mono text-[13px] font-medium text-ink transition-colors hover:text-accent group-hover:text-accent"
        >
          {record.id}
        </Link>
        <StateBadge state={record.state} />
        {record.state === "ready" ? <HealthDot sandboxId={record.id} /> : null}
      </div>

      <div className="flex min-h-0 flex-1 flex-col gap-3 px-4 py-3">
        <div className="min-w-0 rounded-md border border-line bg-app px-2.5 py-2">
          <div className="mb-1 text-[10px] font-medium uppercase tracking-[0.12em] text-ink-faint">
            Workspace
          </div>
          <div className="truncate font-mono text-xs text-ink-mid" title={record.workspace_root}>
            {record.workspace_root}
          </div>
        </div>

        {record.state === "creating" ? (
          <StreamLogPane lines={createLogs ?? []} maxHeightClass="max-h-32" />
        ) : null}

        {record.state === "failed" ? (
          <div className="rounded-md border border-danger/40 bg-danger-soft p-2.5 text-xs text-danger">
            Sandbox failed to reach ready. Inspect the record for endpoint and
            state details.
          </div>
        ) : null}

        {record.state === "ready" ? (
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 rounded-md border border-line bg-app px-2.5 py-2 text-xs text-ink-mid">
            <div className="min-w-0">
              <div className="text-[10px] font-medium uppercase tracking-[0.12em] text-ink-faint">
                Activity
              </div>
              <div className="mt-1 truncate font-medium text-ink">
                {sessions} {sessions === 1 ? "session" : "sessions"} · {commands}{" "}
                {commands === 1 ? "cmd" : "cmds"}
              </div>
            </div>
            <div className="grid gap-1 text-[11px]">
              <span className="flex items-center justify-end gap-1">
                cpu <ResourceSparkline values={spark.cpu} label="cpu" />
              </span>
              <span className="flex items-center justify-end gap-1">
                mem <ResourceSparkline values={spark.mem} label="memory" />
              </span>
            </div>
          </div>
        ) : null}
      </div>

      <div className="mt-auto flex items-center gap-2 border-t border-line px-4 py-3">
        {record.state === "ready" ? (
          <>
            <Button
              size="sm"
              variant="primary"
              onClick={() => void navigate(`/sandboxes/${encodeURIComponent(record.id)}`)}
            >
              Open
            </Button>
            <SquashDialog
              sandboxId={record.id}
              layerCount={layers}
              trigger={
                <DialogTrigger asChild>
                  <Button size="sm">Squash{typeof layers === "number" ? ` (${layers})` : ""}</Button>
                </DialogTrigger>
              }
            />
          </>
        ) : null}
        {record.state === "failed" ? (
          <Button
            size="sm"
            onClick={() => void navigate(`/sandboxes/${encodeURIComponent(record.id)}`)}
          >
            Inspect
          </Button>
        ) : null}
        <span className="ml-auto">
          <DestroyAction sandboxId={record.id} />
        </span>
      </div>
    </article>
  );
}

function DestroyAction({ sandboxId }: { sandboxId: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const { showError } = useErrorToast();
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const destroy = async () => {
    setBusy(true);
    setLogs([]);
    try {
      await rpcStream(
        "destroy_sandbox",
        systemScope,
        { sandbox_id: sandboxId },
        (line) => setLogs((current) => [...current, line]),
      );
      setOpen(false);
      void queryClient.invalidateQueries({ queryKey: ["fleet"] });
      void navigate("/");
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmDestroyDialog
      sandboxId={sandboxId}
      open={open}
      onOpenChange={setOpen}
      onConfirm={() => void destroy()}
      busy={busy}
      logLines={logs}
      trigger={
        <DialogTrigger asChild>
          <Button size="sm" variant="danger" aria-label={`Destroy ${sandboxId}`}>
            <Trash2 size={12} />
          </Button>
        </DialogTrigger>
      }
    />
  );
}
