import type { SandboxRecord } from "@/api/types";
import type { SnapshotResult } from "@/api/observability";
import { HealthDot } from "@/components/HealthDot";
import { DestroyAction } from "@/components/DestroyAction";
import { PortPreview } from "@/components/PortPreview";
import { SquashDialog } from "@/components/SquashDialog";
import { StateBadge } from "@/components/StateBadge";
import { Button } from "@/components/ui/button";
import { DialogTrigger } from "@/components/ui/dialog";
import { Tooltip } from "@/components/ui/tooltip";
import { shortHash } from "@/lib/format";

export function previewScopes(snapshot: SnapshotResult | undefined) {
  const workspaces = snapshot?.sandboxes[0]?.workspaces ?? [];
  return [
    { id: "shared", label: "shared network", isolated: false },
    ...workspaces
      .filter((workspace) => workspace.network_profile === "isolated")
      .map((workspace) => ({
        id: workspace.workspace_id,
        label: `isolated · ${workspace.workspace_id}`,
        isolated: true,
      })),
  ];
}

export function SandboxHeader({
  sandboxId,
  record,
  snapshot,
}: {
  sandboxId: string;
  record: SandboxRecord | null;
  snapshot: SnapshotResult | undefined;
}) {
  const layers = snapshot?.sandboxes[0]?.stack.layer_count;
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 pt-3">
      <span className="font-mono text-sm font-semibold">{sandboxId}</span>
      {record ? <StateBadge state={record.state} /> : null}
      {record?.state === "ready" ? (
        <HealthDot sandboxId={sandboxId} showLabel />
      ) : null}
      {record ? (
        <span className="truncate font-mono text-xs text-ink-mid" title="workspace bind root">
          {record.workspace_root}
        </span>
      ) : null}
      {record?.daemon ? (
        <span className="font-mono text-xs text-ink-faint" title="daemon RPC endpoint">
          rpc {record.daemon.host}:{record.daemon.port}
        </span>
      ) : null}
      {record?.daemon_http ? (
        <span className="font-mono text-xs text-ink-faint" title="daemon_http endpoint">
          http {record.daemon_http.host}:{record.daemon_http.port}
        </span>
      ) : null}
      {record?.shared_base ? (
        <Tooltip
          content={`shared read-only base · root ${record.shared_base.root_hash}`}
        >
          <span className="rounded border border-line bg-idle-soft px-1.5 py-px font-mono text-[11px] text-ink-mid">
            base {shortHash(record.shared_base.root_hash)}
          </span>
        </Tooltip>
      ) : null}
      <span className="ml-auto flex items-center gap-2">
        {record?.state === "ready" ? (
          <>
            <PortPreview sandboxId={sandboxId} scopes={previewScopes(snapshot)} />
            <SquashDialog
              sandboxId={sandboxId}
              layerCount={layers}
              trigger={
                <DialogTrigger asChild>
                  <Button size="sm">Squash</Button>
                </DialogTrigger>
              }
            />
          </>
        ) : null}
        <DestroyAction sandboxId={sandboxId} />
      </span>
    </div>
  );
}
