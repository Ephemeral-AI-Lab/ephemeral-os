import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { Search } from "lucide-react";
import { Input } from "@mantine/core";
import { rpc, systemScope } from "@/api/rpc";
import type { SandboxList } from "@/api/types";
import { fetchFleetSnapshot } from "@/api/observability";
import { usePoll } from "@/poll/usePoll";
import { CreateSandboxModal } from "@/pages/fleet/CreateSandboxModal";
import { FleetSummaryBar } from "@/pages/fleet/FleetSummaryBar";
import { SandboxCard } from "@/pages/fleet/SandboxCard";

export function FleetBoard() {
  const [searchParams, setSearchParams] = useSearchParams();
  const filter = searchParams.get("q") ?? "";
  const filterRef = useRef<HTMLInputElement>(null);
  const [createLogs, setCreateLogs] = useState<string[] | null>(null);

  const list = usePoll({
    key: ["fleet", "list_sandboxes"],
    fn: () => rpc<SandboxList>("list_sandboxes", systemScope),
    mode: "slow",
  });
  const lifecycleActive =
    createLogs !== null ||
    (list.data?.sandboxes ?? []).some(
      (record) => record.state === "creating" || record.state === "stopping",
    );
  const listFast = usePoll({
    key: ["fleet", "list_sandboxes", "fast"],
    fn: () => rpc<SandboxList>("list_sandboxes", systemScope),
    mode: "fast",
    enabled: lifecycleActive,
  });
  const snapshot = usePoll({
    key: ["fleet", "snapshot"],
    fn: fetchFleetSnapshot,
    mode: "slow",
  });

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      event.preventDefault();
      filterRef.current?.focus();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const fleet = currentFleetList(list.data, listFast.data, lifecycleActive);
  const records = fleet?.sandboxes ?? [];
  const snapshots = new Map(
    (snapshot.data?.sandboxes ?? []).map((entry) => [entry.sandbox_id, entry]),
  );
  const needle = filter.trim().toLowerCase();
  const visible = needle
    ? records.filter(
        (record) =>
          record.id.toLowerCase().includes(needle) ||
          record.state.toLowerCase().includes(needle),
      )
    : records;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2">
        <div className="relative w-72">
          <Search
            size={13}
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint"
          />
          <Input
            ref={filterRef}
            value={filter}
            onChange={(event) => {
              const next = new URLSearchParams(searchParams);
              if (event.target.value) next.set("q", event.target.value);
              else next.delete("q");
              setSearchParams(next, { replace: true });
            }}
            placeholder="filter by id or state ( / )"
            className="w-full pl-7"
          />
        </div>
        <div className="ml-auto">
          <CreateSandboxModal onStream={setCreateLogs} />
        </div>
      </div>

      <FleetSummaryBar list={fleet} snapshot={snapshot.data} />

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
        {list.isError && !list.data ? (
          <div className="mx-auto mt-16 max-w-md rounded-lg border border-danger/40 bg-danger-soft p-8 text-center">
            <div className="text-sm font-semibold text-danger">Gateway unreachable</div>
            <p className="mt-2 break-words text-xs text-ink-mid">
              {(list.error as Error).message} — retrying automatically.
            </p>
          </div>
        ) : visible.length === 0 && list.data ? (
          <div className="mx-auto mt-16 max-w-md rounded-lg border border-line bg-surface p-8 text-center">
            <div className="text-sm font-semibold">
              {records.length === 0 ? "No sandboxes yet" : "No matches"}
            </div>
            <p className="mt-2 text-xs text-ink-mid">
              {records.length === 0
                ? "Create the first sandbox to get started."
                : `Nothing matches “${filter}”.`}
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 items-stretch gap-4 md:grid-cols-2 xl:grid-cols-3">
            {visible.map((record) => (
              <SandboxCard
                key={record.id}
                record={record}
                snapshot={snapshots.get(record.id)}
                createLogs={record.state === "creating" ? (createLogs ?? undefined) : undefined}
              />
            ))}
            {createLogs !== null && !records.some((record) => record.state === "creating") ? (
              <div className="flex min-h-56 flex-col rounded-xl border border-dashed border-run/50 bg-surface p-4 shadow-sm">
                <div className="mb-2 text-xs font-medium text-run">creating…</div>
                <div className="max-h-32 overflow-y-auto rounded border border-line bg-app p-2 font-mono text-[11px] text-ink-mid">
                  {createLogs.length === 0 ? "starting…" : createLogs.map((line, i) => <div key={i}>{line}</div>)}
                </div>
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

export function currentFleetList(
  slow: SandboxList | undefined,
  fast: SandboxList | undefined,
  lifecycleActive: boolean,
): SandboxList | undefined {
  return lifecycleActive ? fast ?? slow : slow;
}
