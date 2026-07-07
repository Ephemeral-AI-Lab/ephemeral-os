import { Link } from "react-router";
import type { ReactNode } from "react";
import type { SandboxSnapshot, WorkspaceSnapshot } from "@/api/observability";
import { useSandbox } from "@/pages/sandbox/SandboxContext";
import { StateBadge } from "@/components/StateBadge";
import { formatBytes, shortHash } from "@/lib/format";

export function OverviewTab() {
  const { sandboxId, record, snapshot } = useSandbox();
  const sandboxSnapshot = snapshot?.sandboxes[0];

  return (
    <div className="grid grid-cols-1 gap-3 p-4 lg:grid-cols-2">
      <Panel title="Record">
        {record ? (
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-xs">
            <Field name="id" value={<Mono>{record.id}</Mono>} />
            <Field name="state" value={<StateBadge state={record.state} />} />
            <Field name="workspace_root" value={<Mono>{record.workspace_root}</Mono>} />
            <Field
              name="daemon"
              value={
                record.daemon ? (
                  <Mono>
                    {record.daemon.host}:{record.daemon.port}
                  </Mono>
                ) : (
                  <Faint>none</Faint>
                )
              }
            />
            <Field
              name="daemon_http"
              value={
                record.daemon_http ? (
                  <Mono>
                    {record.daemon_http.host}:{record.daemon_http.port}
                  </Mono>
                ) : (
                  <Faint>none</Faint>
                )
              }
            />
            <Field
              name="shared_base"
              value={
                record.shared_base ? (
                  <span className="font-mono break-all">
                    {shortHash(record.shared_base.root_hash, 16)} →{" "}
                    {record.shared_base.target}
                    {record.shared_base.readonly ? " (ro)" : ""}
                  </span>
                ) : (
                  <Faint>none</Faint>
                )
              }
            />
          </dl>
        ) : (
          <Skeleton />
        )}
      </Panel>

      <Panel title="Resources">
        {sandboxSnapshot ? (
          <ResourceSnapshotPanel snapshot={sandboxSnapshot} />
        ) : (
          <Empty>no snapshot — sandbox not ready</Empty>
        )}
      </Panel>

      <Panel title="Workspace sessions">
        {sandboxSnapshot ? (
          sandboxSnapshot.workspaces.length > 0 ? (
            <ul className="flex flex-col gap-2">
              {sandboxSnapshot.workspaces.map((workspace) => (
                <WorkspaceRow
                  key={workspace.workspace_id}
                  sandboxId={sandboxId}
                  workspace={workspace}
                />
              ))}
            </ul>
          ) : (
            <Empty>
              no live sessions —{" "}
              <Link to="../terminal" className="text-accent hover:underline">
                create one in the Terminal tab
              </Link>
            </Empty>
          )
        ) : (
          <Skeleton />
        )}
      </Panel>

      <Panel title="In-flight executions">
        {sandboxSnapshot ? (
          <InFlightExecutions sandboxId={sandboxId} snapshot={sandboxSnapshot} />
        ) : (
          <Skeleton />
        )}
      </Panel>
    </div>
  );
}

function WorkspaceRow({
  sandboxId,
  workspace,
}: {
  sandboxId: string;
  workspace: WorkspaceSnapshot;
}) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-line px-2 py-1.5 text-xs">
      <Link
        to={`/sandboxes/${encodeURIComponent(sandboxId)}/terminal?session=${encodeURIComponent(workspace.workspace_id)}`}
        className="font-mono text-ink hover:text-accent"
      >
        {workspace.workspace_id}
      </Link>
      <span className="rounded bg-idle-soft px-1 py-px text-[11px] text-ink-mid">
        {workspace.network_profile}
      </span>
      <span className="text-ink-mid">{workspace.lifecycle_state}</span>
      <span className="ml-auto flex items-center gap-3 text-ink-mid">
        <span>{workspace.layers.layer_count} layers</span>
        <span className="font-mono" title="base root hash">
          {shortHash(workspace.layers.base_root_hash)}
        </span>
      </span>
    </li>
  );
}

