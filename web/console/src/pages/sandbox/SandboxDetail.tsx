import { useEffect, useRef } from "react";
import { Box, Tabs } from "@mantine/core";
import { Outlet, useLocation, useNavigate, useParams } from "react-router";
import { rpc, systemScope } from "@/api/rpc";
import type { SandboxRecord } from "@/api/types";
import { fetchSandboxSnapshot } from "@/api/observability";
import { usePoll } from "@/poll/usePoll";
import { useErrorToast } from "@/components/ErrorToast";
import { SandboxHeader } from "@/pages/sandbox/SandboxHeader";

const TABS = [
  { value: "overview", path: "", label: "Overview" },
  { value: "terminal", path: "terminal", label: "Terminal" },
  { value: "files", path: "files", label: "Files" },
  { value: "observability", path: "observability", label: "Observability" },
  { value: "preview", path: "preview", label: "Preview" },
];

function currentTab(pathname: string, basePath: string): string {
  const match = TABS.find(
    (tab) =>
      (tab.path === "" && pathname === basePath) ||
      (tab.path !== "" && (pathname === `${basePath}/${tab.path}` || pathname.startsWith(`${basePath}/${tab.path}/`))),
  );
  return match?.value ?? "overview";
}

export function SandboxDetail() {
  const params = useParams();
  const sandboxId = params.sandboxId ?? "";
  const navigate = useNavigate();
  const location = useLocation();
  const { showError } = useErrorToast();
  const toastShownRef = useRef(false);
  const basePath = `/sandboxes/${encodeURIComponent(sandboxId)}`;

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

  return (
    <Box data-sandbox-detail>
      <Box data-sandbox-navigation>
        <SandboxHeader
          sandboxId={sandboxId}
          record={record.data ?? null}
          snapshot={snapshot.data}
        />
        <Tabs
          onChange={(value) => {
            const tab = TABS.find((item) => item.value === value);
            if (tab) void navigate(tab.path ? `${basePath}/${tab.path}` : basePath);
          }}
          value={currentTab(location.pathname, basePath)}
          variant="outline"
        >
          <Tabs.List aria-label="Sandbox navigation" data-sandbox-tabs>
            {TABS.map((tab) => (
              <Tabs.Tab key={tab.value} value={tab.value}>
                {tab.label}
              </Tabs.Tab>
            ))}
          </Tabs.List>
        </Tabs>
      </Box>
      <Box data-route-scroll-owner="sandbox">
        <Outlet
          context={{
            sandboxId,
            record: record.data ?? null,
            snapshot: snapshot.data ?? null,
            recordError: record.error ?? null,
          }}
        />
      </Box>
    </Box>
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
