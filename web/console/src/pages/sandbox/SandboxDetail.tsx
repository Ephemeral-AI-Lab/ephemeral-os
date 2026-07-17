import { useEffect, useRef } from "react";
import { Alert, Box, Tabs } from "@mantine/core";
import { Outlet, useLocation, useNavigate, useParams } from "react-router";
import { rpc, systemScope } from "@/api/rpc";
import type { SandboxRecord } from "@/api/types";
import { usePoll } from "@/poll/usePoll";
import {
  snapshotHasActivity,
  useSandboxSnapshot,
} from "@/poll/useSandboxSnapshot";
import { useErrorToast } from "@/components/ErrorToast";
import { SandboxHeader } from "@/pages/sandbox/SandboxHeader";

const TABS = [
  { value: "terminal", path: "terminal", label: "Terminal" },
  { value: "files", path: "files", label: "Files" },
  { value: "observability", path: "observability", label: "Observability" },
  { value: "preview", path: "preview", label: "Preview" },
];

function currentTab(pathname: string, basePath: string): string {
  const match = TABS.find(
    (tab) =>
      pathname === `${basePath}/${tab.path}` || pathname.startsWith(`${basePath}/${tab.path}/`),
  );
  return match?.value ?? "terminal";
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

  const snapshot = useSandboxSnapshot(sandboxId, record.data ?? null);

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
        {record.data && record.data.state !== "ready" ? (
          <Alert
            color={record.data.state === "failed" ? "danger" : "warning"}
            m="md"
            title={`Sandbox ${record.data.state}`}
            variant="light"
          >
            Daemon-backed tools are unavailable until this sandbox is ready.
          </Alert>
        ) : null}
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

export { snapshotHasActivity };