function InFlightExecutions({
  sandboxId,
  snapshot,
}: {
  sandboxId: string;
  snapshot: SandboxSnapshot;
}) {
  const executions = snapshot.workspaces.flatMap((workspace) =>
    workspace.active_namespace_executions.map((execution) => ({
      ...execution,
      workspace_id: workspace.workspace_id,
    })),
  );
  if (executions.length === 0) {
    return <Empty>nothing running right now</Empty>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {executions.map((execution) => (
        <li
          key={execution.namespace_execution_id}
          className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded border border-line px-2 py-1.5 text-xs"
        >
          <Link
            to={`/sandboxes/${encodeURIComponent(sandboxId)}/terminal#cmd-${encodeURIComponent(execution.namespace_execution_id)}`}
            className="font-mono text-accent hover:underline"
          >
            {execution.namespace_execution_id}
          </Link>
          <span className="text-ink-mid">{execution.operation}</span>
          <span className="ml-auto">
            <StateBadge state="run" label={execution.lifecycle_state} />
          </span>
          <span className="w-full font-mono text-[11px] text-ink-faint">
            in {execution.workspace_id}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ResourceSnapshotPanel({ snapshot }: { snapshot: SandboxSnapshot }) {
  const latest = snapshot.resources.latest;
  const mem = latest?.metrics["mem_cur"];
  const cpuDelta = latest?.deltas["cpu_usec"];
  return (
    <div className="flex flex-col gap-2 text-xs">
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        <Metric
          label="memory"
          value={typeof mem === "number" ? formatBytes(mem) : "–"}
        />
        <Metric
          label="cpu Δ"
          value={typeof cpuDelta === "number" ? `${cpuDelta} µs` : "–"}
        />
        <Metric label="layers" value={String(snapshot.stack.layer_count)} />
        <Metric
          label="stack bytes"
          value={formatBytes(snapshot.stack.layers_bytes)}
        />
        <Metric label="leases" value={String(snapshot.stack.active_leases)} />
      </div>
      {snapshot.workspaces.map((workspace) => {
        const disk = workspace.resources.latest?.metrics["disk_bytes"];
        const files = workspace.resources.latest?.metrics["files"];
        return (
          <div
            key={workspace.workspace_id}
            className="flex flex-wrap gap-x-6 gap-y-1 border-t border-line pt-2"
          >
            <span className="font-mono text-ink-mid">{workspace.workspace_id}</span>
            <Metric
              label="disk"
              value={typeof disk === "number" ? formatBytes(disk) : "–"}
            />
            <Metric
              label="files"
              value={typeof files === "number" ? String(files) : "–"}
            />
          </div>
        );
      })}
    </div>
  );
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-line bg-surface p-3">
      <h2 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-mid">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Field({ name, value }: { name: string; value: ReactNode }) {
  return (
    <>
      <dt className="text-ink-faint">{name}</dt>
      <dd className="min-w-0 break-all">{value}</dd>
    </>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <span className="text-ink-mid">
      {label} <span className="font-mono text-ink">{value}</span>
    </span>
  );
}

function Mono({ children }: { children: ReactNode }) {
  return <span className="font-mono">{children}</span>;
}

function Faint({ children }: { children: ReactNode }) {
  return <span className="text-ink-faint">{children}</span>;
}

function Empty({ children }: { children: ReactNode }) {
  return <p className="text-xs text-ink-faint">{children}</p>;
}

function Skeleton() {
  return (
    <div className="flex animate-pulse flex-col gap-2">
      <div className="h-3 w-2/3 rounded bg-idle-soft" />
      <div className="h-3 w-1/2 rounded bg-idle-soft" />
      <div className="h-3 w-3/5 rounded bg-idle-soft" />
    </div>
  );
}
