import { Link } from "react-router";
import { rpc, systemScope } from "@/api/rpc";
import type { SandboxList } from "@/api/types";
import { usePoll } from "@/poll/usePoll";
import { StateBadge } from "@/components/StateBadge";

export function FleetBoard() {
  const sandboxes = usePoll({
    key: ["fleet", "list_sandboxes"],
    fn: () => rpc<SandboxList>("list_sandboxes", systemScope),
    mode: "slow",
  });

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-4">
      <h1 className="text-base font-semibold">Fleet</h1>
      <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
        {(sandboxes.data?.sandboxes ?? []).map((record) => (
          <Link
            key={record.id}
            to={`/sandboxes/${encodeURIComponent(record.id)}`}
            className="rounded-lg border border-line bg-surface p-3 hover:border-accent"
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-mono text-[13px]">{record.id}</span>
              <StateBadge state={record.state} />
            </div>
            <div className="mt-1 truncate font-mono text-xs text-ink-mid">
              {record.workspace_root}
            </div>
          </Link>
        ))}
      </div>
      {sandboxes.data && sandboxes.data.sandboxes.length === 0 ? (
        <div className="mt-8 rounded-lg border border-line bg-surface p-8 text-center text-sm text-ink-mid">
          No sandboxes yet.
        </div>
      ) : null}
    </div>
  );
}
