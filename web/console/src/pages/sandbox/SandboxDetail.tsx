import { useEffect, useRef } from "react";
import { NavLink, Outlet, useNavigate, useParams } from "react-router";
import { rpc, systemScope } from "@/api/rpc";
import type { SandboxRecord } from "@/api/types";
import { fetchSandboxSnapshot } from "@/api/observability";
import { usePoll } from "@/poll/usePoll";
import { useErrorToast } from "@/components/ErrorToast";
import { SandboxHeader } from "@/pages/sandbox/SandboxHeader";

const TABS = [
  { path: "", label: "Overview", end: true },
  { path: "terminal", label: "Terminal", end: false },
  { path: "files", label: "Files", end: false },
  { path: "layerstack", label: "LayerStack", end: false },
  { path: "observability", label: "Observability", end: false },
  { path: "preview", label: "Preview", end: false },
];

export function SandboxDetail() {
  const params = useParams();
  const sandboxId = params.sandboxId ?? "";
  const navigate = useNavigate();
  const { showError } = useErrorToast();
  const toastShownRef = useRef(false);

  const record = usePoll({
    key: ["sandbox", sandboxId, "inspect"],
    fn: () => rpc<SandboxRecord>("inspect_sandbox", systemScope, { sandbox_id: sandboxId }),
    mode: "slow",
    enabled: sandboxId !== "",
  });

  const ready = record.data?.state === "ready";
  const snapshot = usePoll({
    key: ["sandbox", sandboxId, "snapshot"],
    fn: () => fetchSandboxSnapshot(sandboxId),
    mode: (data) =>
      snapshotHasActivity(record.data ?? null, data ?? null) ? "fast" : "slow",
    enabled: sandboxId !== "" && ready,
  });

  useEffect(() => {
    if (record.error && !toastShownRef.current) {
      toastShownRef.current = true;
      showError(record.error);
    }
    if (!record.error) toastShownRef.current = false;
  }, [record.error, showError]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.isContentEditable)
      ) {
        return;
      }
      const index = Number(event.key) - 1;
      if (index >= 0 && index < TABS.length) {
        void navigate(TABS[index].path === "" ? "." : TABS[index].path);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line bg-surface">
        <SandboxHeader
          sandboxId={sandboxId}
          record={record.data ?? null}
          snapshot={snapshot.data}
        />
        <nav className="mt-1 flex gap-1 px-4">
          {TABS.map((tab) => (
            <NavLink
              key={tab.path}
              to={tab.path === "" ? "." : tab.path}
              end={tab.end}
              className={({ isActive }) =>
                `border-b-2 px-3 pb-2 pt-1 text-[13px] ${
                  isActive
                    ? "border-accent font-medium text-accent"
                    : "border-transparent text-ink-mid hover:text-ink"
                }`
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <Outlet
          context={{
            sandboxId,
            record: record.data ?? null,
            snapshot: snapshot.data ?? null,
            recordError: record.error ?? null,
          }}
        />
      </div>
    </div>
  );
}

export function snapshotHasActivity(
  record: SandboxRecord | null,
  snapshot?: Awaited<ReturnType<typeof fetchSandboxSnapshot>> | null,
): boolean {
  if (record?.state !== "ready") return false;
  return (snapshot?.sandboxes ?? []).some(
    (sandbox) =>
      sandbox.sandbox_id === record.id &&
      sandbox.workspaces.some(
        (workspace) => workspace.active_namespace_executions.length > 0,
      ),
  );
}
