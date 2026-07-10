import type { WorkspaceSnapshot } from "@/api/observability";

export function SessionSidebar({
  workspaces,
  selected,
  onSelect,
}: {
  workspaces: WorkspaceSnapshot[];
  selected: string | null;
  onSelect: (sessionId: string | null) => void;
}) {
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
          <SessionRow
            key={workspace.workspace_id}
            label={workspace.workspace_id}
            hint={`${workspace.network_profile} · ${workspace.layers.layer_count} layers`}
            active={selected === workspace.workspace_id}
            onClick={() => onSelect(workspace.workspace_id)}
          />
        ))}
        {workspaces.length === 0 ? (
          <p className="px-2 py-3 text-[11px] text-ink-faint">
            No live sessions. Run a command below to publish one.
          </p>
        ) : null}
      </div>
    </aside>
  );
}

function SessionRow({
  label,
  hint,
  active,
  onClick,
}: {
  label: string;
  hint: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <div
      className={`mb-0.5 rounded px-2 py-1.5 ${
        active ? "bg-accent-soft" : "hover:bg-surface-hover"
      }`}
    >
      <button type="button" onClick={onClick} className="w-full min-w-0 text-left">
        <span
          className={`block truncate font-mono text-xs ${active ? "font-medium text-accent" : "text-ink"}`}
        >
          {label}
        </span>
        <span className="block truncate text-[10px] text-ink-faint">{hint}</span>
      </button>
    </div>
  );
}
