import { useEffect, useRef } from "react";
import { NavLink, Outlet, useNavigate, useParams } from "react-router";
import { rpc, systemScope, RpcError } from "@/api/rpc";
import type { SandboxRecord } from "@/api/types";
import { usePoll } from "@/poll/usePoll";
import { StateBadge } from "@/components/StateBadge";
import { useErrorToast } from "@/components/ErrorToast";

const TABS = [
  { path: "", label: "Overview", end: true },
  { path: "terminal", label: "Terminal", end: false },
  { path: "files", label: "Files", end: false },
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

  const unknownSandbox =
    record.error instanceof RpcError &&
    !record.error.transport &&
    record.error.message.includes("not found");

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b border-line bg-surface px-4 pt-3">
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm font-semibold">{sandboxId}</span>
          {record.data ? <StateBadge state={record.data.state} /> : null}
          {unknownSandbox ? <StateBadge state="danger" label="not found" /> : null}
        </div>
        <nav className="mt-2 flex gap-1">
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
      <div className="min-h-0 flex-1">
        <Outlet context={{ sandboxId, record: record.data ?? null }} />
      </div>
    </div>
  );
}
